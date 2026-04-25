"""Property-based tests для РГК прототипа.

См. [planning/rgk-migration-plan.md](../planning/rgk-migration-plan.md) §5
«Property-based test contract». Заменяет bit-identity на инварианты
которые держатся на любых event sequences (не только Phase A fixed).

6 инвариантов:
  1. Mode hysteresis monotone — R↔C через гистерезис, без дребезга
  2. Balance corridor — balance() ∈ [0.2, 2.5] на identity, [0.05, 5.0] на random
  3. Coupling consistency — sync_error математические свойства
     (Inv 3-behavioral «counter-wave reduces sync_error» отложен до Tier 2)
  4. Chem bounds on random traces — 5 chem + valence + agency + burnout + pressure
  5. PE derived consistency — surprise/imbalance/attribution согласованы
  6. Phase A snapshot sentinel — EXPECTED preserved (semantic identity)

Random traces: 10 фиксированных seeds × 100 steps. Без hypothesis dependency.
"""
from __future__ import annotations

import math
import random

import numpy as np
import pytest

from src.rgk import (
    РГК,
    Resonator,
    _USER_DECAYS,
    _run_identity_sequence,
    EXPECTED_USER,
    EXPECTED_SYS,
    EXPECTED_FREEZE,
)

TOL = 1e-5


# ── Helpers: random event traces ────────────────────────────────────────────

EVENT_ALPHABET = (
    "u_hrv", "u_engage", "u_feedback", "u_chat", "u_plan", "u_energy",
    "tick_u_pred",
    "s_graph", "s_outcome", "tick_s_pred",
    "p_conflict", "p_tick",
)


def _emit_random_event(r: РГК, rng: random.Random) -> None:
    """Один случайный event на РГК с валидными границами входов."""
    event = rng.choice(EVENT_ALPHABET)
    if event == "u_hrv":
        r.u_hrv(coherence=rng.uniform(0, 1),
                stress=rng.uniform(0, 1),
                rmssd=rng.uniform(20, 80))
    elif event == "u_engage":
        r.u_engage(signal=rng.uniform(0, 1))
    elif event == "u_feedback":
        r.u_feedback(rng.choice(("accepted", "rejected", "ignored")))
    elif event == "u_chat":
        r.u_chat(rng.uniform(-1, 1))
    elif event == "u_plan":
        planned = rng.randint(0, 10)
        completed = rng.randint(0, planned) if planned else 0
        r.u_plan(completed, planned)
    elif event == "u_energy":
        r.u_energy(rng.randint(0, 30))
    elif event == "tick_u_pred":
        r.tick_u_pred()
    elif event == "s_graph":
        r.s_graph(d=rng.uniform(0, 1),
                  w_change=[rng.uniform(-0.3, 0.3) for _ in range(3)],
                  weights=[rng.uniform(0, 1) for _ in range(3)])
    elif event == "s_outcome":
        r.s_outcome(prior=rng.uniform(0.05, 0.95),
                    posterior=rng.uniform(0.05, 0.95))
    elif event == "tick_s_pred":
        r.tick_s_pred()
    elif event == "p_conflict":
        r.p_conflict(d=rng.uniform(0, 1), serotonin=rng.uniform(0, 1))
    elif event == "p_tick":
        r.p_tick(dt=rng.uniform(1, 3600),
                 sync_err=rng.uniform(0, 1.7),
                 imbalance=rng.uniform(0, 1))


def _run_random_trace(seed: int, steps: int = 100) -> РГК:
    r = РГК()
    r._tod = "day"
    rng = random.Random(seed)
    for _ in range(steps):
        _emit_random_event(r, rng)
    return r


SEEDS = (1, 2, 7, 13, 42, 100, 314, 999, 2026, 31415)


# ── Inv 1: Mode hysteresis monotone ────────────────────────────────────────

class TestModeHysteresis:
    """R↔C переключение монотонно через гистерезис, без дребезга."""

    def test_initial_mode_is_R(self):
        assert Resonator(_USER_DECAYS).mode == "R"

    def test_R_stays_below_threshold(self):
        r = Resonator(_USER_DECAYS)
        for _ in range(20):
            r.update_mode(0.10)  # < THETA_ACT (0.15)
        assert r.mode == "R"

    def test_R_to_C_above_threshold(self):
        r = Resonator(_USER_DECAYS)
        r.update_mode(0.20)  # > THETA_ACT
        assert r.mode == "C"

    def test_C_stays_in_hysteresis_band(self):
        r = Resonator(_USER_DECAYS)
        r.update_mode(0.20)
        assert r.mode == "C"
        for p in (0.12, 0.10, 0.09):
            r.update_mode(p)
            assert r.mode == "C", f"flipped to R at p={p} (band [0.08, 0.15])"

    def test_C_to_R_below_recovery(self):
        r = Resonator(_USER_DECAYS)
        r.update_mode(0.20)
        r.update_mode(0.05)  # < THETA_REC (0.08)
        assert r.mode == "R"

    def test_no_chatter_at_boundary(self):
        """Между THETA_REC и THETA_ACT — никаких oscillations."""
        r = Resonator(_USER_DECAYS)
        r.update_mode(0.20)  # → C
        for i in range(50):
            r.update_mode(0.09 if i % 2 else 0.13)
        assert r.mode == "C"

    def test_full_cycle_R_C_R(self):
        r = Resonator(_USER_DECAYS)
        assert r.mode == "R"
        r.update_mode(0.20); assert r.mode == "C"
        r.update_mode(0.04); assert r.mode == "R"
        r.update_mode(0.30); assert r.mode == "C"
        r.update_mode(0.01); assert r.mode == "R"


# ── Inv 2: Balance corridor ────────────────────────────────────────────────

class TestBalanceCorridor:
    """balance() в коридоре. Identity sequence — узкий, random — широкий."""

    BAL_LOW_IDENT = 0.2
    BAL_HIGH_IDENT = 2.5
    BAL_LOW_RAND = 0.05
    BAL_HIGH_RAND = 5.0

    def test_identity_sequence_balance_in_corridor(self):
        r = _run_identity_sequence()
        bu = r.user.balance()
        bs = r.system.balance()
        assert self.BAL_LOW_IDENT <= bu <= self.BAL_HIGH_IDENT, \
            f"user.balance={bu:.3f} outside [{self.BAL_LOW_IDENT}, {self.BAL_HIGH_IDENT}]"
        assert self.BAL_LOW_IDENT <= bs <= self.BAL_HIGH_IDENT, \
            f"system.balance={bs:.3f} outside [{self.BAL_LOW_IDENT}, {self.BAL_HIGH_IDENT}]"

    @pytest.mark.parametrize("seed", SEEDS)
    def test_random_trace_balance_in_corridor(self, seed):
        r = _run_random_trace(seed, steps=100)
        bu = r.user.balance()
        bs = r.system.balance()
        assert self.BAL_LOW_RAND <= bu <= self.BAL_HIGH_RAND, \
            f"seed={seed} user.balance={bu:.3f} out of [{self.BAL_LOW_RAND}, {self.BAL_HIGH_RAND}]"
        assert self.BAL_LOW_RAND <= bs <= self.BAL_HIGH_RAND, \
            f"seed={seed} system.balance={bs:.3f} out"

    def test_balance_formula_correct(self):
        """balance == (gain·aperture·plasticity) / (hyst·damping)."""
        r = Resonator(_USER_DECAYS)
        r.gain.value = 0.7
        r.hyst.value = 0.6
        r.aperture.value = 0.4
        r.plasticity.value = 0.5
        r.damping.value = 0.5
        expected = (0.7 * 0.4 * 0.5) / (0.6 * 0.5)
        assert r.balance() == pytest.approx(expected, abs=TOL)


# ── Inv 3: Coupling consistency (math sanity) ──────────────────────────────

class TestCouplingConsistency:
    """sync_error математические свойства.

    Inv 3-behavioral «counter-wave actually reduces sync_error» отложен
    до реализации wave-generation step(obs, dt) — Tier 2 после Phase D.
    См. planning/rgk-migration-plan.md §10A.
    """

    def test_sync_error_zero_when_vectors_equal(self):
        r = РГК()
        for axis in ("gain", "hyst", "aperture"):
            getattr(r.user, axis).value = 0.5
            getattr(r.system, axis).value = 0.5
        assert r.sync_error() == pytest.approx(0.0, abs=TOL)

    def test_sync_error_symmetric(self):
        """‖a-b‖ == ‖b-a‖."""
        r = РГК()
        r.user.gain.value, r.user.hyst.value, r.user.aperture.value = 0.7, 0.3, 0.5
        r.system.gain.value, r.system.hyst.value, r.system.aperture.value = 0.3, 0.7, 0.5
        e1 = r.sync_error()
        # Swap user ↔ system
        for axis in ("gain", "hyst", "aperture"):
            u, s = getattr(r.user, axis), getattr(r.system, axis)
            u.value, s.value = s.value, u.value
        e2 = r.sync_error()
        assert e1 == pytest.approx(e2, abs=TOL)

    def test_sync_error_max_distance(self):
        """Worst case [0,0,0] vs [1,1,1] = √3."""
        r = РГК()
        for axis in ("gain", "hyst", "aperture"):
            getattr(r.user, axis).value = 0.0
            getattr(r.system, axis).value = 1.0
        assert r.sync_error() == pytest.approx(math.sqrt(3), abs=TOL)

    @pytest.mark.parametrize("seed", SEEDS)
    def test_sync_error_in_bounds_random(self, seed):
        r = _run_random_trace(seed, steps=100)
        e = r.sync_error()
        assert 0.0 <= e <= math.sqrt(3) + TOL, \
            f"seed={seed} sync_error={e:.4f} out of [0, √3]"

    @pytest.mark.skip(reason="Requires actual counter-wave generation, Tier 2 (rgk-migration-plan.md §10A)")
    def test_counter_wave_reduces_sync_error(self):
        """Когда оба resonator в mode=C и генерируют counter-wave,
        sync_error должен монотонно падать. Реализуется после step(obs, dt)
        с buffer."""


# ── Inv 4: Chem bounds on random traces ────────────────────────────────────

class TestChemBounds:
    """5 chem ∈ [0,1] + valence ∈ [-1,1] + agency/burnout ∈ [0,1] + pressure ∈ [0,1]."""

    @pytest.mark.parametrize("seed", SEEDS)
    def test_user_chem_bounds(self, seed):
        r = _run_random_trace(seed, steps=100)
        for axis in ("gain", "hyst", "aperture", "plasticity", "damping"):
            v = float(getattr(r.user, axis).value)
            assert 0.0 <= v <= 1.0, f"seed={seed} user.{axis}={v}"

    @pytest.mark.parametrize("seed", SEEDS)
    def test_system_chem_bounds(self, seed):
        r = _run_random_trace(seed, steps=100)
        for axis in ("gain", "hyst", "aperture", "plasticity", "damping"):
            v = float(getattr(r.system, axis).value)
            assert 0.0 <= v <= 1.0, f"seed={seed} system.{axis}={v}"

    @pytest.mark.parametrize("seed", SEEDS)
    def test_aux_axes_bounds(self, seed):
        r = _run_random_trace(seed, steps=100)
        v = float(r.valence.value)
        assert -1.0 <= v <= 1.0, f"seed={seed} valence={v}"
        a = float(r.agency.value)
        assert 0.0 <= a <= 1.0, f"seed={seed} agency={a}"
        b = float(r.burnout.value)
        assert 0.0 <= b <= 1.0, f"seed={seed} burnout={b}"

    @pytest.mark.parametrize("seed", SEEDS)
    def test_pressure_bounds(self, seed):
        r = _run_random_trace(seed, steps=100)
        assert 0.0 <= float(r.conflict.value) <= 1.0
        assert 0.0 <= float(r.silence_press) <= 1.0
        assert 0.0 <= float(r.imbalance_press.value) <= 1.0
        assert 0.0 <= float(r.sync_fast.value) <= 1.0
        assert 0.0 <= float(r.sync_slow.value) <= 1.0

    @pytest.mark.parametrize("seed", SEEDS)
    def test_predictive_bounds(self, seed):
        r = _run_random_trace(seed, steps=100)
        assert 0.0 <= float(r.u_exp.value) <= 1.0
        for tod in ("morning", "day", "evening", "night"):
            assert 0.0 <= float(r.u_exp_tod[tod].value) <= 1.0
        for x in r.u_exp_vec.value:
            assert 0.0 <= float(x) <= 1.0
        for x in r.s_exp_vec.value:
            assert 0.0 <= float(x) <= 1.0


# ── Inv 5: PE derived consistency ──────────────────────────────────────────

class TestPredictiveConsistency:
    """surprise/imbalance/attribution согласованы с derived формулами."""

    @pytest.mark.parametrize("seed", SEEDS)
    def test_surprise_in_bounds(self, seed):
        r = _run_random_trace(seed, steps=100)
        u = r.project("user_state")
        assert -1.0 <= u["surprise"] <= 1.0, \
            f"seed={seed} surprise={u['surprise']}"

    @pytest.mark.parametrize("seed", SEEDS)
    def test_imbalance_nonneg_and_bounded(self, seed):
        r = _run_random_trace(seed, steps=100)
        u = r.project("user_state")
        assert 0.0 <= u["imbalance"] <= math.sqrt(3) + TOL, \
            f"seed={seed} imbalance={u['imbalance']}"

    @pytest.mark.parametrize("seed", SEEDS)
    def test_imbalance_matches_norm_formula(self, seed):
        r = _run_random_trace(seed, steps=100)
        u = r.project("user_state")
        vec = np.asarray(u["vector"], dtype=np.float64)
        ev = np.asarray(u["expectation_vec"], dtype=np.float64)
        expected = float(np.linalg.norm(vec - ev))
        assert u["imbalance"] == pytest.approx(expected, abs=1e-4)

    @pytest.mark.parametrize("seed", SEEDS)
    def test_self_imbalance_bounds_system(self, seed):
        r = _run_random_trace(seed, steps=100)
        s = r.project("system")
        assert 0.0 <= s["self_imbalance"] <= math.sqrt(3) + TOL

    @pytest.mark.parametrize("seed", SEEDS)
    def test_gamma_in_expected_range(self, seed):
        """γ = 2 + 3·NE·(1−S) → ∈ [2, 5]."""
        r = _run_random_trace(seed, steps=100)
        s = r.project("system")
        assert 2.0 - TOL <= s["gamma"] <= 5.0 + TOL, \
            f"seed={seed} gamma={s['gamma']}"


# ── Inv 6: Phase A snapshot sentinel ───────────────────────────────────────

class TestPhaseAIdentity:
    """На фиксированном Phase A event sequence, project совпадает с
    EXPECTED snapshot (зафиксирован 2026-04-24 на legacy commit).
    Это semantic identity, не bit-identity."""

    @pytest.fixture
    def rgk(self):
        return _run_identity_sequence()

    def test_user_state_scalars(self, rgk):
        got = rgk.project("user_state")
        for k in ("dopamine", "serotonin", "norepinephrine",
                  "valence", "burnout", "agency", "expectation",
                  "surprise", "imbalance"):
            assert got[k] == pytest.approx(EXPECTED_USER[k], abs=TOL), \
                f"{k}: got={got[k]} exp={EXPECTED_USER[k]}"

    def test_user_state_vectors(self, rgk):
        got = rgk.project("user_state")
        for i, exp_v in enumerate(EXPECTED_USER["vector"]):
            assert got["vector"][i] == pytest.approx(exp_v, abs=TOL)
        for i, exp_v in enumerate(EXPECTED_USER["expectation_vec"]):
            assert got["expectation_vec"][i] == pytest.approx(exp_v, abs=TOL)

    def test_user_tod_baselines(self, rgk):
        got = rgk.project("user_state")
        for tod in ("morning", "day", "evening", "night"):
            assert got["expectation_by_tod"][tod] == pytest.approx(
                EXPECTED_USER["expectation_by_tod"][tod], abs=TOL)
            exp_hrv = EXPECTED_USER["hrv_baseline_by_tod"][tod]
            got_hrv = got["hrv_baseline_by_tod"][tod]
            if exp_hrv is None:
                assert got_hrv is None, f"tod={tod}: expected None, got {got_hrv}"
            else:
                assert got_hrv == pytest.approx(exp_hrv, abs=TOL)

    def test_system_identity(self, rgk):
        got = rgk.project("system")
        for k in ("dopamine", "serotonin", "norepinephrine",
                  "gamma", "recent_rpe", "self_imbalance"):
            assert got[k] == pytest.approx(EXPECTED_SYS[k], abs=TOL), \
                f"{k}: got={got[k]} exp={EXPECTED_SYS[k]}"
        for i, exp_v in enumerate(EXPECTED_SYS["expectation_vec"]):
            assert got["expectation_vec"][i] == pytest.approx(exp_v, abs=TOL)
        for i, exp_v in enumerate(EXPECTED_SYS["vector"]):
            assert got["vector"][i] == pytest.approx(exp_v, abs=TOL)

    def test_freeze_identity(self, rgk):
        got = rgk.project("freeze")
        for k in ("conflict_accumulator", "silence_pressure",
                  "imbalance_pressure", "sync_error_ema_fast",
                  "sync_error_ema_slow", "display_burnout"):
            assert got[k] == pytest.approx(EXPECTED_FREEZE[k], abs=TOL), \
                f"{k}: got={got[k]} exp={EXPECTED_FREEZE[k]}"
        assert got["active"] == EXPECTED_FREEZE["active"]
