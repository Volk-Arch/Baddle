"""Тесты двустороннего моста activity ↔ recurring:
- activity_log.try_match_recurring_instance (taskplayer → instance)
- intent_router.extract_activity_name (chat → activity)

Моки LLM, без сервера.
"""
import pytest


@pytest.fixture
def mock_llm_ir(monkeypatch):
    """Mock `_graph_generate` в intent_router (разные responses по call)."""
    from src import intent_router
    responses: list = []

    def fake(messages, **kwargs):
        if not responses:
            return ("", {})
        return (responses.pop(0), {})

    monkeypatch.setattr(intent_router, "_graph_generate", fake)
    return responses


def test_try_match_recurring_matches(temp_data_dir, mock_llm_ir,
                                       clean_router_cache):
    """«Обед» → находит recurring «покушать 3 раза» по LLM-выбору (#1)."""
    from src.goals_store import add_goal
    from src.activity_log import try_match_recurring_instance
    from src.recurring import get_progress

    gid = add_goal(text="покушать 3 раза в день", kind="recurring",
                   schedule={"times_per_day": 3}, category="food",
                   mode="rhythm")

    # LLM отвечает "1" → первая recurring matched
    mock_llm_ir.append("1")
    r = try_match_recurring_instance("Обед", activity_category="food", lang="ru")
    assert r is not None
    assert r["goal_id"] == gid
    assert r["progress"]["done_today"] == 1


def test_try_match_recurring_no_match(temp_data_dir, mock_llm_ir,
                                        clean_router_cache):
    """«Код» → LLM говорит 'activity', не matched."""
    from src.goals_store import add_goal
    from src.activity_log import try_match_recurring_instance

    add_goal(text="покушать 3 раза в день", kind="recurring",
             schedule={"times_per_day": 3}, category="food")

    mock_llm_ir.append("activity")
    r = try_match_recurring_instance("Код", activity_category="work", lang="ru")
    assert r is None


def test_try_match_recurring_empty_recurring(temp_data_dir, mock_llm_ir):
    """Нет recurring → сразу None, LLM не зовётся."""
    from src.activity_log import try_match_recurring_instance
    r = try_match_recurring_instance("Обед", activity_category="food", lang="ru")
    assert r is None
    # LLM не должен был вызываться
    assert len(mock_llm_ir) == 0


def test_try_match_category_narrows(temp_data_dir, mock_llm_ir,
                                     clean_router_cache):
    """Если category matches — сужаем до неё (уменьшает false positives)."""
    from src.goals_store import add_goal
    from src.activity_log import try_match_recurring_instance

    food_id = add_goal(text="покушать", kind="recurring",
                        schedule={"times_per_day": 3}, category="food")
    add_goal(text="зарядка", kind="recurring",
             schedule={"times_per_day": 1}, category="health")

    # Для category=food — LLM видит только food-цели в списке, ответ "1"
    mock_llm_ir.append("1")
    r = try_match_recurring_instance("Обед", activity_category="food",
                                      lang="ru")
    assert r is not None
    assert r["goal_id"] == food_id   # matched food recurring, не health


def test_auto_stop_previous_on_start(temp_data_dir):
    """start_activity с автостопом предыдущей (поведение «Следующая»)."""
    from src.activity_log import start_activity, stop_activity, get_active

    aid1 = start_activity("Task A")
    assert get_active()["id"] == aid1

    # Старт нового — предыдущий stopped автоматически
    aid2 = start_activity("Task B")
    cur = get_active()
    assert cur["id"] == aid2
    assert cur["id"] != aid1

    # Ручной stop
    stopped = stop_activity("manual")
    assert stopped["id"] == aid2
    assert get_active() is None


def test_category_autodetect_from_name(temp_data_dir):
    """Простая keyword-based категоризация без LLM."""
    from src.activity_log import detect_category
    assert detect_category("Обед") == "food"
    assert detect_category("Код") == "work"
    assert detect_category("Сон") == "health"
    assert detect_category("прогулка") == "health"
    assert detect_category("случайная вещь") is None


def test_day_summary_aggregates(temp_data_dir):
    from src.activity_log import start_activity, stop_activity, day_summary
    import time

    start_activity("Код", category="work")
    time.sleep(0.01)
    stop_activity("manual")

    s = day_summary()
    assert s["activity_count"] >= 1
    assert "work" in s["by_category_s"]
