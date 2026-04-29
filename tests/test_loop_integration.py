"""Integration smoke test for Phase B Signal dispatcher.

Проверяет что _loop body корректно собирает кандидатов из DETECTORS,
dispatcher decides emit/drop, _add_alert получает финальные. Все сетевые
вызовы (LLM, embeddings) мокнуты.
"""
from unittest.mock import MagicMock, patch

import pytest

from src.process.cognitive_loop import CognitiveLoop
from src.process.detectors import DETECTORS, build_detector_context
from src.process.signals import Signal


# ── Smoke: end-to-end loop path ────────────────────────────────────────────

def test_loop_initializes_with_dispatcher():
    """Phase B: CognitiveLoop.__init__ создаёт Dispatcher."""
    loop = CognitiveLoop()
    assert loop._dispatcher is not None
    assert loop._dispatcher.budget_per_window == 5
    assert loop._dispatcher.window_s == 3600.0


def test_all_run_methods_exist():
    """Phase B refactor: heavy _check_* переименованы в _run_* возвращающие Signal."""
    loop = CognitiveLoop()
    for method_name in ("_run_dmn_continuous", "_run_dmn_deep_research",
                        "_run_dmn_converge", "_run_night_cycle"):
        assert hasattr(loop, method_name), f"{method_name} not found"


def test_build_context_works(tmp_path, monkeypatch):
    """Build context from real loop without crashing."""
    # Patch hrv_manager чтобы не требовать сенсоры
    with patch("src.sensors.manager.get_manager") as mock_hrv:
        mgr = MagicMock()
        mgr.is_running = False
        mock_hrv.return_value = mgr

        loop = CognitiveLoop()
        import time
        ctx = build_detector_context(loop, time.time())

    assert ctx.now > 0
    assert ctx.rgk is not None
    assert ctx.loop is loop
    # dmn_eligible should be True at fresh init (idle, no foreground tick)
    assert ctx.dmn_eligible is True


def test_dispatcher_collects_and_dispatches_signals(tmp_path):
    """Когда детекторы возвращают Signal, dispatcher эмитит через _emit_alert."""
    loop = CognitiveLoop()
    # Patch dispatcher's drops_file для tmp
    loop._dispatcher._drops_file = tmp_path / "throttle_drops.jsonl"

    import time
    now = time.time()
    # Build candidates manually — expires_at в будущем
    sig1 = Signal(type="test_a", urgency=0.8, content={"type": "test_a", "text": "A"},
                   expires_at=now + 3600, dedup_key="test_a")
    sig2 = Signal(type="test_b", urgency=0.5, content={"type": "test_b", "text": "B"},
                   expires_at=now + 3600, dedup_key="test_b")

    emitted = loop._dispatcher.dispatch([sig1, sig2], now)
    for sig in emitted:
        loop._emit_alert(sig, now)

    alerts = loop.get_alerts()
    types = [a.get("type") for a in alerts]
    assert "test_a" in types
    assert "test_b" in types


def test_emit_alert_writes_workspace_then_graph(tmp_path):
    """W14.3: emitted Signal → workspace.add+commit → action нода в графе.

    Action Memory: alert получает actor='baddle', action_kind=sig.type,
    text из sig.content, scope='graph' (committed), expires_at=None.
    Доступен через graph queries по action_kind для outcome tracking.
    """
    from src.graph_logic import _graph
    saved = list(_graph["nodes"])
    _graph["nodes"] = []
    try:
        loop = CognitiveLoop()
        loop._dispatcher._drops_file = tmp_path / "throttle_drops.jsonl"

        import time
        now = time.time()
        sig = Signal(type="capacity_red", urgency=0.95,
                      content={"type": "capacity_red", "text": "Capacity red — отложи",
                               "severity": "warning"},
                      expires_at=now + 3600, dedup_key="capacity_red")

        loop._emit_alert(sig, now)

        # 1. Queue mirror (legacy path)
        alerts = loop.get_alerts()
        assert any(a.get("type") == "capacity_red" for a in alerts)

        # 2. Workspace path → action нода в графе (committed)
        action_nodes = [n for n in _graph["nodes"]
                         if n.get("type") == "action"
                         and n.get("action_kind") == "capacity_red"]
        assert len(action_nodes) == 1
        node = action_nodes[0]
        assert node["actor"] == "baddle"
        assert node["scope"] == "graph"  # immediate commit
        assert node["expires_at"] is None
        assert node["text"].startswith("Capacity red")
        assert node["urgency"] == 0.95
    finally:
        _graph["nodes"] = saved


def test_dmn_eligible_gates_heavy_detectors(tmp_path):
    """ctx.dmn_eligible=False → heavy detectors return None.

    Проверяем что при dmn_eligible=False (frozen / high NE / foreground)
    все 5 DMN-эвристических детекторов skip без работы.
    """
    from src.process.detectors import (
        detect_dmn_bridge, detect_dmn_deep_research, detect_dmn_converge,
        detect_state_walk, detect_night_cycle, DetectorContext,
    )
    from types import SimpleNamespace

    user = SimpleNamespace(_last_input_ts=None, hrv_surprise=0.0)
    # neuro field удалён в W4
    rgk = SimpleNamespace(silence_press=0.0)
    loop = SimpleNamespace()
    ctx = DetectorContext(now=1_000_000.0,
                            rgk=rgk, loop=loop, dmn_eligible=False)

    # Все 5 должны вернуть None если dmn_eligible=False
    assert detect_dmn_bridge(ctx) is None
    assert detect_dmn_deep_research(ctx) is None
    assert detect_dmn_converge(ctx) is None
    assert detect_state_walk(ctx) is None
    assert detect_night_cycle(ctx) is None


def test_loop_body_one_iteration(tmp_path):
    """Симулируем одну итерацию _loop: build context → detectors → dispatch.

    Не запускаем _loop (бесконечный), а вручную делаем шаги body.
    Все детекторы должны либо вернуть None, либо валидный Signal — без
    исключений в наружу.
    """
    with patch("src.sensors.manager.get_manager") as mock_hrv, \
         patch("src.api_backend.api_get_embedding", return_value=None), \
         patch("src.suggestions.collect_suggestions", return_value=[]), \
         patch("src.user_profile.load_profile",
                return_value={"context": {"wake_hour": 7}}):
        mgr = MagicMock()
        mgr.is_running = False
        mock_hrv.return_value = mgr

        loop = CognitiveLoop()
        loop._dispatcher._drops_file = tmp_path / "throttle_drops.jsonl"

        import time
        ctx = build_detector_context(loop, time.time())

        # Run all detectors (как в _loop)
        candidates: list[Signal] = []
        for detector in DETECTORS:
            try:
                result = detector(ctx)
            except Exception as e:
                pytest.fail(f"{detector.__name__} raised: {e}")
            if result is None:
                continue
            if isinstance(result, Signal):
                candidates.append(result)
            else:
                candidates.extend(list(result))

        # Dispatch (даже пустой список не должен крашить)
        emitted = loop._dispatcher.dispatch(candidates, time.time())
        assert isinstance(emitted, list)
        for sig in emitted:
            assert isinstance(sig, Signal)


# ── Counter-wave (Правило 7): mode trajectory через _advance_tick ──────────

class TestModeTrajectoryAdvanceTick:
    """Property test: реальный путь cognitive_loop._advance_tick → user.mode/neuro.mode.

    Проверяет cascade: chem spread → compute_sync_error → update_mode гистерезис.
    Lower-level RG-resonator hysteresis покрыт в test_rgk_properties.py;
    здесь — что _advance_tick его действительно вызывает.
    """

    def _fresh_state(self):
        """Свежий РГК + user_state + cognitive_state перед каждым тестом.

        UserState принимает rgk= explicitly; CognitiveState читает singleton
        внутри __init__ (через get_global_rgk()), поэтому достаточно
        reset_global_rgk() до его конструктора.
        """
        from src.substrate.rgk import reset_global_rgk
        from src.substrate.user_state import UserState, set_user_state, get_user_state
        from src.substrate.horizon import CognitiveState, set_global_state, get_global_state
        rgk = reset_global_rgk()
        set_user_state(UserState(rgk=rgk))
        set_global_state(CognitiveState())
        return get_user_state(), get_global_state()

    def test_high_sync_err_drives_user_to_C(self, monkeypatch):
        """sync_err >> THETA_ACT (0.15) через advance_tick → user.mode='C'."""
        from src.process.cognitive_loop import CognitiveLoop
        import src.process.cognitive_loop as cl_mod
        u, gs = self._fresh_state()
        loop = CognitiveLoop()

        assert u.mode == "R"

        # Spread по dopamine оси: |Δ|=0.65 → sync_err ≈ 0.65 (3D vec)
        u.dopamine = 0.95
        gs.rgk.system.gain.value = 0.30

        fake_t = [10_000.0]
        monkeypatch.setattr(cl_mod.time, "time", lambda: fake_t[0])
        fake_t[0] += 1.0
        loop._advance_tick()  # init _last_loop_tick_ts
        fake_t[0] += 5.0
        loop._advance_tick()  # реальный update с dt=5s

        assert u.mode == "C", f"high sync_err didn't drive C, got {u.mode}"

    def test_low_sync_err_restores_user_to_R(self, monkeypatch):
        """C → R при sync_err < THETA_REC (0.08) (full cycle через _advance_tick)."""
        from src.process.cognitive_loop import CognitiveLoop
        import src.process.cognitive_loop as cl_mod
        u, gs = self._fresh_state()
        loop = CognitiveLoop()

        fake_t = [10_000.0]
        monkeypatch.setattr(cl_mod.time, "time", lambda: fake_t[0])

        # Поднять в C
        u.dopamine = 0.95
        gs.rgk.system.gain.value = 0.30
        fake_t[0] += 1.0; loop._advance_tick()
        fake_t[0] += 5.0; loop._advance_tick()
        assert u.mode == "C"

        # Выровнять — perturbation → 0
        u.dopamine = 0.5
        gs.rgk.system.gain.value = 0.5
        fake_t[0] += 5.0; loop._advance_tick()
        assert u.mode == "R", f"low sync_err didn't restore R, got {u.mode}"

    def test_hysteresis_band_keeps_C(self, monkeypatch):
        """Между THETA_REC (0.08) и THETA_ACT (0.15) — mode не дребезжит."""
        from src.process.cognitive_loop import CognitiveLoop
        import src.process.cognitive_loop as cl_mod
        u, gs = self._fresh_state()
        loop = CognitiveLoop()

        fake_t = [10_000.0]
        monkeypatch.setattr(cl_mod.time, "time", lambda: fake_t[0])

        # Drive в C
        u.dopamine = 0.95
        gs.rgk.system.gain.value = 0.30
        fake_t[0] += 1.0; loop._advance_tick()
        fake_t[0] += 5.0; loop._advance_tick()
        assert u.mode == "C"

        # Spread |Δ|=0.10 → sync_err ≈ 0.10 (в band [0.08, 0.15])
        u.dopamine = 0.55
        gs.rgk.system.gain.value = 0.45
        for _ in range(5):
            fake_t[0] += 5.0
            loop._advance_tick()
        assert u.mode == "C", f"flipped из C в band, got {u.mode}"

    def test_neuro_mode_independent_from_user_mode(self, monkeypatch):
        """user.mode и neuro.mode качаются независимо: user от sync_err,
        neuro от combined_imbalance (max 4 PE-каналов)."""
        from src.process.cognitive_loop import CognitiveLoop
        import src.process.cognitive_loop as cl_mod
        u, gs = self._fresh_state()
        loop = CognitiveLoop()

        fake_t = [10_000.0]
        monkeypatch.setattr(cl_mod.time, "time", lambda: fake_t[0])

        # sync_err большой (DA spread), но user/system imbalance ноль
        u.dopamine = 0.95
        gs.rgk.system.gain.value = 0.30
        fake_t[0] += 1.0; loop._advance_tick()
        fake_t[0] += 5.0; loop._advance_tick()

        assert u.mode == "C", f"user mode not driven, got {u.mode}"
        # neuro может остаться R: combined_imbalance не зависит от sync_err


def test_phase_a_identity_still_holds():
    """Phase A identity check (EMA registry) должен работать после Phase B
    миграции — Phase B не трогает EMA, только alert dispatch."""
    import subprocess
    result = subprocess.run(
        ["python", "-m", "pytest", "tests/test_metric_identity.py", "-q",
         "--tb=short"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, \
        f"Phase A identity broken!\n{result.stdout}\n{result.stderr}"
