"""Тесты observation→suggestion pipeline.

Мокаем LLM через `_graph_generate` в suggestions module.
"""
import pytest


@pytest.fixture
def mock_llm_suggestions(monkeypatch):
    """Mock `_graph_generate` в graph_logic — suggestions импортирует его локально."""
    from src import graph_logic
    responses: list = []

    def fake(messages, **kwargs):
        if not responses:
            return ("", {})
        return (responses.pop(0), {})

    monkeypatch.setattr(graph_logic, "_graph_generate", fake)
    return responses


def test_parse_draft_recurring():
    from src.suggestions import _parse_draft_response

    raw = """KIND: recurring
TEXT: завтрак каждое утро
FREQ: 1/day"""
    d = _parse_draft_response(raw)
    assert d["kind"] == "new_recurring"
    assert d["text"] == "завтрак каждое утро"
    assert d["schedule"] == {"times_per_day": 1}


def test_parse_draft_constraint():
    from src.suggestions import _parse_draft_response

    raw = """KIND: constraint
TEXT: не работать после 23:00
POLARITY: avoid"""
    d = _parse_draft_response(raw)
    assert d["kind"] == "new_constraint"
    assert d["polarity"] == "avoid"


def test_parse_draft_weekly():
    from src.suggestions import _parse_draft_response

    raw = """KIND: recurring
TEXT: спорт
FREQ: 3/week"""
    d = _parse_draft_response(raw)
    assert d["kind"] == "new_recurring"
    assert d["schedule"] == {"times_per_week": 3}


def test_parse_draft_malformed():
    from src.suggestions import _parse_draft_response

    # Без обязательных полей
    assert _parse_draft_response("") is None
    assert _parse_draft_response("random text") is None
    assert _parse_draft_response("KIND: recurring") is None  # нет text


def test_suggest_from_pattern_builds_draft(mock_llm_suggestions):
    mock_llm_suggestions.append(
        "KIND: recurring\nTEXT: завтрак в 8:00\nFREQ: 1/day"
    )
    from src.suggestions import suggest_from_pattern

    pattern = {
        "kind": "skip_breakfast",
        "weekday": 3,
        "hint_ru": "3 четверга подряд пропустил завтрак",
    }
    result = suggest_from_pattern(pattern, lang="ru")
    assert result is not None
    assert result["draft"]["kind"] == "new_recurring"
    assert result["trigger"]["type"] == "pattern"
    assert result["trigger"]["kind"] == "skip_breakfast"


def test_suggest_from_pattern_no_hint_returns_none(mock_llm_suggestions):
    from src.suggestions import suggest_from_pattern
    # pattern без hint_ru — не вызываем LLM, сразу None
    assert suggest_from_pattern({"kind": "x"}, lang="ru") is None


def test_suggest_from_checkins_stress_streak(mock_llm_suggestions,
                                               temp_data_dir):
    """Высокий stress 7 дней → LLM предлагает восстановительную привычку."""
    from src.checkins import add_checkin
    # 5 checkins с высоким stress
    for _ in range(5):
        add_checkin(stress=80, focus=40)

    mock_llm_suggestions.append(
        "KIND: recurring\nTEXT: медитация 10 минут\nFREQ: 1/day"
    )
    from src.suggestions import suggest_from_checkins
    result = suggest_from_checkins(days=7, lang="ru")
    assert result is not None
    assert "стресс" in result["trigger"]["reasons"][0]


def test_suggest_from_checkins_no_data(temp_data_dir):
    """Нет checkins → None, без LLM call."""
    from src.suggestions import suggest_from_checkins
    assert suggest_from_checkins(days=7) is None


def test_collect_suggestions_dedup(mock_llm_suggestions, temp_data_dir):
    """Если два источника выдали одинаковый text — dedup."""
    from src.checkins import add_checkin
    for _ in range(5):
        add_checkin(stress=80)

    # Pattern + checkin → оба хотят одну и ту же идею
    mock_llm_suggestions.extend([
        "KIND: recurring\nTEXT: отдых\nFREQ: 1/day",
        "KIND: recurring\nTEXT: отдых\nFREQ: 1/day",
    ])
    from src.suggestions import collect_suggestions
    items = collect_suggestions(lang="ru", include_stress=False)
    # Dedup → не более 1 с текстом "отдых"
    texts = [(it["draft"] or {}).get("text") for it in items]
    assert texts.count("отдых") <= 1


def test_make_suggestion_card_recurring():
    from src.suggestions import make_suggestion_card
    item = {
        "draft": {"kind": "new_recurring", "text": "test",
                   "schedule": {"times_per_day": 1}, "mode": "rhythm"},
        "trigger": {"type": "pattern",
                     "description": "3 дня подряд stress"},
    }
    card = make_suggestion_card(item, lang="ru")
    assert card["type"] == "intent_confirm"
    assert card["kind"] == "new_recurring"
    assert "3 дня" in card["description_ru"]
    assert card["source"] == "observation"
