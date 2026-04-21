"""Cross-graph seed — continuity между сессиями через state-граф.

Выводы одной сессии остаются в state_graph как tick-entries (GOAL REACHED,
Scout bridges, конвергенции). Новая сессия начинается не с пустого листа:
cross-graph модуль извлекает conclusions из недавней истории и создаёт
seed-ноды в текущем content-графе с унаследованными embedding'ами.

Seeds создаются как `rendered=False` (см. docs/thinking-operations.md § Embedding-first):
embedding есть, текст-плейсхолдер разворачивается лениво когда юзер откроет.

Модуль не автоматический — предоставляет `seed_from_history()` для явного
вызова. CognitiveLoop вызывает его опционально при switch на пустой
workspace с накопленной историей.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)


# ── Conclusion extraction ───────────────────────────────────────────────────

# Priority: какие tick-entries считаются «выводами».
# Чем меньше число, тем важнее (goal reached > scout bridge > tick).
_CONCLUSION_PRIORITY = {
    "stable": 1,       # GOAL REACHED или synthesis — высший приоритет
    "compare": 2,      # XOR-судейство (выбрали победителя)
    "collapse": 3,     # кластер сложился в synthesis
    "pump": 4,         # Scout bridge (если сохранён)
    "smartdc": 5,      # отдельное подтверждение
}


def _entry_priority(entry: dict) -> Optional[int]:
    """Приоритет entry как conclusion. None если не conclusion."""
    action = entry.get("action", "")
    if action == "stable":
        reason = entry.get("reason", "") or ""
        if "GOAL REACHED" in reason or entry.get("phase") == "synthesize":
            return _CONCLUSION_PRIORITY["stable"]
        return None
    return _CONCLUSION_PRIORITY.get(action)


def _age_days(ts_iso) -> Optional[float]:
    if not ts_iso:
        return None
    try:
        ts = datetime.fromisoformat(str(ts_iso).replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0
    except Exception:
        return None


def extract_conclusions(
    days: float = 7.0,
    limit: int = 10,
    graph_id: Optional[str] = None,
) -> list[dict]:
    """Читает state_graph и возвращает conclusion-entries за последние `days`.

    Фильтры:
      - timestamp не старше `days`
      - action ∈ приоритетной таблице
      - optional graph_id match (если не указан — любой)

    Дедуп по content_touched: один и тот же triple нод в разных tick'ах
    не плодит дубли. Сортировка по (priority, recency).
    """
    from .state_graph import get_state_graph

    sg = get_state_graph()
    entries = sg.read_all()
    if not entries:
        return []

    candidates: list[tuple[int, str, dict]] = []
    seen_signatures: set[tuple] = set()

    for entry in entries:
        # Graph filter
        if graph_id and entry.get("graph_id") != graph_id:
            continue
        # Age filter
        age = _age_days(entry.get("timestamp"))
        if age is None or age > days:
            continue
        # Priority filter
        prio = _entry_priority(entry)
        if prio is None:
            continue
        # Dedupe: same content_touched signature
        sig = (entry.get("action"), tuple(sorted(entry.get("content_touched", []))))
        if sig in seen_signatures:
            continue
        seen_signatures.add(sig)
        candidates.append((prio, entry.get("timestamp", ""), entry))

    # Sort: priority asc (1 first), then recency desc
    candidates.sort(key=lambda x: (x[0], -1 if x[1] else 0, x[1]), reverse=False)
    # After sort, highest-priority first; within same priority, most-recent last
    # → reverse within same priority to put newest first
    candidates.sort(key=lambda x: (x[0], -(
        datetime.fromisoformat(str(x[1]).replace("Z", "+00:00")).timestamp()
        if x[1] else 0.0
    )))

    return [e for _, _, e in candidates[:limit]]


# ── Seeding ────────────────────────────────────────────────────────────────

def seed_from_history(
    days: float = 7.0,
    limit: int = 5,
    graph_id: Optional[str] = None,
    topic_hint: str = "",
) -> dict:
    """Вытащить conclusions и создать seed-ноды в текущем content-графе.

    Для каждого conclusion:
      - ensure_embedding — лениво считает из state_embeddings.jsonl или
        запрашивает у api_get_embedding
      - _add_node(text=stub, rendered=False, embedding=<inherited>)
      - node["seeded_from"] = hash — провenance

    Дедуп: если в графе уже есть seed с тем же seeded_from hash — не создаём.

    Возвращает {created: [idx], skipped_dup, skipped_no_emb, total_considered}.
    """
    from .state_graph import get_state_graph
    from .graph_logic import _graph, _add_node

    conclusions = extract_conclusions(days=days, limit=limit, graph_id=graph_id)
    if not conclusions:
        return {"created": [], "skipped_dup": 0, "skipped_no_emb": 0,
                "total_considered": 0}

    sg = get_state_graph()
    existing_sources = {
        n.get("seeded_from") for n in _graph.get("nodes", [])
        if n.get("seeded_from")
    }

    created: list[int] = []
    skipped_dup = 0
    skipped_no_emb = 0

    for entry in conclusions:
        h = entry.get("hash")
        if h and h in existing_sources:
            skipped_dup += 1
            continue
        emb = None
        try:
            emb = sg.ensure_embedding(entry)
        except Exception as e:
            log.debug(f"[cross_graph] ensure_embedding failed for {h}: {e}")
        if not emb:
            skipped_no_emb += 1
            continue

        # Stub text из reason (краткая подсказка что это за conclusion)
        reason = (entry.get("reason") or "")[:60] or entry.get("action", "seed")
        stub = f"💭 {reason}"

        idx = _add_node(
            text=stub,
            depth=0,
            topic=topic_hint or _graph.get("meta", {}).get("topic", ""),
            node_type="hypothesis",
            embedding=emb,
            rendered=False,
        )
        _graph["nodes"][idx]["seeded_from"] = h
        _graph["nodes"][idx]["seeded_action"] = entry.get("action")
        _graph["nodes"][idx]["seeded_timestamp"] = entry.get("timestamp")
        created.append(idx)

    log.info(f"[cross_graph] seeded {len(created)} from history "
             f"(dup={skipped_dup}, no_emb={skipped_no_emb})")
    return {
        "created": created,
        "skipped_dup": skipped_dup,
        "skipped_no_emb": skipped_no_emb,
        "total_considered": len(conclusions),
    }
