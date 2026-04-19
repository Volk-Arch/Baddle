"""Solved-tasks archive — snapshot «как решалась задача» при goal-resolved.

Когда tick эмитит action=stable с reason=GOAL REACHED, этот модуль:
  1. Берёт snapshot content-графа (ноды + edges, фильтруя по subtree цели)
  2. Берёт tail state_graph (последние N ticks касающиеся этой цели)
  3. Сохраняет в `solved/{snapshot_ref}.json`
  4. Возвращает snapshot_ref для записи в goals.jsonl

Юзер потом может открыть архив и увидеть полный replay:
  • цель + subgoals
  • какие hypothesis генерились
  • какие smartdc-циклы прошли
  • финальный synthesis

Хранение per-workspace не требуется — snapshot_ref глобально уникален.
"""
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

from .paths import PROJECT_ROOT


def _ws_archive_dir(workspace: str) -> Path:
    """Per-workspace архив: `graphs/<ws>/solved/`. Создаётся при первом write."""
    return PROJECT_ROOT / "graphs" / workspace / "solved"


def _ensure_dir(workspace: str = "main") -> Path:
    d = _ws_archive_dir(workspace)
    d.mkdir(parents=True, exist_ok=True)
    return d


def archive_solved(goal_id: str,
                   goal_text: str,
                   workspace: str = "main",
                   reason: str = "",
                   state_trace_limit: int = 50) -> Optional[str]:
    """Сохранить snapshot и вернуть snapshot_ref.

    Содержит:
      - goal: {id, text, workspace, reason, ts}
      - graph_snapshot: {nodes, edges, meta}
      - state_trace: последние N state_graph entries
      - final_synthesis: если удалось выделить (последняя verified нода)

    Пишется в `graphs/<workspace>/solved/{snapshot_ref}.json`.
    """
    from .graph_logic import _graph
    from .state_graph import get_state_graph

    archive_dir = _ensure_dir(workspace)
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
                "workspace": workspace,
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
                 f"{len(entries)} state entries) → ws={workspace}")
        return snapshot_ref

    except Exception as e:
        log.warning(f"[solved_archive] archive failed: {e}")
        return None


def _find_snapshot(snapshot_ref: str) -> Optional[Path]:
    """Scan all workspaces для snapshot_ref. Первый hit wins."""
    graphs_root = PROJECT_ROOT / "graphs"
    if not graphs_root.exists():
        return None
    for ws_dir in graphs_root.iterdir():
        if not ws_dir.is_dir():
            continue
        cand = ws_dir / "solved" / f"{snapshot_ref}.json"
        if cand.exists():
            return cand
    return None


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
    """Yield `.json` archive files across all workspaces."""
    graphs_root = PROJECT_ROOT / "graphs"
    if not graphs_root.exists():
        return
    for ws_dir in graphs_root.iterdir():
        if not ws_dir.is_dir():
            continue
        sd = ws_dir / "solved"
        if sd.exists():
            yield from sd.glob("*.json")


def list_solved(limit: int = 50) -> list[dict]:
    """List archive index, newest first. Each entry — short summary.

    Сканирует per-workspace `graphs/<ws>/solved/`.
    """
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
                "workspace": goal.get("workspace"),
                "archived_at": goal.get("archived_at"),
                "reason": goal.get("reason"),
                "nodes_count": len(graph.get("nodes") or []),
                "final_synthesis": fs.get("text", "")[:100] if fs else None,
            })
        except Exception:
            continue
    return out
