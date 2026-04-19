"""Тесты связки plan ↔ recurring goal через goal_id.

Без LM — чистая логика append-only replay.
"""
import pytest


def test_plan_add_with_goal_id(temp_data_dir):
    """Plan создаётся с goal_id, persist'ится, видится в get_plan."""
    from src.plans import add_plan, get_plan

    pid = add_plan(
        name="Обед 13:00",
        category="food",
        ts_start=1776000000,
        goal_id="abc123xyz",
    )
    p = get_plan(pid)
    assert p is not None
    assert p["goal_id"] == "abc123xyz"


def test_plan_complete_triggers_instance(temp_data_dir):
    """complete_plan с linked goal_id → record_instance на linked goal."""
    from src.goals_store import add_goal, get_goal
    from src.plans import add_plan, complete_plan
    from src.recurring import get_progress

    gid = add_goal(
        text="пить воду 4 раза",
        kind="recurring",
        schedule={"times_per_day": 4},
        category="health",
    )
    pid = add_plan(name="Стакан воды 10:00", ts_start=1776000000, goal_id=gid)

    # До complete — прогресс 0
    p0 = get_progress(gid)
    assert p0["done_today"] == 0

    # Complete plan → автоматически запишется instance
    result = complete_plan(plan_id=pid, for_date="2026-05-01")
    assert result["linked_goal"] is not None
    assert result["linked_goal"]["goal_id"] == gid

    # После — прогресс 1
    p1 = get_progress(gid)
    assert p1["done_today"] == 1


def test_plan_complete_no_goal_id_no_side_effect(temp_data_dir):
    """Plan без goal_id — complete не трогает goals_store."""
    from src.goals_store import add_goal
    from src.plans import add_plan, complete_plan
    from src.recurring import get_progress

    # Добавим recurring-цель, но plan НЕ линкован
    gid = add_goal(text="привычка", kind="recurring",
                   schedule={"times_per_day": 1})
    pid = add_plan(name="одноразовый event", ts_start=1776000000)

    result = complete_plan(plan_id=pid)
    assert result["linked_goal"] is None

    p = get_progress(gid)
    assert p["done_today"] == 0


def test_plan_complete_closed_goal_ignored(temp_data_dir):
    """Если linked goal абандонен/завершён — complete plan не пишет instance."""
    from src.goals_store import add_goal, abandon_goal
    from src.plans import add_plan, complete_plan

    gid = add_goal(text="привычка", kind="recurring",
                   schedule={"times_per_day": 1})
    abandon_goal(gid, "test")

    pid = add_plan(name="линкованный event", ts_start=1776000000, goal_id=gid)
    result = complete_plan(plan_id=pid)
    assert result["linked_goal"] is None   # goal status != open


def test_plan_complete_oneshot_goal_ignored(temp_data_dir):
    """Plan linked к oneshot goal (не recurring) — instance не пишем."""
    from src.goals_store import add_goal
    from src.plans import add_plan, complete_plan

    gid = add_goal(text="разовая цель", kind="oneshot")
    pid = add_plan(name="event", ts_start=1776000000, goal_id=gid)

    result = complete_plan(plan_id=pid)
    assert result["linked_goal"] is None   # kind != recurring


def test_plan_update_goal_id(temp_data_dir):
    """Можно обновить goal_id через update_plan."""
    from src.plans import add_plan, update_plan, get_plan

    pid = add_plan(name="event", ts_start=1776000000)
    update_plan(pid, {"goal_id": "new123"})

    p = get_plan(pid)
    assert p["goal_id"] == "new123"
