"""Identity check: фиксированный event-sequence через UserState / Neurochem /
ProtectiveFreeze должен давать тот же snapshot ДО и ПОСЛЕ миграции на
MetricRegistry (Фаза A из planning/simplification-plan.md).

EXPECTED захвачен 2026-04-24 через `python scripts/capture_metric_baseline.py`
на коммите до миграции. Если тест падает после миграции — семантика
изменилась, миграция неверная.

Пересоздать EXPECTED: запустить capture-script, вставить сюда. Но это
валидно ТОЛЬКО если сознательно меняете формулу — иначе тест ловит регрессию.
"""
import pytest

from src.user_state import UserState
from src.neurochem import Neurochem, ProtectiveFreeze


# ── Expected snapshot (captured 2026-04-24, pre-migration) ─────────────────

EXPECTED_USER_STATE = {
    "dopamine": 0.566345,
    "serotonin": 0.540951,
    "norepinephrine": 0.418098,
    "valence": 0.103027,
    "burnout": 0.19,
    "agency": 0.505,
    "expectation": 0.517369,
    "expectation_by_tod": {
        "morning": 0.5,
        "day": 0.517369,
        "evening": 0.5,
        "night": 0.5,
    },
    "expectation_vec": [0.529848, 0.518119, 0.463763],
    "hrv_baseline_by_tod": {
        "morning": None,
        "day": 0.6,
        "evening": None,
        "night": None,
    },
    "vector": [0.566345, 0.540951, 0.418098],
    "surprise": 0.03628,
    "imbalance": 0.062759,
}

EXPECTED_NEUROCHEM = {
    "dopamine": 0.424359,
    "serotonin": 0.598492,
    "norepinephrine": 0.700003,
    "expectation_vec": [0.495359, 0.508601, 0.517466],
    "gamma": 2.84317,
    "recent_rpe": -0.15,
    "self_imbalance": 0.215502,
    "vector": [0.424359, 0.598492, 0.700003],
}

EXPECTED_FREEZE = {
    "conflict_accumulator": 0.004225,
    "silence_pressure": 0.001984,
    "imbalance_pressure": 0.004138,
    "sync_error_ema_fast": 0.081833,
    "sync_error_ema_slow": 0.001333,
    "display_burnout": 0.004225,
    "active": False,
}

TOL = 1e-5  # Достаточно с учётом float32 в VectorEMA


# ── Fixed event sequence ───────────────────────────────────────────────────

@pytest.fixture
def states(monkeypatch):
    """UserState/Neurochem/ProtectiveFreeze после identical event sequence.

    TOD запинен в 'day' через monkeypatch — без этого tests на разных часах
    падают (`_current_tod` использует datetime.now()).
    """
    monkeypatch.setattr(UserState, "_current_tod",
                         staticmethod(lambda: "day"))

    us = UserState()
    nc = Neurochem()
    pf = ProtectiveFreeze()

    # UserState events
    for _ in range(5):
        us.update_from_hrv(coherence=0.6, stress=0.3, rmssd=40.0, activity=0.2)
    for _ in range(10):
        us.update_from_engagement(signal=0.65)
    for _ in range(3):
        us.update_from_feedback("accepted")
    for _ in range(2):
        us.update_from_feedback("rejected")
    for s in (0.4, 0.2, -0.1, 0.5, 0.3):
        us.update_from_chat_sentiment(s)
    us.update_from_plan_completion(completed=3, planned=5)
    us.update_from_energy(decisions_today=20)
    for _ in range(10):
        us.tick_expectation()

    # Neurochem events
    for d, wc, w in [
        (0.4,  [0.1, -0.05, 0.2],   [0.3,  0.4,  0.3]),
        (0.3,  [0.05, -0.02, 0.1],  [0.35, 0.3,  0.35]),
        (0.5,  [0.0, 0.0, 0.1],     [0.25, 0.45, 0.3]),
        (0.2,  [-0.05, 0.1, -0.02], [0.4,  0.3,  0.3]),
        (0.45, [0.1, 0.05, -0.05],  [0.3,  0.3,  0.4]),
    ]:
        nc.update(d=d, w_change=wc, weights=w)
    for _ in range(3):
        nc.tick_expectation()
    nc.record_outcome(prior=0.5, posterior=0.7)
    nc.record_outcome(prior=0.6, posterior=0.55)

    # ProtectiveFreeze events
    pf.update(d=0.7, serotonin=0.4)
    pf.update(d=0.65, serotonin=0.45)
    for _ in range(20):
        pf.feed_tick(dt=60.0, sync_err=0.5, imbalance=0.3)

    return us, nc, pf


# ── UserState identity ─────────────────────────────────────────────────────

def test_user_state_scalar_metrics(states):
    us, _, _ = states
    assert us.dopamine == pytest.approx(EXPECTED_USER_STATE["dopamine"], abs=TOL)
    assert us.serotonin == pytest.approx(EXPECTED_USER_STATE["serotonin"], abs=TOL)
    assert us.norepinephrine == pytest.approx(
        EXPECTED_USER_STATE["norepinephrine"], abs=TOL)
    assert us.valence == pytest.approx(EXPECTED_USER_STATE["valence"], abs=TOL)
    assert us.burnout == pytest.approx(EXPECTED_USER_STATE["burnout"], abs=TOL)
    assert us.agency == pytest.approx(EXPECTED_USER_STATE["agency"], abs=TOL)


def test_user_state_predictive_layer(states):
    us, _, _ = states
    assert us.expectation == pytest.approx(
        EXPECTED_USER_STATE["expectation"], abs=TOL)

    tod_actual = us.expectation_by_tod
    tod_expected = EXPECTED_USER_STATE["expectation_by_tod"]
    for key in ("morning", "day", "evening", "night"):
        assert tod_actual[key] == pytest.approx(tod_expected[key], abs=TOL), \
            f"expectation_by_tod[{key}] mismatch"

    assert us.expectation_vec.tolist() == pytest.approx(
        EXPECTED_USER_STATE["expectation_vec"], abs=TOL)

    hrv_actual = us.hrv_baseline_by_tod
    hrv_expected = EXPECTED_USER_STATE["hrv_baseline_by_tod"]
    for key in ("morning", "day", "evening", "night"):
        exp = hrv_expected[key]
        act = hrv_actual[key]
        if exp is None:
            assert act is None, f"hrv_baseline_by_tod[{key}] expected None, got {act}"
        else:
            assert act == pytest.approx(exp, abs=TOL)


def test_user_state_vector_and_derived(states):
    us, _, _ = states
    assert us.vector().tolist() == pytest.approx(
        EXPECTED_USER_STATE["vector"], abs=TOL)
    assert us.surprise == pytest.approx(
        EXPECTED_USER_STATE["surprise"], abs=TOL)
    assert us.imbalance == pytest.approx(
        EXPECTED_USER_STATE["imbalance"], abs=TOL)


# ── Neurochem identity ─────────────────────────────────────────────────────

def test_neurochem_scalars(states):
    _, nc, _ = states
    assert nc.dopamine == pytest.approx(EXPECTED_NEUROCHEM["dopamine"], abs=TOL)
    assert nc.serotonin == pytest.approx(EXPECTED_NEUROCHEM["serotonin"], abs=TOL)
    assert nc.norepinephrine == pytest.approx(
        EXPECTED_NEUROCHEM["norepinephrine"], abs=TOL)


def test_neurochem_predictive_and_derived(states):
    _, nc, _ = states
    assert nc.expectation_vec.tolist() == pytest.approx(
        EXPECTED_NEUROCHEM["expectation_vec"], abs=TOL)
    assert nc.gamma == pytest.approx(EXPECTED_NEUROCHEM["gamma"], abs=TOL)
    assert nc.recent_rpe == pytest.approx(
        EXPECTED_NEUROCHEM["recent_rpe"], abs=TOL)
    assert nc.self_imbalance == pytest.approx(
        EXPECTED_NEUROCHEM["self_imbalance"], abs=TOL)
    assert nc.vector().tolist() == pytest.approx(
        EXPECTED_NEUROCHEM["vector"], abs=TOL)


# ── ProtectiveFreeze identity ──────────────────────────────────────────────

def test_freeze_feeders(states):
    _, _, pf = states
    assert pf.conflict_accumulator == pytest.approx(
        EXPECTED_FREEZE["conflict_accumulator"], abs=TOL)
    assert pf.silence_pressure == pytest.approx(
        EXPECTED_FREEZE["silence_pressure"], abs=TOL)
    assert pf.imbalance_pressure == pytest.approx(
        EXPECTED_FREEZE["imbalance_pressure"], abs=TOL)
    assert pf.sync_error_ema_fast == pytest.approx(
        EXPECTED_FREEZE["sync_error_ema_fast"], abs=TOL)
    assert pf.sync_error_ema_slow == pytest.approx(
        EXPECTED_FREEZE["sync_error_ema_slow"], abs=TOL)


def test_freeze_derived(states):
    _, _, pf = states
    assert pf.display_burnout == pytest.approx(
        EXPECTED_FREEZE["display_burnout"], abs=TOL)
    assert pf.active == EXPECTED_FREEZE["active"]
