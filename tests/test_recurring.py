"""Тесты recurring.py + goals_store (instance/violation/kind).

Не требуют LM-сервера — чистая логика append-only replay.
"""
import pytest
import time
from datetime import datetime, date as date_type


def test_add_recurring_goal(temp_data_dir):
    from src.goals_store import add_goal, get_goal

    gid = add_goal(
        text="пить воду 4 раза в день",
        kind="recurring",
        schedule={"times_per_day": 4},
        category="health",
        mode="rhythm",
    )
    g = get_goal(gid)
    assert g is not None
    assert g["kind"] == "recurring"
    assert g["schedule"]["times_per_day"] == 4
    assert g["category"] == "health"
    assert g["status"] == "open"
    assert g["instances"] == []


def test_add_constraint_goal(temp_data_dir):
    from src.goals_store import add_goal, get_goal

    gid = add_goal(
        text="не ем сахар",
        kind="constraint",
        polarity="avoid",
        category="food",
    )
    g = get_goal(gid)
    assert g["kind"] == "constraint"
    assert g["polarity"] == "avoid"
    assert g["violations"] == []


def test_record_instance_and_progress(temp_data_dir):
    from src.goals_store import add_goal, record_instance
    from src.recurring import get_progress

    gid = add_goal(
        text="пить воду 4 раза",
        kind="recurring",
        schedule={"times_per_day": 4},
    )
    record_instance(gid, note="стакан 1")
    record_instance(gid, note="стакан 2")

    p = get_progress(gid)
    assert p["done_today"] == 2
    assert p["times_per_day"] == 4
    # lag зависит от времени суток — проверим только что это число
    assert isinstance(p["lag"], int)
    assert p["lag"] >= 0
    assert p["active_today"] is True


def test_record_violation_and_status(temp_data_dir):
    from src.goals_store import add_goal, record_violation
    from src.recurring import list_constraint_status

    gid = add_goal(
        text="не ем сладкое",
        kind="constraint",
        polarity="avoid",
    )
    record_violation(gid, note="торт", detected="manual")
    record_violation(gid, note="мороженое", detected="llm_scan")

    status = list_constraint_status(days=7)
    our = next((c for c in status if c["goal_id"] == gid), None)
    assert our is not None
    assert our["violations_today"] == 2
    assert our["violations_7d"] == 2
    assert our["polarity"] == "avoid"


def test_oneshot_default_kind(temp_data_dir):
    """Legacy-совместимость: add_goal без kind даёт oneshot."""
    from src.goals_store import add_goal, get_goal

    gid = add_goal(text="выучить испанский")
    g = get_goal(gid)
    assert g["kind"] == "oneshot"
    assert g["schedule"] is None


def test_list_recurring_filters_kind(temp_data_dir):
    from src.goals_store import add_goal
    from src.recurring import list_recurring, list_constraints

    oneshot = add_goal(text="разово", kind="oneshot")
    recurring = add_goal(text="каждый день", kind="recurring",
                          schedule={"times_per_day": 1})
    constraint = add_goal(text="ограничение", kind="constraint",
                           polarity="avoid")

    rec = list_recurring()
    cs = list_constraints()

    rec_ids = {r["id"] for r in rec}
    cs_ids = {c["id"] for c in cs}

    assert recurring in rec_ids
    assert oneshot not in rec_ids
    assert constraint not in rec_ids
    assert constraint in cs_ids
    assert recurring not in cs_ids


def test_list_lagging(temp_data_dir, monkeypatch):
    """При отсутствии instance'ов lag должен быть > 0 если день в разгаре."""
    from src import recurring as R
    from src.goals_store import add_goal

    # Фиксируем "середину дня" чтобы lag был детерминирован
    fake_now = datetime(2026, 5, 1, 14, 0, 0).timestamp()
    monkeypatch.setattr(R.time, "time", lambda: fake_now)

    add_goal(text="3 раза в день", kind="recurring",
             schedule={"times_per_day": 3})

    lagging = R.list_lagging(min_lag=1)
    assert len(lagging) >= 1
    assert lagging[0]["lag"] >= 1


def test_expected_by_now_with_time_windows(temp_data_dir):
    """Если заданы time_windows, expected считается по окнам, а не линейно."""
    from src.recurring import _expected_by_now
    from datetime import datetime

    # Окна завтрак (7-9), обед (12-14), ужин (18-20)
    schedule = {
        "times_per_day": 3,
        "time_windows": [[7, 9], [12, 14], [18, 20]],
    }
    # Утро 10:00 — только завтрак прошёл серединой
    morning = datetime(2026, 5, 1, 10, 0, 0).timestamp()
    assert _expected_by_now(schedule, now=morning) == 1
    # 15:00 — завтрак + обед
    afternoon = datetime(2026, 5, 1, 15, 0, 0).timestamp()
    assert _expected_by_now(schedule, now=afternoon) == 2
    # 21:00 — все три
    evening = datetime(2026, 5, 1, 21, 0, 0).timestamp()
    assert _expected_by_now(schedule, now=evening) == 3


def test_build_active_context_summary_empty(temp_data_dir):
    """Пустой state → пустая строка."""
    from src.recurring import build_active_context_summary
    assert build_active_context_summary() == ""


def test_build_active_context_summary_full(temp_data_dir):
    from src.goals_store import add_goal, record_instance
    from src.recurring import build_active_context_summary

    add_goal(text="пить воду", kind="recurring",
             schedule={"times_per_day": 4})
    add_goal(text="не ем орехи", kind="constraint", polarity="avoid")

    summary = build_active_context_summary()
    assert "Активные привычки:" in summary
    assert "пить воду" in summary
    assert "Ограничения:" in summary
    assert "не ем орехи" in summary


def test_weekly_recurring_progress(temp_data_dir):
    """times_per_week считает instances за неделю, не за день."""
    from src.goals_store import add_goal, record_instance
    from src.recurring import get_progress

    gid = add_goal(
        text="играть на фортепиано",
        kind="recurring",
        schedule={"times_per_week": 3},
        category="learning",
    )
    # Один instance сегодня, один вчера — неделя одна и та же
    record_instance(gid, note="сегодня")
    record_instance(gid, ts=time.time() - 86400, note="вчера")

    p = get_progress(gid)
    assert p is not None
    assert p["period"] == "week"
    assert p["times_per_week"] == 3
    assert p["done_this_week"] == 2
    # lag зависит от дня недели — просто sanity check
    assert isinstance(p["lag"], int)


def test_weekly_expected_grows_during_week(temp_data_dir, monkeypatch):
    """К концу недели ожидаем больше instance'ов."""
    from src.goals_store import add_goal
    from src.recurring import get_progress
    from src import recurring as R
    import datetime as _dt

    gid = add_goal(text="спорт", kind="recurring",
                   schedule={"times_per_week": 7})  # 1 раз в день = 7 в неделю

    # Фикс: пятница 15:00 (weekday=4)
    class FakeDT(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return _dt.datetime(2026, 5, 1, 15, 0, 0)   # Fri May 1, 2026
    monkeypatch.setattr(R, "datetime", FakeDT)

    p = get_progress(gid)
    # weekday=4 (пт) → (4+1)/7 * 7 = 5
    assert p["expected_by_now"] == 5


def test_abandoned_recurring_excluded(temp_data_dir):
    from src.goals_store import add_goal, abandon_goal
    from src.recurring import list_recurring

    gid = add_goal(text="test", kind="recurring",
                   schedule={"times_per_day": 1})
    assert len(list_recurring(active_only=True)) == 1

    abandon_goal(gid, "test cleanup")
    assert len(list_recurring(active_only=True)) == 0
