"""Wave 0 consolidation tests: РГК-path = production-path.

После Wave 0 (12 расхождений между facade и РГК) РГК — authoritative.
Эти тесты закрепляют behaviour'ы, которые раньше жили только в facade
и не покрывались identity-snapshot тестом:

- #2 surprise boost handling (РГК.tick_u_pred сам применяет fast-decay)
- #3 HRV raw storage в u_hrv
- #4 unknown-kind filter в u_feedback
- #6 RPE constants single source
- #7 freeze thresholds single source
- #1 RPE history single source
- #11 TOD fresh evaluation, _tod field удалён
- #8 _fb counter persist roundtrip
"""
import pytest

from src.rgk import (
    РГК, RPE_GAIN, RPE_WINDOW,
    FREEZE_TAU_STABLE, FREEZE_THETA_ACTIVE, FREEZE_THETA_RECOVERY,
)
from src.neurochem import Neurochem, ProtectiveFreeze
from src.user_state import UserState


# #4 ─────────────────────────────────────────────────────────────────────────
def test_u_feedback_skip_unknown_kind():
    r = РГК()
    r.u_feedback("typo")
    assert r._fb == {"accepted": 0, "rejected": 0, "ignored": 0}
    r.u_feedback("accepted")
    assert r._fb["accepted"] == 1


# #3 ─────────────────────────────────────────────────────────────────────────
def test_u_hrv_raw_storage(monkeypatch):
    monkeypatch.setattr(РГК, "_current_tod", lambda self: "day")
    r = РГК()
    r.u_hrv(coherence=0.7, stress=0.3, rmssd=45.0, activity=0.5)
    assert r.hrv_coherence == pytest.approx(0.7)
    assert r.hrv_stress == pytest.approx(0.3)
    assert r.hrv_rmssd == pytest.approx(45.0)
    assert r.activity_magnitude == pytest.approx(0.5)
    # Без storage frequency_regime() возвращал бы flat.
    assert r.frequency_regime() != "flat"
    az = r.activity_zone()
    assert az["key"] is not None


def test_u_hrv_clamp_input():
    r = РГК()
    r.u_hrv(coherence=1.5, stress=-0.3, activity=10.0)
    assert r.hrv_coherence == 1.0
    assert r.hrv_stress == 0.0
    assert r.activity_magnitude == 5.0


# #2 ─────────────────────────────────────────────────────────────────────────
def test_surprise_boost_applied_in_tick(monkeypatch):
    """tick_u_pred должен сам применять fast-decay при boost > 0
    без помощи facade — иначе после B5 callsites теряют boost."""
    monkeypatch.setattr(РГК, "_current_tod", lambda self: "day")
    r_normal, r_boosted = РГК(), РГК()
    for r in (r_normal, r_boosted):
        r.user.gain.value = 0.9
        r.user.hyst.value = 0.9   # state_level → 0.9, expectation → ~0.5
    r_normal.tick_u_pred()
    r_boosted._surprise_boost_remaining = 5
    r_boosted.tick_u_pred()
    # Boost → fast decay → expectation сдвинется к 0.9 сильнее.
    assert r_boosted.u_exp.value > r_normal.u_exp.value
    # Counter уменьшился.
    assert r_boosted._surprise_boost_remaining == 4


# #11 ────────────────────────────────────────────────────────────────────────
def test_tod_field_removed():
    """_tod field удалён из РГК — TOD evaluate fresh."""
    r = РГК()
    assert not hasattr(r, "_tod"), "_tod должен быть удалён в W0 #11"


def test_tod_fresh_in_each_call(monkeypatch):
    """tick_u_pred / u_hrv каждый раз читают _current_tod() заново."""
    r = РГК()
    monkeypatch.setattr(РГК, "_current_tod", lambda self: "morning")
    r.u_hrv(coherence=0.7)
    assert r.hrv_base_tod["morning"]._seeded
    assert not r.hrv_base_tod["evening"]._seeded
    monkeypatch.setattr(РГК, "_current_tod", lambda self: "evening")
    r.u_hrv(coherence=0.5)
    assert r.hrv_base_tod["evening"]._seeded


def test_tod_unified_slicing():
    """UserState._current_tod удалён — single source в РГК (5-12/12-18/18-23).
    h=11 / h=17 — два часа где старая UserState нарезка (5-11/11-17/17-23)
    расходилась с РГК (#10). После W0 — единая нарезка из РГК."""
    assert not hasattr(UserState, "_current_tod"), \
        "UserState._current_tod должен быть удалён в W0 #10"


# #8 ─────────────────────────────────────────────────────────────────────────
def test_fb_counter_persist_roundtrip():
    """_fb (accepted/rejected/ignored) переживает to_dict/from_dict."""
    us = UserState()
    us.update_from_feedback("accepted")
    us.update_from_feedback("accepted")
    us.update_from_feedback("rejected")
    dump = us.to_dict()
    assert dump["_fb"] == {"accepted": 2, "rejected": 1, "ignored": 0}
    us2 = UserState.from_dict(dump)
    assert us2._rgk._fb == {"accepted": 2, "rejected": 1, "ignored": 0}


# #1 ─────────────────────────────────────────────────────────────────────────
def test_rpe_history_single_source():
    """Neurochem._delta_history → property alias на _rgk._rpe_hist."""
    nc = Neurochem()
    nc.record_outcome(prior=0.5, posterior=0.7)
    nc.record_outcome(prior=0.6, posterior=0.65)
    assert nc._delta_history is nc._rgk._rpe_hist
    assert len(nc._rgk._rpe_hist) == 2


def test_recent_rpe_single_source():
    """Neurochem.recent_rpe → property alias на _rgk.recent_rpe."""
    nc = Neurochem()
    # First outcome: rpe=0 (predicted=actual без history). Need 2+ for non-zero.
    nc.record_outcome(prior=0.5, posterior=0.9)
    nc.record_outcome(prior=0.5, posterior=0.55)
    assert nc.recent_rpe == nc._rgk.recent_rpe
    assert nc.recent_rpe != 0.0


# #6 + #7 ────────────────────────────────────────────────────────────────────
def test_rpe_constants_single_source():
    """Neurochem.RPE_* удалены, рgk module-level RPE_GAIN/RPE_WINDOW."""
    assert not hasattr(Neurochem, "RPE_GAIN")
    assert not hasattr(Neurochem, "RPE_WINDOW")
    assert RPE_GAIN == 0.15
    assert RPE_WINDOW == 20


def test_freeze_thresholds_single_source():
    """ProtectiveFreeze.TAU_STABLE/THETA_* удалены, рgk module-level."""
    assert not hasattr(ProtectiveFreeze, "TAU_STABLE")
    assert not hasattr(ProtectiveFreeze, "THETA_ACTIVE")
    assert not hasattr(ProtectiveFreeze, "THETA_RECOVERY")
    assert FREEZE_TAU_STABLE == 0.6
    assert FREEZE_THETA_ACTIVE == 0.15
    assert FREEZE_THETA_RECOVERY == 0.08
