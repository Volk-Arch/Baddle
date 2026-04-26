"""Нейрохимия второго мозга — пять скаляров, γ derived, burnout отдельно.

EMA-метрики живут в `self._rgk` (общий резонатор, см. src/rgk.py). Правило 2
из docs/architecture-rules.md: «любая производная метрика это
`EMA(source_event, decay)`». Обновления — explicit `feed_*` методы +
`s_graph` / `tick_s_pred` через _rgk.

Максимально простой контракт. Каждая формула — одна строка EMA.

Пример:

    chem = Neurochem()
    chem.update(d=0.42, w_change=[0.1, -0.02, 0.3], weights=[0.2, 0.3, 0.5])
    chem.gamma  # derived property — 2.0 + 3.0 · NE · (1 − S)
    chem.apply_to_bayes(prior=0.5, d=0.2)

Скаляры:
  - dopamine      — реакция на новизну. EMA от d: чем больше различие, тем выше.
                    «Пушит систему в сторону нового». Интерес.
  - serotonin     — стабильность весов. EMA от (1 − std(W_change)): высокая,
                    когда веса мало колеблются. «Уверенность в приорах».
  - norepinephrine — энтропия распределения весов. EMA от H(W): высокая,
                    когда система не определилась. «Напряжение / внимание».

Derived:
  gamma = 2.0 + 3.0 · norepinephrine · (1 − serotonin)
  Высокое напряжение + низкая стабильность → повышенная чувствительность.

Burnout — отдельный защитный механизм (см. `ProtectiveFreeze` ниже). Не
медиатор, а режим. Накапливается при хроническом конфликте.

HRV сюда не приходит. Тело пользователя влияет на **советы** в assistant.py,
не на внутреннюю химию системы. Система может работать и без HRV вообще.
"""
from typing import Optional

import numpy as np

from .rgk import РГК


# Phase D Step 4c: Neurochem extractors + _build_neurochem_registry удалены.
# Все EMA живут в self._rgk.system (chem) + self._rgk.s_exp_vec (predictive).
# Обновления через _rgk.s_graph(d, w_change, weights) и _rgk.tick_s_pred().


class Neurochem:
    """Три скаляра. Реагируют на динамику графа, не на юзера напрямую.

    Self-prediction: `expectation_vec` — медленный EMA собственных
    [DA, S, NE]. `self_surprise_vec = vector − expectation_vec` даёт
    Baddle PE на самой себе — замыкает Фристон loop симметрично с
    UserState.expectation_vec. Питается через `tick_expectation()`,
    вызывается из cognitive_loop._advance_tick параллельно с user EMA.

    EMA живут в `self._rgk.system.*` (chem axes), `self._rgk.s_exp_vec`
    (predictive baseline). Обновления через `update()` (graph delta) /
    `tick_expectation()` (self-prediction baseline). Properties делегируют
    к `_rgk` — единый источник истины.
    """

    def __init__(self,
                 dopamine: float = 0.5,
                 serotonin: float = 0.5,
                 norepinephrine: float = 0.5,
                 *,
                 rgk: "Optional[РГК]" = None):
        # B0: optional shared РГК (production bootstrap передаёт singleton).
        # Default rgk=None → создаётся новый РГК (backward-compat для тестов).
        self._rgk = rgk if rgk is not None else РГК()
        self._rgk.system.gain.value = dopamine
        self._rgk.system.hyst.value = serotonin
        self._rgk.system.aperture.value = norepinephrine
        # RPE history + recent_rpe живут в _rgk (см. _rpe_hist / recent_rpe).
        # Legacy `_delta_history` ключ в to_dict/from_dict для backward-compat.

    # ── Backward-compat read/write accessors ───────────────────────────────

    @property
    def dopamine(self) -> float:
        return float(self._rgk.system.gain.value)

    @dopamine.setter
    def dopamine(self, v: float):
        self._rgk.system.gain.value = max(0.0, min(1.0, float(v)))

    @property
    def serotonin(self) -> float:
        return float(self._rgk.system.hyst.value)

    @serotonin.setter
    def serotonin(self, v: float):
        self._rgk.system.hyst.value = max(0.0, min(1.0, float(v)))

    @property
    def norepinephrine(self) -> float:
        return float(self._rgk.system.aperture.value)

    @norepinephrine.setter
    def norepinephrine(self, v: float):
        self._rgk.system.aperture.value = max(0.0, min(1.0, float(v)))

    # ── Phase D: 5-axis chem (ACh + GABA доступны как property) ─────────────
    # Без feeders default=0.5. Step 5 добавит источники signal в graph_logic /
    # cognitive_loop. См. docs/neurochem-design.md §6 «ACh+GABA feeders».

    @property
    def acetylcholine(self) -> float:
        """Plasticity (текучесть ткани, скорость перестройки графа).
        В Step 5 fed by node_creation_rate + bridge_quality. До тех пор = 0.5."""
        return float(self._rgk.system.plasticity.value)

    @acetylcholine.setter
    def acetylcholine(self, v: float):
        self._rgk.system.plasticity.value = max(0.0, min(1.0, float(v)))

    @property
    def gaba(self) -> float:
        """Damping (стенки стоячей волны, гасит боковые лепестки).
        В Step 5 fed by freeze.active duration + 1−scattering. До тех пор = 0.5."""
        return float(self._rgk.system.damping.value)

    @gaba.setter
    def gaba(self, v: float):
        self._rgk.system.damping.value = max(0.0, min(1.0, float(v)))

    @property
    def expectation_vec(self) -> np.ndarray:
        return self._rgk.s_exp_vec.value

    @expectation_vec.setter
    def expectation_vec(self, v):
        """Direct assign — для legacy from_dict."""
        arr = np.asarray(v, dtype=np.float32)
        ema = self._rgk.s_exp_vec
        if arr.shape == ema.value.shape:
            ema.value = np.clip(arr, 0.0, 1.0).astype(np.float32)

    # ── Updates ─────────────────────────────────────────────────────────

    def update(self,
               d: float = None,
               w_change=None,
               weights=None):
        """Обновить скаляры по сигналам одного tick'а.

        d          — последнее расстояние distinct(a,b), drives dopamine (новизна)
        w_change   — изменения весов (дельты после Bayes update), drives serotonin (стабильность)
        weights    — текущее распределение весов активных нод, drives norepinephrine (неопределённость)

        Любой аргумент может быть None — соответствующий скаляр не обновится.
        """
        self._rgk.s_graph(d=d, w_change=w_change, weights=weights)

    # ── Self-prediction (Friston-loop симметрия) ─────────────────────────

    def vector(self) -> np.ndarray:
        """3D текущее состояние. Зеркально UserState.vector()."""
        return self._rgk.system.vector()

    def tick_expectation(self):
        """Обновить self-baseline. Вызывается из cognitive_loop._advance_tick."""
        self._rgk.tick_s_pred()

    def balance(self) -> float:
        """5-axis резонансный баланс: (Gain·Aperture·Plasticity)/(Hyst·Damping).
        ≈1.0 = резонанс; >1.5 гиперрезонанс (срыв); <0.5 гипостабильность.
        До интеграции feeders ACh/GABA = 0.5, формула эквивалентна (DA·NE)/(5HT).
        См. docs/neurochem-design.md § Балансовая формула."""
        return self._rgk.system.balance()

    @property
    def mode(self) -> str:
        """R/C bit (Правило 7 — Counter-wave). System mirror — обновляется
        cognitive_loop._advance_tick от combined_imbalance через гистерезис."""
        return self._rgk.system.mode

    def update_mode(self, perturbation: float) -> str:
        """Update R/C mode by current perturbation (combined_imbalance)."""
        return self._rgk.system.update_mode(float(perturbation))

    # ── Phase D Step 5: ACh + GABA feeders ─────────────────────────────────

    def feed_acetylcholine(self, node_creation_rate: float = 0.0,
                            bridge_quality: float = None):
        """Trivial delegate в _rgk.s_ach_feed."""
        self._rgk.s_ach_feed(node_creation_rate, bridge_quality)

    def feed_gaba(self, freeze_active: bool, embedding_scattering: float = None):
        """Trivial delegate в _rgk.s_gaba_feed."""
        self._rgk.s_gaba_feed(freeze_active, embedding_scattering)

    @property
    def self_surprise_vec(self) -> np.ndarray:
        """3D PE Baddle против её же baseline: DA/S/NE − expectation_vec."""
        return self.vector() - self.expectation_vec

    @property
    def self_imbalance(self) -> float:
        """‖self_surprise_vec‖. Magnitude of self-prediction-error.
        B4 Wave 1: формула в РГК.project("system")."""
        return self._rgk.project("system")["self_imbalance"]

    # ── Derived ─────────────────────────────────────────────────────────

    @property
    def gamma(self) -> float:
        """γ = 2.0 + 3.0 · NE · (1 − S). Напряжение + нестабильность → чувствительность.
        B4 Wave 1: формула в РГК.gamma()."""
        return self._rgk.gamma()

    # ── RPE: автономный dopamine drift без юзера ────────────────────────

    @property
    def recent_rpe(self) -> float:
        return float(self._rgk.recent_rpe)

    @recent_rpe.setter
    def recent_rpe(self, v: float):
        self._rgk.recent_rpe = float(v)

    @property
    def _delta_history(self) -> list:
        """Backward-compat alias для _rgk._rpe_hist (single source)."""
        return self._rgk._rpe_hist

    @_delta_history.setter
    def _delta_history(self, v):
        self._rgk._rpe_hist = list(v)

    def record_outcome(self, prior: float, posterior: float) -> float:
        """Trivial delegate в _rgk.s_outcome — single source формулы и
        accumulator. Раньше тут жил duplicate с собственным _delta_history."""
        return self._rgk.s_outcome(prior, posterior)

    # ── Bayesian step через distinct ────────────────────────────────────

    def apply_to_bayes(self, prior: float, d: float) -> float:
        """Trivial delegate в _rgk.bayes_step."""
        return self._rgk.bayes_step(prior, d)

    # ── Serialization ───────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "dopamine": round(self.dopamine, 3),
            "serotonin": round(self.serotonin, 3),
            "norepinephrine": round(self.norepinephrine, 3),
            # Phase D: 5-axis chem + balance diagnostic
            "acetylcholine": round(self.acetylcholine, 3),
            "gaba": round(self.gaba, 3),
            "balance": round(self.balance(), 3),
            "mode": self.mode,
            "gamma": round(self.gamma, 3),
            "recent_rpe": round(self.recent_rpe, 3),
            "expectation_vec": [round(float(x), 3) for x in self.expectation_vec.tolist()],
            "self_imbalance": round(self.self_imbalance, 3),
            "_delta_history": [round(x, 3) for x in self._delta_history],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Neurochem":
        n = cls(
            dopamine=d.get("dopamine", 0.5),
            serotonin=d.get("serotonin", 0.5),
            norepinephrine=d.get("norepinephrine", 0.5),
        )
        # Phase D: 5-axis ACh+GABA. Default 0.5 если поле отсутствует
        # (backward-compat для legacy state.json до Phase D).
        n.acetylcholine = float(d.get("acetylcholine", 0.5))
        n.gaba = float(d.get("gaba", 0.5))
        n.recent_rpe = d.get("recent_rpe", 0.0)
        n._delta_history = list(d.get("_delta_history", []))
        vec = d.get("expectation_vec")
        if isinstance(vec, (list, tuple)) and len(vec) == 3:
            try:
                n.expectation_vec = np.array([float(x) for x in vec], dtype=np.float32)
            except Exception:
                pass
        return n


# ProtectiveFreeze class удалён в B5 W3. Все state и dynamics живут в РГК:
#   • conflict / silence_press / imbalance_press / sync_fast / sync_slow / freeze_active
#   • _rgk.p_conflict(d, serotonin) — update accumulator + freeze flag hysteresis
#   • _rgk.p_tick(dt, sync_err, imbalance) — time-based update остальных feeders
#   • _rgk.add_silence(delta) — user-event silence drop
#   • _rgk.combined_burnout(user_burnout) — max(display, user_burnout) helper
#   • _rgk.serialize_freeze() / _rgk.load_freeze(d) — state.json roundtrip
