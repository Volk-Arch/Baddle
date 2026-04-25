"""РГК — физическое ядро (Правило 6 в [docs/architecture-rules.md](../docs/architecture-rules.md)).

Гипотеза: 6150 строк state+dynamics в Baddle = проекция одной модели.
Этот файл — попытка коллапса. ~200 строк ядра + ~100 строк проекторов.

Структура:
  1. `Resonator` — один резонатор: 5-chem (gain/hyst/aperture/plasticity/damping)
     + R/C bit + balance() + vector(). 5 параметров — gain~DA, hyst~5HT,
     aperture~NE, plasticity~ACh (новое), damping~GABA (новое).
  2. `РГК` — два связанных резонатора (user mirror + system mirror) +
     auxiliary state (valence/agency/burnout, predictive baselines, pressure
     accumulators) + projectors → legacy snapshots для identity-сравнения.

Запуск как скрипт:
    python -m src.rgk
печатает diff vs EXPECTED из tests/test_metric_identity.py — semantic
identity check. OK = совпало с TOL=1e-5, DIFF/MISS = диагностика
«что РГК-ядро не закрывает».

Назначение прототипа: НЕ замена production-кода, а **проверка модели**
до реального коллапса в новой ветке. Если identity не получается на
зафиксированном event sequence — РГК-spec неполна, нужна дополнительная
структура.
"""
from __future__ import annotations

import math

import numpy as np

from .ema import EMA, VectorEMA, Decays, TimeConsts


_TOD = ("morning", "day", "evening", "night")


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
        """3D legacy projection (gain, hyst, aperture) для sync_error."""
        return np.array([self.gain.value, self.hyst.value, self.aperture.value],
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
        self.u_exp_vec    = VectorEMA([0.5, 0.5, 0.5], decay=Decays.EXPECTATION_VEC)
        self.hrv_base_tod = {t: EMA(0.0, decay=Decays.HRV_BASELINE,
                                     seed_on_first=True) for t in _TOD}

        # System self-prediction baseline
        self.s_exp_vec = VectorEMA([0.5, 0.5, 0.5], decay=Decays.SELF_EXPECTATION)

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
        self._tod = "day"

        # B4 Wave 2: user-side bespoke state (sensor passthrough + aggregates).
        # Перемещено из UserState чтобы projectors имели полный access к
        # источникам — frequency_regime/hrv_surprise/activity_zone требуют
        # эти поля. UserState facade переиспользует через @property proxies.
        self.hrv_coherence = None        # type: float | None
        self.hrv_stress = None           # type: float | None
        self.hrv_rmssd = None            # type: float | None
        self.activity_magnitude: float = 0.0
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

    def u_hrv(self, coherence=None, stress=None, rmssd=None):
        if rmssd is not None and stress is None:
            stress = max(0.0, min(1.0, 1.0 - float(rmssd) / 80.0))
        if coherence is not None:
            self.user.hyst.feed(float(coherence))
            self.hrv_base_tod[self._tod].feed(float(coherence))
        if stress is not None:
            self.user.aperture.feed(float(stress))
        self.tick_u_pred()

    def u_engage(self, signal: float = 0.65):
        self.user.gain.feed(max(0.0, min(1.0, float(signal))))

    def u_feedback(self, kind: str):
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

    def tick_u_pred(self):
        sl = (float(self.user.gain.value) + float(self.user.hyst.value)) / 2.0
        self.u_exp.feed(sl)
        self.u_exp_tod[self._tod].feed(sl)
        self.u_exp_vec.feed(self.user.vector())

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
            self.system.gain.value + 0.15 * rpe))
        self.recent_rpe = rpe
        self._rpe_hist.append(actual)
        if len(self._rpe_hist) > 20:
            self._rpe_hist = self._rpe_hist[-20:]
        return rpe

    # ── Pressure ──────────────────────────────────────────────────────────

    def p_conflict(self, d: float, serotonin=None):
        if d is None:
            return
        s = float(self.system.hyst.value) if serotonin is None else float(serotonin)
        sig = max(0.0, float(d) - 0.6) * max(0.0, 1.0 - s)
        self.conflict.feed(sig)
        if self.freeze_active and self.conflict.value < 0.08:
            self.freeze_active = False
        elif (not self.freeze_active) and self.conflict.value > 0.15:
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

    # ── Coupling + projections ────────────────────────────────────────────

    def sync_error(self) -> float:
        return float(np.linalg.norm(self.user.vector() - self.system.vector()))

    def gamma(self) -> float:
        ne = float(self.system.aperture.value)
        s  = float(self.system.hyst.value)
        return 2.0 + 3.0 * ne * (1.0 - s)

    _AXIS_NAMES = ("dopamine", "serotonin", "norepinephrine")

    # ── B4 Wave 2: non-chem derivations (HRV / activity bespoke в проекторах)

    def _current_tod(self) -> str:
        """Time-of-day для TOD-scoped baselines. Mirror UserState._current_tod."""
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
        See planning/resonance-code-changes.md §2."""
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
            cur_tod = self.u_exp_tod[self._tod].value
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
                "dopamine":       float(self.user.gain.value),
                "serotonin":      float(self.user.hyst.value),
                "norepinephrine": float(self.user.aperture.value),
                "acetylcholine":  float(self.user.plasticity.value),
                "gaba":           float(self.user.damping.value),
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
                "dopamine":       float(self.system.gain.value),
                "serotonin":      float(self.system.hyst.value),
                "norepinephrine": float(self.system.aperture.value),
                "acetylcholine":  float(self.system.plasticity.value),
                "gaba":           float(self.system.damping.value),
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
        return {}


# ────────────────────────────────────────────────────────────────────────────
# 3. Singleton — каскад зеркал = ОДНА пара резонаторов (D-5).
# Tests НЕ используют global — создают independent РГК через UserState() /
# Neurochem() / ProtectiveFreeze() без `rgk=` argument.
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
    r._tod = "day"

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
    "dopamine": 0.566345, "serotonin": 0.540951, "norepinephrine": 0.418098,
    "valence": 0.103027, "burnout": 0.19, "agency": 0.505,
    "expectation": 0.517369,
    "expectation_by_tod": {"morning": 0.5, "day": 0.517369,
                            "evening": 0.5, "night": 0.5},
    "expectation_vec": [0.529848, 0.518119, 0.463763],
    "hrv_baseline_by_tod": {"morning": None, "day": 0.6,
                              "evening": None, "night": None},
    "vector": [0.566345, 0.540951, 0.418098],
    "surprise": 0.03628, "imbalance": 0.062759,
}
EXPECTED_SYS = {
    "dopamine": 0.424359, "serotonin": 0.598492, "norepinephrine": 0.700003,
    "expectation_vec": [0.495359, 0.508601, 0.517466],
    "gamma": 2.84317, "recent_rpe": -0.15, "self_imbalance": 0.215502,
    "vector": [0.424359, 0.598492, 0.700003],
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
