"""UserState — зеркальный вектор пользователя для прайм-директивы.

SystemState (src/neurochem.py) эволюционирует по динамике графа.
UserState эволюционирует по наблюдаемым сигналам юзера.
Прайм-директива — минимизировать ‖user − system‖.

Структура симметрична Neurochem (3 скаляра + burnout):

    dopamine       — интерес: скорость ответа, частота вовлечения, принятые предложения
    serotonin      — спокойствие/стабильность: HRV coherence, стабильность длины сообщений
    norepinephrine — напряжение: HRV stress, rapid-fire серии сообщений
    burnout        — накопленная усталость: decisions_today, rejects

Все скаляры в [0, 1]. EMA с decay, как в Neurochem — одна строка на сигнал.

sync_error = ‖user_vec − system_vec‖ (L2)
sync_regime ∈ {FLOW, REST, PROTECT, CONFESS} — derived из (sync_error, оба state).

HRV живёт здесь, не в CognitiveState. Это сигнал тела **пользователя**.
"""
import math
import time
from typing import Optional

import numpy as np


# ── Sync regime constants ───────────────────────────────────────────────────

FLOW = "flow"           # оба высокие + sync высокий → полный объём
REST = "rest"           # оба низкие + sync высокий → предлагаем паузу
PROTECT = "protect"     # user low, system high → система берёт на себя
CONFESS = "confess"     # user high, system low → «дай мне время»

# Пороги из TODO «Симбиоз»
SYNC_HIGH_THRESHOLD = 0.3      # error < 0.3 → sync высокий (в L2-norm на [0,2])
STATE_HIGH_THRESHOLD = 0.55    # mean(D,S) > 0.55 → state высокий
STATE_LOW_THRESHOLD = 0.35     # mean(D,S) < 0.35 → state низкий

# Параметры предиктивной модели (MindBalance intuition)
EXPECTATION_EMA_DECAY = 0.98   # медленный baseline — выживает через дни, не часы
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
                 burnout: float = 0.0):
        self.dopamine = dopamine
        self.serotonin = serotonin
        self.norepinephrine = norepinephrine
        self.burnout = burnout

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

        # Предиктивная модель (signed prediction error)
        # expectation = медленный EMA state_level (baseline ожидания)
        # surprise = (current state_level) − expectation, signed в [−1, 1]
        self.expectation: float = 0.5

        # Dual-pool energy (MindBalance v2): daily + долгосрочный резерв
        self.long_reserve: float = LONG_RESERVE_DEFAULT

        # Sleep duration: восстанавливается при утреннем briefing через
        # activity_log.estimate_last_sleep_hours() — либо явная задача «Сон»,
        # либо idle-gap между последним stop вчера и первым start сегодня.
        # None = ещё не оценили за этот день.
        self.last_sleep_duration_h: Optional[float] = None

        # Rolling state для timing/message variance
        self._last_input_ts: Optional[float] = None
        self._msg_lengths = []              # bounded to 10 последних
        self._feedback_counts = {"accepted": 0, "rejected": 0, "ignored": 0}

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
        """
        if coherence is not None:
            self.hrv_coherence = max(0.0, min(1.0, float(coherence)))
            self.serotonin = 0.9 * self.serotonin + 0.1 * self.hrv_coherence
        if rmssd is not None:
            self.hrv_rmssd = float(rmssd)
            if stress is None:
                stress = max(0.0, min(1.0, 1.0 - (self.hrv_rmssd / 80.0)))
        if stress is not None:
            self.hrv_stress = max(0.0, min(1.0, float(stress)))
            self.norepinephrine = 0.9 * self.norepinephrine + 0.1 * self.hrv_stress
        if activity is not None:
            self.activity_magnitude = max(0.0, min(5.0, float(activity)))
        self._clamp()
        self.tick_expectation()

    # ── Timing / engagement ────────────────────────────────────────────────

    def update_from_timing(self, now: Optional[float] = None):
        """Скорость вовлечения → dopamine + лёгкий вклад в valence.

        Быстрый повторный ввод (< 30с) → dopamine EMA растёт (интерес).
        Длинная пауза (> 5 мин) → dopamine EMA decay (охлаждение) + лёгкий
        negative vibe в valence.
        Между — нейтрально.
        """
        now = now or time.time()
        if self._last_input_ts is not None:
            gap = now - self._last_input_ts
            if gap < 30:
                signal = 0.8       # quick engagement
                val_signal = 0.2   # приятно когда хочется ещё
            elif gap > 300:
                signal = 0.2       # long silence
                val_signal = -0.2  # молчание ближе к отстранённости
            else:
                signal = 0.5
                val_signal = 0.0
            self.dopamine = 0.9 * self.dopamine + 0.1 * signal
            self.valence = 0.95 * self.valence + 0.05 * val_signal
        self._last_input_ts = now
        self._clamp()
        self.tick_expectation()

    def update_from_message(self, text: str):
        """Variance длины сообщений → serotonin (стабильный юзер = уверенный).

        Стабильная длина сообщений (низкий std) → serotonin EMA растёт.
        Скачки — нейтрально.
        """
        if not text:
            return
        self._msg_lengths.append(len(text))
        if len(self._msg_lengths) > 10:
            self._msg_lengths = self._msg_lengths[-10:]
        if len(self._msg_lengths) >= 3:
            arr = np.asarray(self._msg_lengths, dtype=np.float32)
            mean = float(np.mean(arr))
            if mean > 0:
                rel_std = float(np.std(arr)) / mean
                stability = max(0.0, 1.0 - min(1.0, rel_std))
                self.serotonin = 0.95 * self.serotonin + 0.05 * stability
        self._clamp()
        self.tick_expectation()

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
        if kind == "accepted":
            self.dopamine = 0.9 * self.dopamine + 0.1 * 0.9
            self.valence = 0.9 * self.valence + 0.1 * 0.7
        elif kind == "rejected":
            self.dopamine = 0.9 * self.dopamine + 0.1 * 0.2
            self.burnout = min(1.0, self.burnout + 0.05)
            self.valence = 0.9 * self.valence + 0.1 * (-0.7)
            # Streak bias: чем больше подряд rejects, тем жёстче спад
            recent_rejects = self._feedback_counts.get("rejected", 0)
            recent_accepts = self._feedback_counts.get("accepted", 0)
            if recent_rejects - recent_accepts >= 3:
                self.valence -= 0.05 * min(5, recent_rejects - recent_accepts - 2)
        self._clamp()
        self.tick_expectation()

    # ── Energy → burnout ───────────────────────────────────────────────────

    def update_from_energy(self, decisions_today: int, max_budget: float = 100.0):
        """Счётчик решений → burnout EMA.

        Каждое решение стоит ~6 энергии (см. _compute_energy в assistant.py).
        Burnout накапливается монотонно за день: decisions * 6 / max_budget.
        Сбрасывается в полночь через _ensure_daily_reset.
        """
        usage = min(1.0, max(0.0, decisions_today * 6.0 / max_budget))
        self.burnout = 0.9 * self.burnout + 0.1 * usage
        self._clamp()
        self.tick_expectation()

    # ── Helpers ────────────────────────────────────────────────────────────

    def _clamp(self):
        self.dopamine = max(0.0, min(1.0, self.dopamine))
        self.serotonin = max(0.0, min(1.0, self.serotonin))
        self.norepinephrine = max(0.0, min(1.0, self.norepinephrine))
        self.burnout = max(0.0, min(1.0, self.burnout))
        self.expectation = max(0.0, min(1.0, self.expectation))
        self.long_reserve = max(0.0, min(float(LONG_RESERVE_MAX), self.long_reserve))
        self.valence = max(-1.0, min(1.0, self.valence))
        self.activity_magnitude = max(0.0, min(5.0, self.activity_magnitude))

    def vector(self) -> np.ndarray:
        """4-мерная точка состояния для sync-метрики."""
        return np.array([self.dopamine, self.serotonin, self.norepinephrine, self.burnout],
                        dtype=np.float32)

    def state_level(self) -> float:
        """Агрегированный «уровень» юзера — mean(dopamine, serotonin).

        Используется в пороге sync_regime (см. STATE_HIGH/LOW_THRESHOLD).
        """
        return float((self.dopamine + self.serotonin) / 2.0)

    # ── Предиктивная модель: expectation EMA + surprise ────────────────────

    def tick_expectation(self):
        """Медленный EMA reality → expectation.

        Вызывается автоматически после каждого `update_from_*` сигнала
        (через _clamp + этот метод в обёртках). Decay 0.98 значит baseline
        переживает ~50 обновлений — дни, не минуты. Это делает surprise
        осмысленным: «против чего именно неожиданность».
        """
        reality = self.state_level()
        self.expectation = (EXPECTATION_EMA_DECAY * self.expectation
                            + (1 - EXPECTATION_EMA_DECAY) * reality)
        self.expectation = max(0.0, min(1.0, self.expectation))

    @property
    def reality(self) -> float:
        """Current observed state_level (для симметрии с MindBalance ID/IP)."""
        return self.state_level()

    @property
    def surprise(self) -> float:
        """Signed prediction error: reality − expectation. [−1, 1].

        Положительный → реальность лучше ожиданий (подъём).
        Отрицательный → реальность хуже (спад, разочарование).
        Магнитуда — это MindBalance ID (Index of Disbalance).
        """
        return float(self.reality - self.expectation)

    @property
    def imbalance(self) -> float:
        """|surprise| — магнитуда ошибки, эквивалент MindBalance ID."""
        return abs(self.surprise)

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
            "valence": round(self.valence, 3),
            "expectation": round(self.expectation, 3),
            "reality": round(self.reality, 3),
            "surprise": round(self.surprise, 3),
            "imbalance": round(self.imbalance, 3),
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
        )
        u.expectation = float(d.get("expectation", 0.5))
        u.long_reserve = float(d.get("long_reserve", LONG_RESERVE_DEFAULT))
        u.valence = float(d.get("valence", 0.0))
        u.activity_magnitude = float(d.get("activity_magnitude", 0.0))
        hrv = d.get("hrv") or {}
        u.hrv_coherence = hrv.get("coherence")
        u.hrv_stress = hrv.get("stress")
        u.hrv_rmssd = hrv.get("rmssd")
        return u


# ── System vector from Neurochem + Freeze ──────────────────────────────────

def system_vector(neuro, freeze) -> np.ndarray:
    """Зеркальное представление SystemState для sync-метрики.

    Те же 4 измерения что и UserState.vector() — выровненно поэлементно.
    """
    return np.array([
        neuro.dopamine,
        neuro.serotonin,
        neuro.norepinephrine,
        freeze.conflict_accumulator,
    ], dtype=np.float32)


def system_state_level(neuro) -> float:
    """Агрегированный уровень системы — mean(dopamine, serotonin)."""
    return float((neuro.dopamine + neuro.serotonin) / 2.0)


# ── Sync error + regime ────────────────────────────────────────────────────

def compute_sync_error(user: UserState, neuro, freeze) -> float:
    """‖user_vec − system_vec‖ (L2). Max ≈ 2.0 (каждая ось в [0,1])."""
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
