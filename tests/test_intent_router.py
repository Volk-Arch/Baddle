"""Тесты intent_router — моки LLM, детерминированно.

Не требуют LM-сервера. Мокаем `_graph_generate`.
"""
import pytest


@pytest.fixture
def mock_llm(monkeypatch):
    """Mock `_graph_generate` с программируемым responder'ом.

    usage:
        responder = mock_llm([
            "task",         # ответ на top-classify
            "new_recurring" # ответ на subtype-classify
        ])
    """
    def _setup(responses: list):
        from src import intent_router
        iter_resp = iter(responses)

        def fake_generate(messages, **kwargs):
            try:
                return (next(iter_resp), {})
            except StopIteration:
                return ("", {})

        monkeypatch.setattr(intent_router, "_graph_generate", fake_generate)
        return fake_generate
    return _setup


def test_chat_single_llm_call(mock_llm, clean_router_cache, monkeypatch):
    """chat → только один LLM call, subtype не спрашиваем."""
    from src.intent_router import route

    calls = []
    from src import intent_router

    def wrapped_top(*args, **kwargs):
        calls.append("top")
        return ("chat", 0.9)

    monkeypatch.setattr(intent_router, "_classify_top", wrapped_top)
    r = route("привет", lang="ru", use_cache=False)
    assert r["kind"] == "chat"
    assert r["subtype"] is None
    assert calls == ["top"]   # только один вызов top


def test_task_two_llm_calls(mock_llm, clean_router_cache):
    mock_llm(["task", "new_recurring"])
    from src.intent_router import route

    r = route("хочу начать бегать каждое утро", lang="ru", use_cache=False)
    assert r["kind"] == "task"
    assert r["subtype"] == "new_recurring"
    assert r["confidence_top"] >= 0.7


def test_task_new_constraint(mock_llm, clean_router_cache):
    mock_llm(["task", "new_constraint"])
    from src.intent_router import route
    r = route("хочу бросить кофе", lang="ru", use_cache=False)
    assert r["kind"] == "task"
    assert r["subtype"] == "new_constraint"


def test_task_new_goal(mock_llm, clean_router_cache):
    mock_llm(["task", "new_goal"])
    from src.intent_router import route
    r = route("хочу выучить испанский", lang="ru", use_cache=False)
    assert r["subtype"] == "new_goal"


def test_task_question(mock_llm, clean_router_cache):
    mock_llm(["task", "question"])
    from src.intent_router import route
    r = route("что мне сделать сегодня?", lang="ru", use_cache=False)
    assert r["subtype"] == "question"


def test_fact_no_recurring_thought(mock_llm, clean_router_cache, temp_data_dir):
    """fact без recurring + LLM сказал 'thought' → thought."""
    mock_llm(["fact", "thought"])
    from src.intent_router import route
    r = route("купил молоко", lang="ru", use_cache=False)
    assert r["kind"] == "fact"
    assert r["subtype"] == "thought"
    assert r["target_goal_id"] is None


def test_fact_no_recurring_but_activity(mock_llm, clean_router_cache,
                                          temp_data_dir):
    """fact без recurring + LLM сказал 'activity' → activity.
    Триггерит chat→taskplayer auto-start."""
    mock_llm(["fact", "activity"])
    from src.intent_router import route
    r = route("начал тренировку", lang="ru", use_cache=False)
    assert r["kind"] == "fact"
    assert r["subtype"] == "activity"
    assert r["target_goal_id"] is None
    assert r["confidence_sub"] >= 0.7


def test_fact_with_matched_recurring(mock_llm, clean_router_cache,
                                      temp_data_dir):
    """fact + активный recurring → matcher возвращает goal_id."""
    from src.goals_store import add_goal
    gid = add_goal(text="пить воду", kind="recurring",
                   schedule={"times_per_day": 4})

    # Mock: top=fact → subtype-matcher выдаёт "1" (первый в списке)
    mock_llm(["fact", "1"])
    from src.intent_router import route
    r = route("выпил стакан воды", lang="ru", use_cache=False)
    assert r["kind"] == "fact"
    assert r["subtype"] == "instance"
    assert r["target_goal_id"] == gid


def test_fact_activity_when_no_match(mock_llm, clean_router_cache,
                                      temp_data_dir):
    from src.goals_store import add_goal
    add_goal(text="пить воду", kind="recurring",
             schedule={"times_per_day": 4})

    mock_llm(["fact", "activity"])
    from src.intent_router import route
    r = route("провёл митинг", lang="ru", use_cache=False)
    assert r["kind"] == "fact"
    assert r["subtype"] == "activity"
    assert r["target_goal_id"] is None


def test_constraint_event(mock_llm, clean_router_cache):
    mock_llm(["constraint_event"])   # subtype для constraint не зовётся
    from src.intent_router import route
    r = route("съел торт хотя обещал не есть", lang="ru", use_cache=False)
    assert r["kind"] == "constraint_event"
    assert r["subtype"] == "violation"


def test_unknown_top_fallback(mock_llm, clean_router_cache):
    """LLM ответил мусором → fallback на task/low-confidence."""
    mock_llm(["абракадабра", "question"])
    from src.intent_router import route
    r = route("hello", lang="ru", use_cache=False)
    # Fallback = task с low conf (не 'chat' т.к. LLM не распознал)
    assert r["kind"] == "task"
    assert r["confidence_top"] < 0.7


def test_cache_hit(mock_llm, clean_router_cache):
    """Второй идентичный route → cache hit, LLM не зовётся."""
    mock_llm(["chat"])
    from src.intent_router import route
    r1 = route("привет", lang="ru", use_cache=True)
    r2 = route("привет", lang="ru", use_cache=True)
    assert r1["kind"] == r2["kind"] == "chat"
    assert r2["source"] == "cache"


def test_make_draft_card_recurring():
    from src.intent_router import make_draft_card
    card = make_draft_card("task", "new_recurring", "пить воду каждый день", lang="ru")
    assert card["type"] == "intent_confirm"
    assert card["kind"] == "new_recurring"
    assert card["draft"]["schedule"]["times_per_day"] == 1
    assert card["draft"]["mode"] == "rhythm"


def test_make_draft_card_constraint():
    from src.intent_router import make_draft_card
    card = make_draft_card("task", "new_constraint", "не ем сахар", lang="ru")
    assert card["draft"]["polarity"] == "avoid"


def test_make_draft_card_goal():
    from src.intent_router import make_draft_card
    card = make_draft_card("task", "new_goal", "выучить испанский", lang="ru")
    assert card["draft"]["mode"] == "horizon"


def test_extract_activity_name_cleans_llm_response(mock_llm):
    """LLM может вернуть с лишними кавычками/пробелами/точками."""
    mock_llm(['"Тренировка".'])
    from src.intent_router import extract_activity_name
    name = extract_activity_name("начал тренировку", lang="ru")
    assert name == "Тренировка"


def test_extract_activity_name_multiline(mock_llm):
    """Берём только первую строку."""
    mock_llm(["Код\nобъяснение не нужно"])
    from src.intent_router import extract_activity_name
    name = extract_activity_name("начинаю писать код", lang="ru")
    assert name == "Код"


def test_extract_activity_name_empty_input():
    from src.intent_router import extract_activity_name
    assert extract_activity_name("") is None
    assert extract_activity_name("   ") is None


def test_extract_activity_name_too_long_filtered(mock_llm):
    """Если LLM вернул слишком длинную строку — None (мусор)."""
    mock_llm(["a" * 100])
    from src.intent_router import extract_activity_name
    assert extract_activity_name("something", lang="ru") is None
