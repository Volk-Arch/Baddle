"""Identity sentinel: фиксированный event-sequence через UserState /
Neurochem / ProtectiveFreeze должен давать тот же snapshot. EXPECTED
захвачен 2026-04-24, расширен на 5D 2026-04-28 (vector/expectation_vec
с 3 → 5 элементов, скаляры идентичны — формулы не изменились).
Если тест падает — семантика формул изменилась.

Пересоздать EXPECTED: capture-script + вставка. Валидно только при
сознательной смене формулы — иначе тест ловит регрессию.
"""
import pytest

from src.user_state import UserState
from src.rgk import РГК


# ── Expected snapshot (captured 2026-04-24, pre-migration) ─────────────────

EXPECTED_USER_STATE = {
    "dopamine_gain": 0.566345,
    "serotonin_hysteresis": 0.540951,
    "norepinephrine_aperture": 0.418098,
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
    "expectation_vec": [0.529848, 0.518119, 0.463763, 0.5, 0.5],
    "hrv_baseline_by_tod": {
        "morning": None,
        "day": 0.6,
        "evening": None,
        "night": None,
    },
    "vector": [0.566345, 0.540951, 0.418098, 0.5, 0.5],
    "surprise": 0.03628,
    "imbalance": 0.062759,
}

EXPECTED_NEUROCHEM = {
    "dopamine_gain": 0.424359,
    "serotonin_hysteresis": 0.598492,
    "norepinephrine_aperture": 0.700003,
    "expectation_vec": [0.495359, 0.508601, 0.517466, 0.5, 0.5],
    "gamma": 2.84317,
    "recent_rpe": -0.15,
    "self_imbalance": 0.215502,
    "vector": [0.424359, 0.598492, 0.700003, 0.5, 0.5],
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
    from src.rgk import РГК
    monkeypatch.setattr(РГК, "_current_tod", lambda self: "day")

    us = UserState()
    nc = РГК()  # отдельный rgk для system events (раньше был Neurochem)
    r = РГК()  # отдельный rgk для pressure layer (раньше был ProtectiveFreeze)

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

    # System (Neurochem) events — теперь через РГК
    for d, wc, w in [
        (0.4,  [0.1, -0.05, 0.2],   [0.3,  0.4,  0.3]),
        (0.3,  [0.05, -0.02, 0.1],  [0.35, 0.3,  0.35]),
        (0.5,  [0.0, 0.0, 0.1],     [0.25, 0.45, 0.3]),
        (0.2,  [-0.05, 0.1, -0.02], [0.4,  0.3,  0.3]),
        (0.45, [0.1, 0.05, -0.05],  [0.3,  0.3,  0.4]),
    ]:
        nc.s_graph(d=d, w_change=wc, weights=w)
    for _ in range(3):
        nc.tick_s_pred()
    nc.s_outcome(prior=0.5, posterior=0.7)
    nc.s_outcome(prior=0.6, posterior=0.55)

    # Pressure layer events (раньше через ProtectiveFreeze, теперь через РГК)
    r.p_conflict(d=0.7, serotonin=0.4)
    r.p_conflict(d=0.65, serotonin=0.45)
    for _ in range(20):
        r.p_tick(dt=60.0, sync_err=0.5, imbalance=0.3)

    return us, nc, r


# ── UserState identity ─────────────────────────────────────────────────────

def test_user_state_scalar_metrics(states):
    us, _, _ = states
    assert us.dopamine == pytest.approx(EXPECTED_USER_STATE["dopamine_gain"], abs=TOL)
    assert us.serotonin == pytest.approx(EXPECTED_USER_STATE["serotonin_hysteresis"], abs=TOL)
    assert us.norepinephrine == pytest.approx(
        EXPECTED_USER_STATE["norepinephrine_aperture"], abs=TOL)
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


# ── System chem identity (через РГК после W4) ──────────────────────────────

def test_neurochem_scalars(states):
    _, nc, _ = states
    assert nc.system.gain.value == pytest.approx(EXPECTED_NEUROCHEM["dopamine_gain"], abs=TOL)
    assert nc.system.hyst.value == pytest.approx(EXPECTED_NEUROCHEM["serotonin_hysteresis"], abs=TOL)
    assert nc.system.aperture.value == pytest.approx(
        EXPECTED_NEUROCHEM["norepinephrine_aperture"], abs=TOL)


def test_neurochem_predictive_and_derived(states):
    _, nc, _ = states
    assert nc.s_exp_vec.value.tolist() == pytest.approx(
        EXPECTED_NEUROCHEM["expectation_vec"], abs=TOL)
    assert nc.gamma() == pytest.approx(EXPECTED_NEUROCHEM["gamma"], abs=TOL)
    assert nc.recent_rpe == pytest.approx(
        EXPECTED_NEUROCHEM["recent_rpe"], abs=TOL)
    assert nc.project("system")["self_imbalance"] == pytest.approx(
        EXPECTED_NEUROCHEM["self_imbalance"], abs=TOL)
    assert nc.system.vector().tolist() == pytest.approx(
        EXPECTED_NEUROCHEM["vector"], abs=TOL)


# ── Freeze identity (через РГК после W3) ──────────────────────────────────

def test_freeze_feeders(states):
    _, _, r = states
    assert r.conflict.value == pytest.approx(
        EXPECTED_FREEZE["conflict_accumulator"], abs=TOL)
    assert r.silence_press == pytest.approx(
        EXPECTED_FREEZE["silence_pressure"], abs=TOL)
    assert r.imbalance_press.value == pytest.approx(
        EXPECTED_FREEZE["imbalance_pressure"], abs=TOL)
    assert r.sync_fast.value == pytest.approx(
        EXPECTED_FREEZE["sync_error_ema_fast"], abs=TOL)
    assert r.sync_slow.value == pytest.approx(
        EXPECTED_FREEZE["sync_error_ema_slow"], abs=TOL)


def test_freeze_derived(states):
    _, _, r = states
    display = max(r.conflict.value, r.silence_press, r.imbalance_press.value)
    assert display == pytest.approx(EXPECTED_FREEZE["display_burnout"], abs=TOL)
    assert r.freeze_active == EXPECTED_FREEZE["active"]


# ── Checkin-flow identity (2026-04-24 consolidation) ──────────────────────

def test_checkin_event_identity(monkeypatch):
    """apply_checkin → NE/serotonin/valence с override-decays должно дать
    то же значение что inline EMA `x = decay*x + (1-decay)*target` в
    старом checkins.py (до миграции). Формулы не менялись, маршрут — apply_checkin
    после Phase D Step 3c.
    """
    from src.rgk import РГК
    monkeypatch.setattr(РГК, "_current_tod", lambda self: "day")
    from src.ema import Decays

    # Path A — ручной inline EMA через setters (semantics старого checkins)
    us_a = UserState()
    us_a.update_from_hrv(coherence=0.6, stress=0.3, rmssd=40.0)
    us_a.update_from_engagement(0.65)

    ds = Decays.CHECKIN_STRESS
    us_a.norepinephrine = ds * us_a.norepinephrine + (1 - ds) * 0.7

    df = Decays.CHECKIN_FOCUS
    us_a.serotonin = df * us_a.serotonin + (1 - df) * 0.8

    dv = Decays.CHECKIN_VALENCE
    us_a.valence = dv * us_a.valence + (1 - dv) * 0.5

    # Path B — через explicit apply_checkin (Phase D Step 3c)
    us_b = UserState()
    us_b.update_from_hrv(coherence=0.6, stress=0.3, rmssd=40.0)
    us_b.update_from_engagement(0.65)
    us_b.apply_checkin(stress=70, focus=80, reality=1)

    assert us_a.norepinephrine == pytest.approx(us_b.norepinephrine, abs=1e-6)
    assert us_a.serotonin == pytest.approx(us_b.serotonin, abs=1e-6)
    assert us_a.valence == pytest.approx(us_b.valence, abs=1e-6)


def test_apply_subjective_surprise_identity(monkeypatch):
    """apply_subjective_surprise должен быть эквивалентен старому
    inline nudge `expectation.feed(reality - s, decay_override=0.6)`.
    """
    from src.rgk import РГК
    monkeypatch.setattr(РГК, "_current_tod", lambda self: "day")

    # Path A — ручной nudge (то что делал старый checkins после fix'а)
    us_a = UserState()
    us_a.update_from_hrv(coherence=0.6, stress=0.3, rmssd=40.0)
    us_a.update_from_engagement(0.65)
    us_a.tick_expectation()

    s = 0.25
    target = max(0.0, min(1.0, us_a.state_level() - s))
    us_a._rgk.u_exp.feed(target, decay_override=0.6)
    us_a._rgk.u_exp_tod["day"].feed(target, decay_override=0.6)

    # Path B — helper
    us_b = UserState()
    us_b.update_from_hrv(coherence=0.6, stress=0.3, rmssd=40.0)
    us_b.update_from_engagement(0.65)
    us_b.tick_expectation()
    us_b.apply_subjective_surprise(0.25, blend=0.4)

    assert us_a.expectation == pytest.approx(us_b.expectation, abs=1e-6)
    assert us_a.expectation_by_tod["day"] == pytest.approx(
        us_b.expectation_by_tod["day"], abs=1e-6)


def test_checkins_apply_to_user_state_end_to_end(monkeypatch, tmp_path):
    """Integration: apply_to_user_state (миграционный) = inline (старый) по всем полям.

    Использует real function из checkins.py на fresh UserState. Проверяет что
    end-to-end путь сохраняет семантику — в т.ч. apply_subjective_surprise
    nudge expectation при присутствии expected+reality.
    """
    from src import paths
    monkeypatch.setattr(paths, "CHECKINS_FILE", tmp_path / "checkins.jsonl")
    from src import checkins
    monkeypatch.setattr(checkins, "_CHECKIN_FILE", tmp_path / "checkins.jsonl")
    from src.rgk import РГК, reset_global_rgk
    monkeypatch.setattr(РГК, "_current_tod", lambda self: "day")

    # Reset global РГК — checkins.apply_to_user_state работает с singleton
    r = reset_global_rgk()
    r.u_hrv(coherence=0.6, stress=0.3, rmssd=40.0)
    r.u_engage(0.65)
    r.tick_u_pred()

    entry = {"energy": 40, "stress": 70, "focus": 80,
             "expected": 1, "reality": -1}
    checkins.apply_to_user_state(entry)

    # Read через РГК напрямую (UserState constructor resets defaults).

    # stress 70 → target_ne 0.7, decay CHECKIN_STRESS (0.7)
    # cur_ne after hrv_update(stress=0.3) = 0.9*0.5 + 0.1*0.3 = 0.48
    # new_ne = 0.7*0.48 + 0.3*0.7 = 0.546
    assert float(r.user.aperture.value) == pytest.approx(0.546, abs=1e-3)

    # focus 80 → target_s 0.8, decay CHECKIN_FOCUS (0.7)
    # cur_s after hrv_update(coh=0.6) = 0.9*0.5 + 0.1*0.6 = 0.51
    # new_s = 0.7*0.51 + 0.3*0.8 = 0.597
    assert float(r.user.hyst.value) == pytest.approx(0.597, abs=1e-3)

    # reality -1 → valence target -0.5, decay CHECKIN_VALENCE (0.6)
    # cur_v = 0 → 0.6*0 + 0.4*(-0.5) = -0.2
    assert float(r.valence.value) == pytest.approx(-0.2, abs=1e-3)
