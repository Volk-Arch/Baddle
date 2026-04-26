"""UserState — зеркальный вектор пользователя для прайм-директивы.

SystemState (src/neurochem.py) эволюционирует по динамике графа.
UserState эволюционирует по наблюдаемым сигналам юзера.
Прайм-директива — минимизировать ‖user − system‖ в 3D.

Структура зеркальна Neurochem (3 соизмеримых скаляра):

    dopamine       — интерес: принятые предложения (accept feedback)
    serotonin      — спокойствие/стабильность: HRV coherence
    norepinephrine — напряжение: HRV stress

Плюс две отдельные оси накопления (не в sync_error — физически
несоизмеримы между user и system):

    burnout        — usage-нагрузка на юзера (decisions_today + rejects).
                     Feed'ит `_idle_multiplier` через `combined_burnout`
                     — если юзер устал, Baddle тоже замедляется.
    agency         — 5-я ось (OQ #2), в measurements.

Все скаляры в [0, 1]. EMA с decay, как в Neurochem.

История 2026-04-20: ранее были feeders через «длину сообщений» (variance →
serotonin) и «скорость ввода» (quick/pause → dopamine + valence). Убраны:
- length variance шумит (разные задачи = разная длина, не «нестабильность»)
- pause 5+ мин → negative valence ложно (юзер работал/думал, не «неприятно»)
- quick <30с → dopamine ложно (быстрый ввод ≠ интерес, часто нервный)
Оставлены объективные feeders: HRV (тело) + feedback (явное действие) +
decisions_today (счётчик).

sync_error = ‖user_vec − system_vec‖ (L2, 3D, max ≈ √3 ≈ 1.732)
sync_regime ∈ {FLOW, REST, PROTECT, CONFESS} — derived из (sync_error, оба state).

До 2026-04-23 vector был 4D (с burnout как 4-й осью). Ось удалена из
sync_error: user.burnout питался decisions+rejects, system.burnout —
графовыми конфликтами. Разные физические явления в одной оси → шум.
Burnout остаётся как отдельное поле для UI и empathy-замедления, но
из метрики резонанса исключён.

HRV живёт здесь, не в CognitiveState. Это сигнал тела **пользователя**.

## Predictive layer (Friston-style active inference)

Prediction error питает `ProtectiveFreeze.imbalance_pressure` — одну из
трёх feeder'ов display_burnout. Сам PE складывается из нескольких
источников (все нормализованы в [0,1] и агрегируются в cognitive_loop):

1. **3D state PE** — `‖vector() − expectation_vec‖` (behaviour prediction).
   Ожидание — baseline EMA в том же 3D пространстве что и `vector()`.

2. **TOD-scoped state PE** — `|state_level − expectation_by_tod[current]|`.
   У Baddle есть 4 baseline'а (morning/day/evening/night) — если юзер
   ведёт себя не так, как обычно в это время суток, surprise всплывает
   специфично. Без TOD-scoping утренняя и вечерняя apathy сливались в
   один averaged baseline → PE терял остроту.

3. **Goal PE / agency_gap** — `1 − agency`. Юзер не выполнил запланированное
   → прямой сигнал «ожидание не совпало с реальностью», прямее чем
   state_level-EMA.

4. **HRV PE** — `|hrv_coherence − hrv_baseline_by_tod[current]|`. С реальным
   Polar это физический канал PE, не просто нейрохимический EMA.

Плюс **self-prediction** живёт в Neurochem (`Neurochem.expectation_vec`):
Baddle предсказывает собственную нейрохимию и меряет self-surprise. Пятый
источник в `imbalance_pressure`. Симметричная Friston-loop: user PE +
self PE, одна метрика давления.

Legacy `expectation` (scalar EMA state_level) и `surprise` (scalar)
сохранены для backward-compat — UI их всё ещё потребляет.

## Implementation note

EMA-метрики живут в `self._rgk` (см. src/rgk.py). UserState — thin facade
над _rgk: 15 @property proxies (HRV/activity/day_summary/focus_residue/
timestamps), 9 chem properties (DA/5HT/NE/ACh/GABA/valence/agency/burnout/
expectation). Правило 2 + Правило 6 из docs/architecture-rules.md.

Updates через explicit methods:
  - `update_from_hrv` → serotonin, norepinephrine, hrv_baseline
  - `update_from_engagement` / `update_from_feedback` → dopamine, valence
  - `update_from_chat_sentiment` → valence
  - `update_from_plan_completion` → agency
  - `update_from_energy` → burnout
  - `tick_expectation` → expectation_by_tod, expectation_vec
  - `feed_acetylcholine` (на surprise) / `feed_gaba` (на focus_residue)

Bespoke остаются: burnout-bump (+0.05 на reject), streak-bias valence
(дискретные additive bumps, не EMA).
"""
import time
from typing import Optional

import numpy as np

from .ema import Decays
from .rgk import РГК


# ── Sync regime constants ───────────────────────────────────────────────────

FLOW = "flow"           # оба высокие + sync высокий → полный объём
REST = "rest"           # оба низкие + sync высокий → предлагаем паузу
PROTECT = "protect"     # user low, system high → система берёт на себя
CONFESS = "confess"     # user high, system low → «дай мне время»

# Пороги из TODO «Симбиоз»
SYNC_HIGH_THRESHOLD = 0.3      # error < 0.3 → sync высокий (L2 в 3D, max ≈ √3 ≈ 1.73)
STATE_HIGH_THRESHOLD = 0.55    # mean(D,S) > 0.55 → state высокий
STATE_LOW_THRESHOLD = 0.35     # mean(D,S) < 0.35 → state низкий

# Параметры предиктивной модели — в src/ema.py::Decays (2026-04-23).
# Экспортируем локальные алиасы для backward-compat с тестами / импортами.
EXPECTATION_EMA_DECAY = Decays.EXPECTATION             # 0.98
EXPECTATION_VEC_DECAY = Decays.EXPECTATION_VEC         # 0.97
HRV_BASELINE_DECAY = Decays.HRV_BASELINE                # 0.99
EXPECTATION_EMA_DECAY_FAST = Decays.EXPECTATION_FAST    # 0.85
EXPECTATION_VEC_DECAY_FAST = Decays.EXPECTATION_VEC_FAST  # 0.80

# Surprise boost: когда юзер detect'ится как удивлённый (OQ #7), ускоряем
# EMA decay на N tick'ов — его модель мира изменилась.
SURPRISE_BOOST_DEFAULT_TICKS = 3
# Phase C Шаг 6: dual-pool константы (LONG_RESERVE_MAX/DEFAULT/TAP_THRESHOLD,
# DAILY_ENERGY_MAX) удалены — заменены 3-zone capacity model.

# Activity zone параметры (из прототипа HRV × акселерометр)
ACTIVITY_THRESHOLD = 0.5       # magnitude выше которого юзер считается «активным»
COHERENCE_HEALTHY = 0.5        # coherence выше → HRV в норме (HIGH HRV)
# 4 зоны из 2×2 грида (hrv_ok, active):
ZONE_RECOVERY = "recovery"         # !active + hrv_ok    → 🟢 здоровое восстановление
ZONE_STRESS_REST = "stress_rest"   # !active + !hrv_ok   → 🟡 беспокойство в покое
ZONE_HEALTHY_LOAD = "healthy_load" #  active + hrv_ok    → 🔵 здоровая нагрузка
ZONE_OVERLOAD = "overload"         #  active + !hrv_ok   → 🔴 перегрузка / overtraining

_TOD_NAMES = ("morning", "day", "evening", "night")


# ── Capacity helpers (Phase C) ─────────────────────────────────────────────
#
# 3-zone capacity model — из docs/capacity-design.md. Заменяет dual-pool
# `daily_spent + long_reserve` на 3 параллельных контура (физио / эмо /
# когнитивный) с явными зонами green/yellow/red.

# Capacity thresholds (per docs/capacity-design.md §Формулы)
CAPACITY_PHYS_COHERENCE_MIN = 0.5
CAPACITY_PHYS_BURNOUT_MAX = 0.3
CAPACITY_AFFECT_SEROTONIN_MIN = 0.4
CAPACITY_AFFECT_DOPAMINE_MIN = 0.35
CAPACITY_COGLOAD_MAX = 0.6


def _normalize(value, cap):
    """Нормализация v/cap в [0, 1]."""
    if cap is None or cap <= 0:
        return 0.0
    try:
        return min(1.0, max(0.0, float(value) / float(cap)))
    except (TypeError, ValueError):
        return 0.0


def compute_cognitive_load(day_summary_today: dict, progress_delta: float) -> float:
    """Дневная когнитивная нагрузка [0, 1] из 6 observable.

    Formula per docs/capacity-design.md §Формулы:
        + 0.20 · normalize(tasks_started, cap=8)
        + 0.30 · normalize(context_switches, cap=10)
        + 0.30 · normalize(complexity_sum, cap=3.0)
        − 0.25 · normalize(tasks_completed, cap=5)
        − 0.25 · max(0, -progress_delta)        # good day reduces load

    NOTE: progress_delta = sync_error_now − sync_error_at_dawn.
    Negative = improvement (resonance улучшился) → "good progress"
    → reduces cognitive_load. Docs literal `max(0, progress_delta)`
    inconsistent with comment «положительный прогресс снижает» —
    implementing semantic interpretation.

    Коэффициенты калибруются по 2-недельному окну данных (через 2 нед
    after merge сравнить cognitive_load распределение с реальным состоянием).

    Args:
        day_summary_today: dict с ключами `tasks_started`, `tasks_completed`,
            `context_switches`, `complexity_sum` (defaults 0).
        progress_delta: float, sync_error change за день (negative = improved).

    Returns: float в [0, 1].
    """
    s = day_summary_today or {}
    load = (
        0.20 * _normalize(s.get("tasks_started", 0), cap=8)
        + 0.30 * _normalize(s.get("context_switches", 0), cap=10)
        + 0.30 * _normalize(s.get("complexity_sum", 0.0), cap=3.0)
        - 0.25 * _normalize(s.get("tasks_completed", 0), cap=5)
        - 0.25 * max(0.0, -float(progress_delta or 0.0))
    )
    return max(0.0, min(1.0, load))


def compute_capacity_indicators(user) -> dict:
    """Thin shim к `RGK.project("capacity")`. Wave 3 cleanup: вычисление
    логики переехало в `rgk.py` (single source). Здесь — backward-compat
    wrapper для существующих callers + tests.

    Returns:
        {phys_ok, affect_ok, cogload_ok, reasons[]} — booleans + list of
        failed-condition tags для capacity_reason property.
    """
    return user._rgk.project("capacity")


# Phase D Step 3c: extractors + _build_user_registry удалены.
# Все EMA UserState'а живут в self._rgk (см. src/rgk.py); fire_event
# абстракция заменена прямыми вызовами в update_from_*/apply_checkin/
# tick_expectation/apply_subjective_surprise.


class UserState:
    """Зеркало Neurochem для пользователя. Питается наблюдаемыми сигналами."""

    def __init__(self,
                 dopamine: float = 0.5,
                 serotonin: float = 0.5,
                 norepinephrine: float = 0.5,
                 burnout: float = 0.0,
                 agency: float = 0.5,
                 *,
                 rgk: "Optional[РГК]" = None):
        # B0: optional shared RGK (production bootstrap передаёт singleton
        # через get_global_rgk() — каскад зеркал работает на одном объекте).
        # Default rgk=None → создаётся новый РГК (backward-compat для тестов).
        # B4 Wave 2: bespoke user-side state (HRV/activity/day_summary/focus_residue/
        # _last_*_ts) перемещён в РГК.__init__ — UserState теперь thin facade
        # с @property proxies (см. ниже). Default values те же.
        self._rgk = rgk if rgk is not None else РГК()
        self._rgk.user.gain.value = dopamine
        self._rgk.user.hyst.value = serotonin
        self._rgk.user.aperture.value = norepinephrine
        self._rgk.burnout.value = burnout
        self._rgk.agency.value = agency

        # _feedback_counts живёт в `_rgk._fb` (shared с u_feedback). Здесь
        # не дублируем — `apply_feedback` читает/пишет напрямую туда.

    # ── Neurochemical mirrors (read/write через _rgk) ──────────────────────

    @property
    def dopamine(self) -> float:
        return float(self._rgk.user.gain.value)

    @dopamine.setter
    def dopamine(self, v: float):
        self._rgk.user.gain.value = max(0.0, min(1.0, float(v)))

    @property
    def serotonin(self) -> float:
        return float(self._rgk.user.hyst.value)

    @serotonin.setter
    def serotonin(self, v: float):
        self._rgk.user.hyst.value = max(0.0, min(1.0, float(v)))

    @property
    def norepinephrine(self) -> float:
        return float(self._rgk.user.aperture.value)

    @norepinephrine.setter
    def norepinephrine(self, v: float):
        self._rgk.user.aperture.value = max(0.0, min(1.0, float(v)))

    @property
    def valence(self) -> float:
        return float(self._rgk.valence.value)

    @valence.setter
    def valence(self, v: float):
        self._rgk.valence.value = max(-1.0, min(1.0, float(v)))

    @property
    def burnout(self) -> float:
        return float(self._rgk.burnout.value)

    @burnout.setter
    def burnout(self, v: float):
        self._rgk.burnout.value = max(0.0, min(1.0, float(v)))

    @property
    def agency(self) -> float:
        return float(self._rgk.agency.value)

    @agency.setter
    def agency(self, v: float):
        self._rgk.agency.value = max(0.0, min(1.0, float(v)))

    # ── Phase D: 5-axis chem (ACh + GABA доступны как property) ─────────────
    # Default 0.5 без feeders. Feeders подключены в cognitive_loop (Step 5c/d).
    # См. docs/neurochem-design.md §6 «User-side ACh + GABA feeders».

    @property
    def acetylcholine(self) -> float:
        """Plasticity (текучесть ткани, открытость новому).
        Fed by feed_acetylcholine(novelty, boost) — message novelty + surprise."""
        return float(self._rgk.user.plasticity.value)

    @acetylcholine.setter
    def acetylcholine(self, v: float):
        self._rgk.user.plasticity.value = max(0.0, min(1.0, float(v)))

    @property
    def gaba(self) -> float:
        """Damping (стенки стоячей волны, чёткость границ).
        Fed by feed_gaba() — производная от focus_residue (existing field)."""
        return float(self._rgk.user.damping.value)

    @gaba.setter
    def gaba(self, v: float):
        self._rgk.user.damping.value = max(0.0, min(1.0, float(v)))

    def balance(self) -> float:
        """5-axis резонансный баланс юзера: (DA·NE·ACh) / (5HT·GABA).
        ≈1.0 = резонанс; >1.5 гиперрезонанс; <0.5 гипостабильность.
        До интеграции feeders ACh/GABA = 0.5, формула эквивалентна (DA·NE)/5HT.
        См. docs/neurochem-design.md § Балансовая формула."""
        return self._rgk.user.balance()

    @property
    def mode(self) -> str:
        """R/C bit (Правило 7 — Counter-wave). 'R' = passive resonance,
        'C' = counter-wave generation. Updated by cognitive_loop._advance_tick
        from sync_error через гистерезис ACT=0.15 / REC=0.08."""
        return self._rgk.user.mode

    def update_mode(self, perturbation: float) -> str:
        """Update R/C mode by current perturbation (sync_error). Hysteresis."""
        return self._rgk.user.update_mode(float(perturbation))

    # ── Predictive layer accessors ─────────────────────────────────────────

    @property
    def expectation(self) -> float:
        return float(self._rgk.u_exp.value)

    @expectation.setter
    def expectation(self, v: float):
        self._rgk.u_exp.value = max(0.0, min(1.0, float(v)))

    @property
    def expectation_by_tod(self) -> dict:
        """Snapshot-копия 4 TOD-baselines. Для мутации — через tick_expectation()."""
        return {tod: float(self._rgk.u_exp_tod[tod].value) for tod in _TOD_NAMES}

    @property
    def expectation_vec(self) -> np.ndarray:
        return self._rgk.u_exp_vec.value

    @expectation_vec.setter
    def expectation_vec(self, v):
        arr = np.asarray(v, dtype=np.float32)
        ema = self._rgk.u_exp_vec
        if arr.shape == ema.value.shape:
            ema.value = np.clip(arr, 0.0, 1.0).astype(np.float32)

    @property
    def hrv_baseline_by_tod(self) -> dict:
        """Snapshot-копия. None за TOD где baseline ещё не seeded."""
        result = {}
        for tod in _TOD_NAMES:
            ema = self._rgk.hrv_base_tod[tod]
            result[tod] = float(ema.value) if ema._seeded else None
        return result

    # ── B4 Wave 2: user-side bespoke state (proxy на _rgk) ──────────────────
    # Переехало из UserState fields в РГК.__init__. UserState facade — thin
    # @property proxies. Сохранено backward-compat: read/write через те же
    # имена (50+ callers не трогаются). Default values, init и сериализация
    # работают через РГК.

    @property
    def hrv_coherence(self):
        return self._rgk.hrv_coherence

    @hrv_coherence.setter
    def hrv_coherence(self, v):
        self._rgk.hrv_coherence = v

    @property
    def hrv_stress(self):
        return self._rgk.hrv_stress

    @hrv_stress.setter
    def hrv_stress(self, v):
        self._rgk.hrv_stress = v

    @property
    def hrv_rmssd(self):
        return self._rgk.hrv_rmssd

    @hrv_rmssd.setter
    def hrv_rmssd(self, v):
        self._rgk.hrv_rmssd = v

    @property
    def activity_magnitude(self) -> float:
        return self._rgk.activity_magnitude

    @activity_magnitude.setter
    def activity_magnitude(self, v):
        self._rgk.activity_magnitude = float(v)

    @property
    def last_sleep_duration_h(self):
        return self._rgk.last_sleep_duration_h

    @last_sleep_duration_h.setter
    def last_sleep_duration_h(self, v):
        self._rgk.last_sleep_duration_h = v

    @property
    def cognitive_load_today(self) -> float:
        return self._rgk.cognitive_load_today

    @cognitive_load_today.setter
    def cognitive_load_today(self, v):
        self._rgk.cognitive_load_today = float(v)

    @property
    def day_summary(self) -> dict:
        return self._rgk.day_summary

    @day_summary.setter
    def day_summary(self, v):
        self._rgk.day_summary = v

    @property
    def focus_residue(self) -> float:
        return self._rgk.focus_residue

    @focus_residue.setter
    def focus_residue(self, v):
        self._rgk.focus_residue = float(v)

    @property
    def _last_focus_input_ts(self):
        return self._rgk._last_focus_input_ts

    @_last_focus_input_ts.setter
    def _last_focus_input_ts(self, v):
        self._rgk._last_focus_input_ts = v

    @property
    def _last_focus_mode_id(self):
        return self._rgk._last_focus_mode_id

    @_last_focus_mode_id.setter
    def _last_focus_mode_id(self, v):
        self._rgk._last_focus_mode_id = v

    @property
    def _last_input_ts(self):
        return self._rgk._last_input_ts

    @_last_input_ts.setter
    def _last_input_ts(self, v):
        self._rgk._last_input_ts = v

    @property
    def _surprise_boost_remaining(self) -> int:
        return self._rgk._surprise_boost_remaining

    @_surprise_boost_remaining.setter
    def _surprise_boost_remaining(self, v):
        self._rgk._surprise_boost_remaining = int(v)

    @property
    def _last_user_surprise_ts(self):
        return self._rgk._last_user_surprise_ts

    @_last_user_surprise_ts.setter
    def _last_user_surprise_ts(self, v):
        self._rgk._last_user_surprise_ts = v

    # ── HRV signal ─────────────────────────────────────────────────────────

    def update_from_hrv(self,
                        coherence: Optional[float] = None,
                        stress: Optional[float] = None,
                        rmssd: Optional[float] = None,
                        activity: Optional[float] = None):
        """HRV → serotonin (coherence) + norepinephrine (stress) + activity passthrough.

        coherence ∈ [0,1] → serotonin EMA (спокойствие = стабильность)
        stress ∈ [0,1] → norepinephrine EMA (напряжение)
        rmssd mapped to stress if stress отсутствует (lower RMSSD = higher stress).
        activity ∈ [0, 5] — L2 magnitude движения от акселерометра. Отдельный
        канал для 4-зонной классификации (см. `activity_zone`).

        Side-effect: если coherence не None, обновляет
        `hrv_baseline_by_tod[current_tod]` — per-TOD HRV baseline для PE.
        """
        # Derive stress from rmssd if needed
        if rmssd is not None:
            self.hrv_rmssd = float(rmssd)
            if stress is None:
                stress = max(0.0, min(1.0, 1.0 - (self.hrv_rmssd / 80.0)))

        if coherence is not None:
            self.hrv_coherence = max(0.0, min(1.0, float(coherence)))
            self._rgk.user.hyst.feed(self.hrv_coherence)
            self._rgk.hrv_base_tod[self._current_tod()].feed(self.hrv_coherence)

        if stress is not None:
            self.hrv_stress = max(0.0, min(1.0, float(stress)))
            self._rgk.user.aperture.feed(self.hrv_stress)

        if activity is not None:
            self.activity_magnitude = max(0.0, min(5.0, float(activity)))

        self.tick_expectation()

    @staticmethod
    def _current_tod() -> str:
        """Одинаковая нарезка что в graph_logic._current_snapshot и
        cognitive_loop._generate_sync_seeking_message: 4 окна по 6 часов.
        """
        import datetime as _dt
        h = _dt.datetime.now().hour
        if 5 <= h < 11:   return "morning"
        if 11 <= h < 17:  return "day"
        if 17 <= h < 23:  return "evening"
        return "night"

    # ── Timing / engagement ────────────────────────────────────────────────

    def register_input(self, now: Optional[float] = None):
        """Просто запоминает timestamp последнего user input.

        Раньше здесь были EMA-вклады в dopamine и valence от timing gap
        (см. docstring модуля). Убраны — gap не отражает «интерес» или
        «приятно», чаще это просто режим работы юзера. Таймстамп остаётся
        для UI «давно ли писал» и будущего Active sync-seeking.
        """
        self._last_input_ts = now or time.time()

    # ── Focus residue (см. docs/resonance-model.md) ───────────────────────

    def bump_focus_residue(self, mode_id: Optional[str], now: Optional[float] = None):
        """Учесть переключение/rapid input в focus_residue.

        Источники приращения:
          • +0.05 если предыдущий user input был < 30 сек назад (rapid input)
          • +0.15 если mode_id отличается от предыдущего (mode switch)

        Тимer и mode tracking хранятся в `_last_focus_input_ts` и
        `_last_focus_mode_id` отдельно от register_input'а — чтобы не было
        race conditions с порядком вызова.

        Вызывается из `record_action` при `actor=user`.
        """
        if now is None:
            now = time.time()
        # Rapid input bump
        if (self._last_focus_input_ts is not None
                and (now - self._last_focus_input_ts) < 30):
            self.focus_residue = min(1.0, self.focus_residue + 0.05)
        # Mode switch bump
        if (mode_id and self._last_focus_mode_id
                and mode_id != self._last_focus_mode_id):
            self.focus_residue = min(1.0, self.focus_residue + 0.15)
        self._last_focus_mode_id = mode_id or self._last_focus_mode_id
        self._last_focus_input_ts = now

    def decay_focus_residue(self, dt_seconds: float):
        """Естественное затухание focus_residue по времени.

        −0.05 за минуту покоя. Вызывается из cognitive_loop._advance_tick
        с реальным `dt` между tick'ами.
        """
        if dt_seconds <= 0:
            return
        self.focus_residue = max(0.0,
            self.focus_residue - 0.05 * (dt_seconds / 60.0))

    def update_from_engagement(self, signal: float = 0.65):
        """Мягкий EMA-вклад в dopamine от факта вовлечённости юзера
        (он написал / нажал кнопку / создал цель).

        Не путать со старым `update_from_timing`: там signal зависел от
        timing gap (быстро = 0.8, долго = 0.2), что шумно. Здесь signal
        константный и небольшой — просто маркёр «юзер активен, не apathy».
        EMA decay 0.95 → одно сообщение даёт +0.007 к dopamine (если был 0.5).
        За серию из 20 сообщений dopamine поднимается к ~0.60.

        Вызывается рядом с `register_input()` в /assist и других user-initiated
        endpoint'ах. Парный feeder для `update_from_chat_sentiment` (valence).
        """
        sig = max(0.0, min(1.0, float(signal)))
        self._rgk.user.gain.feed(sig)

    # ── Feedback → dopamine + burnout ──────────────────────────────────────

    def update_from_feedback(self, kind: str):
        """accept → dopamine + valence ↑; reject → burnout + valence ↓; ignore → нейтрально.

        Valence — основной канал сюда: feedback юзера явно даёт знак переживания.
        Плюс: streak of rejects накапливает negative bias (3 reject подряд →
        ощутимый спад valence).

        EMA feeds идут через registry (decay_override=0.9 для dopamine+valence).
        Burnout additive (+0.05 на reject) и streak-bias остаются bespoke —
        это дискретные bumps, не baseline EMA.
        """
        fb = self._rgk._fb  # shared counter (no duplicate)
        if kind not in fb:
            return
        fb[kind] = fb[kind] + 1

        ov = Decays.USER_DOPAMINE_FEEDBACK
        if kind == "accepted":
            self._rgk.user.gain.feed(0.9, decay_override=ov)
            self._rgk.valence.feed(0.7, decay_override=ov)
        elif kind == "rejected":
            self._rgk.user.gain.feed(0.2, decay_override=ov)
            self._rgk.valence.feed(-0.7, decay_override=ov)
            # Bespoke additive: burnout bump (не EMA — discrete step)
            bn = self._rgk.burnout
            bn.value = max(0.0, min(1.0, bn.value + 0.05))
            # Streak bias: чем больше подряд rejects, тем жёстче спад valence
            diff = fb.get("rejected", 0) - fb.get("accepted", 0)
            if diff >= 3:
                val = self._rgk.valence
                val.value = max(-1.0, min(1.0,
                    val.value - 0.05 * min(5, diff - 2)))
        # "ignored" — только counter, EMA не трогаем

        self.tick_expectation()

    # ── Chat sentiment feeder (Action Memory этап 4) ───────────────────────

    def update_from_chat_sentiment(self, sentiment: float):
        """EMA-вклад в valence от sentiment последнего user-сообщения.

        Высокочастотный сигнал (каждое сообщение) → мягкий EMA с decay 0.92
        (baseline живёт ~12 сообщений). Это дополнение к редкому feedback
        (accept/reject) — теперь valence отражает и «серию положительных
        сообщений» / «раздражённый день», а не только clicks по карточкам.

        Sentiment ∈ [−1, 1] — см. `src/sentiment.py` `classify_message_sentiment`.
        """
        self._rgk.valence.feed(max(-1.0, min(1.0, float(sentiment))))

    # ── Agency (5-я ось, OQ #2) ────────────────────────────────────────────

    def update_from_plan_completion(self, completed: int, planned: int):
        """Обновить `agency` EMA на основе сегодняшней completion ratio.

        `agency = completed / max(1, planned)` с мягким EMA (decay 0.95 —
        baseline живёт ~20 обновлений, день-два реальной динамики).

        Agency как метрика = «чувство контроля над днём». Low agency при
        high energy = learned helplessness (нет сил идти вперёд даже когда
        ресурс есть). Разукрупнение задач помогает больше чем «отдохни».

        Не меняет dopamine/serotonin — агенство это отдельное измерение,
        не derivative от существующих. Пока не в `vector()`, через 2 недели
        решим включать.

        Args:
            completed: сколько запланированных задач сделано сегодня.
            planned: сколько было запланировано (recurring + oneshot).
                     Если 0 — метрика не обновляется (нет данных).
        """
        if planned is None or planned <= 0:
            return
        ratio = max(0.0, min(1.0, float(completed or 0) / float(planned)))
        self._rgk.agency.feed(ratio)

    # ── Energy → burnout ───────────────────────────────────────────────────

    def update_from_energy(self, decisions_today: int, max_budget: float = 100.0):
        """Счётчик решений → burnout EMA.

        Каждое решение стоит ~6 энергии (см. _compute_energy в assistant.py).
        Burnout накапливается монотонно за день: decisions * 6 / max_budget.
        Сбрасывается в полночь через _ensure_daily_reset.
        """
        sig = min(1.0, max(0.0,
            float(decisions_today) * 6.0 / float(max_budget or 100.0)))
        self._rgk.burnout.feed(sig)
        self.tick_expectation()

    # ── Helpers ────────────────────────────────────────────────────────────

    def vector(self) -> np.ndarray:
        """3D точка состояния для sync-метрики. Burnout отдельно (см. module doc)."""
        return self._rgk.user.vector()

    def state_level(self) -> float:
        """Агрегированный «уровень» юзера — mean(dopamine, serotonin).

        Используется в пороге sync_regime (см. STATE_HIGH/LOW_THRESHOLD).
        """
        return float((self.dopamine + self.serotonin) / 2.0)

    # ── Предиктивная модель: expectation EMA + surprise ────────────────────

    def tick_expectation(self):
        """Обновить все три baseline'а: global scalar, TOD-scoped scalar, 3D vector.

        Вызывается автоматически после каждого `update_from_*` сигнала.
        Три EMA параллельны, но дают разные срезы PE:
          • `expectation` — legacy averaged baseline (UI-compat)
          • `expectation_by_tod[cur]` — для surprise specific к времени суток
          • `expectation_vec` — по-осевой (для `surprise_vec` и 3D imbalance)

        Decay 0.97–0.98 → baseline переживает ~50 обновлений, дни а не минуты.
        Если `_surprise_boost_remaining > 0` — используем fast-decay override
        (0.85 / 0.80). Счётчик декрементится.
        """
        if self._surprise_boost_remaining > 0:
            scalar_override = Decays.EXPECTATION_FAST
            vec_override = Decays.EXPECTATION_VEC_FAST
            self._surprise_boost_remaining -= 1
        else:
            scalar_override = None
            vec_override = None

        sl = self.state_level()
        tod = self._current_tod()
        v = self.vector()

        if scalar_override is None:
            self._rgk.u_exp.feed(sl)
            self._rgk.u_exp_tod[tod].feed(sl)
            self._rgk.u_exp_vec.feed(v)
        else:
            self._rgk.u_exp.feed(sl, decay_override=scalar_override)
            self._rgk.u_exp_tod[tod].feed(sl, decay_override=scalar_override)
            self._rgk.u_exp_vec.feed(v, decay_override=vec_override)

    def apply_subjective_surprise(self,
                                    signed_surprise: float,
                                    blend: float = 0.4):
        """Nudge `expectation` baseline из субъективного наблюдения surprise.

        Используется когда у нас есть явный user-ввод вроде «ожидал лёгкий
        день, вышел сложный» (checkin) или «plan_expected_difficulty vs
        actual_difficulty» (plan complete) — semantic-preserving fix от
        старого `user.surprise = blend * user.surprise + (1-blend) * s`
        (broken после того как surprise стал derived @property).

        Алгебра: хотим чтобы next-step `surprise = reality - expectation`
        трендил к signed_surprise. Значит `new_expectation = reality -
        signed_surprise`, feed expectation EMA с decay override = 1 - blend.

        Args:
            signed_surprise: нормированный сигнал в [-1, 1]. Positive →
                реальность лучше ожиданий; negative → хуже.
            blend: сколько веса у наблюдения (0..1). 0.4 = 40% влияния.
        """
        target = max(0.0, min(1.0,
            self.state_level() - float(signed_surprise)))
        override = max(0.001, min(0.999, 1.0 - float(blend)))
        self._rgk.u_exp.feed(target, decay_override=override)
        tod = self._current_tod()
        self._rgk.u_exp_tod[tod].feed(target, decay_override=override)

    def apply_checkin(self,
                       stress: Optional[float] = None,
                       focus: Optional[float] = None,
                       reality: Optional[float] = None):
        """Process a manual check-in: stress (0-100) → NE, focus (0-100) → 5HT,
        reality (-2..+2) → valence. Aggressive decay overrides из Decays.CHECKIN_*.

        Заменяет legacy `metrics.fire_event("checkin", ...)` после Phase D
        Step 3c. Identity сохраняется — те же EMAs, тот же decay, тот же signal.
        """
        if stress is not None:
            self._rgk.user.aperture.feed(float(stress) / 100.0,
                                          decay_override=Decays.CHECKIN_STRESS)
        if focus is not None:
            self._rgk.user.hyst.feed(float(focus) / 100.0,
                                      decay_override=Decays.CHECKIN_FOCUS)
        if reality is not None:
            self._rgk.valence.feed(float(reality) / 2.0,
                                    decay_override=Decays.CHECKIN_VALENCE)

    def apply_surprise_boost(self, n_ticks: int = SURPRISE_BOOST_DEFAULT_TICKS):
        """Сигнал «юзер только что удивился чему-то» (OQ #7).

        Переключает `tick_expectation` в fast-decay режим на N tick'ов.
        Типичное N=3 → ~6 tick'ов до полного подстраивания expectation к
        новому baseline (юзер увидел что-то что меняет его модель мира —
        мы не должны продолжать опираться на старые предсказания).

        Идемпотентно: повторный вызов во время активного boost только
        **продлевает** счётчик если новое значение больше текущего.
        """
        n = max(0, int(n_ticks))
        if n > self._surprise_boost_remaining:
            self._surprise_boost_remaining = n
        self._last_user_surprise_ts = time.time()

    # ── Phase D Step 5: ACh + GABA feeders ─────────────────────────────────

    def feed_acetylcholine(self, novelty: float, boost: bool = False):
        """Plasticity feeder — открытость новому.

        novelty ∈ [0, 1] — обычно `distinct(latest_msg, recent_5)` от
        cognitive_loop. Высокая = темы юзера прыгают / новый паттерн →
        ACh растёт → ткань становится более пластичной.

        boost=True — детект user-side surprise (apply_surprise_boost
        triggered) → принудительный bump до 0.85 с быстрым decay.

        v1 ОГРАНИЧЕНИЕ: novelty считается через distinct() embedding-метрику
        cognitive_loop'а, callers ответственны за качество. Если distinct
        шумит — ACh шумит. Калибровка через 2 нед use, см. docs/world-model.md
        и docs/neurochem-design.md §6.
        """
        sig = max(0.0, min(1.0, float(novelty)))
        if boost:
            self._rgk.user.plasticity.feed(max(sig, 0.85), decay_override=0.85)
        else:
            self._rgk.user.plasticity.feed(sig)

    def feed_gaba(self):
        """Damping feeder — derived из focus_residue (existing field).

        Высокий focus_residue = много переключений / rapid input →
        низкий GABA (волна расползается).
        Низкий focus_residue = стабильная работа → высокий GABA
        (узкая чистая волна).

        v1 ОГРАНИЧЕНИЕ: redirect existing focus_residue, не новый источник.
        Breathing detection (low NE + high HRV coh + slow input rate) пока
        не реализована — см. docs/neurochem-design.md §6 для opt-in
        второго feeder.
        """
        sig = max(0.0, min(1.0, 1.0 - float(self.focus_residue)))
        self._rgk.user.damping.feed(sig)

    @property
    def reality(self) -> float:
        """Current observed state_level (для симметрии с MindBalance ID/IP)."""
        return self.state_level()

    @property
    def surprise(self) -> float:
        """Signed prediction error: reality − expectation_by_tod[current].

        Положительный → реальность лучше ожиданий (подъём).
        Отрицательный → реальность хуже (спад, разочарование).
        TOD-scoped: утренняя apathy не маскирует вечернюю. Если TOD baseline
        ещё в default (0.5) — fallback на global expectation.
        """
        tod = self._current_tod()
        ref = float(self._rgk.u_exp_tod[tod].value)
        if ref == 0.5:
            ref = float(self._rgk.u_exp.value)
        return float(self.reality - ref)

    @property
    def surprise_vec(self) -> np.ndarray:
        """3D PE-вектор: vector() − expectation_vec. По-осевой signed error."""
        return self.vector() - self.expectation_vec

    @property
    def imbalance(self) -> float:
        """‖surprise_vec‖ — 3D магнитуда PE. [0, √3≈1.732].

        До 2026-04-23 был |scalar surprise| в [0, 1] — теряла информацию
        когда оси двигались разнонаправленно. 3D L2 честнее: падение DA
        при стабильном S/NE ≠ падение S при стабильных DA/NE.
        """
        return float(np.linalg.norm(self.surprise_vec))

    # ── PE attribution ────────────────────────────────────────────────────
    # B4 Wave 1: формулы переехали в РГК.project("user_state"). Properties —
    # тонкие delegates (один dict за access; для UI/diagnostic нагрузки ОК).

    AXIS_NAMES = ("dopamine", "serotonin", "norepinephrine")

    @property
    def attribution(self) -> str:
        """Какая ось доминирует в surprise_vec — 'dopamine'/'serotonin'/
        'norepinephrine' либо 'none' при |vec| < 0.05."""
        return self._rgk.project("user_state")["attribution"]

    @property
    def attribution_magnitude(self) -> float:
        return self._rgk.project("user_state")["attribution_magnitude"]

    @property
    def attribution_signed(self) -> float:
        """Signed amount по доминирующей оси. Positive = reality выше ожиданий."""
        return self._rgk.project("user_state")["attribution_signed"]

    @property
    def agency_gap(self) -> float:
        """1 − agency. Goal-prediction-error (complementary к state_level PE)."""
        return self._rgk.project("user_state")["agency_gap"]

    @property
    def hrv_surprise(self) -> float:
        """|hrv_coherence − baseline[current_tod]|. B4 Wave 2: формула в РГК."""
        return self._rgk.hrv_surprise()

    # ── Frequency regime (resonance несущая частота) ───────────────────────

    @property
    def frequency_regime(self) -> str:
        """Несущая частота: long_wave/short_wave/mixed/flat. Формула в РГК.
        См. docs/hrv-design.md § Frequency regime."""
        return self._rgk.frequency_regime()

    # ── Capacity zone (Phase C, 3-зона) ───────────────────────────────────

    @property
    def capacity_zone(self) -> str:
        """green/yellow/red зона из 3 индикаторов (физио / эмо / когнитивный).

        Заменяет dual-pool «энергия 0..100». Decision gate в assistant.py
        читает эту property + capacity_reason для explanation при отказе.

        Spec: docs/capacity-design.md §Capacity-зона.
        """
        return self._rgk.project("capacity")["zone"]

    @property
    def capacity_reason(self) -> list[str]:
        """Причины не-зелёной зоны (tags для UI и decision gate explanation).

        Возможные tags: "hrv_coherence_low", "burnout_high",
        "serotonin_low", "dopamine_low", "cogload_high".
        Пустой list когда все 3 ok (зона green).
        """
        return compute_capacity_indicators(self)["reasons"]

    @property
    def capacity_indicators(self) -> dict:
        """Полный snapshot для UI: 3 boolean + reasons."""
        return compute_capacity_indicators(self)

    def update_cognitive_load(self) -> None:
        """Pull today's activity log + sync_error → recompute cognitive_load_today.

        Aggregates 4 observable из activity_log:
            tasks_started     — count(start events today)
            tasks_completed   — count(done activities today)
            context_switches  — count(stop_reason="switch")
            complexity_sum    — sum(surprise_at_start over today's activities)

        Plus 1 derived:
            progress_delta    — sync_error_slow_now − sync_error_at_dawn

        Saves в `day_summary[today_str]` + recomputes `cognitive_load_today`
        через `compute_cognitive_load` helper.

        Вызывается из cognitive_loop._check_cognitive_load_update раз в 5 мин.
        """
        import datetime as _dt
        today = _dt.date.today()
        today_str = today.strftime("%Y-%m-%d")

        # Pull today's activities from activity_log
        tasks_started = 0
        tasks_completed = 0
        context_switches = 0
        complexity_sum = 0.0
        try:
            from .activity_log import _replay
            start_of_day = _dt.datetime.combine(
                today, _dt.time.min).timestamp()
            for act in _replay().values():
                started = act.get("started_at") or 0
                if started < start_of_day:
                    continue
                tasks_started += 1
                if act.get("status") == "done":
                    tasks_completed += 1
                if act.get("stop_reason") == "switch":
                    context_switches += 1
                complexity_sum += float(act.get("surprise_at_start") or 0.0)
        except Exception:
            pass

        # sync_error progress: now − at_dawn (snapshot в day_summary)
        today_summary = self.day_summary.setdefault(today_str, {})
        sync_at_dawn = today_summary.get("sync_error_at_dawn")
        sync_now = sync_at_dawn or 0.0
        try:
            from .horizon import get_global_state
            sync_now = float(get_global_state().freeze.sync_error_ema_slow)
            if sync_at_dawn is None:
                # Первый update в день — фиксируем dawn как текущий sync_error
                sync_at_dawn = sync_now
                today_summary["sync_error_at_dawn"] = round(sync_at_dawn, 6)
        except Exception:
            sync_at_dawn = sync_at_dawn or 0.0
        progress_delta = sync_now - (sync_at_dawn or 0.0)

        # Update today's aggregate
        today_summary.update({
            "tasks_started": tasks_started,
            "tasks_completed": tasks_completed,
            "context_switches": context_switches,
            "complexity_sum": round(complexity_sum, 4),
            "progress_delta": round(progress_delta, 6),
        })

        # Recompute load
        self.cognitive_load_today = compute_cognitive_load(
            today_summary, progress_delta)

    def rollover_day(self, hrv_recovery: Optional[float] = None) -> None:
        """Полуночный reset: persist yesterday в day_summary, обнулить load.

        Ровно один раз в день (вызов идемпотентен через date check). Saves:
            - cognitive_load_today as final cognitive_load в day_summary[yesterday]
            - sync_error_at_dawn для следующего дня (snapshot для progress_delta)
        Resets:
            - cognitive_load_today = 0.0

        hrv_recovery — параметр сохранён для backward-compat call signature
        (Phase A/B вызывали с recovery), сейчас не используется (после Шага 6
        long_reserve recovery удалён).

        Spec: docs/capacity-design.md §Поля UserState.
        """
        import datetime as _dt
        today = _dt.date.today()
        yesterday_str = (today - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
        today_str = today.strftime("%Y-%m-%d")

        # Save yesterday's final load
        if yesterday_str in self.day_summary:
            self.day_summary[yesterday_str]["cognitive_load"] = round(
                self.cognitive_load_today, 4)

        # Snapshot sync_error для today (нужен для progress_delta)
        try:
            from .horizon import get_global_state
            sync_at_dawn = float(
                get_global_state().freeze.sync_error_ema_slow)
        except Exception:
            sync_at_dawn = 0.0
        self.day_summary.setdefault(today_str, {})
        self.day_summary[today_str]["sync_error_at_dawn"] = round(sync_at_dawn, 6)

        # Reset live field
        self.cognitive_load_today = 0.0

        # Trim history: keep only last 30 days
        if len(self.day_summary) > 30:
            cutoff = sorted(self.day_summary.keys())[:-30]
            for k in cutoff:
                self.day_summary.pop(k, None)

    # ── Activity zone (4 региона HRV × акселерометр) ───────────────────────

    @property
    def activity_zone(self) -> dict:
        """4-зонная classification (HRV coherence × activity_magnitude).
        recovery/stress_rest/healthy_load/overload. B4 Wave 2: формула в РГК."""
        return self._rgk.activity_zone()

    # ── Named state (8-region РГК-карта) ──────────────────────────────────

    @property
    def named_state(self) -> dict:
        """Ближайший регион РГК-карты по 5D chem-профилю (Wave 3: проектор
        на РГК). Возвращает {key, label, advice, emoji, distance, coord} —
        emoji даёт визуальную метку для UI (🔵🟢🟠🟡🔴⚫⚪✨).
        См. [user_state_map.py](user_state_map.py) для координат регионов."""
        return self._rgk.project("named_state")

    # Phase C Шаг 6: dual-pool energy methods (energy_snapshot,
    # debit_energy, recover_long_reserve) удалены — заменены 3-zone capacity
    # model. capacity_zone / capacity_indicators properties выше.

    def to_dict(self) -> dict:
        ns = self.named_state
        az = self.activity_zone
        return {
            "dopamine": round(self.dopamine, 3),
            "serotonin": round(self.serotonin, 3),
            "norepinephrine": round(self.norepinephrine, 3),
            # Phase D: 5-axis chem + balance diagnostic
            "acetylcholine": round(self.acetylcholine, 3),
            "gaba": round(self.gaba, 3),
            "balance": round(self.balance(), 3),
            "mode": self.mode,
            "burnout": round(self.burnout, 3),
            "agency": round(self.agency, 3),
            "valence": round(self.valence, 3),
            "expectation": round(self.expectation, 3),
            "expectation_by_tod": {k: round(float(v), 3)
                                    for k, v in self.expectation_by_tod.items()},
            "expectation_vec": [round(float(x), 3) for x in self.expectation_vec.tolist()],
            "hrv_baseline_by_tod": {k: (round(float(v), 3) if v is not None else None)
                                      for k, v in self.hrv_baseline_by_tod.items()},
            "reality": round(self.reality, 3),
            "surprise": round(self.surprise, 3),
            "imbalance": round(self.imbalance, 3),
            "attribution": self.attribution,
            "attribution_magnitude": round(self.attribution_magnitude, 3),
            "attribution_signed": round(self.attribution_signed, 3),
            "agency_gap": round(self.agency_gap, 3),
            "hrv_surprise": round(self.hrv_surprise, 3),
            "activity_magnitude": round(self.activity_magnitude, 3),
            "activity_zone": az,
            "named_state": {"key": ns["key"], "label": ns["label"],
                            "advice": ns["advice"],
                            "emoji": ns.get("emoji", "")},
            "frequency_regime": self.frequency_regime,
            "focus_residue": round(self.focus_residue, 3),
            "cognitive_load_today": round(self.cognitive_load_today, 3),
            "capacity_zone": self.capacity_zone,
            "capacity_reason": self.capacity_reason,
            "day_summary": self.day_summary,
            "hrv": {
                "coherence": self.hrv_coherence,
                "stress": self.hrv_stress,
                "rmssd": self.hrv_rmssd,
            } if self.hrv_coherence is not None else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "UserState":
        u = cls(
            dopamine=d.get("dopamine", 0.5),
            serotonin=d.get("serotonin", 0.5),
            norepinephrine=d.get("norepinephrine", 0.5),
            burnout=d.get("burnout", 0.0),
            agency=d.get("agency", 0.5),
        )
        # Phase D: 5-axis ACh+GABA. Default 0.5 если поле отсутствует
        # (backward-compat для legacy state.json до Phase D).
        u.acetylcholine = float(d.get("acetylcholine", 0.5))
        u.gaba = float(d.get("gaba", 0.5))
        u.expectation = float(d.get("expectation", 0.5))
        u.valence = float(d.get("valence", 0.0))
        u.activity_magnitude = float(d.get("activity_magnitude", 0.0))
        u.focus_residue = max(0.0, min(1.0,
            float(d.get("focus_residue", 0.0))))
        u.cognitive_load_today = max(0.0, min(1.0,
            float(d.get("cognitive_load_today", 0.0))))
        ds = d.get("day_summary")
        if isinstance(ds, dict):
            u.day_summary = {str(k): dict(v) for k, v in ds.items()
                              if isinstance(v, dict)}
        # Predictive layer (TOD scalar + vector + HRV baseline). Все
        # optional — если legacy dump не имеет их, defaults из __init__.
        tod_map = d.get("expectation_by_tod") or {}
        if isinstance(tod_map, dict):
            for k in _TOD_NAMES:
                if k in tod_map:
                    try:
                        ema = u._rgk.u_exp_tod[k]
                        ema.value = max(0.0, min(1.0, float(tod_map[k])))
                    except Exception:
                        pass
        vec = d.get("expectation_vec")
        if isinstance(vec, (list, tuple)) and len(vec) == 3:
            u.expectation_vec = np.array([float(x) for x in vec], dtype=np.float32)
        hrv_base = d.get("hrv_baseline_by_tod") or {}
        if isinstance(hrv_base, dict):
            for k in _TOD_NAMES:
                if k not in hrv_base:
                    continue
                v = hrv_base[k]
                ema = u._rgk.hrv_base_tod[k]
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
        u.hrv_coherence = hrv.get("coherence")
        u.hrv_stress = hrv.get("stress")
        u.hrv_rmssd = hrv.get("rmssd")
        return u


# ── System vector from Neurochem + Freeze ──────────────────────────────────

def system_vector(neuro, freeze=None) -> np.ndarray:
    """Зеркальное представление SystemState для sync-метрики.

    3D выровнено с UserState.vector(). `freeze` параметр принят для
    backward-compat (старые вызовы передавали 2 args), но не используется
    — display_burnout живёт отдельно от sync_error.
    """
    return np.array([
        neuro.dopamine,
        neuro.serotonin,
        neuro.norepinephrine,
    ], dtype=np.float32)


def system_state_level(neuro) -> float:
    """Агрегированный уровень системы — mean(dopamine, serotonin)."""
    return float((neuro.dopamine + neuro.serotonin) / 2.0)


# ── Sync error + regime ────────────────────────────────────────────────────

def compute_sync_error(user: UserState, neuro, freeze=None) -> float:
    """‖user_vec − system_vec‖ (L2, 3D). Max ≈ √3 ≈ 1.732.

    `freeze` — backward-compat аргумент, не влияет на результат.
    """
    diff = user.vector() - system_vector(neuro, freeze)
    return float(np.linalg.norm(diff))


def compute_sync_regime(user: UserState, neuro, freeze) -> str:
    """4 режима симбиоза — см. TODO.md «Симбиоз».

    FLOW    — sync высокий, оба state высокие → полный объём
    REST    — sync высокий, оба state низкие → предлагаем паузу
    PROTECT — sync низкий, user low, system high → система берёт на себя
    CONFESS — sync низкий, user high, system low → «дай мне время»

    Fallback — FLOW (default при amb).
    """
    err = compute_sync_error(user, neuro, freeze)
    u_level = user.state_level()
    s_level = system_state_level(neuro)

    sync_high = err < SYNC_HIGH_THRESHOLD

    if sync_high:
        if u_level > STATE_HIGH_THRESHOLD and s_level > STATE_HIGH_THRESHOLD:
            return FLOW
        if u_level < STATE_LOW_THRESHOLD and s_level < STATE_LOW_THRESHOLD:
            return REST
        return FLOW  # оба около середины — всё равно работаем

    # Low sync
    if u_level < STATE_LOW_THRESHOLD and s_level > STATE_HIGH_THRESHOLD:
        return PROTECT
    if u_level > STATE_HIGH_THRESHOLD and s_level < STATE_LOW_THRESHOLD:
        return CONFESS

    # Низкий sync без чёткого дисбаланса — по-умолчанию идём как FLOW,
    # но метрика sync_error сама по себе = сигнал для advice слоя
    return FLOW


# ── Global singleton ───────────────────────────────────────────────────────

_global_user: Optional[UserState] = None


def get_user_state() -> UserState:
    """Глобальный UserState — один на человека, shared across workspaces.
    B0: использует singleton РГК, чтобы каскад зеркал (UserState/Neurochem/
    ProtectiveFreeze) работал на одном объекте."""
    global _global_user
    if _global_user is None:
        from .rgk import get_global_rgk
        _global_user = UserState(rgk=get_global_rgk())
    return _global_user


def set_user_state(state: UserState):
    """Replace global user state (for tests or restart)."""
    global _global_user
    _global_user = state
