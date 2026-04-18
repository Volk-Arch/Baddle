"""Plans — карта будущего: запланированные события + regular habits.

Два смысла recurring в одном модуле:
  1. **One-off events** — «митинг 18 апреля 14:00».
  2. **Recurring habits** — «завтрак каждое утро в 08:00», «бег пн/ср/пт в 7».

Обе — первоклассные объекты: имеют name, category, plannedts_start, ts_end,
expected_difficulty (1-5, опционально). Выполнение трекается по `complete`/
`skip` событиям. Streak автоматически считается для recurring.

Связка с прайм-директивой:
  план (plan.ts_start, plan.expected_difficulty)
  vs факт (activity.started_at при matching name или completion event)
  = surprise → UserState.

Файл: `plans.jsonl` append-only. Events:

    {action:"create", id, name, category, ts_start, ts_end?,
     recurring?:{days:[0..6], time:"HH:MM"}, expected_difficulty, note}
    {action:"complete", id, actual_ts, actual_difficulty?, note?}
    {action:"skip",     id, reason?, ts}
    {action:"update",   id, fields}
    {action:"delete",   id}

Для recurring: каждое выполнение = complete-event с `for_date` в meta.
"""
from __future__ import annotations
import json
import logging
import time
import uuid
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_PLANS_FILE = Path(__file__).parent.parent / "plans.jsonl"


def _append(entry: dict):
    entry.setdefault("ts", time.time())
    try:
        with _PLANS_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning(f"[plans] append failed: {e}")


def _read_all() -> list[dict]:
    if not _PLANS_FILE.exists():
        return []
    out = []
    try:
        with _PLANS_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        log.warning(f"[plans] read failed: {e}")
    return out


# ── Replay: build current state from events ────────────────────────────────

def _replay() -> dict[str, dict]:
    """Вернуть {plan_id: plan} с полями:
      id, name, category, ts_start, ts_end, recurring, expected_difficulty,
      note, status ("active"/"done"/"deleted"),
      completions: list[{for_date, actual_ts, actual_difficulty, note}],
      skips: list[{for_date, reason}]
    """
    state: dict[str, dict] = {}
    for e in _read_all():
        pid = e.get("id")
        if not pid:
            continue
        act = e.get("action")
        if act == "create":
            state[pid] = {
                "id": pid,
                "name": e.get("name", ""),
                "category": e.get("category"),
                "ts_start": e.get("ts_start"),
                "ts_end": e.get("ts_end"),
                "recurring": e.get("recurring"),
                "expected_difficulty": e.get("expected_difficulty"),
                "note": e.get("note", ""),
                "created_at": e.get("ts"),
                "status": "active",
                "completions": [],
                "skips": [],
            }
        elif pid in state:
            p = state[pid]
            if act == "complete":
                p["completions"].append({
                    "for_date": e.get("for_date"),
                    "actual_ts": e.get("actual_ts") or e.get("ts"),
                    "actual_difficulty": e.get("actual_difficulty"),
                    "note": e.get("note"),
                })
                if not p.get("recurring"):
                    p["status"] = "done"
            elif act == "skip":
                p["skips"].append({
                    "for_date": e.get("for_date"),
                    "ts": e.get("ts"),
                    "reason": e.get("reason"),
                })
            elif act == "update":
                for k, v in (e.get("fields") or {}).items():
                    if k in {"name", "category", "ts_start", "ts_end",
                            "recurring", "expected_difficulty", "note"}:
                        p[k] = v
            elif act == "delete":
                p["status"] = "deleted"
    return state


# ── CRUD ──────────────────────────────────────────────────────────────────

def add_plan(name: str,
             category: Optional[str] = None,
             ts_start: Optional[float] = None,
             ts_end: Optional[float] = None,
             recurring: Optional[dict] = None,
             expected_difficulty: Optional[int] = None,
             note: str = "") -> str:
    """Создать plan. Для одноразового события — `ts_start`. Для recurring —
    `recurring={days:[0..6], time:"HH:MM"}` вместо конкретного ts_start.
    """
    pid = uuid.uuid4().hex[:12]
    if expected_difficulty is not None:
        try:
            expected_difficulty = max(1, min(5, int(expected_difficulty)))
        except (TypeError, ValueError):
            expected_difficulty = None
    entry = {
        "action": "create",
        "id": pid,
        "name": (name or "").strip()[:200],
        "category": category,
        "ts_start": ts_start,
        "ts_end": ts_end,
        "recurring": recurring,
        "expected_difficulty": expected_difficulty,
        "note": (note or "")[:300],
    }
    _append(entry)
    return pid


def update_plan(plan_id: str, fields: dict):
    allowed = {"name", "category", "ts_start", "ts_end",
               "recurring", "expected_difficulty", "note"}
    clean = {k: v for k, v in (fields or {}).items() if k in allowed}
    if not clean:
        return
    _append({"action": "update", "id": plan_id, "fields": clean})


def delete_plan(plan_id: str):
    _append({"action": "delete", "id": plan_id})


def complete_plan(plan_id: str,
                  for_date: Optional[str] = None,
                  actual_ts: Optional[float] = None,
                  actual_difficulty: Optional[int] = None,
                  note: str = ""):
    """Отметить выполнение. Для recurring `for_date` = YYYY-MM-DD
    (день на который выполнено). Для одноразового — можно пропустить.
    """
    if actual_difficulty is not None:
        try:
            actual_difficulty = max(1, min(5, int(actual_difficulty)))
        except (TypeError, ValueError):
            actual_difficulty = None
    _append({
        "action": "complete",
        "id": plan_id,
        "for_date": for_date,
        "actual_ts": actual_ts or time.time(),
        "actual_difficulty": actual_difficulty,
        "note": (note or "")[:200],
    })


def skip_plan(plan_id: str, for_date: Optional[str] = None, reason: str = ""):
    _append({
        "action": "skip", "id": plan_id,
        "for_date": for_date,
        "reason": (reason or "")[:200],
    })


def get_plan(plan_id: str) -> Optional[dict]:
    p = _replay().get(plan_id)
    if p and p.get("status") != "deleted":
        return p
    return None


def list_plans(status: str = "active",
               kind: Optional[str] = None,
               limit: int = 200) -> list[dict]:
    """status: active / done / all. kind: 'recurring' / 'oneshot' / None."""
    items = list(_replay().values())
    if status == "active":
        items = [p for p in items if p.get("status") in ("active",)]
    elif status == "done":
        items = [p for p in items if p.get("status") in ("done",)]
    elif status == "all":
        items = [p for p in items if p.get("status") != "deleted"]
    if kind == "recurring":
        items = [p for p in items if p.get("recurring")]
    elif kind == "oneshot":
        items = [p for p in items if not p.get("recurring")]
    items.sort(key=lambda p: p.get("ts_start") or p.get("created_at") or 0)
    return items[:limit]


# ── Daily expansion: разложить plans в schedule на конкретный день ────────

def _matches_recurring(rec: dict, target: date) -> bool:
    if not rec:
        return False
    days = rec.get("days") or []  # 0=Mon..6=Sun
    if days and target.weekday() not in days:
        return False
    return True


def _time_to_ts(day: date, hhmm: str) -> float:
    if not hhmm:
        h, m = 0, 0
    else:
        try:
            parts = hhmm.split(":")
            h = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 0
        except Exception:
            h, m = 0, 0
    return datetime(day.year, day.month, day.day, h, m).timestamp()


def schedule_for_day(target: Optional[date] = None) -> list[dict]:
    """Собрать события на конкретный день: one-off в диапазоне + recurring
    развёрнутые. Сортировка по времени.

    Для recurring: добавляет ключи:
      - `for_date` (строка YYYY-MM-DD)
      - `planned_ts` (timestamp time в этот день)
      - `done` / `skipped` — посчитано из completions/skips по for_date
      - `streak` — количество consecutive дней с complete (для active habits)
    """
    target = target or date.today()
    day_start = datetime(target.year, target.month, target.day).timestamp()
    day_end = day_start + 86400.0
    for_date_str = target.strftime("%Y-%m-%d")

    result = []
    for p in _replay().values():
        if p.get("status") == "deleted":
            continue
        rec = p.get("recurring")
        if rec:
            if not _matches_recurring(rec, target):
                continue
            planned_ts = _time_to_ts(target, rec.get("time", "09:00"))
            done = any(c.get("for_date") == for_date_str for c in p.get("completions", []))
            skipped = any(s.get("for_date") == for_date_str for s in p.get("skips", []))
            streak = _compute_streak(p, target)
            result.append({**p, "for_date": for_date_str,
                           "planned_ts": planned_ts,
                           "done": done, "skipped": skipped,
                           "streak": streak, "kind": "recurring"})
        else:
            ts = p.get("ts_start")
            if ts is None:
                continue
            if not (day_start <= float(ts) < day_end):
                continue
            # single instance — done если есть completion
            done = len(p.get("completions", [])) > 0
            skipped = len(p.get("skips", [])) > 0
            result.append({**p, "for_date": for_date_str,
                           "planned_ts": ts, "done": done, "skipped": skipped,
                           "streak": None, "kind": "oneshot"})

    result.sort(key=lambda e: e.get("planned_ts") or 0)
    return result


def _compute_streak(plan: dict, today: date) -> int:
    """Consecutive дни с complete, считая назад от сегодня.

    Для recurring-плана — число последовательных «matches» дней где был complete.
    """
    rec = plan.get("recurring")
    if not rec:
        return 0
    dates_done = {c.get("for_date") for c in plan.get("completions", [])}
    streak = 0
    check = today
    # Идём назад максимум 365 дней (защита от бесконечного цикла)
    for _ in range(365):
        if _matches_recurring(rec, check):
            ds = check.strftime("%Y-%m-%d")
            if ds in dates_done:
                streak += 1
            else:
                # Если это сегодня и ещё не выполнено — не break, пропускаем
                if check == today:
                    pass
                else:
                    break
        check = check - timedelta(days=1)
    return streak


# ── Аналитика: expected vs actual (байесовский слой) ──────────────────────

def last_n_surprises(n: int = 10) -> list[dict]:
    """Последние N выполнений с expected vs actual_difficulty.

    Используется morning briefing для показа «trend ожиданий».
    """
    out = []
    state = _replay()
    for p in state.values():
        exp = p.get("expected_difficulty")
        for c in p.get("completions", []):
            act = c.get("actual_difficulty")
            if exp is not None and act is not None:
                out.append({
                    "plan_id": p["id"], "name": p["name"],
                    "expected": exp, "actual": act,
                    "surprise": act - exp,
                    "for_date": c.get("for_date"),
                    "ts": c.get("actual_ts"),
                })
    out.sort(key=lambda x: x["ts"] or 0, reverse=True)
    return out[:n]
