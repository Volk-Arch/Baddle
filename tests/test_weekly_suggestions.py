"""Тесты weekly-aggregate suggestions."""
import pytest
import time


@pytest.fixture
def mock_llm(monkeypatch):
    from src import graph_logic
    responses: list = []

    def fake(messages, **kwargs):
        if not responses:
            return ("", {})
        return (responses.pop(0), {})
    monkeypatch.setattr(graph_logic, "_graph_generate", fake)
    return responses


def test_weekly_aggregate_no_data(temp_data_dir):
    """Пустые данные → None."""
    from src.suggestions import _weekly_aggregate
    assert _weekly_aggregate() is None


def test_weekly_aggregate_collects_checkins(temp_data_dir):
    from src.checkins import add_checkin
    from src.suggestions import _weekly_aggregate

    # 3 checkins сегодня
    add_checkin(stress=70, focus=40)
    add_checkin(stress=60, focus=50)
    add_checkin(stress=80, focus=30)

    agg = _weekly_aggregate()
    assert agg is not None
    assert agg["checkins"]["this_n"] == 3
    assert agg["checkins"]["this_week"]["stress"] == 70.0


def test_weekly_aggregate_recurring(temp_data_dir):
    from src.goals_store import add_goal, record_instance
    from src.suggestions import _weekly_aggregate

    gid = add_goal(text="пить воду", kind="recurring",
                   schedule={"times_per_day": 4})
    # 2 instance'а сегодня
    record_instance(gid)
    record_instance(gid)
    # 1 instance 10 дней назад — в prev_week не попадёт (он > 2 недели назад)
    record_instance(gid, ts=time.time() - 10 * 86400)

    agg = _weekly_aggregate()
    assert agg is not None
    rec = agg["recurring"]
    assert len(rec) == 1
    assert rec[0]["this_week"] == 2
    assert rec[0]["prev_week"] == 1


def test_suggest_from_weekly_review(temp_data_dir, mock_llm):
    from src.checkins import add_checkin
    from src.suggestions import suggest_from_weekly_review

    # Накопим данные
    for _ in range(4):
        add_checkin(stress=75, focus=35)

    mock_llm.append(
        "KIND: recurring\nTEXT: короткая прогулка каждый день\nFREQ: 1/day"
    )
    result = suggest_from_weekly_review(lang="ru")
    assert result is not None
    assert result["draft"]["kind"] == "new_recurring"
    assert result["trigger"]["type"] == "weekly_review"
    assert "aggregate" in result["trigger"]


def test_suggest_from_weekly_no_data(temp_data_dir):
    """Без данных — sкорее None без LLM call."""
    from src.suggestions import suggest_from_weekly_review
    assert suggest_from_weekly_review(lang="ru") is None
