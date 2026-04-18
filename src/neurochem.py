"""Нейрохимия второго мозга — три скаляра, γ derived, burnout отдельно.

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


class Neurochem:
    """Три скаляра. Реагируют на динамику графа, не на юзера напрямую."""

    RPE_WINDOW = 20    # скользящее окно для baseline Δconfidence
    RPE_GAIN = 0.15    # как сильно dopamine сдвигается на единицу RPE

    def __init__(self,
                 dopamine: float = 0.5,
                 serotonin: float = 0.5,
                 norepinephrine: float = 0.5):
        self.dopamine = dopamine
        self.serotonin = serotonin
        self.norepinephrine = norepinephrine
        self._delta_history: list = []
        self.recent_rpe: float = 0.0

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
        if d is not None:
            self.dopamine = 0.9 * self.dopamine + 0.1 * float(d)

        if w_change is not None:
            arr = np.asarray(list(w_change), dtype=np.float32)
            if arr.size:
                stability = max(0.0, 1.0 - float(np.std(arr)))
                self.serotonin = 0.95 * self.serotonin + 0.05 * stability

        if weights is not None:
            arr = np.asarray(list(weights), dtype=np.float32)
            if arr.size:
                arr = np.clip(arr, 1e-9, 1.0)
                # Normalize to probability distribution
                total = float(np.sum(arr))
                if total > 0:
                    p = arr / total
                    ent = -float(np.sum(p * np.log(p + 1e-9)))
                    # Normalize by max entropy log(n)
                    max_ent = float(np.log(max(2, arr.size)))
                    ent_norm = min(1.0, ent / max_ent) if max_ent > 0 else 0.0
                    self.norepinephrine = 0.9 * self.norepinephrine + 0.1 * ent_norm

        # Clamp [0, 1]
        self.dopamine = max(0.0, min(1.0, self.dopamine))
        self.serotonin = max(0.0, min(1.0, self.serotonin))
        self.norepinephrine = max(0.0, min(1.0, self.norepinephrine))

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
        self.dopamine = max(0.0, min(1.0, self.dopamine + self.RPE_GAIN * rpe))
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
        return n


class ProtectiveFreeze:
    """Защитный режим. Не медиатор — отдельный механизм.

    Накапливается при хроническом конфликте (d > τ_stable) в условиях низкой
    стабильности (serotonin низкий). При пороге θ активируется — Bayes update
    блокируется, система «замирает», выход по восстановлению стабильности.
    """

    TAU_STABLE = 0.6          # порог за которым d считается конфликтом
    THETA_ACTIVE = 0.15        # вход во freeze (учитывая EMA steady-state)
    THETA_RECOVERY = 0.08      # выход из freeze (гистерезис)
    DECAY = 0.95               # EMA: 0.95 значит ~20 тиков до steady state

    def __init__(self):
        self.conflict_accumulator = 0.0
        self.active = False

    def update(self, d: float = None, serotonin: float = 0.5):
        """Обновить накопитель и проверить вход/выход."""
        if d is not None:
            conflict_signal = max(0.0, float(d) - self.TAU_STABLE)
            # Накопление тем больше, чем ниже стабильность
            instability = max(0.0, 1.0 - serotonin)
            self.conflict_accumulator = (
                self.DECAY * self.conflict_accumulator
                + (1.0 - self.DECAY) * conflict_signal * instability
            )
            self.conflict_accumulator = max(0.0, min(1.0, self.conflict_accumulator))

        if self.active:
            if self.conflict_accumulator < self.THETA_RECOVERY:
                self.active = False
        else:
            if self.conflict_accumulator > self.THETA_ACTIVE:
                self.active = True

    def to_dict(self) -> dict:
        return {
            "conflict_accumulator": round(self.conflict_accumulator, 3),
            "active": self.active,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProtectiveFreeze":
        pf = cls()
        pf.conflict_accumulator = d.get("conflict_accumulator", 0.0)
        pf.active = d.get("active", False)
        return pf
