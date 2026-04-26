"""Recurring goals & constraints — вечные цели поверх goals_store.

Вечная цель (kind=recurring) — это `{schedule: {times_per_day, days, ...}}`.
За день копятся `instance` events в goals.jsonl. Прогресс = count(instances
за сегодня) vs expected(schedule, now).

Constraint (kind=constraint, polarity=avoid|prefer) — копятся `violation`
events. Прогресс инвертирован: 0 violations = OK, >0 = проблема.

Этот модуль — **чистые helpers**, без LLM-вызовов. LLM-автоматическая
детекция нарушений реализована в assistant.py как hook (после /assist).
"""
import time
import logging
from datetime import datetime, date as date_type
from typing import Optional

from .goals_store import _replay

log = logging.getLogger(__name__)


# ── Date utils ────────────────────────────────────────────────────────────

def _today_start_ts() -> float:
    """Unix ts начала сегодняшнего дня (локальное время)."""
    now = datetime.now()
    start = datetime(now.year, now.month, now.day)
    return start.timestamp()


def _day_bounds(day: Optional[date_type] = None) -> tuple[float, float]:
    """Ts-интервал (start, end) указанного дня. Default — сегодня."""
    d = day or date_type.today()
    start = datetime(d.year, d.month, d.day).timestamp()
    return start, start + 86400.0


def _weekday_today() -> int:
    """0=пн, 6=вс (ISO)."""
    return datetime.now().weekday()


# ── Filters ───────────────────────────────────────────────────────────────

def list_recurring(active_only: bool = True) -> list[dict]:
    """Все recurring-цели. active_only=True отфильтровывает done/abandoned."""
    state = _replay()
    out = []
    for g in state.values():
        if g.get("kind") != "recurring":
            continue
        if active_only and g.get("status") != "open":
            continue
        out.append(g)
    return out


def list_constraints(active_only: bool = True) -> list[dict]:
    """Все constraint-цели."""
    state = _replay()
    out = []
    for g in state.values():
        if g.get("kind") != "constraint":
            continue
        if active_only and g.get("status") != "open":
            continue
        out.append(g)
    return out


# ── Progress & lag для recurring ──────────────────────────────────────────

def _instances_today(goal: dict) -> list[dict]:
    day_start, day_end = _day_bounds()
    return [i for i in (goal.get("instances") or [])
            if day_start <= (i.get("ts") or 0) < day_end]


def _instances_this_week(goal: dict) -> list[dict]:
    """Instance'ы за текущую неделю (пн..вс локального времени)."""
    now_dt = datetime.now()
    # Понедельник = weekday() == 0
    monday = now_dt - __import__("datetime").timedelta(
        days=now_dt.weekday(),
        hours=now_dt.hour, minutes=now_dt.minute, seconds=now_dt.second,
        microseconds=now_dt.microsecond,
    )
    week_start = monday.timestamp()
    week_end = week_start + 7 * 86400.0
    return [i for i in (goal.get("instances") or [])
            if week_start <= (i.get("ts") or 0) < week_end]


def _expected_by_now(schedule: dict, now: Optional[float] = None) -> int:
    """Сколько instance'ов ожидается к текущему моменту.

    Правило: если есть `time_windows` (list[[h_start, h_end], ...]), считаем
    сколько окон уже прошло своей серединой. Если только `times_per_day` —
    линейно распределяем от 06:00 до 22:00.

    Для `times_per_week` см. `_expected_by_now_weekly()` — возвращает 0
    здесь, дневной прогресс не применим.
    """
    now = now if now is not None else time.time()
    now_dt = datetime.fromtimestamp(now)
    day_start_dt = datetime(now_dt.year, now_dt.month, now_dt.day)
    elapsed_h = (now_dt - day_start_dt).total_seconds() / 3600.0

    # Weekly schedule — daily-расчёт не применим
    if schedule.get("times_per_week") and not schedule.get("times_per_day"):
        return 0

    tpd = int(schedule.get("times_per_day") or 0)
    if tpd <= 0:
        return 0

    windows = schedule.get("time_windows") or []
    if windows:
        # Каждое окно — один ожидаемый instance. Считаем пройденные по середине.
        done = 0
        for w in windows[:tpd]:
            if len(w) != 2:
                continue
            mid = (float(w[0]) + float(w[1])) / 2.0
            if elapsed_h >= mid:
                done += 1
        return done

    # Равномерное распределение от 6:00 до 22:00 (16 часов на tpd instance'ов)
    wake_h, sleep_h = 6.0, 22.0
    if elapsed_h <= wake_h:
        return 0
    if elapsed_h >= sleep_h:
        return tpd
    # i-й instance ожидается в wake_h + (i + 0.5) · (sleep_h - wake_h) / tpd
    slot = (sleep_h - wake_h) / tpd
    expected = 0
    for i in range(tpd):
        target = wake_h + (i + 0.5) * slot
        if elapsed_h >= target:
            expected += 1
    return expected


def _is_active_today(schedule: dict) -> bool:
    """Сегодняшний день в `days`? Если days не задан — считаем что ежедневно."""
    days = schedule.get("days")
    if not days:
        return True
    return _weekday_today() in days


def get_progress(goal_id: str) -> Optional[dict]:
    """Прогресс recurring-цели на сегодня (или неделю для times_per_week).

    Возвращает {goal_id, text, times_per_day|times_per_week, expected_by_now,
                done_today|done_this_week, lag, active_today, period,
                last_instance_ts}
    или None если цель не recurring или не open.

    `period` = "day" | "week" — показывает на каком горизонте считается прогресс.
    Для weekly целей поля `times_per_day`/`done_today` всё равно заполняются
    (обратная совместимость с UI), но `times_per_day=0`.
    """
    state = _replay()
    g = state.get(goal_id)
    if not g or g.get("kind") != "recurring":
        return None
    if g.get("status") != "open":
        return None
    sched = g.get("schedule") or {}
    tpd = int(sched.get("times_per_day") or 0)
    tpw = int(sched.get("times_per_week") or 0)
    active = _is_active_today(sched)

    # Weekly periodicity: считаем по неделе, не по дню
    if tpw > 0 and tpd == 0:
        instances_week = _instances_this_week(g)
        done_week = len(instances_week)
        # Linear expected: к концу n-го дня недели ожидаем (n/7) · tpw раундлено
        weekday = datetime.now().weekday()   # 0=пн
        expected_now = int(round((weekday + 1) / 7.0 * tpw))
        lag = max(0, expected_now - done_week)
        # Последний instance за неделю
        last_ts = max((i.get("ts") or 0) for i in instances_week) if instances_week else None
        return {
            "goal_id": goal_id,
            "text": g.get("text", ""),
            "times_per_week": tpw,
            "times_per_day": 0,
            "expected_by_now": expected_now,
            "done_this_week": done_week,
            "done_today": done_week,   # UI backward-compat
            "lag": lag,
            "active_today": active,
            "period": "week",
            "last_instance_ts": last_ts,
        }

    # Daily periodicity (default)
    instances_today = _instances_today(g) if active else []
    expected_now = _expected_by_now(sched) if active else 0
    done = len(instances_today)
    lag = max(0, expected_now - done)
    last_ts = max((i.get("ts") or 0) for i in instances_today) if instances_today else None
    return {
        "goal_id": goal_id,
        "text": g.get("text", ""),
        "times_per_day": tpd,
        "times_per_week": 0,
        "expected_by_now": expected_now,
        "done_today": done,
        "lag": lag,
        "active_today": active,
        "period": "day",
        "last_instance_ts": last_ts,
    }


def list_lagging(min_lag: int = 1) -> list[dict]:
    """Recurring-цели с отставанием. Сортированы по величине lag."""
    out = []
    for g in list_recurring(active_only=True):
        p = get_progress(g["id"])
        if p and p["active_today"] and p["lag"] >= min_lag:
            out.append(p)
    out.sort(key=lambda p: -p["lag"])
    return out


# ── Violations для constraint ─────────────────────────────────────────────

def _violations_recent(goal: dict, days: int = 7) -> list[dict]:
    cutoff = time.time() - days * 86400
    return [v for v in (goal.get("violations") or [])
            if (v.get("ts") or 0) >= cutoff]


def list_constraint_status(days: int = 7) -> list[dict]:
    """Для каждого constraint — recent violations + summary."""
    out = []
    for g in list_constraints(active_only=True):
        vs = _violations_recent(g, days)
        # За сегодня отдельно
        day_start, day_end = _day_bounds()
        today_vs = [v for v in vs if day_start <= (v.get("ts") or 0) < day_end]
        out.append({
            "goal_id": g["id"],
            "text": g.get("text", ""),
            "polarity": g.get("polarity", "avoid"),
            "violations_7d": len(vs),
            "violations_today": len(today_vs),
            "last_violation_ts": vs[-1].get("ts") if vs else None,
            "last_violation_note": vs[-1].get("note") if vs else None,
        })
    out.sort(key=lambda r: -r["violations_today"])
    return out


# ── Context для assist hook ───────────────────────────────────────────────

def scan_message_for_violations(message: str, lang: str = "ru") -> list[dict]:
    """Быстрый LLM-сканер нарушений constraints в сообщении юзера.

    Возвращает список `{goal_id, text, note}` записанных нарушений. Если
    constraints нет или LLM не детектил — пустой список.

    Стоимость ~0.5-1 сек на вызов. Skipped если `constraints = []`.
    """
    constraints = list_constraints(active_only=True)
    if not constraints or not (message or "").strip():
        return []
    from .goals_store import record_violation
    try:
        from .graph_logic import _graph_generate
    except Exception:
        return []

    # Нумерованный список для LLM
    numbered = "\n".join(f"{i+1}. {c['text']}" for i, c in enumerate(constraints))
    if lang == "ru":
        system = ("/no_think\nТы ассистент который проверяет нарушил ли юзер "
                  "свои ограничения. Отвечай ТОЛЬКО цифрами через запятую "
                  "(номера нарушенных) или словом «нет».")
        user = (f"Ограничения юзера:\n{numbered}\n\n"
                f"Сообщение юзера: «{message[:400]}»\n\n"
                f"Какие ограничения нарушены? Ответ:")
    else:
        system = ("/no_think\nYou check if the user violated any of their "
                  "constraints. Reply ONLY with numbers separated by commas "
                  "(violated ones) or the word 'no'.")
        user = (f"User constraints:\n{numbered}\n\n"
                f"User message: «{message[:400]}»\n\n"
                f"Which constraints were violated? Answer:")
    try:
        result, _ = _graph_generate(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            max_tokens=30, temp=0.1, top_k=10,
        )
    except Exception as e:
        log.debug(f"[scan_violations] LLM failed: {e}")
        return []

    text = (result or "").strip().lower()
    if not text or "нет" in text or text.startswith("no") or text == "none":
        return []

    # Парсим номера
    import re
    nums = [int(m) for m in re.findall(r"\d+", text)]
    recorded = []
    for n in nums:
        if 1 <= n <= len(constraints):
            g = constraints[n - 1]
            note = (message or "")[:200]
            try:
                record_violation(g["id"], note=note, detected="llm_scan")
                recorded.append({
                    "goal_id": g["id"],
                    "text": g.get("text", ""),
                    "note": note,
                })
            except Exception as e:
                log.debug(f"[scan_violations] record failed: {e}")
    return recorded


def build_active_context_summary(max_recurring: int = 5,
                                  max_constraints: int = 8) -> str:
    """Компактный текст про активные recurring+constraints для инжекции в
    LLM prompt. Юзер не видит этот текст — он только для модели.

    Формат:
        Активные привычки:
          - пить воду (2/4 сегодня, отставание 1)
          - йога утром (выполнено)
        Ограничения:
          - не ем орехи (avoid; 0 нарушений за неделю)
          - не работать после 23 (avoid; 2 нарушения за неделю, последнее вчера)
    """
    lines = []

    recurring = list_recurring(active_only=True)[:max_recurring]
    if recurring:
        lines.append("Активные привычки:")
        for g in recurring:
            p = get_progress(g["id"])
            if not p:
                continue
            if not p["active_today"]:
                lines.append(f"  - {p['text']} (не требуется сегодня)")
                continue
            # Weekly vs daily formatting
            if p.get("period") == "week":
                status = (f"выполнено {p['done_this_week']}/"
                          f"{p['times_per_week']} за неделю")
            elif p.get("times_per_day"):
                status = f"выполнено {p['done_today']}/{p['times_per_day']} сегодня"
            else:
                status = f"выполнено {p['done_today']} сегодня"
            if p["lag"] > 0:
                status += f", отставание {p['lag']}"
            lines.append(f"  - {p['text']} ({status})")

    constraints = list_constraint_status(days=7)[:max_constraints]
    if constraints:
        lines.append("Ограничения:")
        for c in constraints:
            v_today = c["violations_today"]
            v_7d = c["violations_7d"]
            bits = [f"{c['polarity']}"]
            if v_today > 0:
                bits.append(f"{v_today} нарушений сегодня")
            elif v_7d > 0:
                bits.append(f"{v_7d} нарушений за 7 дней")
            else:
                bits.append("чисто")
            lines.append(f"  - {c['text']} ({', '.join(bits)})")

    return "\n".join(lines)
