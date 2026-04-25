"""Property-based tests для РГК прототипа.

См. [docs/neurochem-design.md](../docs/neurochem-design.md) §5
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
    См. docs/neurochem-design.md §10A.
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

    @pytest.mark.skip(reason="Requires actual counter-wave generation (step(obs, dt) with delay buffer), Tier 2 — see TODO.md")
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


# ── Phase D Step 5: ACh + GABA feeders semantics ───────────────────────────

class TestPhaseDFeeders:
    """ACh/GABA feeder methods на UserState и Neurochem.

    Default 0.5 без вызовов → balance() = (DA·NE)/(5HT) (3-axis equivalent).
    После calls → 5-axis формула активна.
    """

    def test_user_acetylcholine_default(self):
        from src.user_state import UserState
        us = UserState()
        assert us.acetylcholine == pytest.approx(0.5, abs=TOL)

    def test_user_acetylcholine_novelty(self):
        from src.user_state import UserState
        us = UserState()
        # Initial 0.5, decay 0.9 → after feed(0.8): 0.9*0.5 + 0.1*0.8 = 0.53
        us.feed_acetylcholine(novelty=0.8)
        assert us.acetylcholine == pytest.approx(0.53, abs=TOL)

    def test_user_acetylcholine_boost_clamps_to_min_085(self):
        from src.user_state import UserState
        us = UserState()
        # boost=True with low novelty → still bumps to ≥0.85
        us.feed_acetylcholine(novelty=0.3, boost=True)
        # decay_override=0.85, signal=max(0.3, 0.85)=0.85
        # → 0.85*0.5 + 0.15*0.85 = 0.425 + 0.1275 = 0.5525
        assert us.acetylcholine == pytest.approx(0.5525, abs=TOL)

    def test_user_gaba_default(self):
        from src.user_state import UserState
        us = UserState()
        assert us.gaba == pytest.approx(0.5, abs=TOL)

    def test_user_gaba_high_when_focused(self):
        from src.user_state import UserState
        us = UserState()
        # focus_residue=0.0 (default) → 1-0 = 1.0 → 0.95*0.5+0.05*1.0 = 0.525
        us.feed_gaba()
        assert us.gaba == pytest.approx(0.525, abs=TOL)

    def test_user_gaba_low_when_scattered(self):
        from src.user_state import UserState
        us = UserState()
        us.focus_residue = 0.9
        # 1-0.9 = 0.1 → 0.95*0.5+0.05*0.1 = 0.48
        us.feed_gaba()
        assert us.gaba == pytest.approx(0.48, abs=TOL)

    def test_system_acetylcholine_node_rate(self):
        from src.neurochem import Neurochem
        nc = Neurochem()
        # decay 0.9, signal=0.7 → 0.9*0.5 + 0.1*0.7 = 0.52
        nc.feed_acetylcholine(node_creation_rate=0.7)
        assert nc.acetylcholine == pytest.approx(0.52, abs=TOL)

    def test_system_acetylcholine_with_bridge_quality(self):
        from src.neurochem import Neurochem
        nc = Neurochem()
        # First feed: 0.9*0.5+0.1*0.5 = 0.5
        # Second feed (decay_override=0.9): 0.9*0.5+0.1*0.8 = 0.53
        nc.feed_acetylcholine(node_creation_rate=0.5, bridge_quality=0.8)
        assert nc.acetylcholine == pytest.approx(0.53, abs=TOL)

    def test_system_gaba_freeze_active(self):
        from src.neurochem import Neurochem
        nc = Neurochem()
        # decay 0.95, signal=1.0 → 0.95*0.5+0.05*1.0 = 0.525
        nc.feed_gaba(freeze_active=True)
        assert nc.gaba == pytest.approx(0.525, abs=TOL)

    def test_system_gaba_freeze_inactive(self):
        from src.neurochem import Neurochem
        nc = Neurochem()
        nc.feed_gaba(freeze_active=False)
        # 0.95*0.5+0.05*0.0 = 0.475
        assert nc.gaba == pytest.approx(0.475, abs=TOL)

    def test_system_gaba_with_scattering(self):
        from src.neurochem import Neurochem
        nc = Neurochem()
        # First: feed(1.0): 0.475 (см. выше? actually freeze_active=True → 0.525)
        # Wait — freeze_active=True → first feed=1.0, decay=0.95: 0.525
        # Then scattering=0.2 → inv=0.8, decay_override=0.95: 0.95*0.525+0.05*0.8=0.5388
        nc.feed_gaba(freeze_active=True, embedding_scattering=0.2)
        assert nc.gaba == pytest.approx(0.5388, abs=1e-4)

    def test_user_balance_method(self):
        from src.user_state import UserState
        us = UserState(dopamine=0.7, serotonin=0.6, norepinephrine=0.4)
        # plasticity=damping=0.5 default → (0.7·0.4·0.5)/(0.6·0.5) = 0.14/0.30 = 0.467
        assert us.balance() == pytest.approx(0.4667, abs=1e-3)

    def test_system_balance_method(self):
        from src.neurochem import Neurochem
        nc = Neurochem(dopamine=0.6, serotonin=0.5, norepinephrine=0.5)
        # (0.6·0.5·0.5)/(0.5·0.5) = 0.15/0.25 = 0.6
        assert nc.balance() == pytest.approx(0.6, abs=1e-3)


# ── Phase D Step 5+: 8-region РГК-карта ────────────────────────────────────

class TestNamedState8Region:
    """Voronoi/named_state перепилен на 8-region РГК-карту по chem profile.
    See src/user_state_map.py. Каждый target профиль (хим. координаты региона)
    должен matchить свой регион bit-perfect (distance 0)."""

    @pytest.mark.parametrize("region,da,s,ne,ach,gaba", [
        ("flow",     0.70, 0.60, 0.60, 0.70, 0.50),
        ("stable",   0.50, 0.85, 0.40, 0.50, 0.85),
        ("focus",    0.60, 0.30, 0.90, 0.40, 0.30),
        ("explore",  0.60, 0.50, 0.30, 0.90, 0.50),
        ("overload", 0.50, 0.20, 1.00, 0.50, 0.15),
        ("apathy",   0.15, 0.50, 0.30, 0.40, 0.60),
        ("burnout",  0.30, 0.40, 0.70, 0.30, 0.50),
        ("insight",  0.85, 0.50, 0.40, 0.95, 0.50),
    ])
    def test_target_profile_maps_to_region(self, region, da, s, ne, ach, gaba):
        from src.user_state_map import nearest_named_state
        result = nearest_named_state(da=da, s=s, ne=ne, ach=ach, gaba=gaba)
        assert result["key"] == region, \
            f"target profile for {region} mapped to {result['key']}"
        assert result["distance"] == pytest.approx(0.0, abs=1e-6)

    def test_list_named_states_returns_8(self):
        from src.user_state_map import list_named_states
        states = list_named_states()
        assert len(states) == 8
        assert {s["key"] for s in states} == {
            "flow", "stable", "focus", "explore",
            "overload", "apathy", "burnout", "insight",
        }

    def test_ach_gaba_default_05_for_legacy_callers(self):
        """ACh/GABA необязательные — default 0.5 для backward-compat."""
        from src.user_state_map import nearest_named_state
        result = nearest_named_state(da=0.7, s=0.6, ne=0.6)
        # ach/gaba defaulted to 0.5 — ближе к flow (его профиль 0.7,0.6,0.6,0.7,0.5)
        # дистанция не нулевая (ach offset 0.2) но flow ближайший
        assert result["key"] == "flow"

    def test_user_named_state_uses_chem_profile(self):
        from src.user_state import UserState
        us = UserState(dopamine=0.6, serotonin=0.5, norepinephrine=0.3)
        us.acetylcholine = 0.9
        # Профиль (0.6, 0.5, 0.3, 0.9, 0.5) совпадает с explore exact
        assert us.named_state["key"] == "explore"
        assert us.named_state["emoji"] == "🟡"


# ── Phase D: aperture скаляр в depth engine ─────────────────────────────────

class TestApertureDerivation:
    """Aperture [0,1] заменяет 3 несвязанных knob (format, batched, depth)
    одним slider'ом по апертурному пределу."""

    def setup_method(self):
        from src import api_backend
        # Очищаем потенциальные explicit overrides
        for k in ("deep_aperture", "deep_response_format", "deep_batched_synthesis"):
            api_backend._settings.pop(k, None)

    def test_aperture_default_essay(self):
        from src.api_backend import get_aperture, get_deep_response_format, is_deep_batched
        # Без deep_aperture в settings, без explicit format → derived из default essay → 0.5
        assert get_aperture() == pytest.approx(0.5, abs=1e-6)
        assert get_deep_response_format() == "essay"
        assert is_deep_batched() is True  # 0.5 ≥ 0.4

    def test_aperture_focus(self):
        from src import api_backend
        api_backend._settings["deep_aperture"] = 0.15
        from src.api_backend import get_aperture, get_deep_response_format, is_deep_batched, get_mode_depth
        assert get_aperture() == pytest.approx(0.15, abs=1e-6)
        assert get_deep_response_format() == "brief"
        assert is_deep_batched() is False
        # depth_mult = 0.5 для aperture < 0.2; conkretное value зависит от
        # persisted deep_mode_steps. Ключевое — depth strictly меньше дефолта.
        depth_focus = get_mode_depth("horizon")
        api_backend._settings["deep_aperture"] = 0.5
        depth_default = get_mode_depth("horizon")
        assert depth_focus < depth_default

    def test_aperture_panorama(self):
        from src import api_backend
        api_backend._settings["deep_aperture"] = 0.95
        from src.api_backend import get_aperture, get_deep_response_format, is_deep_batched, get_mode_depth
        assert get_aperture() == pytest.approx(0.95, abs=1e-6)
        assert get_deep_response_format() == "article"
        assert is_deep_batched() is True
        # depth_mult = 2.0 для aperture ≥ 0.9 → strictly больше дефолта.
        depth_panorama = get_mode_depth("horizon")
        api_backend._settings["deep_aperture"] = 0.5
        depth_default = get_mode_depth("horizon")
        assert depth_panorama > depth_default

    def test_aperture_default_when_missing(self):
        """Default aperture 0.5 если settings нет deep_aperture."""
        from src import api_backend
        api_backend._settings.pop("deep_aperture", None)
        from src.api_backend import get_aperture
        assert get_aperture() == pytest.approx(0.5, abs=1e-6)

    def teardown_method(self):
        from src import api_backend
        api_backend._settings.pop("deep_aperture", None)


# ── Counter-wave (Правило 7) — UserState/Neurochem mode activation ──────────

class TestCounterWaveActivation:
    """R/C bit (Правило 7) активирован 2026-04-25. UserState и Neurochem
    exposed `.mode` property + `update_mode(perturbation)` method.
    Cognitive_loop._advance_tick вызывает update_mode каждый tick.
    """

    def test_user_state_mode_default_R(self):
        from src.user_state import UserState
        u = UserState()
        assert u.mode == "R"

    def test_neurochem_mode_default_R(self):
        from src.neurochem import Neurochem
        n = Neurochem()
        assert n.mode == "R"

    def test_user_state_mode_flips_on_perturbation(self):
        """Гистерезис ACT=0.15 / REC=0.08."""
        from src.user_state import UserState
        u = UserState()
        # R → C при perturbation > 0.15
        u.update_mode(0.20)
        assert u.mode == "C"
        # C остаётся при perturbation в hysteresis-окне [0.08, 0.15]
        u.update_mode(0.10)
        assert u.mode == "C"
        # C → R при perturbation < 0.08
        u.update_mode(0.05)
        assert u.mode == "R"

    def test_neurochem_mode_flips_on_perturbation(self):
        from src.neurochem import Neurochem
        n = Neurochem()
        n.update_mode(0.20)
        assert n.mode == "C"
        n.update_mode(0.05)
        assert n.mode == "R"

    def test_user_state_to_dict_includes_mode(self):
        from src.user_state import UserState
        u = UserState()
        u.update_mode(0.2)
        d = u.to_dict()
        assert d["mode"] == "C"

    def test_neurochem_to_dict_includes_mode(self):
        from src.neurochem import Neurochem
        n = Neurochem()
        d = n.to_dict()
        assert d["mode"] == "R"


# ── B0 Singleton РГК ───────────────────────────────────────────────────────

class TestSingletonRGK:
    """B0: каскад зеркал = ОДНА пара резонаторов. Production bootstrap
    (`get_user_state()` + `CognitiveState.__init__`) использует
    `get_global_rgk()` чтобы UserState/Neurochem/ProtectiveFreeze делили
    один объект."""

    def test_get_global_rgk_returns_singleton(self):
        from src.rgk import get_global_rgk
        a = get_global_rgk()
        b = get_global_rgk()
        assert a is b

    def test_reset_global_rgk_returns_fresh(self):
        from src.rgk import get_global_rgk, reset_global_rgk
        a = get_global_rgk()
        b = reset_global_rgk()
        assert a is not b
        c = get_global_rgk()
        assert c is b

    def test_user_state_default_creates_own_rgk(self):
        """Backward compat: UserState() без rgk= создаёт собственный РГК."""
        from src.user_state import UserState
        from src.rgk import get_global_rgk
        u = UserState()
        assert u._rgk is not get_global_rgk()

    def test_user_state_with_explicit_rgk_shares(self):
        """Explicit rgk= sharing — production pattern."""
        from src.user_state import UserState
        from src.neurochem import Neurochem, ProtectiveFreeze
        from src.rgk import РГК
        rgk = РГК()
        u = UserState(rgk=rgk)
        n = Neurochem(rgk=rgk)
        f = ProtectiveFreeze(rgk=rgk)
        assert u._rgk is n._rgk is f._rgk is rgk

    def test_production_bootstrap_shares_global(self):
        """get_user_state() + CognitiveState — каскад зеркал на одном РГК."""
        from src.rgk import reset_global_rgk
        reset_global_rgk()
        # Reset global UserState и global CognitiveState — иначе видим
        # инстансы из прошлых тестов с другим _rgk.
        import src.user_state
        src.user_state._global_user = None
        import src.horizon
        src.horizon._global_state = None

        from src.user_state import get_user_state
        from src.horizon import get_global_state
        u = get_user_state()
        gs = get_global_state()
        assert u._rgk is gs.neuro._rgk
        assert u._rgk is gs.freeze._rgk


# ── B4 Wave 1 — project() expansion ────────────────────────────────────────

class TestProjectExpansion:
    """B4 Wave 1: project() расширен chem-only derivations."""

    def test_user_state_project_has_phase_d_chem(self):
        from src.rgk import РГК
        r = РГК()
        p = r.project("user_state")
        # Phase D 5-axis + B0 mode
        assert "acetylcholine" in p
        assert "gaba" in p
        assert "balance" in p
        assert "mode" in p
        assert p["mode"] == "R"

    def test_user_state_project_has_attribution(self):
        from src.rgk import РГК
        r = РГК()
        # Сместим user vector чтобы attribution был не "none"
        r.user.gain.value = 0.9    # dopamine high
        r.user.hyst.value = 0.5
        r.user.aperture.value = 0.5
        p = r.project("user_state")
        assert p["attribution"] == "dopamine"
        assert p["attribution_signed"] > 0
        assert p["attribution_magnitude"] > 0

    def test_user_state_project_attribution_none_when_small(self):
        from src.rgk import РГК
        r = РГК()
        # vector ≈ baseline → mag < 0.05 → "none"
        p = r.project("user_state")
        assert p["attribution"] == "none"
        assert p["attribution_signed"] == 0.0

    def test_user_state_agency_gap(self):
        from src.rgk import РГК
        r = РГК()
        r.agency.value = 0.3
        p = r.project("user_state")
        assert p["agency_gap"] == pytest.approx(0.7, abs=1e-6)

    def test_system_project_has_phase_d_chem(self):
        from src.rgk import РГК
        r = РГК()
        p = r.project("system")
        assert "acetylcholine" in p
        assert "gaba" in p
        assert "balance" in p
        assert "mode" in p

    def test_user_state_property_delegates_match_project(self):
        """UserState properties и project() должны давать идентичные значения."""
        from src.user_state import UserState
        u = UserState()
        u._rgk.user.gain.value = 0.8
        p = u._rgk.project("user_state")
        assert u.attribution == p["attribution"]
        assert u.attribution_magnitude == p["attribution_magnitude"]
        assert u.attribution_signed == p["attribution_signed"]
        assert u.agency_gap == p["agency_gap"]


# ── B4 Wave 2 — state move + non-chem projectors ───────────────────────────

class TestStateMoveAndProjectors:
    """B4 Wave 2: bespoke user-side state (HRV/activity/day_summary/focus_residue/
    timestamps) перемещён из UserState в РГК. UserState facade — thin @property
    proxies. Non-chem projectors (hrv_surprise/frequency_regime/activity_zone)
    в РГК.
    """

    def test_user_state_hrv_proxy_writes_to_rgk(self):
        """UserState.hrv_coherence теперь @property → читает/пишет _rgk."""
        from src.user_state import UserState
        u = UserState()
        assert u.hrv_coherence is None
        u.hrv_coherence = 0.7
        assert u._rgk.hrv_coherence == 0.7
        # Чтение — тоже через rgk.
        u._rgk.hrv_coherence = 0.3
        assert u.hrv_coherence == 0.3

    def test_user_state_activity_proxy(self):
        from src.user_state import UserState
        u = UserState()
        u.activity_magnitude = 0.8
        assert u._rgk.activity_magnitude == 0.8

    def test_user_state_day_summary_proxy(self):
        from src.user_state import UserState
        u = UserState()
        u.day_summary["2026-04-25"] = {"tasks_started": 3}
        assert u._rgk.day_summary["2026-04-25"]["tasks_started"] == 3

    def test_frequency_regime_long_wave(self):
        from src.rgk import РГК
        r = РГК()
        r.hrv_coherence = 0.7
        r.hrv_rmssd = 35.0
        r.user.aperture.value = 0.3   # NE low
        assert r.frequency_regime() == "long_wave"
        assert r.project("user_state")["frequency_regime"] == "long_wave"

    def test_frequency_regime_short_wave_low_coh(self):
        from src.rgk import РГК
        r = РГК()
        r.hrv_coherence = 0.3
        r.hrv_rmssd = 20.0
        assert r.frequency_regime() == "short_wave"

    def test_frequency_regime_flat_no_hrv(self):
        from src.rgk import РГК
        r = РГК()
        assert r.frequency_regime() == "flat"

    def test_hrv_surprise_zero_when_no_baseline(self):
        from src.rgk import РГК
        r = РГК()
        r.hrv_coherence = 0.7
        # baseline не seeded → 0
        assert r.hrv_surprise() == 0.0

    def test_activity_zone_no_hrv(self):
        from src.rgk import РГК
        r = РГК()
        z = r.activity_zone()
        assert z["key"] is None

    def test_activity_zone_recovery(self):
        from src.rgk import РГК
        r = РГК()
        r.hrv_coherence = 0.7
        r.activity_magnitude = 0.1   # not active
        z = r.activity_zone()
        assert z["key"] == "recovery"

    def test_user_state_delegates_match_rgk(self):
        """UserState.frequency_regime/hrv_surprise/activity_zone делегируют."""
        from src.user_state import UserState
        u = UserState()
        u.hrv_coherence = 0.5
        u.activity_magnitude = 0.6
        assert u.frequency_regime == u._rgk.frequency_regime()
        assert u.hrv_surprise == u._rgk.hrv_surprise()
        assert u.activity_zone == u._rgk.activity_zone()
