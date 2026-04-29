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


# ── workflow patterns ───────────────────────────────────────────────────


def test_record_committed_helper(clean_graph):
    """record_committed = add + immediate commit за один call. Возвращает idx."""
    idx = workspace.record_committed(
        actor="baddle", action_kind="alert",
        text="hello", urgency=0.7, accumulate=False,
        extras={"severity": "info"},
    )
    assert idx is not None
    node = _graph["nodes"][idx]
    assert node["scope"] == "graph"
    assert node["expires_at"] is None
    assert node["urgency"] == 0.7
    assert node["severity"] == "info"
    assert "committed_at" in node


def test_add_immediate_then_commit_workflow(clean_graph):
    """Pattern для chat-сообщений (W14.2): add(accumulate=False) + commit([idx]).

    Используется в /assist для baddle_reply и /assist/chat/append для user_chat —
    нода живёт в workspace миллисекунды, потом сразу promote в LTM.
    """
    idx = workspace.add(actor="baddle", action_kind="baddle_reply",
                        text="response", urgency=1.0, accumulate=False)
    n = workspace.commit([idx])
    assert n == 1
    node = _graph["nodes"][idx]
    assert node["scope"] == "graph"
    assert node["expires_at"] is None
    assert node["actor"] == "baddle"
    assert node["action_kind"] == "baddle_reply"
    assert "committed_at" in node


def test_briefing_workflow_through_workspace(clean_graph):
    """Pattern для briefings (W14.4): action_kind в (brief_morning, brief_weekly),
    accumulate=False + immediate commit, разный TTL.
    """
    bm_idx = workspace.add(actor="baddle", action_kind="brief_morning",
                           text="Morning recap", urgency=0.6,
                           accumulate=False, ttl_seconds=24 * 3600,
                           extras={"sections_count": 5, "lang": "ru",
                                    "recovery_pct": 75})
    workspace.commit([bm_idx])

    bw_idx = workspace.add(actor="baddle", action_kind="brief_weekly",
                           text="Weekly digest", urgency=0.6,
                           accumulate=False, ttl_seconds=7 * 24 * 3600,
                           extras={"decisions_this_week": 12, "lang": "en"})
    workspace.commit([bw_idx])

    briefings = [n for n in _graph["nodes"]
                 if n.get("action_kind") in ("brief_morning", "brief_weekly")]
    assert len(briefings) == 2
    for b in briefings:
        assert b["scope"] == "graph"
        assert b["expires_at"] is None
        assert b["actor"] == "baddle"
        assert b["urgency"] == 0.6
    morning = next(b for b in briefings if b["action_kind"] == "brief_morning")
    assert morning["recovery_pct"] == 75
    assert morning["lang"] == "ru"


# ── cross-processing (W14.5) ───────────────────────────────────────────


def test_synthesize_similar_aggregates(clean_graph):
    """3 accumulate=True ноды одного kind → auto-synthesis в record_committed."""
    a = workspace.add(actor="baddle", action_kind="sync_seeking",
                      text="silence 5min", urgency=0.4, accumulate=True)
    b = workspace.add(actor="baddle", action_kind="sync_seeking",
                      text="silence 10min", urgency=0.5, accumulate=True)
    # Третий add триггерит cross-process автоматически
    c = workspace.add(actor="baddle", action_kind="sync_seeking",
                      text="silence 15min", urgency=0.6, accumulate=True)

    # Synthesized нода: action_kind='sync_seeking_synthesized', committed.
    synth = next((n for n in _graph["nodes"]
                   if n.get("action_kind") == "sync_seeking_synthesized"), None)
    assert synth is not None
    assert synth["scope"] == "graph"
    assert synth["expires_at"] is None
    assert synth["actor"] == "baddle"
    assert synth["synthesized_from"] == [a, b, c]
    assert synth["synthesis_count"] == 3
    assert synth["urgency"] == pytest.approx(0.7)  # max(0.6) + 0.1
    assert "silence 5min" in synth["text"]

    # Sources mark'нуты superseded_by, остаются в workspace
    for idx in (a, b, c):
        node = _graph["nodes"][idx]
        assert node["scope"] == "workspace"
        assert node["superseded_by"] == synth["id"]


def test_cross_process_below_threshold_no_synthesis(clean_graph):
    """2 accumulate=True ноды — недостаточно для trigger."""
    workspace.add(actor="baddle", action_kind="observation_suggestion",
                  text="A", urgency=0.5, accumulate=True)
    workspace.add(actor="baddle", action_kind="observation_suggestion",
                  text="B", urgency=0.5, accumulate=True)

    synth = [n for n in _graph["nodes"]
             if "_synthesized" in (n.get("action_kind") or "")]
    assert len(synth) == 0


def test_cross_process_skips_immediate(clean_graph):
    """5 accumulate=False ноды — НЕ trigger (immediate path не накапливается).

    record_committed (accumulate=False) — стандартный path для chat/alert/brief.
    Не должен случайно triggернуть synthesis.
    """
    for i in range(5):
        workspace.record_committed(
            actor="baddle", action_kind="alert",
            text=f"alert {i}", urgency=0.5, accumulate=False,
        )

    synth = [n for n in _graph["nodes"]
             if "_synthesized" in (n.get("action_kind") or "")]
    assert len(synth) == 0


def test_cross_process_no_recursion_on_synthesized(clean_graph):
    """После synthesis, новый round тех же sources не triggers повторно.

    superseded_by + synthesized_from filters в _maybe_cross_process
    исключают уже-обработанные ноды.
    """
    # Round 1 — 3 ноды → synthesis создаётся
    for i in range(3):
        workspace.add(actor="baddle", action_kind="sync_seeking",
                      text=f"r1-{i}", urgency=0.5, accumulate=True)
    synth_count_after_r1 = sum(
        1 for n in _graph["nodes"]
        if "_synthesized" in (n.get("action_kind") or ""))
    assert synth_count_after_r1 == 1

    # Round 2 — ещё 3 ноды (того же kind, но новые) → новый synthesis
    for i in range(3):
        workspace.add(actor="baddle", action_kind="sync_seeking",
                      text=f"r2-{i}", urgency=0.5, accumulate=True)
    synth_count_after_r2 = sum(
        1 for n in _graph["nodes"]
        if "_synthesized" in (n.get("action_kind") or ""))
    assert synth_count_after_r2 == 2  # ровно 2, не больше — нет recursion


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
