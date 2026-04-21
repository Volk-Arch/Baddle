"""EMA helpers — scalar и vector exponential moving averages.

Единое место для всех EMA-обновлений в Baddle. До 2026-04-23 паттерн
`x = decay * x + (1-decay) * signal` был разбросан по 20+ сайтам с
8 разными decay константами. Кристаллизовано в рамках
[planning/TODO.md](../planning/TODO.md) Stage 2.

## Два режима

### Tick-constant
```python
ema = EMA(0.5, decay=0.95)
ema.feed(0.7)   # x = 0.95*x + 0.05*0.7
```
Фиксированный decay per call. Используется когда опрос равномерный.

### Time-constant (независим от частоты опроса)
```python
ema = EMA(0.0, time_const=3600.0)  # 1 hour TC
ema.feed(0.3, dt=60.0)   # alpha = 1 - exp(-60/3600) ≈ 0.0165
```
`alpha = 1 − exp(−dt / T)`. Удобно когда tick frequency adaptive
(как в cognitive_loop._advance_tick).

## Seed-on-first

```python
ema = EMA(0.0, decay=0.99, seed_on_first=True)
ema.feed(0.6)   # first call: value = 0.6 (bypass EMA)
ema.feed(0.5)   # subsequent: EMA apply
```
Для baseline'ов которые seed'ятся первым observation
(например `hrv_baseline_by_tod`).

## Temp fast-decay (surprise boost)

```python
ema = EMA(0.5, decay=0.98)
# When user surprise detected:
for _ in range(3):
    ema.feed(signal, decay_override=0.85)  # fast-decay для 3 tick'ов
```

## Serialization

`to_dict() → {"value": float, "seeded": bool}` — config (decay / bounds) не
сериализуется, пересоздаётся при load. Обычно parent class хранит только
`ema.value` в своём to_dict (под своим field name) и восстанавливает через
`ema.value = d.get(field_name, default)`.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence, Union

import numpy as np


_Number = Union[int, float]


# ── Central decay registry ──────────────────────────────────────────────────
#
# Все EMA decay-параметры Baddle в одном месте. До 2026-04-23 были magic
# numbers в user_state.py / neurochem.py / checkins.py — сейчас тут.
# Используются либо напрямую (inline EMA: `x = DECAYS.X * x + (1 - DECAYS.X) * sig`)
# либо через `EMA(..., decay=DECAYS.X)`.

class Decays:
    """Tick-constant decay parameters. Higher = slower baseline (longer memory).

    Convention: 0.9 → reacts in ~10 ticks, 0.95 → ~20 ticks, 0.98 → ~50,
    0.99 → ~100. Tune relative to tick frequency at each call site.
    """

    # ── Neurochem (system-side, fed by graph dynamics) ─────────────────────
    NEURO_DOPAMINE = 0.9           # reaction to `d = distinct(a, b)` novelty
    NEURO_SEROTONIN = 0.95         # weights stability
    NEURO_NOREPINEPHRINE = 0.9     # entropy of active weights
    NEURO_CONFLICT_ACCUMULATOR = 0.95   # ProtectiveFreeze feeder (conflict path)

    # ── UserState neurochemical mirrors (HRV + feedback) ────────────────────
    USER_SEROTONIN_HRV = 0.9       # hrv_coherence → serotonin
    USER_NOREPINEPHRINE_HRV = 0.9  # hrv_stress → norepinephrine
    USER_DOPAMINE_ENGAGEMENT = 0.95   # any message → +0.007 to DA
    USER_DOPAMINE_FEEDBACK = 0.9   # accept/reject button
    USER_VALENCE_FEEDBACK = 0.9    # accept/reject → valence
    USER_VALENCE_SENTIMENT = 0.92  # LLM classify chat sentiment → valence
    USER_AGENCY = 0.95             # completed/planned plan ratio
    USER_BURNOUT_ENERGY = 0.9      # decisions_today → burnout

    # ── UserState predictive layer (Friston) ────────────────────────────────
    EXPECTATION = 0.98             # global + TOD scalar baselines (MindBalance)
    EXPECTATION_VEC = 0.97         # 3D vector baseline (slightly faster per-axis)
    HRV_BASELINE = 0.99            # HRV physical baselines per TOD (slowest)
    SELF_EXPECTATION = 0.97        # Neurochem self-prediction vector

    # Fast-decay overrides (surprise boost — see apply_surprise_boost)
    EXPECTATION_FAST = 0.85        # ~7× faster baseline adaptation when
    EXPECTATION_VEC_FAST = 0.80    # user is detected as surprised

    # ── Checkins (user-provided manual input, agressive corrections) ────────
    CHECKIN_ENERGY = 0.85          # long_reserve correction from stated energy
    CHECKIN_STRESS = 0.7           # stress 0-100 → norepinephrine
    CHECKIN_FOCUS = 0.7            # focus 0-100 → serotonin
    CHECKIN_VALENCE = 0.6          # reality rating → valence
    # checkins.py:193 was assigning to user.surprise (now @property) —
    # see planning/TODO.md 5.1 side-discovery. Removed.


class TimeConsts:
    """Time-constant EMAs для случаев когда tick frequency adaptive.

    Used exclusively in `ProtectiveFreeze.feed_tick` (called from
    `cognitive_loop._advance_tick` with variable dt).
    """

    SYNC_EMA_FAST = 3600.0             # 1 hour — reactive sync_error EMA (UI trend)
    SYNC_EMA_SLOW = 3 * 24 * 3600.0    # 3 days — for prime-directive weekly trend
    IMBALANCE = 24 * 3600.0            # 1 day — EMA of aggregated PE
    SILENCE_RAMP = 7 * 24 * 3600.0     # 7 days — linear ramp (not EMA but time-based)


class EMA:
    """Scalar EMA с опциональным time-constant режимом и bounds clamp."""

    __slots__ = ("value", "decay", "time_const", "bounds", "seed_on_first", "_seeded")

    def __init__(self,
                 initial: _Number = 0.5,
                 *,
                 decay: Optional[float] = None,
                 time_const: Optional[float] = None,
                 bounds: tuple = (0.0, 1.0),
                 seed_on_first: bool = False):
        """
        Args:
            initial: стартовое значение
            decay: tick-constant decay ∈ (0, 1). Высокий = медленный baseline.
            time_const: time-constant в секундах. Альтернатива decay.
            bounds: (low, high) после update. `(None, None)` = без clamp'а.
                    Default (0, 1). Для valence использовать (-1, 1).
            seed_on_first: если True, первый feed overwrites value (no EMA).
                          После первого feed — обычная EMA.

        Exactly one of decay / time_const must be set.
        """
        if (decay is None) == (time_const is None):
            raise ValueError("EMA: specify exactly one of decay / time_const")
        if decay is not None and not (0.0 < decay < 1.0):
            raise ValueError(f"decay must be in (0, 1), got {decay}")
        if time_const is not None and time_const <= 0:
            raise ValueError(f"time_const must be > 0, got {time_const}")
        self.decay = decay
        self.time_const = time_const
        self.bounds = bounds
        self.seed_on_first = seed_on_first
        self.value: float = self._clamp(float(initial))
        self._seeded: bool = not seed_on_first

    def feed(self,
             signal: _Number,
             dt: Optional[float] = None,
             decay_override: Optional[float] = None) -> float:
        """Обновить value, вернуть новое.

        Args:
            signal: новое наблюдение
            dt: секунды с последнего feed; required в time-constant режиме
            decay_override: форсировать этот decay для данного вызова
                            (surprise boost, temp fast-decay)

        Returns: новое clamped значение.
        """
        # Seed case: first call overwrites instead of EMA.
        if not self._seeded:
            self.value = self._clamp(float(signal))
            self._seeded = True
            return self.value

        # Determine effective decay
        if decay_override is not None:
            if not (0.0 < decay_override < 1.0):
                raise ValueError(f"decay_override must be in (0, 1), got {decay_override}")
            d = decay_override
        elif self.decay is not None:
            d = self.decay
        else:  # time-constant mode
            if dt is None or dt <= 0:
                return self.value
            alpha = 1.0 - math.exp(-dt / self.time_const)
            d = 1.0 - alpha

        new = d * self.value + (1.0 - d) * float(signal)
        self.value = self._clamp(new)
        return self.value

    def _clamp(self, x: float) -> float:
        lo, hi = self.bounds
        if lo is not None and x < lo:
            x = lo
        if hi is not None and x > hi:
            x = hi
        return x

    def reset(self, value: Optional[_Number] = None, re_seed: bool = False) -> None:
        """Reset к новому значению и/или re-enable seed_on_first."""
        if value is not None:
            self.value = self._clamp(float(value))
        if re_seed and self.seed_on_first:
            self._seeded = False

    def to_dict(self) -> dict:
        return {"value": round(self.value, 6), "seeded": self._seeded}

    def load(self, d: dict) -> "EMA":
        """In-place update из dict. Возвращает self для chaining."""
        if "value" in d and d["value"] is not None:
            self.value = self._clamp(float(d["value"]))
        if "seeded" in d:
            self._seeded = bool(d["seeded"])
        return self

    def __float__(self) -> float:
        return float(self.value)

    def __repr__(self) -> str:
        mode = (f"decay={self.decay}" if self.decay is not None
                else f"T={self.time_const:.0f}s")
        return f"EMA({self.value:.4f}, {mode})"


class VectorEMA:
    """Numpy-vector EMA с тем же API что и EMA."""

    __slots__ = ("value", "decay", "time_const", "bounds", "seed_on_first",
                 "_seeded", "_dim")

    def __init__(self,
                 initial: Sequence[float],
                 *,
                 decay: Optional[float] = None,
                 time_const: Optional[float] = None,
                 bounds: tuple = (0.0, 1.0),
                 seed_on_first: bool = False):
        if (decay is None) == (time_const is None):
            raise ValueError("VectorEMA: specify exactly one of decay / time_const")
        if decay is not None and not (0.0 < decay < 1.0):
            raise ValueError(f"decay must be in (0, 1), got {decay}")
        if time_const is not None and time_const <= 0:
            raise ValueError(f"time_const must be > 0, got {time_const}")
        arr = np.asarray(initial, dtype=np.float32)
        if arr.ndim != 1:
            raise ValueError(f"VectorEMA expects 1D vector, got shape {arr.shape}")
        self._dim = int(arr.shape[0])
        self.decay = decay
        self.time_const = time_const
        self.bounds = bounds
        self.seed_on_first = seed_on_first
        self.value: np.ndarray = self._clamp(arr.copy(), bounds)
        self._seeded: bool = not seed_on_first

    def feed(self,
             signal,
             dt: Optional[float] = None,
             decay_override: Optional[float] = None) -> np.ndarray:
        sig = np.asarray(signal, dtype=np.float32)
        if sig.shape != self.value.shape:
            raise ValueError(f"shape mismatch: expected {self.value.shape}, got {sig.shape}")

        if not self._seeded:
            self.value = self._clamp(sig.copy(), self.bounds)
            self._seeded = True
            return self.value

        if decay_override is not None:
            if not (0.0 < decay_override < 1.0):
                raise ValueError(f"decay_override must be in (0, 1), got {decay_override}")
            d = decay_override
        elif self.decay is not None:
            d = self.decay
        else:
            if dt is None or dt <= 0:
                return self.value
            alpha = 1.0 - math.exp(-dt / self.time_const)
            d = 1.0 - alpha

        new = d * self.value + (1.0 - d) * sig
        self.value = self._clamp(new, self.bounds)
        return self.value

    @staticmethod
    def _clamp(arr: np.ndarray, bounds: tuple) -> np.ndarray:
        lo, hi = bounds
        if lo is not None:
            arr = np.maximum(arr, lo)
        if hi is not None:
            arr = np.minimum(arr, hi)
        return arr.astype(np.float32)

    def reset(self, value: Optional[Sequence[float]] = None,
              re_seed: bool = False) -> None:
        if value is not None:
            arr = np.asarray(value, dtype=np.float32)
            if arr.shape != self.value.shape:
                raise ValueError(f"shape mismatch on reset: expected {self.value.shape}")
            self.value = self._clamp(arr, self.bounds)
        if re_seed and self.seed_on_first:
            self._seeded = False

    def to_dict(self) -> dict:
        return {
            "value": [round(float(x), 6) for x in self.value.tolist()],
            "seeded": self._seeded,
        }

    def load(self, d: dict) -> "VectorEMA":
        v = d.get("value")
        if v is not None:
            arr = np.asarray(v, dtype=np.float32)
            if arr.shape == self.value.shape:
                self.value = self._clamp(arr, self.bounds)
        if "seeded" in d:
            self._seeded = bool(d["seeded"])
        return self

    def __repr__(self) -> str:
        mode = (f"decay={self.decay}" if self.decay is not None
                else f"T={self.time_const:.0f}s")
        return f"VectorEMA({self.value.tolist()}, {mode})"
