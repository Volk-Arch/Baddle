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

        # Rolling state для timing/message variance
        self._last_input_ts: Optional[float] = None
        self._msg_lengths = []              # bounded to 10 последних
        self._feedback_counts = {"accepted": 0, "rejected": 0, "ignored": 0}

    # ── HRV signal ─────────────────────────────────────────────────────────

    def update_from_hrv(self,
                        coherence: Optional[float] = None,
                        stress: Optional[float] = None,
                        rmssd: Optional[float] = None):
        """HRV → serotonin (coherence) + norepinephrine (stress).

        coherence ∈ [0,1] → serotonin EMA (спокойствие = стабильность)
        stress ∈ [0,1] → norepinephrine EMA (напряжение)
        rmssd mapped to stress if stress отсутствует (lower RMSSD = higher stress).
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
        self._clamp()

    # ── Timing / engagement ────────────────────────────────────────────────

    def update_from_timing(self, now: Optional[float] = None):
        """Скорость вовлечения → dopamine.

        Быстрый повторный ввод (< 30с) → dopamine EMA растёт (интерес).
        Длинная пауза (> 5 мин) → dopamine EMA decay (охлаждение).
        Между — нейтрально.
        """
        now = now or time.time()
        if self._last_input_ts is not None:
            gap = now - self._last_input_ts
            if gap < 30:
                signal = 0.8   # quick engagement
            elif gap > 300:
                signal = 0.2   # long silence
            else:
                signal = 0.5
            self.dopamine = 0.9 * self.dopamine + 0.1 * signal
        self._last_input_ts = now
        self._clamp()

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

    # ── Feedback → dopamine + burnout ──────────────────────────────────────

    def update_from_feedback(self, kind: str):
        """accept → dopamine ↑; reject → burnout ↑ + dopamine ↓; ignore → ничего."""
        if kind not in self._feedback_counts:
            return
        self._feedback_counts[kind] = self._feedback_counts[kind] + 1
        if kind == "accepted":
            self.dopamine = 0.9 * self.dopamine + 0.1 * 0.9
        elif kind == "rejected":
            self.dopamine = 0.9 * self.dopamine + 0.1 * 0.2
            self.burnout = min(1.0, self.burnout + 0.05)
        self._clamp()

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

    # ── Helpers ────────────────────────────────────────────────────────────

    def _clamp(self):
        self.dopamine = max(0.0, min(1.0, self.dopamine))
        self.serotonin = max(0.0, min(1.0, self.serotonin))
        self.norepinephrine = max(0.0, min(1.0, self.norepinephrine))
        self.burnout = max(0.0, min(1.0, self.burnout))

    def vector(self) -> np.ndarray:
        """4-мерная точка состояния для sync-метрики."""
        return np.array([self.dopamine, self.serotonin, self.norepinephrine, self.burnout],
                        dtype=np.float32)

    def state_level(self) -> float:
        """Агрегированный «уровень» юзера — mean(dopamine, serotonin).

        Используется в пороге sync_regime (см. STATE_HIGH/LOW_THRESHOLD).
        """
        return float((self.dopamine + self.serotonin) / 2.0)

    def to_dict(self) -> dict:
        return {
            "dopamine": round(self.dopamine, 3),
            "serotonin": round(self.serotonin, 3),
            "norepinephrine": round(self.norepinephrine, 3),
            "burnout": round(self.burnout, 3),
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
