"""MetricRegistry — тонкий event-driven контейнер для EMA-метрик.

Правило 2 из [planning/simplification-plan.md](../planning/simplification-plan.md):
«любая производная метрика это `EMA(source_event, decay)`». Здесь
инфраструктура; конкретные метрики регистрируются в соответствующих
state-классах (UserState, Neurochem, ProtectiveFreeze), каждый владеет
своим MetricRegistry.

## Паттерн использования

```python
from src.ema import EMA, Decays
from src.metrics import MetricRegistry

reg = MetricRegistry()
reg.register(
    "dopamine",
    EMA(0.5, decay=Decays.USER_DOPAMINE_ENGAGEMENT),
    listens=[("engagement", lambda p: p.get("signal"))],
)
reg.fire_event("engagement", signal=0.65)
reg.value("dopamine")    # 0.5075
```

## Что регистрируется

- Скалярные EMA и векторные VectorEMA
- TOD-scoped — как отдельные метрики с суффиксом (`*_morning`, `*_day`, ...),
  extractor фильтрует по `payload["tod"]`
- Time-constant EMA — extractor возвращает `(signal, dt)`-tuple, fire_event
  передаёт dt в `ema.feed`

## Что НЕ регистрируется (bespoke остаётся отдельно)

- Linear ramps (`silence_pressure` в ProtectiveFreeze)
- Counters (`_feedback_counts`, `_surprise_boost_remaining`)
- Additive bumps (`burnout += 0.05`, `dopamine += RPE_GAIN*rpe`)
- State flags (`active`)
- Timestamps (`_last_input_ts`)

См. [planning/phase-a-metric-registry.md § 6](../planning/phase-a-metric-registry.md).

## Decay override

Events могут передавать `_decay_override` в payload — применяется ко всем
матчнутым EMA этого события. Используется для feedback-события (dopamine +
valence оба с decay 0.9 вместо их default engagement/sentiment-decays).

Для time-constant EMA override не применяется (extractor возвращает tuple с
dt, fire_event видит tuple и идёт time-const путём).
"""
from __future__ import annotations

from typing import Callable, Optional, Union
import numpy as np

from .ema import EMA, VectorEMA


_Metric = Union[EMA, VectorEMA]
_Signal = Union[float, np.ndarray, None]
_Extractor = Callable[[dict], Union[_Signal, tuple]]


class MetricRegistry:
    """Registry of EMA metrics + event routing table.

    Per-class ownership: UserState / Neurochem / ProtectiveFreeze — каждый
    свой инстанс. Не global singleton (см. phase-a-metric-registry § Q5).
    """

    __slots__ = ("_metrics", "_routes")

    def __init__(self):
        self._metrics: dict[str, _Metric] = {}
        # event_type → [(metric_name, extractor), ...]
        self._routes: dict[str, list[tuple[str, _Extractor]]] = {}

    def register(self,
                 name: str,
                 ema: _Metric,
                 *,
                 listens: Optional[list[tuple[str, _Extractor]]] = None,
                 ) -> None:
        """Register EMA и подписки на события.

        Args:
            name: уникальное имя внутри этого registry
            ema: EMA или VectorEMA instance
            listens: список (event_type, extractor). Extractor принимает
                payload-dict и возвращает:
                  - None → метрика пропускает это событие
                  - scalar/array → feed(signal) с default decay
                  - (signal, dt)-tuple → feed(signal, dt=dt) для time-const

        Raises:
            ValueError если name уже зарегистрирован.
        """
        if name in self._metrics:
            raise ValueError(f"metric '{name}' already registered")
        self._metrics[name] = ema
        for event_type, extractor in (listens or []):
            self._routes.setdefault(event_type, []).append((name, extractor))

    def fire_event(self, event_type: str, **payload) -> None:
        """Route событие во все подписанные метрики.

        Extractor может вернуть (в порядке приоритета):
        - `None` → метрика пропускает событие
        - `dict` с ключом `signal` + optional `dt`/`decay_override` — полный
          контроль (`ema.feed(signal, **kwargs)`)
        - 2-tuple `(signal, dt)` — time-constant shorthand
        - scalar/ndarray — обычный feed; если payload содержит
          `_decay_override` (не None), применяется ко всем таким extractor'ам

        Dict-форма используется когда разные метрики в одном событии нужно
        feed-ить с разными overrides (surprise boost: scalar EMA с 0.85,
        vector EMA с 0.80). `_decay_override` — shortcut когда override
        одинаков для всех (feedback: dopamine и valence оба 0.9).
        """
        override = payload.get("_decay_override")
        for name, extract in self._routes.get(event_type, []):
            result = extract(payload)
            if result is None:
                continue
            ema = self._metrics[name]
            if isinstance(result, dict):
                signal = result.get("signal")
                if signal is None:
                    continue
                kwargs = {k: v for k, v in result.items() if k != "signal"}
                ema.feed(signal, **kwargs)
            elif isinstance(result, tuple):
                signal, dt = result
                ema.feed(signal, dt=dt)
            elif override is not None:
                ema.feed(result, decay_override=override)
            else:
                ema.feed(result)

    def get(self, name: str) -> _Metric:
        """Прямой доступ к EMA-объекту (для редких случаев: direct mutation,
        seed check, reset). Обычное чтение — через `value()`."""
        return self._metrics[name]

    def value(self, name: str) -> Union[float, np.ndarray]:
        """Быстрый accessor к `.value`. Float или numpy array."""
        return self._metrics[name].value

    def vector(self, names: list[str]) -> np.ndarray:
        """Собрать 1D-вектор из скалярных метрик в указанном порядке.

        Используется для UserState.vector() / Neurochem.vector(): сборка
        [dopamine, serotonin, norepinephrine] для sync_error.
        """
        return np.array([float(self._metrics[n].value) for n in names],
                        dtype=np.float32)

    def to_dict(self) -> dict:
        """Сериализация всех EMA. Каждое значение — dict `{value, seeded}`.
        Порядок не гарантируется (Python dict insertion order сохраняется
        de facto, но не контракт registry)."""
        return {name: ema.to_dict() for name, ema in self._metrics.items()}

    def load(self, d: dict) -> None:
        """In-place load из dict формата to_dict(). Отсутствующие метрики
        в `d` оставляют текущий EMA как есть (не reset к default)."""
        for name, state in (d or {}).items():
            if name in self._metrics and isinstance(state, dict):
                self._metrics[name].load(state)

    def __contains__(self, name: str) -> bool:
        return name in self._metrics

    def __repr__(self) -> str:
        return f"MetricRegistry({len(self._metrics)} metrics, " \
               f"{sum(len(v) for v in self._routes.values())} routes)"
