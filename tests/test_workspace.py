"""W14.1 — workspace primitive: add / list_pending / select / commit / archive_expired."""
import time

import pytest

from src.memory import workspace
from src.graph_logic import _graph


@pytest.fixture
def clean_graph():
    """Очищаем _graph между тестами."""
    saved = {"nodes": list(_graph["nodes"])}
    _graph["nodes"] = []
    yield
    _graph["nodes"] = saved["nodes"]


@pytest.fixture
def force_user_mode_R(monkeypatch):
    """Counter-wave penalty не активен (mode='R' default)."""
    class FakeUser:
        mode = "R"

    class FakeRGK:
        user = FakeUser()

    monkeypatch.setattr("src.substrate.rgk.get_global_rgk", lambda: FakeRGK())


@pytest.fixture
def force_user_mode_C(monkeypatch):
    """Counter-wave penalty активен."""
    class FakeUser:
        mode = "C"

    class FakeRGK:
        user = FakeUser()

    monkeypatch.setattr("src.substrate.rgk.get_global_rgk", lambda: FakeRGK())


# ── add() ───────────────────────────────────────────────────────────────


def test_add_creates_workspace_node(clean_graph):
    idx = workspace.add(actor="baddle", action_kind="alert", text="test alert",
                        urgency=0.7, ttl_seconds=600)
    assert idx == 0
    node = _graph["nodes"][idx]
    assert node["scope"] == "workspace"
    assert node["expires_at"] is not None
    assert node["expires_at"] > time.time()
    assert node["urgency"] == 0.7
    assert node["accumulate"] is True  # default
    assert node["actor"] == "baddle"
    assert node["action_kind"] == "alert"
    assert node["text"] == "test alert"


def test_add_with_dedup_key_returns_existing(clean_graph):
    """Повторный add с тем же dedup_key не создаёт новую ноду."""
    idx1 = workspace.add(actor="baddle", action_kind="alert", text="repeat",
                         dedup_key="alert:plan_overdue:42", ttl_seconds=600)
    idx2 = workspace.add(actor="baddle", action_kind="alert", text="repeat",
                         dedup_key="alert:plan_overdue:42", ttl_seconds=600)
    assert idx1 == idx2
    assert len(_graph["nodes"]) == 1


def test_add_dedup_after_expire_creates_new(clean_graph):
    """Если ноду expired — повторный add с тем же key создаёт новую."""
    idx1 = workspace.add(actor="baddle", action_kind="alert", text="t",
                         dedup_key="k1", ttl_seconds=0.001)
    time.sleep(0.01)
    idx2 = workspace.add(actor="baddle", action_kind="alert", text="t",
                         dedup_key="k1", ttl_seconds=600)
    assert idx1 != idx2
    assert len(_graph["nodes"]) == 2


# ── list_pending() ──────────────────────────────────────────────────────


def test_list_pending_excludes_graph_nodes(clean_graph):
    """list_pending не возвращает LTM-ноды (scope='graph')."""
    workspace.add(actor="baddle", action_kind="alert", text="ws", ttl_seconds=600)
    # Симулируем существующую LTM ноду
    _graph["nodes"].append({
        "id": 1, "scope": "graph", "expires_at": None,
        "type": "thought", "text": "ltm",
    })
    pending = workspace.list_pending()
    assert len(pending) == 1
    assert pending[0]["text"] == "ws"


def test_list_pending_excludes_expired(clean_graph):
    workspace.add(actor="baddle", action_kind="alert", text="alive",
                  ttl_seconds=600)
    workspace.add(actor="baddle", action_kind="alert", text="dead",
                  ttl_seconds=0.001)
    time.sleep(0.01)
    pending = workspace.list_pending()
    assert len(pending) == 1
    assert pending[0]["text"] == "alive"


# ── select() ────────────────────────────────────────────────────────────


def test_select_immediate_preempts_budget(clean_graph, force_user_mode_R):
    """accumulate=False ноды все эмитятся, не ограничены max_emit."""
    workspace.add(actor="user", action_kind="user_chat", text="msg1",
                  urgency=1.0, accumulate=False)
    workspace.add(actor="user", action_kind="user_chat", text="msg2",
                  urgency=1.0, accumulate=False)
    workspace.add(actor="user", action_kind="user_chat", text="msg3",
                  urgency=1.0, accumulate=False)
    selected = workspace.select(max_emit=1)
    assert len(selected) == 3  # все 3 immediate, max_emit не ограничивает их


def test_select_accumulating_top_k_by_urgency(clean_graph, force_user_mode_R):
    """accumulate=True: top-K по urgency desc."""
    a = workspace.add(actor="baddle", action_kind="brief_morning", text="A",
                      urgency=0.3, accumulate=True)
    b = workspace.add(actor="baddle", action_kind="brief_morning", text="B",
                      urgency=0.8, accumulate=True)
    c = workspace.add(actor="baddle", action_kind="brief_morning", text="C",
                      urgency=0.5, accumulate=True)
    selected = workspace.select(max_emit=2)
    assert selected == [b, c]  # B (0.8) → C (0.5), пропуск A (0.3)


def test_select_drops_expired(clean_graph, force_user_mode_R):
    workspace.add(actor="baddle", action_kind="brief_morning", text="alive",
                  urgency=0.5, ttl_seconds=600)
    workspace.add(actor="baddle", action_kind="brief_morning", text="dead",
                  urgency=0.9, ttl_seconds=0.001)
    time.sleep(0.01)
    selected = workspace.select(max_emit=2)
    assert len(selected) == 1
    assert _graph["nodes"][selected[0]]["text"] == "alive"


def test_select_counter_wave_penalty(clean_graph, force_user_mode_C):
    """В mode='C' push-style action_kind получает -0.3 urgency."""
    push = workspace.add(actor="baddle", action_kind="alert", text="push",
                         urgency=0.6, accumulate=True)  # 0.6 - 0.3 = 0.3
    pull = workspace.add(actor="user", action_kind="user_chat", text="pull",
                         urgency=0.4, accumulate=True)  # без penalty (не push)
    selected = workspace.select(max_emit=1)
    assert selected == [pull]  # 0.4 > 0.3 после penalty


def test_select_counter_wave_inactive_in_mode_R(clean_graph, force_user_mode_R):
    """В mode='R' penalty не применяется — push выигрывает по urgency."""
    push = workspace.add(actor="baddle", action_kind="alert", text="push",
                         urgency=0.6, accumulate=True)
    pull = workspace.add(actor="user", action_kind="user_chat", text="pull",
                         urgency=0.4, accumulate=True)
    selected = workspace.select(max_emit=1)
    assert selected == [push]  # 0.6 > 0.4


def test_select_empty_returns_empty(clean_graph, force_user_mode_R):
    assert workspace.select() == []


# ── commit() ────────────────────────────────────────────────────────────


def test_commit_promotes_to_graph(clean_graph):
    idx = workspace.add(actor="baddle", action_kind="alert", text="t",
                        ttl_seconds=600)
    n = workspace.commit([idx])
    assert n == 1
    node = _graph["nodes"][idx]
    assert node["scope"] == "graph"
    assert node["expires_at"] is None
    assert "committed_at" in node


def test_commit_skips_non_workspace(clean_graph):
    """commit() игнорирует уже committed / non-workspace ноды."""
    idx = workspace.add(actor="baddle", action_kind="alert", text="t",
                        ttl_seconds=600)
    workspace.commit([idx])  # первый раз промоут
    n = workspace.commit([idx])  # второй раз ничего не делает
    assert n == 0


def test_commit_handles_invalid_idx(clean_graph):
    """commit() с несуществующим idx — без ошибки."""
    n = workspace.commit([999, -1])
    assert n == 0


# ── archive_expired() ───────────────────────────────────────────────────


def test_archive_expired_marks_archived(clean_graph):
    workspace.add(actor="baddle", action_kind="alert", text="dead",
                  ttl_seconds=0.001)
    workspace.add(actor="baddle", action_kind="alert", text="alive",
                  ttl_seconds=600)
    time.sleep(0.01)
    n = workspace.archive_expired()
    assert n == 1
    scopes = [node["scope"] for node in _graph["nodes"]]
    assert "archived" in scopes
    assert "workspace" in scopes


# ── identity к LTM операциям ────────────────────────────────────────────


def test_workspace_orthogonal_to_ltm_count(clean_graph):
    """Workspace-ноды не должны учитываться в LTM-операциях по умолчанию.

    Sanity-check: list_pending() ≠ полный список нод.
    """
    workspace.add(actor="baddle", action_kind="alert", text="ws", ttl_seconds=600)
    _graph["nodes"].append({
        "id": 1, "scope": "graph", "expires_at": None,
        "type": "thought", "text": "ltm",
    })
    assert len(workspace.list_pending()) == 1
    assert len(_graph["nodes"]) == 2
