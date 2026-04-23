"""Solved-tasks archive — snapshot «как решалась задача» при goal-resolved.

Когда tick эмитит action=stable с reason=GOAL REACHED, этот модуль:
  1. Берёт snapshot content-графа (ноды + edges)
  2. Берёт tail state_graph (последние N ticks касающиеся этой цели)
  3. Сохраняет в `graphs/main/solved/{snapshot_ref}.json`
  4. Возвращает snapshot_ref для записи в goals.jsonl

Юзер потом может открыть архив и увидеть полный replay:
  • цель + subgoals
  • какие hypothesis генерились
  • какие smartdc-циклы прошли
  • финальный synthesis
"""
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

from .paths import PROJECT_ROOT

_ARCHIVE_DIR = PROJECT_ROOT / "graphs" / "main" / "solved"


def _ensure_dir() -> Path:
    _ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    return _ARCHIVE_DIR


def archive_solved(goal_id: str,
                   goal_text: str,
                   reason: str = "",
                   state_trace_limit: int = 50) -> Optional[str]:
    """Сохранить snapshot и вернуть snapshot_ref.

    Содержит:
      - goal: {id, text, reason, ts}
      - graph_snapshot: {nodes, edges, meta}
      - state_trace: последние N state_graph entries
      - final_synthesis: если удалось выделить (последняя verified нода)

    Пишется в `graphs/main/solved/{snapshot_ref}.json`.
    """
    from .graph_logic import _graph
    from .state_graph import get_state_graph

    archive_dir = _ensure_dir()
    snapshot_ref = f"{int(time.time())}_{goal_id}_{uuid.uuid4().hex[:6]}"

    try:
        # Content-graph snapshot (копия, не ссылка)
        graph_snap = {
            "nodes": [dict(n) for n in _graph.get("nodes", [])],
            "edges": {
                "directed": list(_graph.get("edges", {}).get("directed", [])),
                "manual_links": list(_graph.get("edges", {}).get("manual_links", [])),
            },
            "meta": dict(_graph.get("meta", {})),
        }
        # hub_nodes as set → list для JSON
        hubs = graph_snap["meta"].get("hub_nodes")
        if isinstance(hubs, set):
            graph_snap["meta"]["hub_nodes"] = list(hubs)

        # State-trace
        sg = get_state_graph()
        try:
            entries = sg.read_all()[-state_trace_limit:]
        except Exception:
            entries = []

        # Попытаемся выделить финальный synthesis: последняя verified нода
        final_synthesis = None
        for n in reversed(graph_snap["nodes"]):
            conf = n.get("confidence", 0)
            if isinstance(conf, (int, float)) and conf >= 0.8:
                final_synthesis = {"text": n.get("text", ""), "confidence": conf,
                                   "idx": n.get("id")}
                break

        payload = {
            "snapshot_ref": snapshot_ref,
            "goal": {
                "id": goal_id,
                "text": (goal_text or "")[:400],
                "reason": (reason or "")[:200],
                "archived_at": time.time(),
            },
            "graph_snapshot": graph_snap,
            "state_trace": entries,
            "final_synthesis": final_synthesis,
        }

        path = archive_dir / f"{snapshot_ref}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info(f"[solved_archive] saved {snapshot_ref} ({len(graph_snap['nodes'])} nodes, "
                 f"{len(entries)} state entries)")
        return snapshot_ref

    except Exception as e:
        log.warning(f"[solved_archive] archive failed: {e}")
        return None


def _find_snapshot(snapshot_ref: str) -> Optional[Path]:
    cand = _ARCHIVE_DIR / f"{snapshot_ref}.json"
    return cand if cand.exists() else None


def load_solved(snapshot_ref: str) -> Optional[dict]:
    """Load archived snapshot. Returns None если нет."""
    path = _find_snapshot(snapshot_ref)
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning(f"[solved_archive] load failed for {snapshot_ref}: {e}")
        return None


def _iter_archive_files():
    """Yield `.json` archive files."""
    if not _ARCHIVE_DIR.exists():
        return
    yield from _ARCHIVE_DIR.glob("*.json")


def find_similar_solved(query_text: str, top_k: int = 3,
                         min_similarity: float = 0.55) -> list[dict]:
    """RAG-lite: поиск похожих решённых задач для нового запроса юзера.

    Считает cosine similarity между embedding query и goal-text
    каждого solved archive. Возвращает топ-K с similarity >= min.

    Используется в /assist когда юзер задаёт новый вопрос — система
    может подтянуть «ты уже решал похожее 2 недели назад, вот синтез».

    Быстрая версия: читает только goal.text + final_synthesis.text из
    каждого snapshot. Не грузит весь граф. На 100 архивов — ~200ms +
    1 embedding-call (для query).
    """
    import numpy as np
    try:
        from .api_backend import api_get_embedding
        from .main import cosine_similarity
    except Exception:
        return []
    q_text = (query_text or "").strip()
    if not q_text:
        return []
    try:
        q_emb = api_get_embedding(q_text)
    except Exception as e:
        log.debug(f"[solved_archive] query embedding failed: {e}")
        return []
    if not q_emb:
        return []
    q_vec = np.array(q_emb, dtype=np.float32)
    if q_vec.size == 0:
        return []

    scored = []
    for f in _iter_archive_files():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        goal = data.get("goal") or {}
        goal_text = goal.get("text", "")
        if not goal_text:
            continue
        # Используем cached embedding если есть в graph_snapshot (goal-нода)
        snap_nodes = (data.get("graph_snapshot") or {}).get("nodes") or []
        goal_emb = None
        for n in snap_nodes:
            if n.get("type") == "goal" and n.get("embedding"):
                goal_emb = n["embedding"]
                break
        # Если нет embedding в архиве — считаем на лету
        if not goal_emb:
            try:
                goal_emb = api_get_embedding(goal_text)
            except Exception:
                continue
        if not goal_emb:
            continue
        g_vec = np.array(goal_emb, dtype=np.float32)
        if g_vec.size == 0:
            continue
        sim = float(cosine_similarity(q_vec, g_vec))
        if sim < min_similarity:
            continue
        final_synth = data.get("final_synthesis") or {}
        scored.append({
            "snapshot_ref": data.get("snapshot_ref") or f.stem,
            "goal_text": goal_text,
            "archived_at": goal.get("archived_at"),
            "final_synthesis": (final_synth.get("text") or "")[:400],
            "similarity": round(sim, 3),
        })

    scored.sort(key=lambda x: -x["similarity"])
    return scored[:top_k]


def list_solved(limit: int = 50) -> list[dict]:
    """List archive index, newest first. Each entry — short summary."""
    files = sorted(_iter_archive_files(),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    out: list[dict] = []
    for f in files[:limit]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            goal = data.get("goal") or {}
            fs = data.get("final_synthesis") or {}
            graph = data.get("graph_snapshot") or {}
            out.append({
                "snapshot_ref": data.get("snapshot_ref") or f.stem,
                "goal_id": goal.get("id"),
                "goal_text": goal.get("text"),
                "archived_at": goal.get("archived_at"),
                "reason": goal.get("reason"),
                "nodes_count": len(graph.get("nodes") or []),
                "final_synthesis": fs.get("text", "")[:100] if fs else None,
            })
        except Exception:
            continue
    return out
