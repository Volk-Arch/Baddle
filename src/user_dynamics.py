"""Filesystem-touching user dynamics (cognitive_load aggregation, daily rollover).

Эти функции работают над РГК-state (day_summary, cognitive_load_today) и
читают внешние источники: activity_log, horizon. Не подходят для самой
РГК (substrate должен быть pure data, без I/O или global imports).

Раньше жили как методы на UserState — после B5 W2 пакет 4 вытащены сюда
как module-level functions, принимающие `rgk` объект напрямую. UserState
оставляет thin wrappers для backward-compat (см. user_state.py).
"""
import datetime as _dt
from typing import Optional

from .user_state import compute_cognitive_load


def update_cognitive_load(rgk) -> None:
    """Pull today's activity log + sync_error → recompute cognitive_load_today.

    Aggregates 4 observable из activity_log:
        tasks_started     — count(start events today)
        tasks_completed   — count(done activities today)
        context_switches  — count(stop_reason="switch")
        complexity_sum    — sum(surprise_at_start over today's activities)

    Plus 1 derived:
        progress_delta    — sync_error_slow_now − sync_error_at_dawn

    Saves в `rgk.day_summary[today_str]` + recomputes `rgk.cognitive_load_today`
    через `compute_cognitive_load` helper.

    Вызывается из cognitive_loop._check_cognitive_load_update раз в 5 мин.
    """
    today = _dt.date.today()
    today_str = today.strftime("%Y-%m-%d")

    tasks_started = 0
    tasks_completed = 0
    context_switches = 0
    complexity_sum = 0.0
    try:
        from .activity_log import _replay
        start_of_day = _dt.datetime.combine(today, _dt.time.min).timestamp()
        for act in _replay().values():
            started = act.get("started_at") or 0
            if started < start_of_day:
                continue
            tasks_started += 1
            if act.get("status") == "done":
                tasks_completed += 1
            if act.get("stop_reason") == "switch":
                context_switches += 1
            complexity_sum += float(act.get("surprise_at_start") or 0.0)
    except Exception:
        pass

    today_summary = rgk.day_summary.setdefault(today_str, {})
    sync_at_dawn = today_summary.get("sync_error_at_dawn")
    sync_now = sync_at_dawn or 0.0
    try:
        from .horizon import get_global_state
        sync_now = float(get_global_state().rgk.sync_slow.value)
        if sync_at_dawn is None:
            sync_at_dawn = sync_now
            today_summary["sync_error_at_dawn"] = round(sync_at_dawn, 6)
    except Exception:
        sync_at_dawn = sync_at_dawn or 0.0
    progress_delta = sync_now - (sync_at_dawn or 0.0)

    today_summary.update({
        "tasks_started": tasks_started,
        "tasks_completed": tasks_completed,
        "context_switches": context_switches,
        "complexity_sum": round(complexity_sum, 4),
        "progress_delta": round(progress_delta, 6),
    })

    rgk.cognitive_load_today = compute_cognitive_load(today_summary, progress_delta)


def rollover_day(rgk, hrv_recovery: Optional[float] = None) -> None:
    """Полуночный reset: persist yesterday в day_summary, обнулить load.

    Ровно один раз в день (вызов идемпотентен через date check). Saves:
        - cognitive_load_today as final `cognitive_load` в day_summary[yesterday]
        - sync_error_at_dawn для следующего дня (snapshot для progress_delta)
    Resets:
        - cognitive_load_today = 0.0

    hrv_recovery — параметр сохранён для backward-compat call signature
    (Phase A/B вызывали с recovery), сейчас не используется (после Шага 6
    long_reserve recovery удалён).

    Spec: docs/capacity-design.md §Поля UserState.
    """
    today = _dt.date.today()
    yesterday_str = (today - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
    today_str = today.strftime("%Y-%m-%d")

    if yesterday_str in rgk.day_summary:
        rgk.day_summary[yesterday_str]["cognitive_load"] = round(
            rgk.cognitive_load_today, 4)

    try:
        from .horizon import get_global_state
        sync_at_dawn = float(get_global_state().rgk.sync_slow.value)
    except Exception:
        sync_at_dawn = 0.0
    rgk.day_summary.setdefault(today_str, {})
    rgk.day_summary[today_str]["sync_error_at_dawn"] = round(sync_at_dawn, 6)

    rgk.cognitive_load_today = 0.0

    # Trim history: keep only last 30 days
    if len(rgk.day_summary) > 30:
        cutoff = sorted(rgk.day_summary.keys())[:-30]
        for k in cutoff:
            rgk.day_summary.pop(k, None)
