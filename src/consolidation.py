"""Консолидация — «забывание» как феномен.

Два процесса, имитирующих биологическую консолидацию памяти:

  1. **Content-graph pruning** — удаляет слабые ноды графа мыслей:
     hypothesis/thought с низкой confidence + давно не тронутые +
     не связанные с активной целью + без входящих ссылок от goal/fact.

  2. **State-graph archiving** — переносит старые tick-снапшоты из
     основного `state_graph.jsonl` в `state_graph.archive.jsonl`.
     Парент-цепочка остаётся валидной: архивные хэши продолжают существовать
     (просто в другом файле).

Оба процесса опциональны, дают `dry_run` для проверки. Триггерятся:
  - вручную через POST /graph/consolidate
  - автоматически CognitiveLoop раз в 24 часа когда NE низкое (sleep-like)

Принцип: **забывание — это фича, не баг**. Граф не должен расти линейно
в N тиков; слабая информация должна уходить, освобождая внимание для
релевантной.
"""
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .graph_logic import _graph, _remove_node
from .state_graph import get_state_graph

log = logging.getLogger(__name__)


# ── Configuration (hard-coded defaults, can be moved to settings later) ─────

CONTENT_CONFIDENCE_THRESHOLD = 0.3   # ниже этого = слабая нода
CONTENT_AGE_DAYS = 30                # давность last_accessed для прунинга
STATE_RETAIN_DAYS = 14                # сколько дней держим в основном файле


def _age_days(ts_iso) -> Optional[float]:
    """Возраст в днях от ISO timestamp до now (UTC). None если не парсится."""
    if not ts_iso:
        return None
    try:
        ts = datetime.fromisoformat(str(ts_iso).replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0
    except Exception:
        return None


# ── Content-graph consolidation ─────────────────────────────────────────────

def consolidate_content_graph(
    confidence_threshold: float = CONTENT_CONFIDENCE_THRESHOLD,
    age_days: float = CONTENT_AGE_DAYS,
    dry_run: bool = False,
) -> dict:
    """Прунинг слабых веток content-графа.

    Кандидат на удаление должен одновременно:
      • type ∈ {hypothesis, thought}
      • confidence < confidence_threshold
      • last_accessed (или created_at) старше age_days
      • НЕ входит в subgoals какой-либо цели
      • НЕТ входящих directed-рёбер от goal / fact / action ноды
      • НЕТ исходящих evidence-связей (evidence_target) от других нод

    Возвращает {"removed": N, "candidates": [idx...]} (либо dry-run preview).
    """
    nodes = _graph["nodes"]
    if not nodes:
        return {"removed": 0, "candidates": [], "total_before": 0, "total_after": 0}

    # Защитные множества
    goal_subgoals: set[int] = set()
    for n in nodes:
        if n.get("type") == "goal":
            for sg in (n.get("subgoals") or []):
                if isinstance(sg, int):
                    goal_subgoals.add(sg)

    # Ноды, на которые ссылаются goal/fact/action через directed
    protected_by_strong: set[int] = set()
    for pair in _graph.get("edges", {}).get("directed", []):
        if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
            continue
        src, dst = pair
        if 0 <= src < len(nodes) and nodes[src].get("type") in ("goal", "fact", "action"):
            protected_by_strong.add(dst)

    # Ноды на которые указывает evidence из других нод (они поддерживают гипотезу)
    evidence_targets: set[int] = set()
    for n in nodes:
        if n.get("type") == "evidence":
            t = n.get("evidence_target")
            if isinstance(t, int):
                evidence_targets.add(t)

    # Кандидаты
    candidates: list[int] = []
    for i, n in enumerate(nodes):
        if n.get("type") not in ("hypothesis", "thought"):
            continue
        if n.get("depth", 0) < 0:
            continue  # topic roots
        if float(n.get("confidence", 0.5)) >= confidence_threshold:
            continue
        if i in goal_subgoals or i in protected_by_strong or i in evidence_targets:
            continue
        age = _age_days(n.get("last_accessed") or n.get("created_at"))
        if age is None or age < age_days:
            continue
        candidates.append(i)

    total_before = len(nodes)

    if dry_run or not candidates:
        return {
            "removed": 0 if dry_run else 0,
            "candidates": candidates,
            "total_before": total_before,
            "total_after": total_before,
            "dry_run": dry_run,
        }

    # Удаление от конца к началу — индексы не сдвинутся до обработки
    for idx in sorted(candidates, reverse=True):
        _remove_node(idx)

    total_after = len(_graph["nodes"])
    log.info(f"[consolidation] content pruned {len(candidates)} nodes "
             f"({total_before} -> {total_after})")
    return {
        "removed": len(candidates),
        "candidates": candidates,
        "total_before": total_before,
        "total_after": total_after,
        "dry_run": False,
    }


# ── State-graph consolidation (archive old entries) ─────────────────────────

def consolidate_state_graph(
    retain_days: float = STATE_RETAIN_DAYS,
    dry_run: bool = False,
) -> dict:
    """Архивирует старые state_graph entries в `state_graph.archive.jsonl`.

    Retention:
      - Новее retain_days (timestamp проходит)  → остаются в основном файле
      - Старше retain_days                        → переезжают в archive
      - Не парсятся / без timestamp                → остаются (safe)

    Парент-цепочка: хэши архивных entries остаются валидными (в архивном файле).
    Последний entry в основном файле продолжает чейнить на предка из архива.
    """
    sg = get_state_graph()
    path: Path = sg.path
    if not path.exists():
        return {"archived": 0, "retained": 0, "dry_run": dry_run}

    retain: list[str] = []
    archive: list[str] = []
    cutoff_sec = retain_days * 86400
    now_utc = datetime.now(timezone.utc)

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            raw = line.rstrip("\n")
            if not raw.strip():
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                # Сохраняем нераспарсенные строки в main — не трогаем
                retain.append(raw)
                continue
            ts_iso = entry.get("timestamp")
            keep = True
            if ts_iso:
                try:
                    ts = datetime.fromisoformat(str(ts_iso).replace("Z", "+00:00"))
                    age_sec = (now_utc - ts).total_seconds()
                    keep = age_sec < cutoff_sec
                except Exception:
                    keep = True
            (retain if keep else archive).append(raw)

    if dry_run:
        return {
            "archived": len(archive),
            "retained": len(retain),
            "dry_run": True,
        }

    if not archive:
        return {"archived": 0, "retained": len(retain), "dry_run": False}

    archive_path = path.parent / "state_graph.archive.jsonl"
    # Append archived to archive file (preserve order)
    with archive_path.open("a", encoding="utf-8") as f:
        for line in archive:
            f.write(line + "\n")

    # Rewrite main file atomically via .tmp
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        for line in retain:
            f.write(line + "\n")
    shutil.move(str(tmp_path), str(path))

    log.info(f"[consolidation] state_graph archived {len(archive)} "
             f"entries (retained {len(retain)}) -> {archive_path.name}")
    return {
        "archived": len(archive),
        "retained": len(retain),
        "archive_path": str(archive_path),
        "dry_run": False,
    }


# ── Combined entry (endpoint + nightly) ─────────────────────────────────────

def consolidate_all(
    confidence_threshold: float = CONTENT_CONFIDENCE_THRESHOLD,
    content_age_days: float = CONTENT_AGE_DAYS,
    state_retain_days: float = STATE_RETAIN_DAYS,
    dry_run: bool = False,
) -> dict:
    """Run both consolidation passes. Returns combined summary."""
    content = consolidate_content_graph(
        confidence_threshold=confidence_threshold,
        age_days=content_age_days,
        dry_run=dry_run,
    )
    state = consolidate_state_graph(
        retain_days=state_retain_days,
        dry_run=dry_run,
    )
    return {"content": content, "state": state, "dry_run": dry_run}
