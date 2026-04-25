"""Integration smoke test for Phase B Signal dispatcher.

Проверяет что _loop body корректно собирает кандидатов из DETECTORS,
dispatcher decides emit/drop, _add_alert получает финальные. Все сетевые
вызовы (LLM, embeddings) мокнуты.
"""
from unittest.mock import MagicMock, patch

import pytest

from src.cognitive_loop import CognitiveLoop
from src.detectors import DETECTORS, build_detector_context
from src.signals import Signal


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
    with patch("src.hrv_manager.get_manager") as mock_hrv:
        mgr = MagicMock()
        mgr.is_running = False
        mock_hrv.return_value = mgr

        loop = CognitiveLoop()
        import time
        ctx = build_detector_context(loop, time.time())

    assert ctx.now > 0
    assert ctx.user is not None
    assert ctx.neuro is not None
    assert ctx.freeze is not None
    assert ctx.loop is loop
    # dmn_eligible should be True at fresh init (idle, no foreground tick)
    assert ctx.dmn_eligible is True


def test_dispatcher_collects_and_dispatches_signals(tmp_path):
    """Когда детекторы возвращают Signal, dispatcher эмитит через _add_alert."""
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
        loop._add_alert(sig.content)

    alerts = loop.get_alerts()
    types = [a.get("type") for a in alerts]
    assert "test_a" in types
    assert "test_b" in types


def test_dmn_eligible_gates_heavy_detectors(tmp_path):
    """ctx.dmn_eligible=False → heavy detectors return None.

    Проверяем что при dmn_eligible=False (frozen / high NE / foreground)
    все 5 DMN-эвристических детекторов skip без работы.
    """
    from src.detectors import (
        detect_dmn_bridge, detect_dmn_deep_research, detect_dmn_converge,
        detect_state_walk, detect_night_cycle, DetectorContext,
    )
    from types import SimpleNamespace

    user = SimpleNamespace(_last_input_ts=None, hrv_surprise=0.0)
    neuro = SimpleNamespace()
    freeze = SimpleNamespace(silence_pressure=0.0)
    loop = SimpleNamespace()
    ctx = DetectorContext(now=1_000_000.0, user=user, neuro=neuro,
                            freeze=freeze, loop=loop, dmn_eligible=False)

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
    with patch("src.hrv_manager.get_manager") as mock_hrv, \
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
