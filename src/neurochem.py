"""Нейрохимия второго мозга — три скаляра, γ derived, burnout отдельно.

Все EMA-метрики живут в `self.metrics: MetricRegistry` (2026-04-24, Фаза A
из planning/simplification-plan.md). Правило 2: «любая производная метрика
это `EMA(source_event, decay)`». Обновления идут через `fire_event(type, **payload)`.

Максимально простой контракт. Каждая формула — одна строка EMA.

Пример в духе пользовательского эскиза:

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
import math
import numpy as np

from .ema import EMA, VectorEMA, Decays, TimeConsts
from .metrics import MetricRegistry


# ── Extractors (module-level, pure functions) ──────────────────────────────

def _extract_d(payload: dict):
    """dopamine feeder: raw distinct() value, when provided."""
    return payload.get("d")


def _extract_stability(payload: dict):
    """serotonin feeder: 1 - std(w_change) ∈ [0, 1]. None если w_change пуст."""
    wc = payload.get("w_change")
    if wc is None:
        return None
    arr = np.asarray(list(wc), dtype=np.float32)
    if arr.size == 0:
        return None
    return max(0.0, 1.0 - float(np.std(arr)))


def _extract_entropy_norm(payload: dict):
    """norepinephrine feeder: normalized entropy of weights ∈ [0, 1]."""
    w = payload.get("weights")
    if w is None:
        return None
    arr = np.asarray(list(w), dtype=np.float32)
    if arr.size == 0:
        return None
    arr = np.clip(arr, 1e-9, 1.0)
    total = float(np.sum(arr))
    if total <= 0:
        return None
    pnorm = arr / total
    ent = -float(np.sum(pnorm * np.log(pnorm + 1e-9)))
    max_ent = float(np.log(max(2, arr.size)))
    return min(1.0, ent / max_ent) if max_ent > 0 else 0.0


def _extract_vector(payload: dict):
    """self_expectation_vec feeder: текущий neurochem vector."""
    return payload.get("vector")


def _build_neurochem_registry(dopamine: float,
                                serotonin: float,
                                norepinephrine: float) -> MetricRegistry:
    """Factory: построить registry с initials и подписками."""
    reg = MetricRegistry()
    reg.register(
        "dopamine",
        EMA(dopamine, decay=Decays.NEURO_DOPAMINE, bounds=(0.0, 1.0)),
        listens=[("graph_update", _extract_d)],
    )
    reg.register(
        "serotonin",
        EMA(serotonin, decay=Decays.NEURO_SEROTONIN, bounds=(0.0, 1.0)),
        listens=[("graph_update", _extract_stability)],
    )
    reg.register(
        "norepinephrine",
        EMA(norepinephrine, decay=Decays.NEURO_NOREPINEPHRINE,
            bounds=(0.0, 1.0)),
        listens=[("graph_update", _extract_entropy_norm)],
    )
    reg.register(
        "self_expectation_vec",
        VectorEMA([0.5, 0.5, 0.5], decay=Decays.SELF_EXPECTATION,
                  bounds=(0.0, 1.0)),
        listens=[("tick", _extract_vector)],
    )
    return reg


class Neurochem:
    """Три скаляра. Реагируют на динамику графа, не на юзера напрямую.

    Self-prediction: `expectation_vec` — медленный EMA собственных
    [DA, S, NE]. `self_surprise_vec = vector − expectation_vec` даёт
    Baddle PE на самой себе — замыкает Фристон loop симметрично с
    UserState.expectation_vec. Питается через `tick_expectation()`,
    вызывается из cognitive_loop._advance_tick параллельно с user EMA.

    **Implementation note (2026-04-24):** все EMA живут в `self.metrics`
    (MetricRegistry). Обновления через `fire_event("graph_update", ...)` /
    `fire_event("tick", vector=...)`. Properties `.dopamine` / `.serotonin` /
    `.norepinephrine` / `.expectation_vec` — backward-compat.
    """

    RPE_WINDOW = 20    # скользящее окно для baseline Δconfidence
    RPE_GAIN = 0.15    # как сильно dopamine сдвигается на единицу RPE

    def __init__(self,
                 dopamine: float = 0.5,
                 serotonin: float = 0.5,
                 norepinephrine: float = 0.5):
        self.metrics = _build_neurochem_registry(
            dopamine, serotonin, norepinephrine)
        self._delta_history: list = []
        self.recent_rpe: float = 0.0

    # ── Backward-compat read/write accessors ───────────────────────────────

    @property
    def dopamine(self) -> float:
        return float(self.metrics.value("dopamine"))

    @dopamine.setter
    def dopamine(self, v: float):
        self.metrics.get("dopamine").value = max(0.0, min(1.0, float(v)))

    @property
    def serotonin(self) -> float:
        return float(self.metrics.value("serotonin"))

    @serotonin.setter
    def serotonin(self, v: float):
        self.metrics.get("serotonin").value = max(0.0, min(1.0, float(v)))

    @property
    def norepinephrine(self) -> float:
        return float(self.metrics.value("norepinephrine"))

    @norepinephrine.setter
    def norepinephrine(self, v: float):
        self.metrics.get("norepinephrine").value = max(0.0, min(1.0, float(v)))

    @property
    def expectation_vec(self) -> np.ndarray:
        return self.metrics.value("self_expectation_vec")

    @expectation_vec.setter
    def expectation_vec(self, v):
        """Direct assign — для legacy from_dict."""
        arr = np.asarray(v, dtype=np.float32)
        ema = self.metrics.get("self_expectation_vec")
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
        self.metrics.fire_event(
            "graph_update",
            d=d,
            w_change=w_change,
            weights=weights,
        )

    # ── Self-prediction (Friston-loop симметрия) ─────────────────────────

    def vector(self) -> np.ndarray:
        """3D текущее состояние. Зеркально UserState.vector()."""
        return self.metrics.vector(["dopamine", "serotonin", "norepinephrine"])

    def tick_expectation(self):
        """Обновить self-baseline. Вызывается из cognitive_loop._advance_tick."""
        self.metrics.fire_event("tick", vector=self.vector())

    @property
    def self_surprise_vec(self) -> np.ndarray:
        """3D PE Baddle против её же baseline: DA/S/NE − expectation_vec."""
        return self.vector() - self.expectation_vec

    @property
    def self_imbalance(self) -> float:
        """‖self_surprise_vec‖. Magnitude of self-prediction-error.
        Высокая = собственная нейрохимия уехала от привычного baseline.
        """
        return float(np.linalg.norm(self.self_surprise_vec))

    # ── Derived ─────────────────────────────────────────────────────────

    @property
    def gamma(self) -> float:
        """γ = 2.0 + 3.0 · NE · (1 − S). Напряжение + нестабильность → чувствительность."""
        return 2.0 + 3.0 * self.norepinephrine * (1.0 - self.serotonin)

    # ── RPE: автономный dopamine drift без юзера ────────────────────────

    def record_outcome(self, prior: float, posterior: float) -> float:
        """Reward prediction error из Bayes-обновления.

        actual    = |posterior − prior|  — сколько информации реально получили
        predicted = mean(recent_deltas)   — сколько обычно получаем (baseline)
        rpe       = actual − predicted

        Положительный RPE (больше изменений чем обычно) → фазовый bump dopamine.
        Отрицательный (меньше чем обычно) → dopamine слегка падает.
        Это делает dopamine модуляцией **неожиданности**, а не просто новизны.

        Возвращает RPE (для логов / метрик).
        """
        actual = abs(float(posterior) - float(prior))
        if self._delta_history:
            predicted = sum(self._delta_history) / len(self._delta_history)
        else:
            predicted = actual   # первый раз — RPE=0, просто записываем
        rpe = actual - predicted
        # RPE — additive bump not EMA, direct value mutation + clamp.
        # Side effect остаётся bespoke (см. planning/phase-a-metric-registry § 6).
        da = self.metrics.get("dopamine")
        da.value = max(0.0, min(1.0, da.value + self.RPE_GAIN * rpe))
        self.recent_rpe = rpe
        self._delta_history.append(actual)
        if len(self._delta_history) > self.RPE_WINDOW:
            self._delta_history = self._delta_history[-self.RPE_WINDOW:]
        return rpe

    # ── Bayesian step через distinct ────────────────────────────────────

    def apply_to_bayes(self, prior: float, d: float) -> float:
        """Signed NAND-Bayes: logit(post) = logit(prior) + γ · (1 − 2d)."""
        prior = max(0.01, min(0.99, prior))
        log_prior = math.log(prior / (1.0 - prior))
        log_post = log_prior + self.gamma * (1.0 - 2.0 * d)
        posterior = 1.0 / (1.0 + math.exp(-log_post))
        return round(max(0.01, min(0.99, posterior)), 3)

    # ── Serialization ───────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "dopamine": round(self.dopamine, 3),
            "serotonin": round(self.serotonin, 3),
            "norepinephrine": round(self.norepinephrine, 3),
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
        n.recent_rpe = d.get("recent_rpe", 0.0)
        n._delta_history = list(d.get("_delta_history", []))
        vec = d.get("expectation_vec")
        if isinstance(vec, (list, tuple)) and len(vec) == 3:
            try:
                n.expectation_vec = np.array([float(x) for x in vec], dtype=np.float32)
            except Exception:
                pass
        return n


# ── ProtectiveFreeze ───────────────────────────────────────────────────────

def _extract_conflict(payload: dict):
    """conflict_accumulator feeder: (d - TAU_STABLE)+ * (1 - serotonin)."""
    d = payload.get("d")
    if d is None:
        return None
    s = payload.get("serotonin", 0.5)
    conflict_signal = max(0.0, float(d) - ProtectiveFreeze.TAU_STABLE)
    instability = max(0.0, 1.0 - float(s))
    return conflict_signal * instability


def _extract_imbalance_dt(payload: dict):
    """imbalance_pressure feeder: (|imbalance|, dt)-tuple для time-const EMA."""
    dt = payload.get("dt")
    if dt is None or dt <= 0:
        return None
    return (abs(float(payload.get("imbalance", 0.0))), float(dt))


def _extract_sync_dt(payload: dict):
    """sync_error EMA feeder: (sync_err/√3, dt)-tuple. Нормализация ~[0,√3]→[0,1]."""
    dt = payload.get("dt")
    if dt is None or dt <= 0:
        return None
    s = max(0.0, min(1.0, float(payload.get("sync_err", 0.0)) / 1.732))
    return (s, float(dt))


def _build_freeze_registry() -> MetricRegistry:
    reg = MetricRegistry()
    reg.register(
        "conflict_accumulator",
        EMA(0.0, decay=Decays.NEURO_CONFLICT_ACCUMULATOR, bounds=(0.0, 1.0)),
        listens=[("conflict_update", _extract_conflict)],
    )
    reg.register(
        "imbalance_pressure",
        EMA(0.0, time_const=TimeConsts.IMBALANCE, bounds=(0.0, 1.0)),
        listens=[("feed_tick", _extract_imbalance_dt)],
    )
    reg.register(
        "sync_error_fast",
        EMA(0.0, time_const=TimeConsts.SYNC_EMA_FAST, bounds=(0.0, 1.0)),
        listens=[("feed_tick", _extract_sync_dt)],
    )
    reg.register(
        "sync_error_slow",
        EMA(0.0, time_const=TimeConsts.SYNC_EMA_SLOW, bounds=(0.0, 1.0)),
        listens=[("feed_tick", _extract_sync_dt)],
    )
    return reg


class ProtectiveFreeze:
    """Защитный режим + накопители «усталости» Baddle.

    Три независимых feeder'а — одно displayed-поле burnout в UI, плюс два
    агрегата sync_error (fast/slow) для валидации прайм-директивы.

      1. `conflict_accumulator` — хронический графовый конфликт (d > τ_stable
         при низкой стабильности). Единственный feeder который **активирует
         Bayes-freeze** (жёсткий режим, блокирует обновления confidence).

      2. `silence_pressure` — хроническое молчание юзера. Растёт линейно
         по времени без user-events (1.0 за SILENCE_RAMP_SECONDS), падает
         на user-event через `add_silence_pressure(-drop)`. Раньше
         назывался `desync_pressure`, но по факту это не рассинхрон, а
         таймер тишины — переименован для честности.

      3. `imbalance_pressure` — EMA |UserState.surprise| (predictive error
         в Фристоновском смысле). Растёт когда юзер ведёт себя не так, как
         ожидали. Медленный time-constant (1 день) — отражает сдвиг baseline.

    `display_burnout = max` всех трёх. UI показывает одну «Усталость Baddle»,
    `cognitive_loop._idle_multiplier()` замедляет циклы при любом из кризисов.

    `sync_error_ema_fast/slow` — чистый агрегат прайм-директивы. Не входят
    в display_burnout (это не усталость, а качество резонанса), пишутся раз
    в час в `data/prime_directive.jsonl` через `src/prime_directive.py`.
    Через 2 мес use сравниваем mean(slow) за первый/последний месяц →
    падает = механики резонансного протокола работают.

    **Implementation note (2026-04-24):** все EMA живут в `self.metrics`
    (MetricRegistry). `update(d, serotonin)` → `fire_event("conflict_update")`,
    `feed_tick(dt, sync_err, imbalance)` → `fire_event("feed_tick", ...)`.
    `silence_pressure` остаётся как float — это линейный ramp timer, не EMA.
    """

    TAU_STABLE = 0.6          # порог за которым d считается конфликтом
    THETA_ACTIVE = 0.15        # вход во freeze (учитывая EMA steady-state)
    THETA_RECOVERY = 0.08      # выход из freeze (гистерезис)
    # Feeder time-constants — см. `src.ema.TimeConsts`.
    SILENCE_RAMP_SECONDS = TimeConsts.SILENCE_RAMP

    def __init__(self):
        self.metrics = _build_freeze_registry()
        # Linear ramp timer, not EMA (остаётся bespoke)
        self.silence_pressure: float = 0.0
        self.active: bool = False

    # ── Public read accessors (backward-compat) ────────────────────────────

    @property
    def conflict_accumulator(self) -> float:
        return float(self.metrics.value("conflict_accumulator"))

    @property
    def imbalance_pressure(self) -> float:
        return float(self.metrics.value("imbalance_pressure"))

    @property
    def sync_error_ema_fast(self) -> float:
        return float(self.metrics.value("sync_error_fast"))

    @property
    def sync_error_ema_slow(self) -> float:
        return float(self.metrics.value("sync_error_slow"))

    # ── Feeders ─────────────────────────────────────────────────────────────

    def update(self, d: float = None, serotonin: float = 0.5):
        """Обновить conflict accumulator и проверить вход/выход freeze.

        Остальные feeders (silence/imbalance/sync_error) обновляются через
        `feed_tick(dt, ...)` из cognitive_loop на каждом background tick'е.
        Они **не** активируют Bayes-freeze — только display_burnout и
        idle multiplier.
        """
        self.metrics.fire_event("conflict_update", d=d, serotonin=serotonin)

        # State machine остаётся bespoke (не EMA) — см. phase-a-metric-registry § 6.
        conflict = self.conflict_accumulator
        if self.active:
            if conflict < self.THETA_RECOVERY:
                self.active = False
        else:
            if conflict > self.THETA_ACTIVE:
                self.active = True

    def feed_tick(self, dt: float, sync_err: float = 0.0,
                   imbalance: float = 0.0):
        """Единый time-based update всех feeders кроме conflict_accumulator.

        Вызывается из `cognitive_loop._advance_tick` на каждой итерации.
        `dt` — секунды с прошлого вызова (capped во внешнем коде).

        Args:
            dt: секунды с прошлого тика. <= 0 → no-op.
            sync_err: current L2-distance user↔system (0..≈√3 в 3D).
            imbalance: aggregated PE ∈ [0, 1] (см. cognitive_loop._advance_tick).
        """
        if dt <= 0:
            return
        # Silence — linear ramp. Не EMA, не в registry.
        self.silence_pressure = max(0.0, min(1.0,
            self.silence_pressure + dt / float(self.SILENCE_RAMP_SECONDS)
        ))

        # EMA feeders (imbalance + 2 sync_error) через registry
        self.metrics.fire_event(
            "feed_tick", dt=dt, sync_err=sync_err, imbalance=imbalance)

    @property
    def display_burnout(self) -> float:
        """«Baddle: Усталость» — max всех трёх feeder'ов Baddle-side.
        sync_error EMA намеренно НЕ входит: это качество резонанса,
        отдельная семантика от усталости.
        """
        return max(self.conflict_accumulator, self.silence_pressure,
                   self.imbalance_pressure)

    def combined_burnout(self, user_burnout: float = 0.0) -> float:
        """Для `_idle_multiplier`: эмпатия к юзеру встроена.

        max(display_burnout, user_burnout) — если юзер устал, Baddle тоже
        тише. Это один канал, не отдельный check — само замедление есть
        мягкое предложение.
        """
        ub = max(0.0, min(1.0, float(user_burnout or 0.0)))
        return max(self.display_burnout, ub)

    def add_silence_pressure(self, delta: float):
        """User-event input: снижение (−) при активности, рост (+) — редко."""
        self.silence_pressure = max(0.0, min(1.0,
            self.silence_pressure + float(delta)))

    def to_dict(self) -> dict:
        return {
            "conflict_accumulator": round(self.conflict_accumulator, 3),
            "silence_pressure": round(self.silence_pressure, 3),
            "imbalance_pressure": round(self.imbalance_pressure, 3),
            "sync_error_ema_fast": round(self.sync_error_ema_fast, 4),
            "sync_error_ema_slow": round(self.sync_error_ema_slow, 4),
            "display_burnout": round(self.display_burnout, 3),
            "active": self.active,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProtectiveFreeze":
        pf = cls()
        pf.metrics.get("conflict_accumulator").value = float(
            d.get("conflict_accumulator", 0.0))
        # Legacy fallback: до 2026-04-23 поле называлось `desync_pressure`.
        pf.silence_pressure = float(
            d.get("silence_pressure",
                  d.get("desync_pressure", 0.0))
        )
        pf.metrics.get("imbalance_pressure").value = float(
            d.get("imbalance_pressure", 0.0))
        pf.metrics.get("sync_error_fast").value = float(
            d.get("sync_error_ema_fast", 0.0))
        pf.metrics.get("sync_error_slow").value = float(
            d.get("sync_error_ema_slow", 0.0))
        pf.active = bool(d.get("active", False))
        return pf
