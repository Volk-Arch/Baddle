"""РГК — физическое ядро (Правило 6 в [docs/architecture-rules.md](../docs/architecture-rules.md)).

Subsтрат всех state+dynamics Baddle. UserState/Neurochem/ProtectiveFreeze —
thin facades с @property proxies (см. B0/B4 в memory snapshots).

Структура:
  1. `Resonator` — один резонатор: 5-chem (gain/hyst/aperture/plasticity/damping)
     + R/C bit + balance() + vector(). 5 параметров — gain~DA, hyst~5HT,
     aperture~NE, plasticity~ACh, damping~GABA.
  2. `РГК` — два связанных резонатора (user mirror + system mirror) +
     auxiliary state (valence/agency/burnout, predictive baselines, pressure
     accumulators, HRV/activity passthrough, day_summary, focus_residue,
     timestamps) + projectors через `project(domain)`.
  3. `get_global_rgk()` — singleton для production bootstrap (каскад зеркал
     = ОДНА пара резонаторов).

Запуск как скрипт:
    python -m src.rgk
печатает diff vs EXPECTED из tests/test_metric_identity.py — semantic
identity check для регрессий формул. OK = совпало с TOL=1e-5, DIFF/MISS =
диагностика «что изменилось». Используется как CLI-debug, не как тест.
"""
from __future__ import annotations

import time

import numpy as np

from ..ema import EMA, VectorEMA, Decays, TimeConsts


_TOD = ("morning", "day", "evening", "night")

# RPE (reward prediction error) — параметры s_outcome.
# Раньше дублировались в Neurochem.RPE_GAIN/RPE_WINDOW. Single source — здесь.
RPE_GAIN = 0.15      # как сильно dopamine сдвигается на единицу RPE
RPE_WINDOW = 20      # скользящее окно для baseline Δconfidence

# Freeze thresholds — параметры p_conflict (хроническое накопление conflict
# → активация защитного freeze flag). Раньше определены в ProtectiveFreeze
# class (TAU_STABLE/THETA_ACTIVE/THETA_RECOVERY), но never read — p_conflict
# хардкодил те же числа. Single source — здесь.
# Семантически отдельно от Resonator.THETA_ACT/REC (R/C mode bit).
FREEZE_TAU_STABLE = 0.6        # порог за которым d считается конфликтом
FREEZE_THETA_ACTIVE = 0.15     # вход во freeze (на conflict EMA steady-state)
FREEZE_THETA_RECOVERY = 0.08   # выход из freeze (гистерезис)


# ────────────────────────────────────────────────────────────────────────────
# 1. Resonator — один зеркальный контур
# ────────────────────────────────────────────────────────────────────────────

class Resonator:
    """5 chem-параметров + R/C bit + balance().

    Маппинг к legacy:
        gain        ~ DA  (амплитуда захвата, intersect intensity)
        hyst        ~ 5HT (ширина гистерезиса, гашение шума)
        aperture    ~ NE  (Q-фактор, ширина полосы)
        plasticity  ~ ACh (текучесть ткани, скорость перестройки)  ← новое
        damping     ~ GABA (демпфирование, стенки стоячей волны)   ← новое

    `vector()` возвращает (gain, hyst, aperture) — 3D-проекция в legacy
    (DA, 5HT, NE) для совместимости с sync_error и UI.

    `balance()` = (Gain × Aperture × Plasticity) / (Hysteresis × Damping).
    ≈ 1.0 → резонанс. > 1.5 → гиперрезонанс. < 0.5 → гипостабильность.

    `mode` — R (passive resonance) / C (counter-wave generation), переключается
    `update_mode(perturbation)` с гистерезисом (mirror ProtectiveFreeze).
    Для identity-теста не активируется; оставлено для иллюстрации концепта.
    """

    THETA_ACT = 0.15   # mirror ProtectiveFreeze
    THETA_REC = 0.08

    def __init__(self, decays: dict):
        self.gain       = EMA(0.5, decay=decays["gain"])
        self.hyst       = EMA(0.5, decay=decays["hyst"])
        self.aperture   = EMA(0.5, decay=decays["aperture"])
        self.plasticity = EMA(0.5, decay=decays.get("plasticity", 0.9))
        self.damping    = EMA(0.5, decay=decays.get("damping", 0.95))
        self.mode = "R"

    def vector(self) -> np.ndarray:
        """5D projection (DA, 5HT, NE, ACh, GABA) для sync_error.

        Ось: gain (DA), hyst (5HT), aperture (NE), plasticity (ACh), damping (GABA).
        Legacy 3D version (только первые три) удалена 2026-04-28 при clean break
        на полный chem-space. См. compute_sync_error / sync_error_wave.
        """
        return np.array([self.gain.value, self.hyst.value, self.aperture.value,
                         self.plasticity.value, self.damping.value],
                        dtype=np.float32)

    def balance(self) -> float:
        num = float(self.gain.value) * float(self.aperture.value) * float(self.plasticity.value)
        den = float(self.hyst.value) * float(self.damping.value)
        return num / max(den, 1e-6)

    def update_mode(self, perturbation: float) -> str:
        if self.mode == "R" and perturbation > self.THETA_ACT:
            self.mode = "C"
        elif self.mode == "C" and perturbation < self.THETA_REC:
            self.mode = "R"
        return self.mode


_USER_DECAYS = {
    "gain":       Decays.USER_DOPAMINE_ENGAGEMENT,    # 0.95
    "hyst":       Decays.USER_SEROTONIN_HRV,          # 0.9
    "aperture":   Decays.USER_NOREPINEPHRINE_HRV,     # 0.9
    "plasticity": 0.9,
    "damping":    0.95,
}
_SYS_DECAYS = {
    "gain":       Decays.NEURO_DOPAMINE,              # 0.9
    "hyst":       Decays.NEURO_SEROTONIN,             # 0.95
    "aperture":   Decays.NEURO_NOREPINEPHRINE,        # 0.9
    "plasticity": 0.9,
    "damping":    0.95,
}


# ────────────────────────────────────────────────────────────────────────────
# 2. РГК — пара связанных резонаторов + auxiliaries
# ────────────────────────────────────────────────────────────────────────────

class РГК:
    """Two coupled resonators (user mirror + system mirror) + auxiliaries.

    Каскад зеркал (docs/world-model.md): user-резонатор настроен на сигналы
    юзера (HRV, engagement, feedback), system — на динамику графа (distinct,
    weights). Coupling: sync_error = ‖user.vector() − system.vector()‖.

    AUXILIARY state (НЕ chem, НЕ в balance()):
        valence/agency/burnout — отдельные observable axes
        u_exp / u_exp_tod / u_exp_vec — predictive layer (Friston baselines)
        s_exp_vec — system self-prediction
        conflict / silence / imbalance / sync_fast / sync_slow — pressure
        accumulators (mirror ProtectiveFreeze)
        _rpe_hist, _fb — bespoke counters

    Это «не закрывается ядром РГК» — диагностика того что физическая
    модель РГК-spec покрывает резонанс+balance+R/C, но НЕ описывает
    откуда берутся valence (sentiment LLM), agency (план vs выполнение),
    burnout (usage счётчик), expectation (медленный baseline для PE),
    silence_pressure (linear timer), feedback streak.
    """

    def __init__(self):
        self.user   = Resonator(_USER_DECAYS)
        self.system = Resonator(_SYS_DECAYS)

        # Aux axes (отдельные observables — НЕ chem-параметры)
        self.valence = EMA(0.0, decay=Decays.USER_VALENCE_SENTIMENT, bounds=(-1.0, 1.0))
        self.agency  = EMA(0.5, decay=Decays.USER_AGENCY)
        self.burnout = EMA(0.0, decay=Decays.USER_BURNOUT_ENERGY)

        # Predictive baselines (Friston) — user side
        self.u_exp        = EMA(0.5, decay=Decays.EXPECTATION)
        self.u_exp_tod    = {t: EMA(0.5, decay=Decays.EXPECTATION) for t in _TOD}
        self.u_exp_vec    = VectorEMA([0.5, 0.5, 0.5, 0.5, 0.5], decay=Decays.EXPECTATION_VEC)
        self.hrv_base_tod = {t: EMA(0.0, decay=Decays.HRV_BASELINE,
                                     seed_on_first=True) for t in _TOD}

        # System self-prediction baseline
        self.s_exp_vec = VectorEMA([0.5, 0.5, 0.5, 0.5, 0.5], decay=Decays.SELF_EXPECTATION)

        # Pressure accumulators (mirror ProtectiveFreeze)
        self.conflict        = EMA(0.0, decay=Decays.NEURO_CONFLICT_ACCUMULATOR)
        self.silence_press   = 0.0
        self.imbalance_press = EMA(0.0, time_const=TimeConsts.IMBALANCE)
        self.sync_fast       = EMA(0.0, time_const=TimeConsts.SYNC_EMA_FAST)
        self.sync_slow       = EMA(0.0, time_const=TimeConsts.SYNC_EMA_SLOW)
        self.freeze_active   = False

        # Bespoke counters
        self._rpe_hist: list = []
        self.recent_rpe: float = 0.0
        self._fb = {"accepted": 0, "rejected": 0, "ignored": 0}

        # W16.1b: phase-aware sync_error_wave snapshot.
        # {ts, user: [5 floats], system: [5 floats]} | None.
        # Используется compute_sync_error_wave для velocity per axis (∂axis/∂t)
        # и detection phase mismatch (расходимся в фазе по этой оси).
        # Lazy update — refresh когда age >= PHASE_SNAPSHOT_MIN_AGE.
        self._phase_snapshot: dict | None = None

        # B4 Wave 2: user-side bespoke state (sensor passthrough + aggregates).
        # Перемещено из UserState чтобы projectors имели полный access к
        # источникам — frequency_regime/hrv_surprise/activity_zone требуют
        # эти поля. UserState facade переиспользует через @property proxies.
        self.hrv_coherence = None        # type: float | None
        self.hrv_stress = None           # type: float | None
        self.hrv_rmssd = None            # type: float | None
        self._activity_magnitude: float = 0.0
        self.last_sleep_duration_h = None  # type: float | None
        self.cognitive_load_today: float = 0.0
        self.day_summary: dict = {}
        self.focus_residue: float = 0.0
        self._last_focus_input_ts = None   # type: float | None
        self._last_focus_mode_id = None    # type: str | None
        self._last_input_ts = None         # type: float | None
        self._surprise_boost_remaining: int = 0
        self._last_user_surprise_ts = None # type: float | None

    # ── User feeds ────────────────────────────────────────────────────────

    def u_hrv(self, coherence=None, stress=None, rmssd=None, activity=None):
        # Save raw values на self (single source — раньше дублировано в
        # UserState.update_from_hrv через @property proxies). Без storage
        # frequency_regime() / activity_zone() возвращали бы flat / None.
        if rmssd is not None:
            self.hrv_rmssd = float(rmssd)
            if stress is None:
                stress = max(0.0, min(1.0, 1.0 - float(rmssd) / 80.0))
        if coherence is not None:
            self.hrv_coherence = max(0.0, min(1.0, float(coherence)))
            self.user.hyst.feed(self.hrv_coherence)
            self.hrv_base_tod[self._current_tod()].feed(self.hrv_coherence)
        if stress is not None:
            self.hrv_stress = max(0.0, min(1.0, float(stress)))
            self.user.aperture.feed(self.hrv_stress)
        if activity is not None:
            self.activity_magnitude = float(activity)  # clamp в setter
        self.tick_u_pred()

    def u_engage(self, signal: float = 0.65):
        self.user.gain.feed(max(0.0, min(1.0, float(signal))))

    _FB_KINDS = ("accepted", "rejected", "ignored")

    def u_feedback(self, kind: str):
        # Skip unknown kinds — раньше facade'ом, теперь в РГК (single source).
        if kind not in self._FB_KINDS:
            return
        self._fb[kind] = self._fb.get(kind, 0) + 1
        ov = Decays.USER_DOPAMINE_FEEDBACK
        if kind == "accepted":
            self.user.gain.feed(0.9, decay_override=ov)
            self.valence.feed(0.7, decay_override=ov)
        elif kind == "rejected":
            self.user.gain.feed(0.2, decay_override=ov)
            self.valence.feed(-0.7, decay_override=ov)
            # Bespoke: burnout bump + streak bias
            self.burnout.value = max(0.0, min(1.0, self.burnout.value + 0.05))
            diff = self._fb["rejected"] - self._fb["accepted"]
            if diff >= 3:
                self.valence.value = max(-1.0, min(1.0,
                    self.valence.value - 0.05 * min(5, diff - 2)))
        self.tick_u_pred()

    def u_chat(self, sentiment: float):
        self.valence.feed(max(-1.0, min(1.0, float(sentiment))))

    def u_plan(self, completed: int, planned: int):
        if planned > 0:
            self.agency.feed(max(0.0, min(1.0, completed / float(planned))))

    def u_energy(self, decisions: int, max_budget: float = 100.0):
        self.burnout.feed(min(1.0, max(0.0,
            float(decisions) * 6.0 / float(max_budget))))
        self.tick_u_pred()

    # ── User bespoke (focus residue, checkin, ACh/GABA feeders, boost) ─────

    def u_register_input(self, now=None):
        """User-event timestamp save. Используется для idle-timer / sync-seeking."""
        import time as _t
        self._last_input_ts = now if now is not None else _t.time()

    def u_focus_bump(self, mode_id, now=None):
        """+0.05 если rapid input (<30 сек) + 0.15 если mode switch.
        Tracking timer в _last_focus_input_ts, _last_focus_mode_id."""
        import time as _t
        if now is None:
            now = _t.time()
        if (self._last_focus_input_ts is not None
                and (now - self._last_focus_input_ts) < 30):
            self.focus_residue = min(1.0, self.focus_residue + 0.05)
        if (mode_id and self._last_focus_mode_id
                and mode_id != self._last_focus_mode_id):
            self.focus_residue = min(1.0, self.focus_residue + 0.15)
        self._last_focus_mode_id = mode_id or self._last_focus_mode_id
        self._last_focus_input_ts = now

    def u_focus_decay(self, dt_seconds: float):
        """−0.05/мин естественное затухание. dt<=0 → no-op."""
        if dt_seconds <= 0:
            return
        self.focus_residue = max(0.0,
            self.focus_residue - 0.05 * (dt_seconds / 60.0))

    def u_apply_surprise(self, signed_surprise: float, blend: float = 0.4):
        """Nudge expectation baseline из субъективного surprise observation.
        Алгебра: new_expectation = reality - signed_surprise (decay = 1-blend)."""
        sl = (float(self.user.gain.value) + float(self.user.hyst.value)) / 2.0
        target = max(0.0, min(1.0, sl - float(signed_surprise)))
        override = max(0.001, min(0.999, 1.0 - float(blend)))
        self.u_exp.feed(target, decay_override=override)
        self.u_exp_tod[self._current_tod()].feed(target, decay_override=override)

    def u_apply_checkin(self, stress=None, focus=None, reality=None):
        """Manual checkin: stress (0-100)→NE, focus (0-100)→5HT, reality (-2..+2)→valence.
        Aggressive decay overrides из Decays.CHECKIN_*."""
        if stress is not None:
            self.user.aperture.feed(float(stress) / 100.0,
                                     decay_override=Decays.CHECKIN_STRESS)
        if focus is not None:
            self.user.hyst.feed(float(focus) / 100.0,
                                 decay_override=Decays.CHECKIN_FOCUS)
        if reality is not None:
            self.valence.feed(float(reality) / 2.0,
                               decay_override=Decays.CHECKIN_VALENCE)

    def u_apply_boost(self, n_ticks: int = 3):
        """Trigger fast-decay режим на N tick'ов (модель мира юзера изменилась).
        Идемпотентно: продлевает счётчик если новое значение больше."""
        import time as _t
        n = max(0, int(n_ticks))
        if n > self._surprise_boost_remaining:
            self._surprise_boost_remaining = n
        self._last_user_surprise_ts = _t.time()

    def u_ach_feed(self, novelty: float, boost: bool = False):
        """Plasticity feeder. boost=True → bump до 0.85 c override 0.85."""
        sig = max(0.0, min(1.0, float(novelty)))
        if boost:
            self.user.plasticity.feed(max(sig, 0.85), decay_override=0.85)
        else:
            self.user.plasticity.feed(sig)

    def u_gaba_feed(self):
        """Damping feeder — derived из focus_residue (1.0 - focus_residue)."""
        sig = max(0.0, min(1.0, 1.0 - float(self.focus_residue)))
        self.user.damping.feed(sig)

    def tick_u_pred(self):
        # Surprise boost: если _surprise_boost_remaining > 0, fast-decay
        # override на N tick'ов (модель мира юзера изменилась). Раньше
        # логика жила только в UserState.tick_expectation; field — в РГК.
        if self._surprise_boost_remaining > 0:
            scalar_override = Decays.EXPECTATION_FAST
            vec_override = Decays.EXPECTATION_VEC_FAST
            self._surprise_boost_remaining -= 1
        else:
            scalar_override = None
            vec_override = None

        sl = (float(self.user.gain.value) + float(self.user.hyst.value)) / 2.0
        tod = self._current_tod()
        v = self.user.vector()

        if scalar_override is None:
            self.u_exp.feed(sl)
            self.u_exp_tod[tod].feed(sl)
            self.u_exp_vec.feed(v)
        else:
            self.u_exp.feed(sl, decay_override=scalar_override)
            self.u_exp_tod[tod].feed(sl, decay_override=scalar_override)
            self.u_exp_vec.feed(v, decay_override=vec_override)

    # ── System feeds ──────────────────────────────────────────────────────

    def s_graph(self, d=None, w_change=None, weights=None):
        if d is not None:
            self.system.gain.feed(max(0.0, min(1.0, float(d))))
        if w_change is not None:
            arr = np.asarray(list(w_change), dtype=np.float32)
            if arr.size:
                self.system.hyst.feed(max(0.0, 1.0 - float(np.std(arr))))
        if weights is not None:
            arr = np.asarray(list(weights), dtype=np.float32)
            if arr.size:
                arr = np.clip(arr, 1e-9, 1.0)
                tot = float(np.sum(arr))
                if tot > 0:
                    pn = arr / tot
                    ent = -float(np.sum(pn * np.log(pn + 1e-9)))
                    me = float(np.log(max(2, arr.size)))
                    if me > 0:
                        self.system.aperture.feed(min(1.0, ent / me))

    def tick_s_pred(self):
        self.s_exp_vec.feed(self.system.vector())

    def s_outcome(self, prior: float, posterior: float) -> float:
        actual = abs(float(posterior) - float(prior))
        predicted = (sum(self._rpe_hist) / len(self._rpe_hist)
                     if self._rpe_hist else actual)
        rpe = actual - predicted
        # Bespoke additive (не EMA — discrete bump)
        self.system.gain.value = max(0.0, min(1.0,
            self.system.gain.value + RPE_GAIN * rpe))
        self.recent_rpe = rpe
        self._rpe_hist.append(actual)
        if len(self._rpe_hist) > RPE_WINDOW:
            self._rpe_hist = self._rpe_hist[-RPE_WINDOW:]
        return rpe

    def s_ach_feed(self, node_creation_rate: float = 0.0,
                    bridge_quality: float = None):
        """Plasticity feeder для system. node_creation_rate ∈ [0,1] cap=10/h.
        bridge_quality ∈ [0,1] — если найден (override 0.9)."""
        rate_norm = max(0.0, min(1.0, float(node_creation_rate)))
        self.system.plasticity.feed(rate_norm)
        if bridge_quality is not None:
            bq = max(0.0, min(1.0, float(bridge_quality)))
            self.system.plasticity.feed(bq, decay_override=0.9)

    def s_gaba_feed(self, freeze_active: bool, embedding_scattering: float = None):
        """Damping feeder для system. freeze_active=True → 1.0 sig.
        embedding_scattering inverted (1 - scattering, override 0.95)."""
        sig = 1.0 if freeze_active else 0.0
        self.system.damping.feed(sig)
        if embedding_scattering is not None:
            inv = max(0.0, min(1.0, 1.0 - float(embedding_scattering)))
            self.system.damping.feed(inv, decay_override=0.95)

    def bayes_step(self, prior: float, d: float) -> float:
        """Signed NAND-Bayes: logit(post) = logit(prior) + γ · (1 − 2d)."""
        import math as _math
        prior = max(0.01, min(0.99, prior))
        log_prior = _math.log(prior / (1.0 - prior))
        log_post = log_prior + self.gamma() * (1.0 - 2.0 * d)
        posterior = 1.0 / (1.0 + _math.exp(-log_post))
        return round(max(0.01, min(0.99, posterior)), 3)

    # ── Pressure ──────────────────────────────────────────────────────────

    def p_conflict(self, d: float, serotonin=None):
        if d is None:
            return
        s = float(self.system.hyst.value) if serotonin is None else float(serotonin)
        sig = max(0.0, float(d) - FREEZE_TAU_STABLE) * max(0.0, 1.0 - s)
        self.conflict.feed(sig)
        if self.freeze_active and self.conflict.value < FREEZE_THETA_RECOVERY:
            self.freeze_active = False
        elif (not self.freeze_active) and self.conflict.value > FREEZE_THETA_ACTIVE:
            self.freeze_active = True

    def p_tick(self, dt: float, sync_err: float = 0.0, imbalance: float = 0.0):
        if dt <= 0:
            return
        self.silence_press = max(0.0, min(1.0,
            self.silence_press + dt / float(TimeConsts.SILENCE_RAMP)))
        self.imbalance_press.feed(abs(float(imbalance)), dt=dt)
        snorm = max(0.0, min(1.0, float(sync_err) / 1.732))
        self.sync_fast.feed(snorm, dt=dt)
        self.sync_slow.feed(snorm, dt=dt)

    def add_silence(self, delta: float):
        """User-event input: snижение (−) при активности, рост (+) — редко."""
        self.silence_press = max(0.0, min(1.0,
            self.silence_press + float(delta)))

    def combined_burnout(self, user_burnout: float = 0.0) -> float:
        """max(display_burnout, user_burnout) для _idle_multiplier:
        эмпатия к юзеру встроена. Если юзер устал, Baddle тоже тише."""
        ub = max(0.0, min(1.0, float(user_burnout or 0.0)))
        display = max(self.conflict.value, self.silence_press,
                       self.imbalance_press.value)
        return max(display, ub)

    def serialize_freeze(self) -> dict:
        """Freeze (pressure layer) snapshot для state.json. Симметрично
        старому ProtectiveFreeze.to_dict — keys preserved для backward-compat."""
        display = max(self.conflict.value, self.silence_press,
                       self.imbalance_press.value)
        return {
            "conflict_accumulator": round(self.conflict.value, 3),
            "silence_pressure":     round(self.silence_press, 3),
            "imbalance_pressure":   round(self.imbalance_press.value, 3),
            "sync_error_ema_fast":  round(self.sync_fast.value, 4),
            "sync_error_ema_slow":  round(self.sync_slow.value, 4),
            "display_burnout":      round(display, 3),
            "active":               bool(self.freeze_active),
        }

    def load_freeze(self, d: dict) -> None:
        """Restore freeze layer из state.json dump. Симметрично старому
        ProtectiveFreeze.from_dict."""
        self.conflict.value      = float(d.get("conflict_accumulator", 0.0))
        self.silence_press       = max(0.0, min(1.0, float(d.get("silence_pressure", 0.0))))
        self.imbalance_press.value = float(d.get("imbalance_pressure", 0.0))
        self.sync_fast.value     = float(d.get("sync_error_ema_fast", 0.0))
        self.sync_slow.value     = float(d.get("sync_error_ema_slow", 0.0))
        self.freeze_active       = bool(d.get("active", False))

    def serialize_system(self) -> dict:
        """System (Neurochem) snapshot для state.json. Симметрично старому
        Neurochem.to_dict — keys preserved."""
        return {
            "dopamine_gain":       round(float(self.system.gain.value), 3),
            "serotonin_hysteresis":      round(float(self.system.hyst.value), 3),
            "norepinephrine_aperture": round(float(self.system.aperture.value), 3),
            "acetylcholine_plasticity":  round(float(self.system.plasticity.value), 3),
            "gaba_damping":           round(float(self.system.damping.value), 3),
            "balance":        round(self.system.balance(), 3),
            "mode":           self.system.mode,
            "gamma":          round(self.gamma(), 3),
            "recent_rpe":     round(float(self.recent_rpe), 3),
            "expectation_vec": [round(float(x), 3) for x in self.s_exp_vec.value.tolist()],
            "self_imbalance": round(self.project("system")["self_imbalance"], 3),
            "_delta_history": [round(x, 3) for x in self._rpe_hist],
        }

    def load_system(self, d: dict) -> None:
        """Restore system layer из state.json dump. Симметрично старому
        Neurochem.from_dict (incl. Phase D 5-axis defaults для legacy)."""
        self.system.gain.value     = max(0.0, min(1.0, float(d.get("dopamine_gain", 0.5))))
        self.system.hyst.value     = max(0.0, min(1.0, float(d.get("serotonin_hysteresis", 0.5))))
        self.system.aperture.value = max(0.0, min(1.0, float(d.get("norepinephrine_aperture", 0.5))))
        self.system.plasticity.value = max(0.0, min(1.0, float(d.get("acetylcholine_plasticity", 0.5))))
        self.system.damping.value  = max(0.0, min(1.0, float(d.get("gaba_damping", 0.5))))
        self.recent_rpe = float(d.get("recent_rpe", 0.0))
        self._rpe_hist = list(d.get("_delta_history", []))
        vec = d.get("expectation_vec")
        if isinstance(vec, (list, tuple)) and len(vec) == 5:
            try:
                arr = np.array([float(x) for x in vec], dtype=np.float32)
                self.s_exp_vec.value = np.clip(arr, 0.0, 1.0).astype(np.float32)
            except Exception:
                pass

    def serialize_user(self) -> dict:
        """User layer snapshot для state.json. Симметрично старому
        UserState.to_dict — keys preserved (incl. derived projections)."""
        proj = self.project("user_state")
        named = self.project("named_state")
        az = self.activity_zone()
        return {
            "dopamine_gain":       round(float(self.user.gain.value), 3),
            "serotonin_hysteresis":      round(float(self.user.hyst.value), 3),
            "norepinephrine_aperture": round(float(self.user.aperture.value), 3),
            "acetylcholine_plasticity":  round(float(self.user.plasticity.value), 3),
            "gaba_damping":           round(float(self.user.damping.value), 3),
            "balance":        round(self.user.balance(), 3),
            "mode":           self.user.mode,
            "burnout":        round(float(self.burnout.value), 3),
            "agency":         round(float(self.agency.value), 3),
            "valence":        round(float(self.valence.value), 3),
            "expectation":    round(float(self.u_exp.value), 3),
            "expectation_by_tod": {t: round(float(self.u_exp_tod[t].value), 3) for t in _TOD},
            "expectation_vec": [round(float(x), 3) for x in self.u_exp_vec.value.tolist()],
            "hrv_baseline_by_tod": {t: (round(float(self.hrv_base_tod[t].value), 3)
                                          if self.hrv_base_tod[t]._seeded else None)
                                      for t in _TOD},
            "reality":   round((proj["dopamine_gain"] + proj["serotonin_hysteresis"]) / 2.0, 3),
            "surprise":  round(float(proj["surprise"]), 3),
            "imbalance": round(float(proj["imbalance"]), 3),
            "attribution":            proj["attribution"],
            "attribution_magnitude":  round(float(proj["attribution_magnitude"]), 3),
            "attribution_signed":     round(float(proj["attribution_signed"]), 3),
            "agency_gap":             round(float(proj["agency_gap"]), 3),
            "hrv_surprise":           round(float(proj["hrv_surprise"]), 3),
            "activity_magnitude":     round(float(self.activity_magnitude), 3),
            "activity_zone":          az,
            "named_state":            {"key": named["key"], "label": named["label"],
                                          "advice": named["advice"], "emoji": named.get("emoji", "")},
            "frequency_regime":       self.frequency_regime(),
            "focus_residue":          round(float(self.focus_residue), 3),
            "cognitive_load_today":   round(float(self.cognitive_load_today), 3),
            "capacity_zone":          self.project("capacity")["zone"],
            "capacity_reason":        self.project("capacity")["reasons"],
            "day_summary":            self.day_summary,
            "hrv":                    {"coherence": self.hrv_coherence,
                                         "stress":    self.hrv_stress,
                                         "rmssd":     self.hrv_rmssd}
                                     if self.hrv_coherence is not None else None,
            "_fb":                    dict(self._fb),
        }

    def load_user(self, d: dict) -> None:
        """Restore user layer из state.json dump. Симметрично UserState.from_dict."""
        self.user.gain.value     = max(0.0, min(1.0, float(d.get("dopamine_gain", 0.5))))
        self.user.hyst.value     = max(0.0, min(1.0, float(d.get("serotonin_hysteresis", 0.5))))
        self.user.aperture.value = max(0.0, min(1.0, float(d.get("norepinephrine_aperture", 0.5))))
        self.user.plasticity.value = max(0.0, min(1.0, float(d.get("acetylcholine_plasticity", 0.5))))
        self.user.damping.value  = max(0.0, min(1.0, float(d.get("gaba_damping", 0.5))))
        self.burnout.value       = max(0.0, min(1.0, float(d.get("burnout", 0.0))))
        self.agency.value        = max(0.0, min(1.0, float(d.get("agency", 0.5))))
        self.valence.value       = max(-1.0, min(1.0, float(d.get("valence", 0.0))))
        self.u_exp.value         = max(0.0, min(1.0, float(d.get("expectation", 0.5))))
        self.activity_magnitude  = float(d.get("activity_magnitude", 0.0))  # clamp в setter
        self.focus_residue       = max(0.0, min(1.0, float(d.get("focus_residue", 0.0))))
        self.cognitive_load_today = max(0.0, min(1.0, float(d.get("cognitive_load_today", 0.0))))
        ds = d.get("day_summary")
        if isinstance(ds, dict):
            self.day_summary = {str(k): dict(v) for k, v in ds.items()
                                  if isinstance(v, dict)}
        # TOD-scoped baselines (optional; legacy без них = defaults)
        tod_map = d.get("expectation_by_tod") or {}
        if isinstance(tod_map, dict):
            for t in _TOD:
                if t in tod_map:
                    try:
                        self.u_exp_tod[t].value = max(0.0, min(1.0, float(tod_map[t])))
                    except Exception:
                        pass
        vec = d.get("expectation_vec")
        if isinstance(vec, (list, tuple)) and len(vec) == 5:
            try:
                arr = np.array([float(x) for x in vec], dtype=np.float32)
                self.u_exp_vec.value = np.clip(arr, 0.0, 1.0).astype(np.float32)
            except Exception:
                pass
        hrv_base = d.get("hrv_baseline_by_tod") or {}
        if isinstance(hrv_base, dict):
            for t in _TOD:
                if t not in hrv_base:
                    continue
                v = hrv_base[t]
                ema = self.hrv_base_tod[t]
                if v is None:
                    ema.value = 0.0
                    ema._seeded = False
                else:
                    try:
                        ema.value = max(0.0, min(1.0, float(v)))
                        ema._seeded = True
                    except Exception:
                        pass
        hrv = d.get("hrv") or {}
        self.hrv_coherence = hrv.get("coherence")
        self.hrv_stress    = hrv.get("stress")
        self.hrv_rmssd     = hrv.get("rmssd")
        # Feedback counter restore
        fb_dump = d.get("_fb")
        if isinstance(fb_dump, dict):
            for k in self._FB_KINDS:
                if k in fb_dump:
                    try:
                        self._fb[k] = int(fb_dump[k])
                    except (TypeError, ValueError):
                        pass

    # ── Coupling + projections ────────────────────────────────────────────

    def sync_error(self) -> float:
        return float(np.linalg.norm(self.user.vector() - self.system.vector()))

    def sync_error_wave(self) -> dict:
        """Per-axis breakdown sync_error (5D, MVP W16.1).

        Spec: docs/synchronization.md. Возвращает dict с axes/max_axis/scalar_5d
        + phase data (W16.1b) если есть snapshot.
        Spectral diagnosis — «по какой частоте расхождение», не только «насколько».
        """
        from .user_state import compute_sync_error_wave
        return compute_sync_error_wave(self)

    # W16.1b: phase tracking parameters
    PHASE_SNAPSHOT_MIN_AGE_S = 30.0   # ниже — не обновляем snapshot (velocity на 30s окне)
    PHASE_VELOCITY_NOISE = 0.005      # |velocity| ниже — считаем стационарным

    def phase_per_axis(self) -> dict:
        """Per-axis velocity (∂axis/∂t) для user и system + phase mismatch flags.

        Возвращает:
            {
              "<axis>": {
                  "user_velocity":   float (signed),
                  "system_velocity": float (signed),
                  "mismatch":        bool — расходимся в фазе по этой оси
                                       (signs opposite AND обе >> noise),
              },
              ...
              "_snapshot_age_s": float | None,   # null если первый call
              "_mismatch_count": int,            # сколько axes в фазовом конфликте
            }

        Lazy snapshot: при age >= PHASE_SNAPSHOT_MIN_AGE_S обновляется.
        Первый call возвращает все velocities = 0 (нет baseline).
        """
        from .user_state import _AXIS_FIELDS
        now = time.time()
        cur_user = [float(getattr(self.user, f).value) for f in _AXIS_FIELDS.values()]
        cur_sys  = [float(getattr(self.system, f).value) for f in _AXIS_FIELDS.values()]

        snap = self._phase_snapshot
        result: dict = {}
        if snap is None:
            # First call — нет baseline. Создаём snapshot, возвращаем zeros.
            self._phase_snapshot = {"ts": now, "user": cur_user, "system": cur_sys}
            for axis in _AXIS_FIELDS.keys():
                result[axis] = {"user_velocity": 0.0, "system_velocity": 0.0,
                                "mismatch": False}
            result["_snapshot_age_s"] = None
            result["_mismatch_count"] = 0
            return result

        age = max(1e-3, now - float(snap["ts"]))
        mismatch_count = 0
        for i, axis in enumerate(_AXIS_FIELDS.keys()):
            uv = (cur_user[i] - snap["user"][i]) / age
            sv = (cur_sys[i]  - snap["system"][i]) / age
            mismatch = (uv * sv < 0
                        and abs(uv) > self.PHASE_VELOCITY_NOISE
                        and abs(sv) > self.PHASE_VELOCITY_NOISE)
            if mismatch:
                mismatch_count += 1
            result[axis] = {
                "user_velocity":   round(uv, 5),
                "system_velocity": round(sv, 5),
                "mismatch":        mismatch,
            }
        result["_snapshot_age_s"] = round(age, 1)
        result["_mismatch_count"] = mismatch_count

        # Refresh snapshot если состарилось — следующий call даст velocity на новом окне
        if age >= self.PHASE_SNAPSHOT_MIN_AGE_S:
            self._phase_snapshot = {"ts": now, "user": cur_user, "system": cur_sys}
        return result

    def gamma(self) -> float:
        ne = float(self.system.aperture.value)
        s  = float(self.system.hyst.value)
        return 2.0 + 3.0 * ne * (1.0 - s)

    _AXIS_NAMES = ("dopamine_gain", "serotonin_hysteresis", "norepinephrine_aperture", "acetylcholine_plasticity", "gaba_damping")

    # ── B4 Wave 2: non-chem state with clamped setters ─────────────────────

    @property
    def activity_magnitude(self) -> float:
        return self._activity_magnitude

    @activity_magnitude.setter
    def activity_magnitude(self, v):
        # Clamp [0, 5] в setter — раньше через UserState._clamp() извне.
        self._activity_magnitude = max(0.0, min(5.0, float(v)))

    # ── B4 Wave 2: non-chem derivations (HRV / activity bespoke в проекторах)

    def _current_tod(self) -> str:
        """Time-of-day для TOD-scoped baselines. 4 окна:
        morning [5,12), day [12,18), evening [18,23), night otherwise.
        Single source — раньше дублировался в UserState._current_tod
        с РАСХОЖДЕНИЕМ нарезки (5-11/11-17/17-23) — fix для h=11,17."""
        import datetime as _dt
        h = _dt.datetime.now().hour
        if 5 <= h < 12:
            return "morning"
        if 12 <= h < 18:
            return "day"
        if 18 <= h < 23:
            return "evening"
        return "night"

    def hrv_surprise(self) -> float:
        """|hrv_coherence − baseline[current_tod]|. Физический PE от тела.
        0 если HRV не запущен или baseline не seeded."""
        if self.hrv_coherence is None:
            return 0.0
        tod = self._current_tod()
        ema = self.hrv_base_tod[tod]
        if not ema._seeded:
            return 0.0
        return abs(float(self.hrv_coherence) - float(ema.value))

    def frequency_regime(self) -> str:
        """Несущая частота: long_wave (coh>0.6 + rmssd>30 + ne<0.5) /
        short_wave (coh<0.4 OR ne>0.75) / mixed / flat (no HRV).
        См. docs/hrv-design.md § Frequency regime."""
        if self.hrv_coherence is None:
            return "flat"
        hrv = float(self.hrv_coherence)
        rmssd = float(self.hrv_rmssd or 0)
        ne = float(self.user.aperture.value)
        if hrv > 0.6 and rmssd > 30 and ne < 0.5:
            return "long_wave"
        if hrv < 0.4 or ne > 0.75:
            return "short_wave"
        return "mixed"

    def activity_zone(self) -> dict:
        """4-зонная classification (HRV coherence × activity_magnitude).
        recovery / stress_rest / healthy_load / overload (или None если HRV пуст)."""
        if self.hrv_coherence is None:
            return {"key": None, "label": None, "advice": None}
        # Constants mirror UserState — single source of truth in projector.
        ACTIVITY_THRESHOLD = 0.5
        COHERENCE_HEALTHY = 0.5
        active = self.activity_magnitude >= ACTIVITY_THRESHOLD
        hrv_ok = self.hrv_coherence >= COHERENCE_HEALTHY
        if not active and hrv_ok:
            return {"key": "recovery", "label": "Восстановление",
                    "advice": "Хорошее время для отдыха / медитации.",
                    "emoji": "🟢"}
        if not active and not hrv_ok:
            return {"key": "stress_rest", "label": "Стресс в покое",
                    "advice": "Подыши минуту. Тело в напряжении без физической нагрузки.",
                    "emoji": "🟡"}
        if active and hrv_ok:
            return {"key": "healthy_load", "label": "Здоровая нагрузка",
                    "advice": "Ритм хороший. Используй для дела.",
                    "emoji": "🔵"}
        return {"key": "overload", "label": "Перегрузка",
                "advice": "Сильная активность + низкое HRV = риск overtraining. Снизь темп.",
                "emoji": "🔴"}

    def project(self, domain: str) -> dict:
        """Spec §7: «все 13 detectors + capacity + regime + UI → projections».

        B4 Wave 1 (2026-04-25): extended user_state с chem-only derivations,
        которые уже live в РГК (Phase D + B0): acetylcholine/gaba/balance/mode +
        attribution/attribution_magnitude/attribution_signed + agency_gap.
        """
        if domain == "user_state":
            ev = self.u_exp_vec.value
            cur_tod = self.u_exp_tod[self._current_tod()].value
            ref = cur_tod if abs(cur_tod - 0.5) > 1e-9 else self.u_exp.value
            sl = (self.user.gain.value + self.user.hyst.value) / 2.0
            vec = self.user.vector()
            surprise_vec = vec - ev
            mag = float(np.linalg.norm(surprise_vec))
            if mag < 0.05:
                attribution = "none"
                attribution_signed = 0.0
            else:
                idx = int(np.argmax(np.abs(surprise_vec)))
                attribution = self._AXIS_NAMES[idx]
                attribution_signed = float(surprise_vec[idx])
            return {
                "dopamine_gain":       float(self.user.gain.value),
                "serotonin_hysteresis":      float(self.user.hyst.value),
                "norepinephrine_aperture": float(self.user.aperture.value),
                "acetylcholine_plasticity":  float(self.user.plasticity.value),
                "gaba_damping":           float(self.user.damping.value),
                "balance":        self.user.balance(),
                "mode":           self.user.mode,
                "valence":        float(self.valence.value),
                "burnout":        float(self.burnout.value),
                "agency":         float(self.agency.value),
                "agency_gap":     max(0.0, 1.0 - float(self.agency.value)),
                "expectation":    float(self.u_exp.value),
                "expectation_by_tod": {t: float(self.u_exp_tod[t].value) for t in _TOD},
                "expectation_vec":    [float(x) for x in ev.tolist()],
                "hrv_baseline_by_tod": {t: (float(self.hrv_base_tod[t].value)
                                            if self.hrv_base_tod[t]._seeded else None)
                                        for t in _TOD},
                "vector":    [float(x) for x in vec.tolist()],
                "surprise":  float(sl - ref),
                "surprise_vec": [float(x) for x in surprise_vec.tolist()],
                "imbalance": mag,
                "attribution":            attribution,
                "attribution_magnitude":  float(np.max(np.abs(surprise_vec))) if mag >= 0.05 else 0.0,
                "attribution_signed":     attribution_signed,
                # B4 Wave 2: non-chem derivations
                "hrv_surprise":           self.hrv_surprise(),
                "frequency_regime":       self.frequency_regime(),
                "activity_zone":          self.activity_zone(),
                "activity_magnitude":     float(self.activity_magnitude),
                "focus_residue":          float(self.focus_residue),
                "cognitive_load_today":   float(self.cognitive_load_today),
            }
        if domain == "system":
            sv = self.system.vector()
            return {
                "dopamine_gain":       float(self.system.gain.value),
                "serotonin_hysteresis":      float(self.system.hyst.value),
                "norepinephrine_aperture": float(self.system.aperture.value),
                "acetylcholine_plasticity":  float(self.system.plasticity.value),
                "gaba_damping":           float(self.system.damping.value),
                "balance":        self.system.balance(),
                "mode":           self.system.mode,
                "expectation_vec": [float(x) for x in self.s_exp_vec.value.tolist()],
                "gamma":           self.gamma(),
                "recent_rpe":      float(self.recent_rpe),
                "self_imbalance":  float(np.linalg.norm(sv - self.s_exp_vec.value)),
                "vector":          [float(x) for x in sv.tolist()],
            }
        if domain == "freeze":
            return {
                "conflict_accumulator": float(self.conflict.value),
                "silence_pressure":     float(self.silence_press),
                "imbalance_pressure":   float(self.imbalance_press.value),
                "sync_error_ema_fast":  float(self.sync_fast.value),
                "sync_error_ema_slow":  float(self.sync_slow.value),
                "display_burnout":      float(max(self.conflict.value,
                                                   self.silence_press,
                                                   self.imbalance_press.value)),
                "active":               bool(self.freeze_active),
            }
        if domain == "balance":
            return {
                "user":   self.user.balance(),
                "system": self.system.balance(),
                "user_mode":   self.user.mode,
                "system_mode": self.system.mode,
            }
        if domain == "named_state":
            # 8-region РГК-карта по 5D chem профилю. UserState.named_state
            # property делегирует сюда. См. user_state_map.py.
            from ..user_state_map import nearest_named_state
            return nearest_named_state(
                da=float(self.user.gain.value),
                s=float(self.user.hyst.value),
                ne=float(self.user.aperture.value),
                ach=float(self.user.plasticity.value),
                gaba=float(self.user.damping.value),
            )
        if domain == "capacity":
            # Phase C 3-zone модель: phys/affect/cogload индикаторы + reasons.
            # UserState.capacity_* properties делегируют сюда; module-level
            # compute_capacity_indicators(user) — thin shim к project("capacity").
            from .user_state import (
                CAPACITY_PHYS_COHERENCE_MIN, CAPACITY_PHYS_BURNOUT_MAX,
                CAPACITY_AFFECT_SEROTONIN_MIN, CAPACITY_AFFECT_DOPAMINE_MIN,
                CAPACITY_COGLOAD_MAX,
            )
            serotonin = float(self.user.hyst.value)
            burnout   = float(self.burnout.value)
            dopamine  = float(self.user.gain.value)
            cogload   = float(self.cognitive_load_today)
            coh       = self.hrv_coherence
            reasons: list[str] = []
            if coh is not None:
                coh_ok = float(coh) > CAPACITY_PHYS_COHERENCE_MIN
                burnout_ok = burnout < CAPACITY_PHYS_BURNOUT_MAX
                phys_ok = coh_ok and burnout_ok
                if not coh_ok:    reasons.append("hrv_coherence_low")
                if not burnout_ok: reasons.append("burnout_high")
            else:
                burnout_ok = burnout < CAPACITY_PHYS_BURNOUT_MAX
                phys_ok = burnout_ok
                if not burnout_ok: reasons.append("burnout_high")
            sero_ok = serotonin > CAPACITY_AFFECT_SEROTONIN_MIN
            da_ok   = dopamine  > CAPACITY_AFFECT_DOPAMINE_MIN
            affect_ok = sero_ok and da_ok
            if not sero_ok: reasons.append("serotonin_low")
            if not da_ok:   reasons.append("dopamine_low")
            cogload_ok = cogload < CAPACITY_COGLOAD_MAX
            if not cogload_ok: reasons.append("cogload_high")
            n_ok = sum([phys_ok, affect_ok, cogload_ok])
            zone = "green" if n_ok == 3 else "yellow" if n_ok == 2 else "red"
            return {
                "phys_ok": phys_ok,
                "affect_ok": affect_ok,
                "cogload_ok": cogload_ok,
                "reasons": reasons,
                "zone": zone,
            }
        return {}


# ────────────────────────────────────────────────────────────────────────────
# 3. Singleton — каскад зеркал = ОДНА пара резонаторов.
# Production bootstrap (`get_user_state()` + `CognitiveState.__init__`)
# использует `get_global_rgk()`, чтобы UserState/Neurochem/ProtectiveFreeze
# делили один объект. Tests НЕ используют global — создают independent
# РГК через UserState() / Neurochem() / ProtectiveFreeze() без `rgk=` arg.
# ────────────────────────────────────────────────────────────────────────────

_GLOBAL_RGK: "РГК | None" = None


def get_global_rgk() -> "РГК":
    """Singleton РГК. Lazy init на первом вызове. Production bootstrap
    (`get_user_state()` + `CognitiveState.__init__`) передаёт этот объект как
    `rgk=` параметр всем трём facades — UserState/Neurochem/ProtectiveFreeze
    делят один резонатор."""
    global _GLOBAL_RGK
    if _GLOBAL_RGK is None:
        _GLOBAL_RGK = РГК()
    return _GLOBAL_RGK


def reset_global_rgk() -> "РГК":
    """Reset singleton к fresh РГК. Для production restart / test fixture."""
    global _GLOBAL_RGK
    _GLOBAL_RGK = РГК()
    return _GLOBAL_RGK


# ────────────────────────────────────────────────────────────────────────────
# 4. Identity diagnostic — прогон фиксированного event sequence
# ────────────────────────────────────────────────────────────────────────────

def _run_identity_sequence() -> "РГК":
    """Reproduce фиксированный event sequence из tests/test_metric_identity.py.

    Цель: после прогона значения через `project()` сравнить с EXPECTED
    snapshot (зафиксирован 2026-04-24 на legacy-коде).
    """
    r = РГК()
    # Fixate TOD для repeatability (вместо реального datetime.now()).
    r._current_tod = lambda: "day"

    # User events
    for _ in range(5):
        r.u_hrv(coherence=0.6, stress=0.3, rmssd=40.0)
    for _ in range(10):
        r.u_engage(signal=0.65)
    for _ in range(3):
        r.u_feedback("accepted")
    for _ in range(2):
        r.u_feedback("rejected")
    for s in (0.4, 0.2, -0.1, 0.5, 0.3):
        r.u_chat(s)
    r.u_plan(completed=3, planned=5)
    r.u_energy(decisions=20)
    for _ in range(10):
        r.tick_u_pred()

    # System events
    for d, wc, w in [
        (0.4,  [0.1, -0.05, 0.2],   [0.3,  0.4,  0.3]),
        (0.3,  [0.05, -0.02, 0.1],  [0.35, 0.3,  0.35]),
        (0.5,  [0.0, 0.0, 0.1],     [0.25, 0.45, 0.3]),
        (0.2,  [-0.05, 0.1, -0.02], [0.4,  0.3,  0.3]),
        (0.45, [0.1, 0.05, -0.05],  [0.3,  0.3,  0.4]),
    ]:
        r.s_graph(d=d, w_change=wc, weights=w)
    for _ in range(3):
        r.tick_s_pred()
    r.s_outcome(prior=0.5, posterior=0.7)
    r.s_outcome(prior=0.6, posterior=0.55)

    # Pressure events
    r.p_conflict(d=0.7, serotonin=0.4)
    r.p_conflict(d=0.65, serotonin=0.45)
    for _ in range(20):
        r.p_tick(dt=60.0, sync_err=0.5, imbalance=0.3)

    return r


# EXPECTED captured 2026-04-24 на legacy commit (mirror tests/test_metric_identity.py)
EXPECTED_USER = {
    "dopamine_gain": 0.566345, "serotonin_hysteresis": 0.540951, "norepinephrine_aperture": 0.418098,
    "valence": 0.103027, "burnout": 0.19, "agency": 0.505,
    "expectation": 0.517369,
    "expectation_by_tod": {"morning": 0.5, "day": 0.517369,
                            "evening": 0.5, "night": 0.5},
    "expectation_vec": [0.529848, 0.518119, 0.463763, 0.5, 0.5],
    "hrv_baseline_by_tod": {"morning": None, "day": 0.6,
                              "evening": None, "night": None},
    "vector": [0.566345, 0.540951, 0.418098, 0.5, 0.5],
    "surprise": 0.03628, "imbalance": 0.062759,
}
EXPECTED_SYS = {
    "dopamine_gain": 0.424359, "serotonin_hysteresis": 0.598492, "norepinephrine_aperture": 0.700003,
    "expectation_vec": [0.495359, 0.508601, 0.517466, 0.5, 0.5],
    "gamma": 2.84317, "recent_rpe": -0.15, "self_imbalance": 0.215502,
    "vector": [0.424359, 0.598492, 0.700003, 0.5, 0.5],
}
EXPECTED_FREEZE = {
    "conflict_accumulator": 0.004225, "silence_pressure": 0.001984,
    "imbalance_pressure": 0.004138, "sync_error_ema_fast": 0.081833,
    "sync_error_ema_slow": 0.001333, "display_burnout": 0.004225,
    "active": False,
}


def _diff(label: str, got, exp, tol: float = 1e-5) -> tuple[int, int]:
    """Recursive comparator. Returns (n_ok, n_diff)."""
    if isinstance(exp, dict):
        ok = bad = 0
        for k, v in exp.items():
            sub_ok, sub_bad = _diff(f"{label}.{k}",
                                     got.get(k) if isinstance(got, dict) else None, v, tol)
            ok += sub_ok; bad += sub_bad
        return ok, bad
    if isinstance(exp, (list, tuple)):
        ok = bad = 0
        for i, e in enumerate(exp):
            g = got[i] if isinstance(got, (list, tuple)) and i < len(got) else None
            sub_ok, sub_bad = _diff(f"{label}[{i}]", g, e, tol)
            ok += sub_ok; bad += sub_bad
        return ok, bad
    if exp is None or got is None:
        same = (exp is None and got is None)
        tag = "OK  " if same else "MISS"
        print(f"  [{tag}] {label:50s}  got={got}  exp={exp}")
        return (1, 0) if same else (0, 1)
    if isinstance(exp, bool):
        same = bool(got) == exp
        tag = "OK  " if same else "DIFF"
        print(f"  [{tag}] {label:50s}  got={got}  exp={exp}")
        return (1, 0) if same else (0, 1)
    try:
        d = abs(float(got) - float(exp))
        same = d < tol
        tag = "OK  " if same else "DIFF"
        print(f"  [{tag}] {label:50s}  got={float(got): .6f}  exp={float(exp): .6f}  Δ={d:.2e}")
        return (1, 0) if same else (0, 1)
    except (TypeError, ValueError):
        same = got == exp
        tag = "OK  " if same else "DIFF"
        print(f"  [{tag}] {label:50s}  got={got}  exp={exp}")
        return (1, 0) if same else (0, 1)


if __name__ == "__main__":
    rgk = _run_identity_sequence()

    print("=" * 78)
    print("РГК prototype identity check vs legacy EXPECTED (TOL=1e-5)")
    print("=" * 78)

    print("\n── domain: user_state ──")
    u_ok, u_bad = _diff("user", rgk.project("user_state"), EXPECTED_USER)

    print("\n── domain: system (Neurochem) ──")
    s_ok, s_bad = _diff("sys", rgk.project("system"), EXPECTED_SYS)

    print("\n── domain: freeze (ProtectiveFreeze) ──")
    f_ok, f_bad = _diff("pf", rgk.project("freeze"), EXPECTED_FREEZE)

    print("\n── new diagnostic (РГК-only): balance + R/C ──")
    bal = rgk.project("balance")
    print(f"  user.balance   = {bal['user']:.4f}  (≈1.0 = резонанс)")
    print(f"  system.balance = {bal['system']:.4f}")
    print(f"  user.mode/system.mode = {bal['user_mode']}/{bal['system_mode']}")
    print(f"  coupling sync_error    = {rgk.sync_error():.4f}")

    total_ok = u_ok + s_ok + f_ok
    total_bad = u_bad + s_bad + f_bad
    print("\n" + "=" * 78)
    print(f"SUMMARY:  {total_ok} OK  /  {total_bad} DIFF/MISS  "
          f"({total_ok}/{total_ok + total_bad} = "
          f"{100.0*total_ok/(total_ok+total_bad):.1f}%)")
    print("=" * 78)
