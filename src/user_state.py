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
"""
import math
import time
from typing import Optional

import numpy as np

from .ema import EMA, VectorEMA, Decays


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
LONG_RESERVE_MAX = 2000        # общий резерв (как в MindBalance v2)
LONG_RESERVE_DEFAULT = 1500    # стартовое значение (можно восстановить от hrv)
DAILY_ENERGY_MAX = 100
LONG_RESERVE_TAP_THRESHOLD = 20  # ниже daily → начинаем тратить long reserve

# Activity zone параметры (из прототипа HRV × акселерометр)
ACTIVITY_THRESHOLD = 0.5       # magnitude выше которого юзер считается «активным»
COHERENCE_HEALTHY = 0.5        # coherence выше → HRV в норме (HIGH HRV)
# 4 зоны из 2×2 грида (hrv_ok, active):
ZONE_RECOVERY = "recovery"         # !active + hrv_ok    → 🟢 здоровое восстановление
ZONE_STRESS_REST = "stress_rest"   # !active + !hrv_ok   → 🟡 беспокойство в покое
ZONE_HEALTHY_LOAD = "healthy_load" #  active + hrv_ok    → 🔵 здоровая нагрузка
ZONE_OVERLOAD = "overload"         #  active + !hrv_ok   → 🔴 перегрузка / overtraining


class UserState:
    """Зеркало Neurochem для пользователя. Питается наблюдаемыми сигналами."""

    def __init__(self,
                 dopamine: float = 0.5,
                 serotonin: float = 0.5,
                 norepinephrine: float = 0.5,
                 burnout: float = 0.0,
                 agency: float = 0.5):
        self.dopamine = dopamine
        self.serotonin = serotonin
        self.norepinephrine = norepinephrine
        self.burnout = burnout
        # Agency — 5-я ось (OQ #2). «Могу / не могу влиять на день».
        # Derived из completed/planned ratio через `update_from_plan_completion`.
        # Пока НЕ входит в `vector()` / `sync_error` — собираем данные, через
        # 2-3 недели решаем включать или нет (см. planning/open-questions.md#2).
        # Default 0.5 = нейтральный baseline пока ничего не измерили.
        self.agency = agency

        # HRV passthrough — UI читает отсюда
        self.hrv_coherence: Optional[float] = None
        self.hrv_stress: Optional[float] = None
        self.hrv_rmssd: Optional[float] = None

        # Activity magnitude (акселерометр Polar или симулятор-слайдер).
        # 0 = покой, 0.5 = порог «активен», 1.0 = ходьба, 2+ = бег.
        # `activity_zone` derived property: recovery / stress_rest / healthy_load / overload.
        self.activity_magnitude: float = 0.0

        # Валентность: приятно/неприятно ∈ [−1, 1]. Отдельный канал от arousal.
        # HRV/dopamine ловят возбуждение, но не знак переживания. Собирается
        # EMA из feedback (accept/reject), timing (engagement/silence) и
        # стрик отказов (накопительный negative bias). См. tick_valence.
        self.valence: float = 0.0

        # Predictive layer (Friston loop, 2026-04-23 — см. docs/friston-loop.md).
        # Все baseline'ы — EMA/VectorEMA из src/ema.py. Decays в Decays.* .

        # Legacy global scalar expectation (UI-compat)
        self._expectation = EMA(
            0.5, decay=Decays.EXPECTATION, bounds=(0.0, 1.0)
        )
        # TOD-scoped scalar: 4 EMA, обновляется только активное окно.
        # Без scoping утренняя и вечерняя apathy сливаются в averaged baseline.
        self._expectation_by_tod: dict = {
            tod: EMA(0.5, decay=Decays.EXPECTATION, bounds=(0.0, 1.0))
            for tod in ("morning", "day", "evening", "night")
        }
        # 3D vector expectation (зеркально vector()). По-осевой PE
        # информативнее скаляра при разнонаправленном drift'е осей.
        self._expectation_vec = VectorEMA(
            [0.5, 0.5, 0.5],
            decay=Decays.EXPECTATION_VEC,
            bounds=(0.0, 1.0),
        )
        # HRV baseline по 4-м TOD — физический PE канал (2026-04-23).
        # seed_on_first: первый замер за окно становится baseline, далее EMA.
        self._hrv_baseline_by_tod: dict = {
            tod: EMA(0.0, decay=Decays.HRV_BASELINE,
                      bounds=(0.0, 1.0), seed_on_first=True)
            for tod in ("morning", "day", "evening", "night")
        }

        # Surprise boost (OQ #7): когда юзер detect'ится как удивлённый
        # (text markers или HRV spike), на N последующих tick'ов expectation
        # EMA идёт быстрее — модель быстро адаптируется к новой реальности.
        # Decrement на каждый `tick_expectation`. 0 = нормальный режим.
        self._surprise_boost_remaining: int = 0
        # Timestamp последнего surprise event — для debouncing и UI.
        self._last_user_surprise_ts: Optional[float] = None

        # Dual-pool energy (MindBalance v2): daily + долгосрочный резерв
        self.long_reserve: float = LONG_RESERVE_DEFAULT

        # Sleep duration: восстанавливается при утреннем briefing через
        # activity_log.estimate_last_sleep_hours() — либо явная задача «Сон»,
        # либо idle-gap между последним stop вчера и первым start сегодня.
        # None = ещё не оценили за этот день.
        self.last_sleep_duration_h: Optional[float] = None

        # Timestamp последнего user input (для UI / будущего sync-seeking)
        self._last_input_ts: Optional[float] = None
        self._feedback_counts = {"accepted": 0, "rejected": 0, "ignored": 0}

    # ── Backward-compat accessors для predictive layer ─────────────────────

    @property
    def expectation(self) -> float:
        return self._expectation.value

    @expectation.setter
    def expectation(self, v: float):
        self._expectation.value = max(0.0, min(1.0, float(v)))

    @property
    def expectation_by_tod(self) -> dict:
        """Snapshot-копия dict of 4 TOD-baselines. Мутировать извне
        бесполезно — изменения не попадут в EMA objects. Для изменения
        используйте `tick_expectation()` или from_dict."""
        return {tod: ema.value for tod, ema in self._expectation_by_tod.items()}

    @property
    def expectation_vec(self) -> np.ndarray:
        return self._expectation_vec.value

    @expectation_vec.setter
    def expectation_vec(self, v):
        arr = np.asarray(v, dtype=np.float32)
        if arr.shape == self._expectation_vec.value.shape:
            self._expectation_vec.value = np.clip(arr, 0.0, 1.0).astype(np.float32)

    @property
    def hrv_baseline_by_tod(self) -> dict:
        """Snapshot-копия. None за TOD где baseline ещё не seeded."""
        return {
            tod: (ema.value if ema._seeded else None)
            for tod, ema in self._hrv_baseline_by_tod.items()
        }

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
        if coherence is not None:
            self.hrv_coherence = max(0.0, min(1.0, float(coherence)))
            self.serotonin = (Decays.USER_SEROTONIN_HRV * self.serotonin
                              + (1 - Decays.USER_SEROTONIN_HRV) * self.hrv_coherence)
            # TOD-scoped HRV baseline — seed-on-first, далее EMA (см. src/ema.py).
            tod = self._current_tod()
            self._hrv_baseline_by_tod[tod].feed(self.hrv_coherence)
        if rmssd is not None:
            self.hrv_rmssd = float(rmssd)
            if stress is None:
                stress = max(0.0, min(1.0, 1.0 - (self.hrv_rmssd / 80.0)))
        if stress is not None:
            self.hrv_stress = max(0.0, min(1.0, float(stress)))
            self.norepinephrine = (Decays.USER_NOREPINEPHRINE_HRV * self.norepinephrine
                                     + (1 - Decays.USER_NOREPINEPHRINE_HRV) * self.hrv_stress)
        if activity is not None:
            self.activity_magnitude = max(0.0, min(5.0, float(activity)))
        self._clamp()
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
        try:
            s = max(0.0, min(1.0, float(signal)))
        except Exception:
            return
        self.dopamine = (Decays.USER_DOPAMINE_ENGAGEMENT * self.dopamine
                         + (1 - Decays.USER_DOPAMINE_ENGAGEMENT) * s)
        self._clamp()

    # ── Feedback → dopamine + burnout ──────────────────────────────────────

    def update_from_feedback(self, kind: str):
        """accept → dopamine + valence ↑; reject → burnout + valence ↓; ignore → нейтрально.

        Valence — основной канал сюда: feedback юзера явно даёт знак переживания.
        Плюс: streak of rejects накапливает negative bias (3 reject подряд →
        ощутимый спад valence).
        """
        if kind not in self._feedback_counts:
            return
        self._feedback_counts[kind] = self._feedback_counts[kind] + 1
        df = Decays.USER_DOPAMINE_FEEDBACK
        vf = Decays.USER_VALENCE_FEEDBACK
        if kind == "accepted":
            self.dopamine = df * self.dopamine + (1 - df) * 0.9
            self.valence = vf * self.valence + (1 - vf) * 0.7
        elif kind == "rejected":
            self.dopamine = df * self.dopamine + (1 - df) * 0.2
            self.burnout = min(1.0, self.burnout + 0.05)
            self.valence = vf * self.valence + (1 - vf) * (-0.7)
            # Streak bias: чем больше подряд rejects, тем жёстче спад
            recent_rejects = self._feedback_counts.get("rejected", 0)
            recent_accepts = self._feedback_counts.get("accepted", 0)
            if recent_rejects - recent_accepts >= 3:
                self.valence -= 0.05 * min(5, recent_rejects - recent_accepts - 2)
        self._clamp()
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
        try:
            s = max(-1.0, min(1.0, float(sentiment)))
        except Exception:
            return
        self.valence = (Decays.USER_VALENCE_SENTIMENT * self.valence
                        + (1 - Decays.USER_VALENCE_SENTIMENT) * s)
        self._clamp()

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
        if planned <= 0:
            # Нет плана → нет сигнала. Не перезаписываем baseline шумом.
            return
        raw = max(0.0, min(1.0, completed / float(planned)))
        self.agency = (Decays.USER_AGENCY * self.agency
                       + (1 - Decays.USER_AGENCY) * raw)
        self._clamp()
        # tick_expectation() не вызываем — agency пока не в state_level

    # ── Energy → burnout ───────────────────────────────────────────────────

    def update_from_energy(self, decisions_today: int, max_budget: float = 100.0):
        """Счётчик решений → burnout EMA.

        Каждое решение стоит ~6 энергии (см. _compute_energy в assistant.py).
        Burnout накапливается монотонно за день: decisions * 6 / max_budget.
        Сбрасывается в полночь через _ensure_daily_reset.
        """
        usage = min(1.0, max(0.0, decisions_today * 6.0 / max_budget))
        self.burnout = (Decays.USER_BURNOUT_ENERGY * self.burnout
                        + (1 - Decays.USER_BURNOUT_ENERGY) * usage)
        self._clamp()
        self.tick_expectation()

    # ── Helpers ────────────────────────────────────────────────────────────

    def _clamp(self):
        self.dopamine = max(0.0, min(1.0, self.dopamine))
        self.serotonin = max(0.0, min(1.0, self.serotonin))
        self.norepinephrine = max(0.0, min(1.0, self.norepinephrine))
        self.burnout = max(0.0, min(1.0, self.burnout))
        self.agency = max(0.0, min(1.0, self.agency))
        self.expectation = max(0.0, min(1.0, self.expectation))
        self.long_reserve = max(0.0, min(float(LONG_RESERVE_MAX), self.long_reserve))
        self.valence = max(-1.0, min(1.0, self.valence))
        self.activity_magnitude = max(0.0, min(5.0, self.activity_magnitude))

    def vector(self) -> np.ndarray:
        """3D точка состояния для sync-метрики. Burnout отдельно (см. module doc)."""
        return np.array([self.dopamine, self.serotonin, self.norepinephrine],
                        dtype=np.float32)

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
          • `_expectation` — legacy averaged baseline (UI-compat)
          • `_expectation_by_tod[cur]` — для surprise specific к времени суток
          • `_expectation_vec` — по-осевой (для `surprise_vec` и 3D imbalance)

        Decay 0.97–0.98 → baseline переживает ~50 обновлений, дни а не минуты.
        Если `_surprise_boost_remaining > 0` — используем fast-decay override
        (0.85 / 0.80). Счётчик декрементится.
        """
        # Определяем override decay (surprise boost mode)
        if self._surprise_boost_remaining > 0:
            scalar_override = Decays.EXPECTATION_FAST
            vec_override = Decays.EXPECTATION_VEC_FAST
            self._surprise_boost_remaining -= 1
        else:
            scalar_override = None
            vec_override = None

        reality = self.state_level()
        tod = self._current_tod()

        self._expectation.feed(reality, decay_override=scalar_override)
        self._expectation_by_tod[tod].feed(reality, decay_override=scalar_override)
        self._expectation_vec.feed(self.vector(), decay_override=vec_override)

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
        ref_ema = self._expectation_by_tod.get(tod)
        ref = ref_ema.value if ref_ema is not None else 0.5
        if ref == 0.5:
            ref = self._expectation.value   # fallback на global
        return float(self.reality - float(ref))

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

    # ── PE attribution (OQ #6) ─────────────────────────────────────────────

    AXIS_NAMES = ("dopamine", "serotonin", "norepinephrine")

    @property
    def attribution(self) -> str:
        """Какая ось доминирует в surprise_vec — 'dopamine' | 'serotonin' |
        'norepinephrine'. Для UI «в чём именно модель ошиблась».

        Если все три близки к 0 (|vec| < 0.05) — возвращает 'none'.
        """
        vec = self.surprise_vec
        mag = float(np.linalg.norm(vec))
        if mag < 0.05:
            return "none"
        return self.AXIS_NAMES[int(np.argmax(np.abs(vec)))]

    @property
    def attribution_magnitude(self) -> float:
        """|max_axis_surprise| — насколько сильна ошибка по доминирующей оси."""
        return float(np.max(np.abs(self.surprise_vec)))

    @property
    def attribution_signed(self) -> float:
        """Signed amount по доминирующей оси. Positive = reality выше ожиданий,
        negative = ниже. Для UI-фразы «недооценил интерес» vs «переоценил стабильность».
        """
        vec = self.surprise_vec
        if np.linalg.norm(vec) < 0.05:
            return 0.0
        idx = int(np.argmax(np.abs(vec)))
        return float(vec[idx])

    @property
    def agency_gap(self) -> float:
        """1 − agency. Gap между ожиданием что юзер выполнит план и реальностью.
        Прямой goal-prediction-error (complementary к state_level PE).
        """
        return max(0.0, 1.0 - float(self.agency))

    @property
    def hrv_surprise(self) -> float:
        """|hrv_coherence − hrv_baseline_by_tod[current]|. [0, 1].

        Физический канал PE от реального тела. 0 если HRV не запущен или
        baseline ещё не seeded за это TOD (first measurement).
        """
        if self.hrv_coherence is None:
            return 0.0
        tod = self._current_tod()
        ref = self.hrv_baseline_by_tod.get(tod)
        if ref is None:
            return 0.0
        return abs(float(self.hrv_coherence) - float(ref))

    # ── Activity zone (4 региона HRV × акселерометр) ───────────────────────

    @property
    def activity_zone(self) -> dict:
        """Derived 4-зонная классификация (HRV coherence × activity_magnitude).

        Из прототипа HRV-Reader (Polar H10 + accelerometer). Даёт
        **физический контекст** поверх чисто нейрохимического состояния:
        одинаковая coherence значит разное, если юзер лежит vs бежит.

          !active & hrv_ok     → recovery       🟢 здоровое восстановление
          !active & !hrv_ok    → stress_rest    🟡 беспокойство в покое
           active & hrv_ok     → healthy_load   🔵 здоровая нагрузка
           active & !hrv_ok    → overload       🔴 перегрузка

        Если HRV не запущен (coherence=None) → zone=None.
        """
        if self.hrv_coherence is None:
            return {"key": None, "label": None, "advice": None}
        active = self.activity_magnitude >= ACTIVITY_THRESHOLD
        hrv_ok = self.hrv_coherence >= COHERENCE_HEALTHY
        if not active and hrv_ok:
            return {"key": ZONE_RECOVERY, "label": "Восстановление",
                    "advice": "Хорошее время для отдыха / медитации.",
                    "emoji": "🟢"}
        if not active and not hrv_ok:
            return {"key": ZONE_STRESS_REST, "label": "Стресс в покое",
                    "advice": "Подыши минуту. Тело в напряжении без физической нагрузки.",
                    "emoji": "🟡"}
        if active and hrv_ok:
            return {"key": ZONE_HEALTHY_LOAD, "label": "Здоровая нагрузка",
                    "advice": "Ритм хороший. Используй для дела.",
                    "emoji": "🔵"}
        return {"key": ZONE_OVERLOAD, "label": "Перегрузка",
                "advice": "Сильная активность + низкое HRV = риск overtraining. Снизь темп.",
                "emoji": "🔴"}

    # ── Named state (Voronoi) ──────────────────────────────────────────────

    @property
    def named_state(self) -> dict:
        """Ближайший именованный регион в (T, A) пространстве.

        T (emotional tone) = serotonin (стабильность, валентность)
        A (activation) = weighted mean(DA, NE) + activity_contribution
          — до этого A было чисто когнитивным arousal;
          теперь physical activity_magnitude даёт дополнительный вклад
          (клампом в [0, 1]) с весом 0.3. Бегущий юзер не может быть в
          «медитации» по когнитивным скалярам.

        Возвращает {key, label, advice, distance, coord}. 10 регионов
        из MindBalance v4 (flow / stress / burnout / curiosity / ...).
        """
        from .user_state_map import nearest_named_state
        t = self.serotonin
        cog_arousal = (self.dopamine + self.norepinephrine) / 2.0
        phys_arousal = min(1.0, self.activity_magnitude / 2.0)  # 2+ = max
        a = 0.7 * cog_arousal + 0.3 * phys_arousal
        return nearest_named_state(t, max(0.0, min(1.0, a)))

    # ── Dual-pool energy ───────────────────────────────────────────────────

    def energy_snapshot(self, decisions_today: int) -> dict:
        """Мгновенный срез дуальной энергетики.

        daily_energy   = max − decisions_today · avg_cost (рассчитывается в assistant.py)
        long_reserve   = self.long_reserve (медленный пул)
        burnout_risk   = 1 − long_reserve/LONG_RESERVE_MAX
        Возвращает dict для API + UI.
        """
        long_pct = self.long_reserve / LONG_RESERVE_MAX if LONG_RESERVE_MAX > 0 else 0.0
        return {
            "decisions_today": decisions_today,
            "long_reserve": round(self.long_reserve, 1),
            "long_reserve_max": LONG_RESERVE_MAX,
            "long_reserve_pct": round(long_pct, 3),
            "burnout_risk": round(1.0 - long_pct, 3),
        }

    def debit_energy(self, cost: float, daily_remaining: float) -> dict:
        """Списание cost из дневной энергии. Если daily < 20 → часть уходит в long.

        cost: стоимость решения (определяется mode в assistant.py)
        daily_remaining: сколько осталось daily перед этим решением
        Возвращает {daily_used, long_used} — что реально списалось откуда.
        """
        daily_used = min(cost, max(0.0, daily_remaining))
        overflow = cost - daily_used
        long_used = 0.0
        if overflow > 0 or daily_remaining < LONG_RESERVE_TAP_THRESHOLD:
            # cascading: если daily был мал, часть уходит из long
            # + full overflow идёт из long
            extra = overflow
            if daily_remaining < LONG_RESERVE_TAP_THRESHOLD and cost > 0:
                # Дополнительный tax: при low daily расход дороже
                extra += cost * 0.3
            long_used = min(extra, self.long_reserve)
            self.long_reserve -= long_used
        self._clamp()
        return {"daily_used": daily_used, "long_used": long_used}

    def recover_long_reserve(self, hrv_recovery: Optional[float] = None):
        """Ночное восстановление long_reserve (вызывается консолидацией).

        hrv_recovery ∈ [0, 1] (из energy_recovery HRV) — скейлит amount.
        Без HRV — консервативно восстанавливаем как при среднем сне (0.7).
        """
        recovery = hrv_recovery if hrv_recovery is not None else 0.7
        # MindBalance v2 defaults: sleep_recovery=90, rest_bonus=20
        amount = 90.0 * recovery + 20.0 * recovery
        self.long_reserve = min(float(LONG_RESERVE_MAX), self.long_reserve + amount)
        self._clamp()

    def to_dict(self) -> dict:
        ns = self.named_state
        az = self.activity_zone
        return {
            "dopamine": round(self.dopamine, 3),
            "serotonin": round(self.serotonin, 3),
            "norepinephrine": round(self.norepinephrine, 3),
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
            "long_reserve": round(self.long_reserve, 1),
            "activity_magnitude": round(self.activity_magnitude, 3),
            "activity_zone": az,
            "named_state": {"key": ns["key"], "label": ns["label"],
                            "advice": ns["advice"]},
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
        u.expectation = float(d.get("expectation", 0.5))
        u.long_reserve = float(d.get("long_reserve", LONG_RESERVE_DEFAULT))
        u.valence = float(d.get("valence", 0.0))
        u.activity_magnitude = float(d.get("activity_magnitude", 0.0))
        # Predictive layer (TOD scalar + vector + HRV baseline). Все
        # optional — если legacy dump не имеет их, defaults из __init__.
        # Записываем напрямую в EMA objects (publica property возвращает snapshot,
        # мутация snapshot'а бесполезна).
        tod_map = d.get("expectation_by_tod") or {}
        if isinstance(tod_map, dict):
            for k in ("morning", "day", "evening", "night"):
                if k in tod_map:
                    try:
                        u._expectation_by_tod[k].value = max(0.0, min(1.0, float(tod_map[k])))
                    except Exception:
                        pass
        vec = d.get("expectation_vec")
        if isinstance(vec, (list, tuple)) and len(vec) == 3:
            u.expectation_vec = np.array([float(x) for x in vec], dtype=np.float32)
        hrv_base = d.get("hrv_baseline_by_tod") or {}
        if isinstance(hrv_base, dict):
            for k in ("morning", "day", "evening", "night"):
                if k not in hrv_base:
                    continue
                v = hrv_base[k]
                ema = u._hrv_baseline_by_tod[k]
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
    """Глобальный UserState — один на человека, shared across workspaces."""
    global _global_user
    if _global_user is None:
        _global_user = UserState()
    return _global_user


def set_user_state(state: UserState):
    """Replace global user state (for tests or restart)."""
    global _global_user
    _global_user = state
