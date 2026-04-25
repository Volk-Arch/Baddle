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

from .ema import TimeConsts
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
        # Phase D Step 4c — chem state в self._rgk.system. MetricRegistry удалён;
        # обновления через прямые вызовы _rgk.s_graph / tick_s_pred.
        self._rgk = РГК()
        self._rgk.system.gain.value = dopamine
        self._rgk.system.hyst.value = serotonin
        self._rgk.system.aperture.value = norepinephrine

        # RPE history: legacy bespoke (record_outcome).
        self._delta_history: list = []
        self.recent_rpe: float = 0.0

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
    # cognitive_loop. См. planning/rgk-migration-plan.md §6 «ACh+GABA feeders».

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
        См. planning/rgk-spec.md §3.5."""
        return self._rgk.system.balance()

    # ── Phase D Step 5: ACh + GABA feeders ─────────────────────────────────

    def feed_acetylcholine(self, node_creation_rate: float = 0.0,
                            bridge_quality: float = None):
        """Plasticity feeder — пластичность графа.

        node_creation_rate ∈ [0, 1] — нормированная rate новых нод/час
        (cap=10 default → если 10+ нод за час → 1.0, 5 нод → 0.5).
        bridge_quality ∈ [0, 1] — качество последнего DMN-моста, если найден.
        Если None — этот feeder не активирован.

        v1 ОГРАНИЧЕНИЕ: node_creation_rate vs «реальная пластичность ткани»
        — proxy. Граф может расти быстро, но ноды могут быть тривиальными
        copy-paste, не отражая «открытость новому». Bridge_quality ловит
        эту разницу частично (только бы значимые мосты находились).
        Калибровка через 2 нед use, см. planning/rgk-migration-plan.md §6.
        """
        rate_norm = max(0.0, min(1.0, float(node_creation_rate)))
        self._rgk.system.plasticity.feed(rate_norm)
        if bridge_quality is not None:
            bq = max(0.0, min(1.0, float(bridge_quality)))
            self._rgk.system.plasticity.feed(bq, decay_override=0.9)

    def feed_gaba(self, freeze_active: bool, embedding_scattering: float = None):
        """Damping feeder — стенки стоячей волны.

        freeze_active: True если ProtectiveFreeze.active. Сильный сигнал
        для Damping — система чётко тормозит.
        embedding_scattering ∈ [0, 1] — нормированная std embeddings активных
        нод. Высокая = разнобой, низкая = узкая стоячая волна. Если None —
        skip второй feeder.

        v1 ОГРАНИЧЕНИЕ: freeze_active boolean — slow indicator. Между
        активациями freeze_active=False даёт GABA=0 → damping ~0.5 default
        EMA. embedding_scattering как proxy «чёткости границ» — но граф
        может быть seman‌tically widely spread даже когда фокус узкий
        (юзер думает над одним концептом, граф богат). Калибровка нужна.
        """
        sig = 1.0 if freeze_active else 0.0
        self._rgk.system.damping.feed(sig)
        if embedding_scattering is not None:
            inv = max(0.0, min(1.0, 1.0 - float(embedding_scattering)))
            self._rgk.system.damping.feed(inv, decay_override=0.95)

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
        da = self._rgk.system.gain
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
            # Phase D: 5-axis chem + balance diagnostic
            "acetylcholine": round(self.acetylcholine, 3),
            "gaba": round(self.gaba, 3),
            "balance": round(self.balance(), 3),
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


# ── ProtectiveFreeze ───────────────────────────────────────────────────────

# Phase D Step 4c: extractors + _build_freeze_registry удалены.
# Pressure layer EMAs живут в self._rgk (conflict, imbalance_press, sync_fast,
# sync_slow + silence_press linear ramp + freeze_active flag).
# Обновления через _rgk.p_conflict(d, serotonin) и _rgk.p_tick(dt, sync_err, imbalance).


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
        # Phase D Step 4c — pressure layer полностью в self._rgk.
        # silence_pressure (linear ramp), conflict/imbalance/sync_fast/sync_slow EMAs,
        # active flag — всё через _rgk.
        self._rgk = РГК()

    # ── Public read accessors (backward-compat) ────────────────────────────

    @property
    def conflict_accumulator(self) -> float:
        return float(self._rgk.conflict.value)

    @property
    def imbalance_pressure(self) -> float:
        return float(self._rgk.imbalance_press.value)

    @property
    def sync_error_ema_fast(self) -> float:
        return float(self._rgk.sync_fast.value)

    @property
    def sync_error_ema_slow(self) -> float:
        return float(self._rgk.sync_slow.value)

    @property
    def silence_pressure(self) -> float:
        return float(self._rgk.silence_press)

    @silence_pressure.setter
    def silence_pressure(self, v: float):
        self._rgk.silence_press = max(0.0, min(1.0, float(v)))

    @property
    def active(self) -> bool:
        return bool(self._rgk.freeze_active)

    @active.setter
    def active(self, v: bool):
        self._rgk.freeze_active = bool(v)

    # ── Feeders ─────────────────────────────────────────────────────────────

    def update(self, d: float = None, serotonin: float = 0.5):
        """Обновить conflict accumulator и проверить вход/выход freeze.

        Остальные feeders (silence/imbalance/sync_error) обновляются через
        `feed_tick(dt, ...)` из cognitive_loop на каждом background tick'е.
        Они **не** активируют Bayes-freeze — только display_burnout и
        idle multiplier.
        """
        # Phase D: gate в _rgk.p_conflict (формула conflict EMA + freeze hysteresis).
        # _rgk.p_conflict тоже обновляет freeze_active; self.active = property → _rgk.
        self._rgk.p_conflict(d=d, serotonin=serotonin)

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
        # Phase D: silence_press ramp + 3 EMA feeders в _rgk.p_tick.
        self._rgk.p_tick(dt=dt, sync_err=sync_err, imbalance=imbalance)

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
        pf._rgk.conflict.value = float(d.get("conflict_accumulator", 0.0))
        # Legacy fallback: до 2026-04-23 поле называлось `desync_pressure`.
        pf.silence_pressure = float(
            d.get("silence_pressure", d.get("desync_pressure", 0.0)))
        pf._rgk.imbalance_press.value = float(d.get("imbalance_pressure", 0.0))
        pf._rgk.sync_fast.value = float(d.get("sync_error_ema_fast", 0.0))
        pf._rgk.sync_slow.value = float(d.get("sync_error_ema_slow", 0.0))
        pf.active = bool(d.get("active", False))
        return pf
