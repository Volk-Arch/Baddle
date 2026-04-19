"""Тесты workspace-aware фильтрации recurring/constraints."""
import pytest


def test_list_recurring_workspace_filter(temp_data_dir):
    from src.goals_store import add_goal
    from src.recurring import list_recurring

    work_gid = add_goal(text="стендап", kind="recurring", workspace="work",
                        schedule={"times_per_day": 1})
    personal_gid = add_goal(text="йога", kind="recurring", workspace="personal",
                             schedule={"times_per_day": 1})

    # Без фильтра — видим обе
    all_recs = list_recurring(active_only=True)
    all_ids = {r["id"] for r in all_recs}
    assert work_gid in all_ids
    assert personal_gid in all_ids

    # С workspace=work — только work
    work_recs = list_recurring(active_only=True, workspace="work")
    work_ids = {r["id"] for r in work_recs}
    assert work_gid in work_ids
    assert personal_gid not in work_ids


def test_list_constraints_workspace_filter(temp_data_dir):
    from src.goals_store import add_goal
    from src.recurring import list_constraints

    wc = add_goal(text="не переработки", kind="constraint", workspace="work",
                  polarity="avoid")
    pc = add_goal(text="не сахар", kind="constraint", workspace="personal",
                  polarity="avoid")

    work_cs = list_constraints(active_only=True, workspace="work")
    ids = {c["id"] for c in work_cs}
    assert wc in ids
    assert pc not in ids


def test_global_recurring_visible_everywhere(temp_data_dir):
    """Если у цели workspace=None — она global и видна из всех воркспейсов."""
    from src.goals_store import _append
    import uuid
    # Создаём через _append чтобы явно оставить workspace=None
    gid = uuid.uuid4().hex[:12]
    _append({
        "action": "create", "id": gid,
        "text": "пить воду",
        "mode": "rhythm", "workspace": None,
        "kind": "recurring", "schedule": {"times_per_day": 4},
    })

    from src.recurring import list_recurring
    # Видна в любом workspace
    for ws in ("work", "personal", "main"):
        items = list_recurring(active_only=True, workspace=ws)
        assert any(r["id"] == gid for r in items), \
            f"global goal not visible in {ws}"


def test_build_context_summary_workspace_scoped(temp_data_dir):
    from src.goals_store import add_goal
    from src.recurring import build_active_context_summary

    add_goal(text="стендап", kind="recurring", workspace="work",
             schedule={"times_per_day": 1})
    add_goal(text="йога", kind="recurring", workspace="personal",
             schedule={"times_per_day": 1})

    # В work видим «стендап», не видим «йога»
    work_ctx = build_active_context_summary(workspace="work")
    assert "стендап" in work_ctx
    assert "йога" not in work_ctx

    # В personal — наоборот
    personal_ctx = build_active_context_summary(workspace="personal")
    assert "йога" in personal_ctx
    assert "стендап" not in personal_ctx


def test_router_cache_per_workspace(temp_data_dir, mock_llm_suggestions,
                                      clean_router_cache):
    """Router cache key включает workspace — одно сообщение в разных ws
    не конфликтует."""
    from src.intent_router import route, _cache

    # В work cache — ответ "task"
    mock_llm_suggestions.append("task")
    mock_llm_suggestions.append("question")
    r_work = route("что-то спросить", lang="ru", workspace="work",
                   use_cache=True)

    # В personal — независимый entry, LLM зовётся снова
    mock_llm_suggestions.append("chat")
    r_personal = route("что-то спросить", lang="ru", workspace="personal",
                       use_cache=True)

    # Cache должен иметь 2 разных key'а (work::, personal::)
    keys = list(_cache.keys())
    assert any(k.startswith("work::") for k in keys)
    assert any(k.startswith("personal::") for k in keys)


@pytest.fixture
def mock_llm_suggestions(monkeypatch):
    """Shared с suggestions — мокаем graph_logic._graph_generate."""
    from src import graph_logic
    responses: list = []
    def fake(messages, **kwargs):
        if not responses:
            return ("", {})
        return (responses.pop(0), {})
    monkeypatch.setattr(graph_logic, "_graph_generate", fake)
    return responses
