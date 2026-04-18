"""Persistent goals store — append-only event log.

Цели сейчас живут как ноды `type=goal` в `_graph["nodes"]` — эфемерно:
умирают при workspace switch, нет aggregation по завершениям. Этот модуль
добавляет **персистентный реестр событий** над goal-нодами.

Файл: `goals.jsonl`. Каждая строка = одно событие:

    {"action": "create", "id", "workspace", "text", "mode", "priority",
     "deadline", "category", "ts"}
    {"action": "complete", "id", "reason", "snapshot_ref", "energy_pct", "ts"}
    {"action": "abandon",  "id", "reason", "ts"}
    {"action": "update",   "id", "fields": {...}, "ts"}

Status юзера replay'ится из событий: open → (done | abandoned).

Статистика: completion_rate, avg_time_to_done, by_mode, by_category.
"""
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_GOALS_FILE = Path(__file__).parent.parent / "goals.jsonl"


def _append(entry: dict):
    entry.setdefault("ts", time.time())
    try:
        with _GOALS_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning(f"[goals_store] append failed: {e}")


def _read_all() -> list[dict]:
    if not _GOALS_FILE.exists():
        return []
    out: list[dict] = []
    try:
        with _GOALS_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        log.warning(f"[goals_store] read failed: {e}")
    return out


# ── Mutators ──────────────────────────────────────────────────────────────

def add_goal(text: str,
             mode: str = "horizon",
             workspace: str = "main",
             priority: Optional[int] = None,
             deadline: Optional[str] = None,
             category: Optional[str] = None) -> str:
    """Создать новую цель. Возвращает её ID."""
    goal_id = uuid.uuid4().hex[:12]
    _append({
        "action": "create",
        "id": goal_id,
        "workspace": workspace,
        "text": (text or "").strip()[:400],
        "mode": mode,
        "priority": priority,
        "deadline": deadline,
        "category": category,
    })
    return goal_id


def complete_goal(goal_id: str, reason: str = "",
                  snapshot_ref: Optional[str] = None,
                  energy_pct: Optional[float] = None):
    _append({
        "action": "complete",
        "id": goal_id,
        "reason": (reason or "")[:200],
        "snapshot_ref": snapshot_ref,
        "energy_pct": energy_pct,
    })


def abandon_goal(goal_id: str, reason: str = ""):
    _append({
        "action": "abandon",
        "id": goal_id,
        "reason": (reason or "")[:200],
    })


def update_goal(goal_id: str, fields: dict):
    """Patch selected fields (priority, deadline, category)."""
    allowed = {"priority", "deadline", "category", "mode", "text"}
    clean = {k: v for k, v in (fields or {}).items() if k in allowed}
    if not clean:
        return
    _append({
        "action": "update",
        "id": goal_id,
        "fields": clean,
    })


# ── Replay: текущее состояние из event log ───────────────────────────────

def _replay() -> dict[str, dict]:
    """Построить current-state dict по event log.

    Возвращает {goal_id: {id, text, mode, workspace, priority, deadline,
                          category, status, created_at, completed_at, ...}}
    """
    state: dict[str, dict] = {}
    for e in _read_all():
        gid = e.get("id")
        if not gid:
            continue
        action = e.get("action")
        if action == "create":
            state[gid] = {
                "id": gid,
                "text": e.get("text", ""),
                "mode": e.get("mode"),
                "workspace": e.get("workspace", "main"),
                "priority": e.get("priority"),
                "deadline": e.get("deadline"),
                "category": e.get("category"),
                "status": "open",
                "created_at": e.get("ts"),
            }
        elif gid in state:
            g = state[gid]
            if action == "complete":
                g["status"] = "done"
                g["completed_at"] = e.get("ts")
                g["complete_reason"] = e.get("reason")
                g["snapshot_ref"] = e.get("snapshot_ref")
                g["energy_pct"] = e.get("energy_pct")
            elif action == "abandon":
                g["status"] = "abandoned"
                g["abandoned_at"] = e.get("ts")
                g["abandon_reason"] = e.get("reason")
            elif action == "update":
                for k, v in (e.get("fields") or {}).items():
                    g[k] = v
    return state


def list_goals(status: Optional[str] = None,
               workspace: Optional[str] = None,
               category: Optional[str] = None,
               limit: int = 100) -> list[dict]:
    """Current goals, newest first. Optional filters."""
    state = _replay()
    items = list(state.values())
    items.sort(key=lambda g: g.get("created_at") or 0, reverse=True)
    if status:
        items = [g for g in items if g.get("status") == status]
    if workspace:
        items = [g for g in items if g.get("workspace") == workspace]
    if category:
        items = [g for g in items if g.get("category") == category]
    return items[:limit]


def get_goal(goal_id: str) -> Optional[dict]:
    state = _replay()
    return state.get(goal_id)


# ── Stats ─────────────────────────────────────────────────────────────────

def goal_stats() -> dict:
    """Агрегаты: completion_rate, avg_time_to_done, distribution by mode/cat."""
    state = _replay()
    total = len(state)
    if total == 0:
        return {"total": 0, "open": 0, "done": 0, "abandoned": 0,
                "completion_rate": 0.0, "avg_time_to_done_h": None,
                "by_mode": {}, "by_category": {}}

    opn = done = abd = 0
    time_deltas = []
    by_mode: dict = {}
    by_cat: dict = {}
    for g in state.values():
        st = g.get("status")
        if st == "open":
            opn += 1
        elif st == "done":
            done += 1
            ct = g.get("completed_at"); crt = g.get("created_at")
            if ct and crt:
                time_deltas.append(float(ct) - float(crt))
        elif st == "abandoned":
            abd += 1

        m = g.get("mode") or "unknown"
        by_mode[m] = by_mode.get(m, 0) + 1
        c = g.get("category") or "uncategorized"
        by_cat[c] = by_cat.get(c, 0) + 1

    avg_h = (sum(time_deltas) / len(time_deltas) / 3600.0) if time_deltas else None

    closed = done + abd
    completion_rate = (done / closed) if closed else 0.0

    return {
        "total": total,
        "open": opn, "done": done, "abandoned": abd,
        "completion_rate": round(completion_rate, 3),
        "avg_time_to_done_h": round(avg_h, 2) if avg_h is not None else None,
        "by_mode": by_mode,
        "by_category": by_cat,
    }
