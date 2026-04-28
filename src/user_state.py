"""UserState — backward-compat shim над РГК (ужат в B5 W5 final).

После B5 (W0-W5) substrate всех state+dynamics — `src.rgk.РГК`. UserState
оставлен как тонкий facade для тестов и historical API: все методы — 1-line
delegates на `_rgk.X`, properties читают `_rgk.user/system/etc.` напрямую.

Production code больше не использует UserState — мигрирован в W5. Этот файл
существует для:
  • `tests/test_*` которые делают `UserState()` или `us.dopamine` для convenience
  • module-level helpers (`compute_sync_error`, `compute_capacity_indicators`,
    `compute_cognitive_load`) — используются user_dynamics + tests

Если class станет отбалластом — удалить вместе с `from .user_state import …`
в test fixtures.
"""
from typing import Optional

import numpy as np

from .rgk import РГК, get_global_rgk


# ── Sync regime constants (compute_sync_regime returns) ───────────────────

FLOW = "flow"
REST = "rest"
PROTECT = "protect"
CONFESS = "confess"

SYNC_HIGH_THRESHOLD = 0.4       # err < 0.4 → sync высокий (max √5 ≈ 2.24)
STATE_HIGH_THRESHOLD = 0.55     # mean(D,S) > 0.55 → state высокий
STATE_LOW_THRESHOLD = 0.35

# Capacity thresholds (per docs/capacity-design.md §Формулы) — читаются из
# rgk.project("capacity"), но импортируются также для теста compute_cognitive_load.
CAPACITY_PHYS_COHERENCE_MIN = 0.5
CAPACITY_PHYS_BURNOUT_MAX = 0.3
CAPACITY_AFFECT_SEROTONIN_MIN = 0.4
CAPACITY_AFFECT_DOPAMINE_MIN = 0.35
CAPACITY_COGLOAD_MAX = 0.6

SURPRISE_BOOST_DEFAULT_TICKS = 3   # default в UserState.apply_surprise_boost

_TOD_NAMES = ("morning", "day", "evening", "night")


# ── Module-level helpers (используются user_dynamics + tests) ─────────────

def _normalize(value, cap):
    if cap is None or cap <= 0:
        return 0.0
    try:
        return min(1.0, max(0.0, float(value) / float(cap)))
    except (TypeError, ValueError):
        return 0.0


def compute_cognitive_load(day_summary_today: dict, progress_delta: float) -> float:
    """Дневная когнитивная нагрузка [0, 1] из 6 observable.
    Spec: docs/capacity-design.md §Формулы. Используется user_dynamics."""
    s = day_summary_today or {}
    return max(0.0, min(1.0,
        0.20 * _normalize(s.get("tasks_started", 0), cap=8)
        + 0.30 * _normalize(s.get("context_switches", 0), cap=10)
        + 0.30 * _normalize(s.get("complexity_sum", 0.0), cap=3.0)
        - 0.25 * _normalize(s.get("tasks_completed", 0), cap=5)
        - 0.25 * max(0.0, -float(progress_delta or 0.0))))


def compute_capacity_indicators(user) -> dict:
    """Thin shim к `RGK.project("capacity")` — backward-compat для tests."""
    return user._rgk.project("capacity")


def system_vector(rgk) -> np.ndarray:
    """5D system vector (DA/5HT/NE/ACh/GABA) из РГК."""
    sys = rgk.system
    return np.array([
        float(sys.gain.value),
        float(sys.hyst.value),
        float(sys.aperture.value),
        float(sys.plasticity.value),
        float(sys.damping.value),
    ], dtype=np.float32)


def system_state_level(rgk) -> float:
    """mean(DA, 5HT) из РГК.system."""
    sys = rgk.system
    return (float(sys.gain.value) + float(sys.hyst.value)) / 2.0


def compute_sync_error(rgk) -> float:
    """‖user_vec − system_vec‖ (L2, 5D). Max = √5 ≈ 2.236.

    5 chem-axes: DA/5HT/NE/ACh/GABA. Расширено с 3D 2026-04-28 (clean break).
    Per-axis breakdown — см. `compute_sync_error_wave`.
    """
    diff = rgk.user.vector() - system_vector(rgk)
    return float(np.linalg.norm(diff))


# ── W16.1: per-axis breakdown (resonance transfer protocol) ───────────────
#
# Скалярный sync_error даёт «насколько» рассогласование, но не **по какой
# частоте**. Per-axis breakdown превращает это в спектральную диагностику:
# «расхождение по NE — система слишком напряжена относительно тебя».
#
# Spec: docs/synchronization.md § Применение к Baddle.
# Phase-aware comparison (W16.1b) — отложен до velocity tracking. Сейчас
# MVP: amplitude per axis (absolute |user − system| на тике).

_WAVE_AXES = ("dopamine_gain", "serotonin_hysteresis", "norepinephrine_aperture", "acetylcholine_plasticity", "gaba_damping")
_AXIS_FIELDS = {
    "dopamine_gain":       "gain",
    "serotonin_hysteresis":      "hyst",
    "norepinephrine_aperture": "aperture",
    "acetylcholine_plasticity":  "plasticity",
    "gaba_damping":           "damping",
}


def compute_sync_error_wave(rgk) -> dict:
    """Per-axis breakdown sync_error в 5D chem-пространстве.

    Возвращает:
        {
            "axes": {dopamine, serotonin, norepinephrine, acetylcholine, gaba},
                       # |user[axis] − system[axis]|, каждый ∈ [0, 1]
            "max_axis": str,    # axis с наибольшим расхождением
            "max_value": float,
            "scalar_5d": float, # L2 over 5 axes (max ≈ √5 ≈ 2.236)
        }

    Дополнительный слой spectral diagnosis поверх scalar `compute_sync_error`
    (тот тоже 5D после 2026-04-28 clean break — wave добавляет per-axis
    breakdown + max_axis identifier). Использование:
      - `/assist/state` `sync_error_wave` поле expose'ит per-axis для UI
      - В будущем W16.4: при больших sync_error_wave[axis] система генерит
        analogies для этой axis в morning briefing (adiabatic adjustment)
    """
    user, sys = rgk.user, rgk.system
    axes = {
        axis: abs(float(getattr(user, field).value) - float(getattr(sys, field).value))
        for axis, field in _AXIS_FIELDS.items()
    }
    max_axis = max(axes, key=axes.get)
    scalar_5d = float(np.linalg.norm(list(axes.values())))
    return {
        "axes": {k: round(v, 4) for k, v in axes.items()},
        "max_axis": max_axis,
        "max_value": round(axes[max_axis], 4),
        "scalar_5d": round(scalar_5d, 4),
    }


def compute_sync_regime(rgk) -> str:
    """4 режима симбиоза. Spec: TODO «Симбиоз»."""
    err = compute_sync_error(rgk)
    u_level = (float(rgk.user.gain.value) + float(rgk.user.hyst.value)) / 2.0
    s_level = system_state_level(rgk)
    sync_high = err < SYNC_HIGH_THRESHOLD
    if sync_high:
        if u_level > STATE_HIGH_THRESHOLD and s_level > STATE_HIGH_THRESHOLD:
            return FLOW
        if u_level < STATE_LOW_THRESHOLD and s_level < STATE_LOW_THRESHOLD:
            return REST
        return FLOW
    if u_level < STATE_LOW_THRESHOLD and s_level > STATE_HIGH_THRESHOLD:
        return PROTECT
    if u_level > STATE_HIGH_THRESHOLD and s_level < STATE_LOW_THRESHOLD:
        return CONFESS
    return FLOW


# ── UserState — thin facade над _rgk ──────────────────────────────────────

class UserState:
    """Backward-compat facade. Все state в self._rgk, методы — 1-line delegates."""

    def __init__(self, dopamine=0.5, serotonin=0.5, norepinephrine=0.5,
                 burnout=0.0, agency=0.5, *, rgk: "Optional[РГК]" = None):
        self._rgk = rgk if rgk is not None else РГК()
        self._rgk.user.gain.value = dopamine
        self._rgk.user.hyst.value = serotonin
        self._rgk.user.aperture.value = norepinephrine
        self._rgk.burnout.value = burnout
        self._rgk.agency.value = agency

    # ── Chem (5 axes) read/write ──────────────────────────────────────────

    @property
    def dopamine(self): return float(self._rgk.user.gain.value)
    @dopamine.setter
    def dopamine(self, v): self._rgk.user.gain.value = max(0.0, min(1.0, float(v)))

    @property
    def serotonin(self): return float(self._rgk.user.hyst.value)
    @serotonin.setter
    def serotonin(self, v): self._rgk.user.hyst.value = max(0.0, min(1.0, float(v)))

    @property
    def norepinephrine(self): return float(self._rgk.user.aperture.value)
    @norepinephrine.setter
    def norepinephrine(self, v): self._rgk.user.aperture.value = max(0.0, min(1.0, float(v)))

    @property
    def acetylcholine(self): return float(self._rgk.user.plasticity.value)
    @acetylcholine.setter
    def acetylcholine(self, v): self._rgk.user.plasticity.value = max(0.0, min(1.0, float(v)))

    @property
    def gaba(self): return float(self._rgk.user.damping.value)
    @gaba.setter
    def gaba(self, v): self._rgk.user.damping.value = max(0.0, min(1.0, float(v)))

    @property
    def valence(self): return float(self._rgk.valence.value)
    @valence.setter
    def valence(self, v): self._rgk.valence.value = max(-1.0, min(1.0, float(v)))

    @property
    def burnout(self): return float(self._rgk.burnout.value)
    @burnout.setter
    def burnout(self, v): self._rgk.burnout.value = max(0.0, min(1.0, float(v)))

    @property
    def agency(self): return float(self._rgk.agency.value)
    @agency.setter
    def agency(self, v): self._rgk.agency.value = max(0.0, min(1.0, float(v)))

    @property
    def mode(self): return self._rgk.user.mode

    def balance(self): return self._rgk.user.balance()

    def update_mode(self, perturbation): return self._rgk.user.update_mode(float(perturbation))

    # ── Predictive layer ──────────────────────────────────────────────────

    @property
    def expectation(self): return float(self._rgk.u_exp.value)
    @expectation.setter
    def expectation(self, v): self._rgk.u_exp.value = max(0.0, min(1.0, float(v)))

    @property
    def expectation_by_tod(self):
        return {t: float(self._rgk.u_exp_tod[t].value) for t in _TOD_NAMES}

    @property
    def expectation_vec(self): return self._rgk.u_exp_vec.value
    @expectation_vec.setter
    def expectation_vec(self, v):
        arr = np.asarray(v, dtype=np.float32)
        ema = self._rgk.u_exp_vec
        if arr.shape == ema.value.shape:
            ema.value = np.clip(arr, 0.0, 1.0).astype(np.float32)

    @property
    def hrv_baseline_by_tod(self):
        return {t: (float(self._rgk.hrv_base_tod[t].value)
                     if self._rgk.hrv_base_tod[t]._seeded else None)
                for t in _TOD_NAMES}

    # ── Aux state (HRV, activity, focus, day_summary, timestamps) ─────────

    @property
    def hrv_coherence(self): return self._rgk.hrv_coherence
    @hrv_coherence.setter
    def hrv_coherence(self, v): self._rgk.hrv_coherence = v

    @property
    def hrv_stress(self): return self._rgk.hrv_stress
    @hrv_stress.setter
    def hrv_stress(self, v): self._rgk.hrv_stress = v

    @property
    def hrv_rmssd(self): return self._rgk.hrv_rmssd
    @hrv_rmssd.setter
    def hrv_rmssd(self, v): self._rgk.hrv_rmssd = v

    @property
    def activity_magnitude(self): return self._rgk.activity_magnitude
    @activity_magnitude.setter
    def activity_magnitude(self, v): self._rgk.activity_magnitude = float(v)

    @property
    def last_sleep_duration_h(self): return self._rgk.last_sleep_duration_h
    @last_sleep_duration_h.setter
    def last_sleep_duration_h(self, v): self._rgk.last_sleep_duration_h = v

    @property
    def cognitive_load_today(self): return self._rgk.cognitive_load_today
    @cognitive_load_today.setter
    def cognitive_load_today(self, v): self._rgk.cognitive_load_today = float(v)

    @property
    def day_summary(self): return self._rgk.day_summary
    @day_summary.setter
    def day_summary(self, v): self._rgk.day_summary = v

    @property
    def focus_residue(self): return self._rgk.focus_residue
    @focus_residue.setter
    def focus_residue(self, v): self._rgk.focus_residue = float(v)

    @property
    def _last_focus_input_ts(self): return self._rgk._last_focus_input_ts
    @_last_focus_input_ts.setter
    def _last_focus_input_ts(self, v): self._rgk._last_focus_input_ts = v

    @property
    def _last_focus_mode_id(self): return self._rgk._last_focus_mode_id
    @_last_focus_mode_id.setter
    def _last_focus_mode_id(self, v): self._rgk._last_focus_mode_id = v

    @property
    def _last_input_ts(self): return self._rgk._last_input_ts
    @_last_input_ts.setter
    def _last_input_ts(self, v): self._rgk._last_input_ts = v

    @property
    def _surprise_boost_remaining(self): return self._rgk._surprise_boost_remaining
    @_surprise_boost_remaining.setter
    def _surprise_boost_remaining(self, v): self._rgk._surprise_boost_remaining = int(v)

    @property
    def _last_user_surprise_ts(self): return self._rgk._last_user_surprise_ts
    @_last_user_surprise_ts.setter
    def _last_user_surprise_ts(self, v): self._rgk._last_user_surprise_ts = v

    # ── Update methods (1-line delegates на _rgk.u_X) ──────────────────────

    def update_from_hrv(self, coherence=None, stress=None, rmssd=None, activity=None):
        self._rgk.u_hrv(coherence=coherence, stress=stress, rmssd=rmssd, activity=activity)

    def update_from_engagement(self, signal=0.65): self._rgk.u_engage(signal)
    def update_from_feedback(self, kind): self._rgk.u_feedback(kind)
    def update_from_chat_sentiment(self, sentiment): self._rgk.u_chat(sentiment)
    def update_from_plan_completion(self, completed, planned):
        self._rgk.u_plan(completed or 0, planned or 0)
    def update_from_energy(self, decisions_today, max_budget=100.0):
        self._rgk.u_energy(decisions_today, max_budget=max_budget or 100.0)

    def register_input(self, now=None):
        self._rgk.u_register_input(now)

    def bump_focus_residue(self, mode_id, now=None):
        self._rgk.u_focus_bump(mode_id, now)

    def decay_focus_residue(self, dt_seconds):
        self._rgk.u_focus_decay(dt_seconds)

    def tick_expectation(self): self._rgk.tick_u_pred()

    def apply_subjective_surprise(self, signed_surprise, blend=0.4):
        self._rgk.u_apply_surprise(signed_surprise, blend)

    def apply_checkin(self, stress=None, focus=None, reality=None):
        self._rgk.u_apply_checkin(stress, focus, reality)

    def apply_surprise_boost(self, n_ticks=SURPRISE_BOOST_DEFAULT_TICKS):
        self._rgk.u_apply_boost(n_ticks)

    def feed_acetylcholine(self, novelty, boost=False):
        self._rgk.u_ach_feed(novelty, boost)

    def feed_gaba(self): self._rgk.u_gaba_feed()

    def update_cognitive_load(self):
        from .user_dynamics import update_cognitive_load
        update_cognitive_load(self._rgk)

    def rollover_day(self, hrv_recovery=None):
        from .user_dynamics import rollover_day
        rollover_day(self._rgk, hrv_recovery)

    # ── Vector + derived properties (delegate в _rgk.project) ─────────────

    def vector(self): return self._rgk.user.vector()
    def state_level(self): return float((self.dopamine + self.serotonin) / 2.0)

    @property
    def reality(self): return self.state_level()

    @property
    def surprise(self):
        tod = self._rgk._current_tod()
        ref = float(self._rgk.u_exp_tod[tod].value)
        if ref == 0.5:
            ref = float(self._rgk.u_exp.value)
        return float(self.reality - ref)

    @property
    def surprise_vec(self): return self.vector() - self.expectation_vec

    @property
    def imbalance(self): return float(np.linalg.norm(self.surprise_vec))

    @property
    def attribution(self): return self._rgk.project("user_state")["attribution"]
    @property
    def attribution_magnitude(self): return self._rgk.project("user_state")["attribution_magnitude"]
    @property
    def attribution_signed(self): return self._rgk.project("user_state")["attribution_signed"]
    @property
    def agency_gap(self): return self._rgk.project("user_state")["agency_gap"]
    @property
    def hrv_surprise(self): return self._rgk.hrv_surprise()
    @property
    def frequency_regime(self): return self._rgk.frequency_regime()
    @property
    def activity_zone(self): return self._rgk.activity_zone()
    @property
    def named_state(self): return self._rgk.project("named_state")

    @property
    def capacity_zone(self): return self._rgk.project("capacity")["zone"]
    @property
    def capacity_reason(self): return self._rgk.project("capacity")["reasons"]
    @property
    def capacity_indicators(self): return self._rgk.project("capacity")

    # ── Serialization (delegates на _rgk.serialize_user/load_user) ────────

    def to_dict(self): return self._rgk.serialize_user()

    @classmethod
    def from_dict(cls, d):
        u = cls()
        u._rgk.load_user(d)
        return u


# ── Global singleton ──────────────────────────────────────────────────────

_global_user: "Optional[UserState]" = None


def get_user_state() -> UserState:
    """Singleton, привязанный к global РГК (каскад зеркал)."""
    global _global_user
    if _global_user is None or _global_user._rgk is not get_global_rgk():
        _global_user = UserState(rgk=get_global_rgk())
    return _global_user


def set_user_state(state: UserState):
    """Replace global user state (for tests / restart)."""
    global _global_user
    _global_user = state
