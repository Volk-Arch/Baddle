"""Unit tests для детекторов (Phase B Step 3).

Каждый детектор проверяется на:
  - возвращает None когда primary условия не выполнены
  - возвращает Signal с правильными type/urgency/dedup_key shape когда есть
  - urgency масштабируется по контексту (boundary cases)

Stub'ы вместо real cognitive_loop / hrv_manager / plans — детекторы pure
functions, их легко тестировать с минимальным окружением.
"""
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.detectors import (
    DetectorContext,
    detect_coherence_crit,
    detect_low_energy,
    detect_plan_reminder,
    detect_recurring_lag,
    detect_sync_seeking,
    detect_evening_retro,
    detect_morning_briefing,
    detect_observation_suggestions,
    detect_state_walk,
    detect_dmn_bridge,
    detect_dmn_deep_research,
    detect_dmn_converge,
    detect_night_cycle,
)
from src.signals import Signal


# ── Fixtures: stub context ─────────────────────────────────────────────────

@pytest.fixture
def stub_loop():
    """Минимальный CognitiveLoop stub с константами + helpers."""
    loop = SimpleNamespace(
        LOW_ENERGY_THRESHOLD=30,
        HEAVY_MODES={"horizon", "tournament", "decision"},
        PLAN_REMINDER_MINUTES=10,
        RECURRING_LAG_MIN=1,
        DEFAULT_WAKE_HOUR=7,
        EVENING_RETRO_HOUR_OFFSET=14,
        BRIEFING_INTERVAL=72000.0,
        STATE_WALK_INTERVAL=1200.0,
        SUGGESTIONS_CHECK_INTERVAL=86400.0,
        SUGGESTIONS_MAX_PER_DAY=2,
        # Tracking state
        _last_evening_retro_date=None,
        _last_briefing=0.0,
        _last_state_walk=0.0,
        _last_suggestions_check=0.0,
        _briefing_loaded_from_disk=True,   # skip disk lazy-load в тестах
    )
    # Helper methods
    loop._record_baddle_action = MagicMock()
    loop._generate_sync_seeking_message = MagicMock(
        return_value=("Привет, давно не писал.", "caring"))
    loop._build_morning_briefing_text = MagicMock(return_value="Briefing text")
    loop._build_morning_briefing_sections = MagicMock(return_value=[])
    loop._build_current_state_signature = MagicMock(return_value="state sig")
    loop._throttled = MagicMock(return_value=True)   # by default throttle passes
    loop._throttled_idle = MagicMock(return_value=True)
    return loop


@pytest.fixture
def ctx(stub_loop):
    """DetectorContext с минимальными stubs.

    rgk mock с пустым capacity (по умолчанию). Per-test setup capacity через
    `_ctx_with_capacity` ниже устанавливает `_capacity` атрибут, и `project`
    возвращает его."""
    rgk = SimpleNamespace(
        silence_press=0.0,
        _last_input_ts=None,
        _capacity={"zone": "green", "reasons": []},
        hrv_surprise=lambda: 0.0,
    )
    rgk.project = lambda dom: rgk._capacity if dom == "capacity" else {}
    return DetectorContext(
        now=1_000_000.0,
        rgk=rgk, loop=stub_loop,
    )


# ── detect_coherence_crit ──────────────────────────────────────────────────

def test_coherence_crit_emits_when_low(ctx):
    with patch("src.hrv_manager.get_manager") as mock_get:
        mgr = MagicMock()
        mgr.is_running = True
        mgr.get_baddle_state.return_value = {"coherence": 0.2}
        mock_get.return_value = mgr
        sig = detect_coherence_crit(ctx)
    assert sig is not None
    assert sig.type == "coherence_crit"
    assert sig.urgency >= 0.75
    assert sig.dedup_key == "coherence_crit"
    assert sig.content["severity"] == "warning"


def test_coherence_crit_critical_when_very_low(ctx):
    with patch("src.hrv_manager.get_manager") as mock_get:
        mgr = MagicMock()
        mgr.is_running = True
        mgr.get_baddle_state.return_value = {"coherence": 0.05}
        mock_get.return_value = mgr
        sig = detect_coherence_crit(ctx)
    assert sig is not None
    assert sig.urgency >= 0.9   # critical


def test_coherence_crit_none_when_above_threshold(ctx):
    with patch("src.hrv_manager.get_manager") as mock_get:
        mgr = MagicMock()
        mgr.is_running = True
        mgr.get_baddle_state.return_value = {"coherence": 0.5}
        mock_get.return_value = mgr
        assert detect_coherence_crit(ctx) is None


def test_coherence_crit_none_when_hrv_off(ctx):
    with patch("src.hrv_manager.get_manager") as mock_get:
        mgr = MagicMock()
        mgr.is_running = False
        mock_get.return_value = mgr
        assert detect_coherence_crit(ctx) is None


def test_coherence_crit_none_when_no_data(ctx):
    with patch("src.hrv_manager.get_manager") as mock_get:
        mgr = MagicMock()
        mgr.is_running = True
        mgr.get_baddle_state.return_value = {"coherence": None}
        mock_get.return_value = mgr
        assert detect_coherence_crit(ctx) is None


# ── detect_low_capacity_heavy (Phase C: capacity_zone gate) ───────────────

def _ctx_with_capacity(ctx, zone, reasons):
    """Helper: устанавливает capacity на rgk mock."""
    ctx.rgk._capacity = {"zone": zone, "reasons": reasons}


def test_low_capacity_emits_with_heavy_goal(ctx):
    _ctx_with_capacity(ctx, "red", ["serotonin_low", "cogload_high"])
    with patch("src.goals_store.list_goals") as mock_goals:
        mock_goals.return_value = [
            {"id": 42, "text": "Switch tech stack", "mode": "decision"}
        ]
        sig = detect_low_energy(ctx)
    assert sig is not None
    assert sig.type == "low_energy_heavy"   # backward-compat alert.type
    assert sig.dedup_key == "low_energy_heavy:42"
    assert sig.content["goal_id"] == 42
    assert sig.content["zone"] == "red"
    assert "serotonin_low" in sig.content["reason"]
    assert sig.urgency >= 0.6


def test_low_capacity_critical_when_all_fail(ctx):
    """4 fail'а (max possible — все 4 reason тегов) → critical 0.95."""
    _ctx_with_capacity(ctx, "red",
        ["hrv_coherence_low", "burnout_high", "serotonin_low", "cogload_high"])
    with patch("src.goals_store.list_goals") as mock_goals:
        mock_goals.return_value = [
            {"id": 1, "text": "decision", "mode": "decision"}
        ]
        sig = detect_low_energy(ctx)
    assert sig is not None
    assert sig.urgency >= 0.9


def test_low_capacity_none_when_zone_not_red(ctx):
    """zone=yellow или green — не fire, юзер не в красной зоне."""
    _ctx_with_capacity(ctx, "yellow", ["serotonin_low"])
    with patch("src.goals_store.list_goals") as mock_goals:
        mock_goals.return_value = [
            {"id": 1, "text": "decision", "mode": "decision"}
        ]
        assert detect_low_energy(ctx) is None


def test_low_capacity_none_without_heavy_goal(ctx):
    """red zone + только light-mode goal → не fire."""
    _ctx_with_capacity(ctx, "red", ["serotonin_low", "cogload_high"])
    with patch("src.goals_store.list_goals") as mock_goals:
        mock_goals.return_value = [
            {"id": 1, "text": "lightweight", "mode": "fan"}
        ]
        assert detect_low_energy(ctx) is None


# ── detect_plan_reminder ───────────────────────────────────────────────────

def test_plan_reminder_emits_within_window(ctx):
    plan = {"id": 7, "name": "Meeting",
             "category": "work",
             "planned_ts": ctx.now + 300,   # 5 min from now
             "for_date": "2026-04-25",
             "done": False, "skipped": False}
    with patch("src.plans.schedule_for_day", return_value=[plan]):
        sig = detect_plan_reminder(ctx)
    assert sig is not None
    assert sig.type == "plan_reminder"
    assert sig.content["minutes_before"] == 5
    assert sig.dedup_key == "plan_reminder:7:2026-04-25"
    # 5 min remaining → urgency around 0.85
    assert 0.7 < sig.urgency <= 1.0


def test_plan_reminder_critical_when_imminent(ctx):
    plan = {"id": 1, "name": "Now",
             "planned_ts": ctx.now + 60,   # 1 min
             "for_date": "2026-04-25",
             "done": False, "skipped": False}
    with patch("src.plans.schedule_for_day", return_value=[plan]):
        sig = detect_plan_reminder(ctx)
    assert sig is not None
    assert sig.urgency >= 0.95


def test_plan_reminder_none_outside_window(ctx):
    plan = {"id": 1, "name": "Far",
             "planned_ts": ctx.now + 7200,   # 2 hours
             "done": False, "skipped": False}
    with patch("src.plans.schedule_for_day", return_value=[plan]):
        assert detect_plan_reminder(ctx) is None


def test_plan_reminder_none_when_done(ctx):
    plan = {"id": 1, "name": "Done", "planned_ts": ctx.now + 300,
             "done": True, "skipped": False}
    with patch("src.plans.schedule_for_day", return_value=[plan]):
        assert detect_plan_reminder(ctx) is None


def test_plan_reminder_picks_closest(ctx):
    """Несколько планов в окне → возвращает ближайший."""
    plans = [
        {"id": 1, "name": "A", "planned_ts": ctx.now + 480,
         "done": False, "skipped": False},
        {"id": 2, "name": "B", "planned_ts": ctx.now + 120,   # closer
         "done": False, "skipped": False},
        {"id": 3, "name": "C", "planned_ts": ctx.now + 360,
         "done": False, "skipped": False},
    ]
    with patch("src.plans.schedule_for_day", return_value=plans):
        sig = detect_plan_reminder(ctx)
    assert sig is not None
    assert sig.content["plan_id"] == 2


# ── detect_recurring_lag ───────────────────────────────────────────────────

def test_recurring_lag_emits(ctx):
    lagging = [{"goal_id": "g1", "text": "Drink water",
                "lag": 2, "done_today": 1, "times_per_day": 3}]
    with patch("src.recurring.list_lagging", return_value=lagging):
        sig = detect_recurring_lag(ctx)
    assert sig is not None
    assert sig.type == "recurring_lag"
    assert sig.dedup_key == "recurring_lag:g1"
    assert sig.content["goal_id"] == "g1"
    assert sig.content["lag"] == 2
    # urgency = 0.3 + 0.15*min(5, 2) = 0.6
    assert sig.urgency == pytest.approx(0.6, abs=1e-6)


def test_recurring_lag_high_urgency_at_max_lag(ctx):
    lagging = [{"goal_id": "g1", "text": "Lag5",
                "lag": 5, "done_today": 0, "times_per_day": 5}]
    with patch("src.recurring.list_lagging", return_value=lagging):
        sig = detect_recurring_lag(ctx)
    # urgency = 0.3 + 0.15*5 = 1.05 → clamped 1.0
    assert sig.urgency == pytest.approx(1.0, abs=1e-6)


def test_recurring_lag_picks_highest(ctx):
    lagging = [
        {"goal_id": "low", "lag": 1, "text": "x", "done_today": 0, "times_per_day": 1},
        {"goal_id": "high", "lag": 4, "text": "y", "done_today": 0, "times_per_day": 4},
        {"goal_id": "mid", "lag": 2, "text": "z", "done_today": 0, "times_per_day": 2},
    ]
    with patch("src.recurring.list_lagging", return_value=lagging):
        sig = detect_recurring_lag(ctx)
    assert sig.content["goal_id"] == "high"


def test_recurring_lag_none_when_empty(ctx):
    with patch("src.recurring.list_lagging", return_value=[]):
        assert detect_recurring_lag(ctx) is None


# ── detect_sync_seeking ────────────────────────────────────────────────────

def test_sync_seeking_emits_when_silence_high_and_idle(ctx):
    ctx.rgk.silence_press = 0.6
    ctx.rgk._last_input_ts = ctx.now - 3 * 3600   # 3h idle
    with patch("random.random", return_value=0.5):  # not counterfactual
        sig = detect_sync_seeking(ctx)
    assert sig is not None
    assert sig.type == "sync_seeking"
    assert 0.3 <= sig.urgency <= 1.0
    assert sig.dedup_key == "sync_seeking"
    assert sig.content["tone"] == "caring"


def test_sync_seeking_none_when_silence_low(ctx):
    ctx.rgk.silence_press = 0.2   # below threshold
    ctx.rgk._last_input_ts = ctx.now - 3 * 3600
    assert detect_sync_seeking(ctx) is None


def test_sync_seeking_none_when_idle_short(ctx):
    ctx.rgk.silence_press = 0.6
    ctx.rgk._last_input_ts = ctx.now - 600   # only 10 min idle
    assert detect_sync_seeking(ctx) is None


def test_sync_seeking_counterfactual_skip_records_action(ctx):
    """10% случаев → None но action_memory записан."""
    ctx.rgk.silence_press = 0.6
    ctx.rgk._last_input_ts = ctx.now - 3 * 3600
    with patch("random.random", return_value=0.05):   # < 0.1 → counterfactual
        sig = detect_sync_seeking(ctx)
    assert sig is None
    ctx.loop._record_baddle_action.assert_called_once()
    args = ctx.loop._record_baddle_action.call_args
    assert args[0][0] == "sync_seeking_counterfactual"


def test_sync_seeking_urgency_scales_with_silence(ctx):
    """Higher silence → higher urgency."""
    ctx.rgk._last_input_ts = ctx.now - 3 * 3600

    ctx.rgk.silence_press = 0.4
    with patch("random.random", return_value=0.5):
        s_low = detect_sync_seeking(ctx)

    ctx.rgk.silence_press = 0.9
    with patch("random.random", return_value=0.5):
        s_high = detect_sync_seeking(ctx)

    assert s_low.urgency < s_high.urgency


# ── detect_evening_retro ───────────────────────────────────────────────────

def test_evening_retro_emits_after_retro_hour(ctx, monkeypatch):
    import datetime as _dt
    fake_dt = _dt.datetime(2026, 4, 25, 22, 0, 0)   # 22:00 — past 7+14=21
    fake_today = _dt.date(2026, 4, 25)

    class _FakeDT:
        @classmethod
        def now(cls):
            return fake_dt
    class _FakeDate:
        @classmethod
        def today(cls):
            return fake_today
    monkeypatch.setattr(_dt, "datetime", _FakeDT)
    monkeypatch.setattr(_dt, "date", _FakeDate)

    with patch("src.user_profile.load_profile",
                return_value={"context": {"wake_hour": 7}}), \
         patch("src.plans.schedule_for_day", return_value=[
             {"id": 1, "name": "Task A", "done": False, "skipped": False},
         ]):
        sig = detect_evening_retro(ctx)
    assert sig is not None
    assert sig.type == "evening_retro"
    assert sig.dedup_key == "evening_retro:2026-04-25"
    assert sig.content["unfinished"] != []
    # State updated
    assert ctx.loop._last_evening_retro_date == "2026-04-25"


def test_evening_retro_none_before_hour(ctx, monkeypatch):
    import datetime as _dt
    fake_dt = _dt.datetime(2026, 4, 25, 15, 0, 0)   # 15:00 — too early

    class _FakeDT:
        @classmethod
        def now(cls):
            return fake_dt
    monkeypatch.setattr(_dt, "datetime", _FakeDT)

    with patch("src.user_profile.load_profile",
                return_value={"context": {"wake_hour": 7}}):
        assert detect_evening_retro(ctx) is None


def test_evening_retro_none_when_already_today(ctx, monkeypatch):
    import datetime as _dt
    fake_today = _dt.date(2026, 4, 25)
    class _FakeDate:
        @classmethod
        def today(cls):
            return fake_today
    monkeypatch.setattr(_dt, "date", _FakeDate)

    ctx.loop._last_evening_retro_date = "2026-04-25"
    assert detect_evening_retro(ctx) is None


# ── detect_morning_briefing ────────────────────────────────────────────────

def test_morning_briefing_emits_after_wake_hour_and_interval(ctx, monkeypatch):
    import datetime as _dt
    fake_dt = _dt.datetime(2026, 4, 25, 8, 0, 0)   # 8:00 — past wake_hour=7

    class _FakeDT:
        @classmethod
        def now(cls):
            return fake_dt
    monkeypatch.setattr(_dt, "datetime", _FakeDT)

    ctx.loop._last_briefing = 0.0   # never run
    with patch("src.user_profile.load_profile",
                return_value={"context": {"wake_hour": 7}}), \
         patch("src.assistant._load_state", return_value={}), \
         patch("src.assistant._save_state"):
        sig = detect_morning_briefing(ctx)
    assert sig is not None
    assert sig.type == "morning_briefing"
    assert sig.urgency == 0.8
    assert sig.dedup_key == "morning_briefing:2026-04-25"
    assert ctx.loop._last_briefing == ctx.now


def test_morning_briefing_none_before_wake_hour(ctx, monkeypatch):
    import datetime as _dt
    fake_dt = _dt.datetime(2026, 4, 25, 5, 0, 0)   # 5:00 — too early

    class _FakeDT:
        @classmethod
        def now(cls):
            return fake_dt
    monkeypatch.setattr(_dt, "datetime", _FakeDT)
    ctx.loop._last_briefing = 0.0
    with patch("src.user_profile.load_profile",
                return_value={"context": {"wake_hour": 7}}), \
         patch("src.assistant._load_state", return_value={}):
        assert detect_morning_briefing(ctx) is None


def test_morning_briefing_none_within_interval(ctx):
    ctx.loop._last_briefing = ctx.now - 3600   # 1h ago, less than 20h
    assert detect_morning_briefing(ctx) is None


# ── detect_observation_suggestions ─────────────────────────────────────────

def test_observation_returns_list_of_signals(ctx):
    items = [
        {"trigger": {"type": "pattern_x"}, "strength": 0.8},
        {"trigger": {"type": "pattern_y"}, "strength": 0.4},
    ]
    cards = [
        {"title": "Card A", "draft": {"text": "card a body", "kind": "habit"}},
        {"title": "Card B", "draft": {"text": "card b body", "kind": "habit"}},
    ]
    with patch("src.suggestions.collect_suggestions", return_value=items), \
         patch("src.suggestions.make_suggestion_card", side_effect=cards):
        result = detect_observation_suggestions(ctx)
    sigs = list(result)
    assert len(sigs) == 2
    assert all(s.type == "observation_suggestion" for s in sigs)
    # Distinct dedup_keys
    assert sigs[0].dedup_key != sigs[1].dedup_key
    # urgency scales with strength
    assert sigs[0].urgency > sigs[1].urgency


def test_observation_caps_at_max_per_day(ctx):
    items = [{"trigger": {"type": f"p{i}"}, "strength": 0.5} for i in range(5)]
    card = {"title": "Card", "draft": {"text": "txt", "kind": "habit"}}
    with patch("src.suggestions.collect_suggestions", return_value=items), \
         patch("src.suggestions.make_suggestion_card", return_value=card):
        result = list(detect_observation_suggestions(ctx))
    assert len(result) <= ctx.loop.SUGGESTIONS_MAX_PER_DAY


def test_observation_skipped_when_user_active(ctx):
    """user_active < 10 min → silent skip без update _last_suggestions_check."""
    ctx.rgk._last_input_ts = ctx.now - 60   # 1 min ago
    with patch("src.suggestions.collect_suggestions") as mock_collect:
        result = list(detect_observation_suggestions(ctx))
    assert result == []
    mock_collect.assert_not_called()   # not even called — full skip


def test_observation_throttled_returns_empty(ctx):
    ctx.loop._throttled.return_value = False   # throttle blocks
    with patch("src.suggestions.collect_suggestions") as mock_collect:
        result = list(detect_observation_suggestions(ctx))
    assert result == []
    mock_collect.assert_not_called()


def test_observation_skips_empty_drafts(ctx):
    """Cards с пустым draft.text или title не пушатся."""
    items = [{"trigger": {"type": "p1"}, "strength": 0.5}]
    bad_card = {"title": "", "draft": {"text": "", "kind": "habit"}}
    with patch("src.suggestions.collect_suggestions", return_value=items), \
         patch("src.suggestions.make_suggestion_card", return_value=bad_card):
        result = list(detect_observation_suggestions(ctx))
    assert result == []


# ── detect_state_walk ──────────────────────────────────────────────────────

def test_state_walk_emits_with_match(ctx):
    from datetime import datetime, timezone, timedelta
    old_ts = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    similar = [
        {"hash": "abc123", "action": "synthesize",
         "reason": "old reason", "timestamp": old_ts, "score": 0.7}
    ]
    with patch("src.state_graph.get_state_graph") as mock_get_sg, \
         patch("src.api_backend.api_get_embedding", return_value=[0.1] * 768):
        sg = MagicMock()
        sg.count.return_value = 50
        sg.tail.return_value = []
        sg.query_similar.return_value = similar
        mock_get_sg.return_value = sg
        sig = detect_state_walk(ctx)
    assert sig is not None
    assert sig.type == "state_walk"
    assert "synthesize" in sig.content["match"]["action"]


def test_state_walk_none_when_state_graph_small(ctx):
    with patch("src.state_graph.get_state_graph") as mock_get_sg:
        sg = MagicMock()
        sg.count.return_value = 5   # < 10
        mock_get_sg.return_value = sg
        assert detect_state_walk(ctx) is None


def test_state_walk_none_when_throttled(ctx):
    ctx.loop._throttled_idle.return_value = False
    with patch("src.state_graph.get_state_graph") as mock_get_sg:
        sg = MagicMock()
        sg.count.return_value = 50
        mock_get_sg.return_value = sg
        # No api call should happen if throttled
        assert detect_state_walk(ctx) is None


def test_state_walk_filters_recent_matches(ctx):
    """Matches younger than 1h should be skipped."""
    from datetime import datetime, timezone, timedelta
    recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    similar = [
        {"hash": "x", "action": "walk", "reason": "r",
         "timestamp": recent_ts, "score": 0.9},
    ]
    with patch("src.state_graph.get_state_graph") as mock_get_sg, \
         patch("src.api_backend.api_get_embedding", return_value=[0.1] * 768):
        sg = MagicMock()
        sg.count.return_value = 50
        sg.tail.return_value = []
        sg.query_similar.return_value = similar
        mock_get_sg.return_value = sg
        assert detect_state_walk(ctx) is None


# ── Heavy-work detectors (delegate to loop._run_*) ────────────────────────

@pytest.mark.parametrize("detector,method_name", [
    (detect_dmn_bridge, "_run_dmn_continuous"),
    (detect_dmn_deep_research, "_run_dmn_deep_research"),
    (detect_dmn_converge, "_run_dmn_converge"),
    (detect_night_cycle, "_run_night_cycle"),
])
def test_heavy_detector_returns_none_when_method_missing(ctx, detector, method_name):
    """Step 3c: методы _run_* пока не существуют → детектор возвращает None
    gracefully (Step 4 экстрагирует методы из _check_*)."""
    # stub_loop фикстура не имеет _run_* методов
    assert not hasattr(ctx.loop, method_name)
    assert detector(ctx) is None


@pytest.mark.parametrize("detector,method_name", [
    (detect_dmn_bridge, "_run_dmn_continuous"),
    (detect_dmn_deep_research, "_run_dmn_deep_research"),
    (detect_dmn_converge, "_run_dmn_converge"),
    (detect_night_cycle, "_run_night_cycle"),
])
def test_heavy_detector_delegates_to_loop_method(ctx, detector, method_name):
    expected = Signal(type=method_name.replace("_run_", "").replace("_", "_"),
                       urgency=0.7, content={}, expires_at=ctx.now + 100)
    setattr(ctx.loop, method_name, MagicMock(return_value=expected))
    result = detector(ctx)
    assert result is expected
    getattr(ctx.loop, method_name).assert_called_once_with(ctx)


@pytest.mark.parametrize("detector,method_name", [
    (detect_dmn_bridge, "_run_dmn_continuous"),
    (detect_dmn_deep_research, "_run_dmn_deep_research"),
    (detect_dmn_converge, "_run_dmn_converge"),
    (detect_night_cycle, "_run_night_cycle"),
])
def test_heavy_detector_swallows_exception(ctx, detector, method_name):
    """Если loop._run_* падает — детектор возвращает None, не пробрасывает."""
    setattr(ctx.loop, method_name, MagicMock(side_effect=RuntimeError("boom")))
    assert detector(ctx) is None


def test_all_13_detectors_registered():
    """Sanity check: DETECTORS has all 13 (4 simple + 5 medium + 4 heavy)."""
    from src.detectors import DETECTORS
    assert len(DETECTORS) == 13


# ── Common contract ───────────────────────────────────────────────────────

def test_all_detectors_handle_exceptions_gracefully(ctx):
    """Детектор не должен пропускать exception наружу — лучше None / [].

    Симулируем падающие внешние вызовы и проверяем что результат возвращается
    в одной из валидных форм (None / Signal / list[Signal]).
    """
    from src.signals import Signal as _Sig
    from src.detectors import DETECTORS
    with patch("src.hrv_manager.get_manager", side_effect=RuntimeError("boom")), \
         patch("src.assistant._get_context", side_effect=RuntimeError("boom")), \
         patch("src.assistant._load_state", side_effect=RuntimeError("boom")), \
         patch("src.plans.schedule_for_day", side_effect=RuntimeError("boom")), \
         patch("src.recurring.list_lagging", side_effect=RuntimeError("boom")), \
         patch("src.user_profile.load_profile", side_effect=RuntimeError("boom")), \
         patch("src.suggestions.collect_suggestions", side_effect=RuntimeError("boom")), \
         patch("src.state_graph.get_state_graph", side_effect=RuntimeError("boom")), \
         patch("src.api_backend.api_get_embedding", side_effect=RuntimeError("boom")):
        for det in DETECTORS:
            try:
                result = det(ctx)
            except Exception as e:
                pytest.fail(f"{det.__name__} raised {type(e).__name__}: {e}")
            # Valid: None, Signal, или iterable of Signal
            if result is None:
                continue
            if isinstance(result, _Sig):
                continue
            # Должен быть iterable Signal'ов
            items = list(result)
            assert all(isinstance(s, _Sig) for s in items), \
                f"{det.__name__} returned non-Signal items: {items!r}"
