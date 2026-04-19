"""Persistent goals store — append-only event log.

Цели сейчас живут как ноды `type=goal` в `_graph["nodes"]` — эфемерно:
умирают при workspace switch, нет aggregation по завершениям. Этот модуль
добавляет **персистентный реестр событий** над goal-нодами.

Файл: `goals.jsonl`. Каждая строка = одно событие:

    {"action": "create", "id", "workspace", "text", "mode", "priority",
     "deadline", "category", "kind", "schedule", "polarity", "ts"}
    {"action": "complete", "id", "reason", "snapshot_ref", "energy_pct", "ts"}
    {"action": "abandon",  "id", "reason", "ts"}
    {"action": "update",   "id", "fields": {...}, "ts"}
    {"action": "instance", "id", "note", "ts"}           # выполнение recurring
    {"action": "violation","id", "note", "detected", "ts"}  # нарушение constraint

Kind'ы целей (в create event):
  - "oneshot"   — обычная цель (default, закрывается complete/abandon)
  - "recurring" — привычка с расписанием, instances копятся
  - "constraint"— граница (избегать чего-то), violations копятся

Status юзера replay'ится из событий: open → (done | abandoned).
Recurring и constraint всегда open — они не закрываются автоматически.

Статистика: completion_rate, avg_time_to_done, by_mode, by_category.
"""
import gzip
import json
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

from .paths import GOALS_FILE as _GOALS_FILE, DATA_DIR
_GOALS_ARCHIVE_DIR = DATA_DIR / "archives"

# Rotation parameters
_ROTATE_SIZE_BYTES = 2 * 1024 * 1024   # 2 MB
_ROTATE_EVENT_AGE_DAYS = 120           # события старше 4 месяцев — кандидаты


def _append(entry: dict):
    entry.setdefault("ts", time.time())
    try:
        with _GOALS_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning(f"[goals_store] append failed: {e}")


# ── Rotation: gzip архив старых событий при превышении размера ────────────

def rotate_if_needed(force: bool = False) -> Optional[str]:
    """Перенести события старше `_ROTATE_EVENT_AGE_DAYS` в gzip-архив.

    Триггер: либо `force=True`, либо файл > `_ROTATE_SIZE_BYTES`.
    Архив: `archives/goals-YYYYMMDD.jsonl.gz`. Возвращает путь к архиву
    или None если ротация не потребовалась.

    Замечание: замкнутых целей (status=done/abandoned) в старом окне
    безопасно переносить — их `_replay()` больше не восстанавливает как
    открытые. Открытые цели НЕ ротируем (даже старые) чтобы replay
    сохранял consistency.
    """
    if not _GOALS_FILE.exists():
        return None
    try:
        size = _GOALS_FILE.stat().st_size
    except OSError:
        return None
    if not force and size < _ROTATE_SIZE_BYTES:
        return None

    cutoff_ts = time.time() - _ROTATE_EVENT_AGE_DAYS * 86400

    # Replay чтобы знать какие goal_id завершены — только их события переносим
    closed_ids = set()
    events = _read_all()
    for e in events:
        if e.get("action") in ("complete", "abandon"):
            closed_ids.add(e.get("id"))

    to_archive = []
    to_keep = []
    for e in events:
        ts = float(e.get("ts") or 0)
        gid = e.get("id")
        if ts < cutoff_ts and gid in closed_ids:
            to_archive.append(e)
        else:
            to_keep.append(e)

    if not to_archive:
        return None

    _GOALS_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"goals-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl.gz"
    arch_path = _GOALS_ARCHIVE_DIR / fname
    try:
        with gzip.open(arch_path, "wt", encoding="utf-8") as gz:
            for e in to_archive:
                gz.write(json.dumps(e, ensure_ascii=False) + "\n")
        # Rewrite active file (atomic через temp)
        tmp = _GOALS_FILE.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for e in to_keep:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        tmp.replace(_GOALS_FILE)
        log.info(f"[goals_store] rotated {len(to_archive)} events → {arch_path.name}")
        return str(arch_path)
    except Exception as e:
        log.warning(f"[goals_store] rotation failed: {e}")
        return None


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
             category: Optional[str] = None,
             kind: str = "oneshot",
             schedule: Optional[dict] = None,
             polarity: Optional[str] = None) -> str:
    """Создать новую цель. Возвращает её ID.

    kind:
      - "oneshot"   — обычная цель, закрывается complete/abandon
      - "recurring" — привычка, требует schedule (см. `schedule` dict)
      - "constraint"— граница, требует polarity ("avoid" | "prefer")

    schedule (для recurring):
      {
        "times_per_day": 3,              # сколько раз в день
        "days": [0, 1, 2, 3, 4, 5, 6],   # дни недели (0=пн, опционально, default все)
        "time_windows": [[6,10],[12,15],[18,22]]  # окна в часах (optional)
      }

    polarity (для constraint):
      - "avoid"  — избегать (не есть орехи, не работать после 23)
      - "prefer" — предпочитать (есть меньше сахара) — soft constraint
    """
    goal_id = uuid.uuid4().hex[:12]
    entry = {
        "action": "create",
        "id": goal_id,
        "workspace": workspace,
        "text": (text or "").strip()[:400],
        "mode": mode,
        "priority": priority,
        "deadline": deadline,
        "category": category,
        "kind": kind,
    }
    if kind == "recurring" and schedule:
        entry["schedule"] = schedule
    if kind == "constraint":
        entry["polarity"] = polarity or "avoid"
    _append(entry)
    return goal_id


def record_instance(goal_id: str, note: str = "", ts: Optional[float] = None):
    """Отметить выполнение recurring goal (юзер или автомат)."""
    _append({
        "action": "instance",
        "id": goal_id,
        "note": (note or "")[:200],
        "ts": ts if ts is not None else time.time(),
    })


def record_violation(goal_id: str, note: str = "",
                     detected: str = "manual",
                     ts: Optional[float] = None):
    """Отметить нарушение constraint.

    detected:
      - "manual"    — юзер сам сказал
      - "llm_scan"  — LLM детектил в сообщении юзера
      - "tick"      — детектил tick-хук по pattern'у
    """
    _append({
        "action": "violation",
        "id": goal_id,
        "note": (note or "")[:200],
        "detected": detected,
        "ts": ts if ts is not None else time.time(),
    })


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
                          category, kind, schedule, polarity, status,
                          created_at, completed_at, instances, violations}}
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
                # Legacy records без kind считаем oneshot.
                "kind": e.get("kind") or "oneshot",
                "schedule": e.get("schedule"),
                "polarity": e.get("polarity"),
                "status": "open",
                "created_at": e.get("ts"),
                "instances": [],     # [{ts, note}, ...]
                "violations": [],    # [{ts, note, detected}, ...]
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
            elif action == "instance":
                g["instances"].append({
                    "ts": e.get("ts"),
                    "note": e.get("note", ""),
                })
            elif action == "violation":
                g["violations"].append({
                    "ts": e.get("ts"),
                    "note": e.get("note", ""),
                    "detected": e.get("detected", "manual"),
                })
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
