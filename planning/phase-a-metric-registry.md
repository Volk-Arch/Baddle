# Фаза A — Metric Registry: спецификация перед кодингом

> Зафиксировано 2026-04-24 в продолжение [simplification-plan.md § Фаза A](simplification-plan.md). Документ — **дизайн, не план работ**. Цель: принять решения **до** кодинга, чтобы рефакторинг шёл по одной траектории, не переделывался в процессе.

---

## 1. Контекст

[simplification-plan.md Правило 2 (строки 116-156)](simplification-plan.md) обещает: «любая производная метрика это `EMA(source_event, decay)`, declarative registry, `vector()` просто читает». Сейчас это выражено **наполовину** — инфраструктура в `src/ema.py` есть (классы `EMA`, `VectorEMA`, `Decays`, `TimeConsts`), но регистрации и event-routing нет. Разные классы владеют своими EMA, `update_from_*` методы bespoke в каждом классе, 7 call-sites разбросаны по assistant/cognitive_loop/graph_routes.

**Что добавляем:** `MetricRegistry` как тонкий контейнер `{name → EMA}` + event-routing `{event_type → [metric_names + extractors]}`. Весь API для апдейтов — `fire_event(type, **payload)`. Читателям: `registry.get(name)`, `registry.vector([names])`, `registry.to_dict()`.

**Что НЕ делаем в Фазе A:** не трогаем Signal dispatcher (Фаза B), не трогаем граф/storage (Фаза C), не добавляем новые метрики, не меняем формулы/decay-константы, не ускоряем ничего — семантика 1:1, только структура.

---

## 2. Что уже есть в `src/ema.py`

- `EMA(initial, *, decay | time_const, bounds, seed_on_first)` — скаляр с двумя режимами обновления
- `VectorEMA(initial, ...)` — 1D-numpy vector
- `feed(signal, dt=None, decay_override=None)` — поддерживает surprise-boost out-of-the-box
- `to_dict() / load(d)` — сериализация (value + seeded flag)
- `Decays` — 28 decay-констант по зонам (Neurochem / UserState / Predictive / Checkins / Fast-overrides)
- `TimeConsts` — 4 time-constants (sync fast/slow, imbalance, silence ramp)

Этого достаточно. В Фазе A мы **не трогаем ema.py**, только строим registry поверх.

---

## 3. API `MetricRegistry`

Минимальный контракт. Файл `src/metrics.py`.

```python
# src/metrics.py
from typing import Callable, Optional, Union
import numpy as np
from .ema import EMA, VectorEMA

_Extractor = Callable[[dict], Optional[Union[float, np.ndarray]]]


class MetricRegistry:
    """Контейнер EMA-метрик с event-driven обновлениями.

    Одна точка для всех baseline-дрейфов. Не Signal dispatcher (Фаза B),
    не state (per-class ownership — UserState.metrics / Neurochem.metrics).
    """

    def __init__(self):
        self._metrics: dict[str, Union[EMA, VectorEMA]] = {}
        # event_type → [(metric_name, extractor), ...]
        self._routes: dict[str, list[tuple[str, _Extractor]]] = {}

    def register(self,
                 name: str,
                 ema: Union[EMA, VectorEMA],
                 *,
                 listens: Optional[list[tuple[str, _Extractor]]] = None,
                 ) -> None:
        """Добавить метрику и её роуты.

        listens: [(event_type, extractor), ...]. Extractor принимает payload
        и возвращает signal (или None если этот event не применим).
        Для time-constant EMA extractor может вернуть (signal, dt)-tuple.
        """
        if name in self._metrics:
            raise ValueError(f"metric '{name}' already registered")
        self._metrics[name] = ema
        for event_type, extractor in (listens or []):
            self._routes.setdefault(event_type, []).append((name, extractor))

    def fire_event(self, event_type: str, **payload) -> None:
        """Route событие во все подписанные метрики."""
        for name, extract in self._routes.get(event_type, []):
            result = extract(payload)
            if result is None:
                continue
            ema = self._metrics[name]
            if isinstance(result, tuple):
                signal, dt = result
                ema.feed(signal, dt=dt)
            elif payload.get("_decay_override") is not None:
                ema.feed(result, decay_override=payload["_decay_override"])
            else:
                ema.feed(result)

    def get(self, name: str) -> Union[EMA, VectorEMA]:
        return self._metrics[name]

    def value(self, name: str) -> Union[float, np.ndarray]:
        """Быстрый accessor к .value без instance-leak."""
        return self._metrics[name].value

    def vector(self, names: list[str]) -> np.ndarray:
        """Собрать 1D-вектор из скалярных метрик в указанном порядке."""
        return np.array([float(self._metrics[n].value) for n in names],
                        dtype=np.float32)

    def to_dict(self) -> dict:
        return {name: ema.to_dict() for name, ema in self._metrics.items()}

    def load(self, d: dict) -> None:
        """In-place load. Отсутствующие ключи — оставляют EMA как есть."""
        for name, state in (d or {}).items():
            if name in self._metrics:
                self._metrics[name].load(state)
```

**Что НЕ в API (сознательно):**
- Нет `unregister` / `clear` — registry строится один раз в `__init__`
- Нет итерации по всем метрикам снаружи — читайте через явные имена
- Нет wildcard-routes — каждая подписка явная
- Нет middleware / hooks — если надо, extractor делает работу

---

## 4. Ответы на 6 вопросов дизайна

### Q1. Namespaces — `dopamine_user` vs `dopamine_system`?

**Решение: два отдельных registry, owned by class.**

- `UserState.metrics: MetricRegistry` — всё user-side (dopamine, serotonin, NE, valence, burnout, agency, expectation*, hrv_baseline*)
- `Neurochem.metrics: MetricRegistry` — всё system-side (dopamine, serotonin, NE, self_expectation_vec)
- `ProtectiveFreeze.metrics: MetricRegistry` — conflict, imbalance, sync_fast, sync_slow

**Почему не flat с префиксами:** UserState — per-user singleton, Neurochem — per-graph-state. Один flat registry требовал бы глобального владельца, что ломает multi-user и Lab-scratch (OQ #4). Per-class ownership симметрично с текущим `vector()` (есть и у UserState, и у Neurochem) и с to_dict/from_dict (каждый класс сохраняет своё).

**Минус:** нельзя одной командой «покажи все метрики». В Фазе B dispatcher может взять три registry и объединить для дебага — это его задача, не registry.

### Q2. Event routing — static table или EMA.listens_to()?

**Решение: static routes при регистрации.** Каждый `register(name, ema, listens=[...])` явно декларирует событийные связи. В коде это выглядит как декларативный манифест — видно на глаз какая метрика на какие события реагирует.

Альтернатива «EMA знает свои события» раздувает EMA class (event knowledge — не её ответственность; EMA должна быть pure numerical utility).

### Q3. TOD-scoped EMA (`expectation_by_tod[4]`, `hrv_baseline_by_tod[4]`)?

**Решение: 4 отдельных метрики с суффиксом TOD.** Каждый bucket регистрируется как самостоятельная метрика:
```
expectation_by_tod_morning
expectation_by_tod_day
expectation_by_tod_evening
expectation_by_tod_night
```

Routing: extractor смотрит на `payload["tod"]` и возвращает signal только если его суффикс совпадает. Иначе None (не обновляется).

```python
def _extract_for_tod(tod_name: str) -> _Extractor:
    def _fn(payload: dict):
        if payload.get("tod") != tod_name:
            return None
        return payload.get("state_level")
    return _fn

for tod in ("morning", "day", "evening", "night"):
    registry.register(
        f"expectation_by_tod_{tod}",
        EMA(0.5, decay=Decays.EXPECTATION),
        listens=[("tick", _extract_for_tod(tod))],
    )
```

**Почему не family-object:** flat naming проще для to_dict/from_dict (ключи dict соответствуют именам). Чтобы прочитать «сейчас baseline для текущего tod» — `registry.value(f"expectation_by_tod_{current_tod}")`.

### Q4. Не-EMA side effects (burnout +=0.05, streak bias valence, RPE bump)

**Решение: остаются в bespoke-методах, НЕ в registry.**

Правило: **registry — только EMA-дрейф**. Любая additive/discrete мутация — side-effect поверх. Примеры что остаётся bespoke:

| Side effect | Где сейчас | Почему не EMA |
|---|---|---|
| `burnout += 0.05` при reject | `update_from_feedback` | Discrete bump, не baseline |
| streak-bias `valence -= 0.05 * n` | `update_from_feedback` | Conditional на counter |
| `_dopamine_ema.value += RPE_GAIN * rpe` | `Neurochem.record_outcome` | Additive mutation |
| `_feedback_counts[kind] += 1` | `update_from_feedback` | Counter, не EMA |
| `silence_pressure += dt/RAMP` | `ProtectiveFreeze.feed_tick` | Linear ramp, не exponential |
| `_surprise_boost_remaining -= 1` | `tick_expectation` | Counter |

**Как это сочетается с fire_event:** bespoke метод **и** fire_event живут рядом:

```python
def update_from_feedback(self, kind: str):
    self._feedback_counts[kind] += 1          # bookkeeping
    signal = {"accepted": 0.9, "rejected": 0.2, "ignored": 0.5}.get(kind)
    self.metrics.fire_event(
        "feedback", kind=kind, dopamine_signal=signal, valence_signal=...,
    )
    if kind == "rejected":                    # discrete bump
        self.burnout_additive(0.05)
        if self._rejected_streak() >= 3:
            self.valence_additive(-0.05 * min(5, ...))
    self.tick_expectation()
```

Registry делает EMA. Remaining — 3-5 строк bespoke. Это то самое «registry — инфраструктура, а не всё».

### Q5. Registry — singleton или часть state?

**Решение: per-class, сериализуется вместе с classом.**

`UserState.__init__` создаёт `self.metrics = _build_user_registry()` — helper function, декларативно регистрирующая все 13 метрик. То же для Neurochem и ProtectiveFreeze.

`to_dict` делегирует: `"metrics": self.metrics.to_dict()`. `from_dict` load'ит обратно.

**Backward-compat:** старые сериализации (`dopamine: 0.63`) должны грузиться. Решение: `from_dict` сначала пытается `"metrics"`, fallback — мигрирует flat-поля в registry.load-dict при старте.

### Q6. `tick_expectation()` через событие?

**Решение: да, событие `"tick"` с payload `{vector, state_level, tod, boost}`.**

В UserState `tick_expectation` становится двумя строками:
```python
def tick_expectation(self):
    override = (Decays.EXPECTATION_FAST
                if self._surprise_boost_remaining > 0 else None)
    if self._surprise_boost_remaining > 0:
        self._surprise_boost_remaining -= 1
    self.metrics.fire_event(
        "tick",
        vector=self.vector(),
        state_level=self.state_level(),
        tod=self._current_tod(),
        _decay_override=override,
    )
```

В Neurochem аналогично, payload `{vector: self.vector()}`. Route: `self_expectation_vec` подписана на `"tick"` с extractor'ом `lambda p: p.get("vector")`.

**Plus:** `decay_override` передаётся через специальный `_decay_override` ключ в payload (уже реализован в `fire_event`). Extractor возвращает просто signal, `fire_event` подхватывает override.

---

## 5. Таблица всех метрик с событиями

### 5.1. `UserState.metrics` — 13 метрик

| Name | Type | Initial | Decay/TC | Listens events (extractor) |
|---|---|---|---|---|
| `dopamine` | EMA | 0.5 | `USER_DOPAMINE_ENGAGEMENT` | `engagement(signal)` → `signal`; `feedback(dopamine_signal)` с override `USER_DOPAMINE_FEEDBACK` ⚠ |
| `serotonin` | EMA | 0.5 | `USER_SEROTONIN_HRV` | `hrv_update(coherence)` → `coherence` |
| `norepinephrine` | EMA | 0.5 | `USER_NOREPINEPHRINE_HRV` | `hrv_update(stress)` → `stress` |
| `valence` | EMA | 0.0, bounds (-1,1) | `USER_VALENCE_SENTIMENT` | `chat_sentiment(sentiment)` → `sentiment`; `feedback(valence_signal)` с override `USER_VALENCE_FEEDBACK` ⚠ |
| `burnout` | EMA | 0.0 | `USER_BURNOUT_ENERGY` | `energy(decisions_today, max_budget)` → `usage=min(1, d*6/max)` |
| `agency` | EMA | 0.5 | `USER_AGENCY` | `plan_completion(completed, planned)` → `completed/planned` if `planned>0` else None |
| `expectation` | EMA | 0.5 | `EXPECTATION` | `tick(state_level)` → `state_level` |
| `expectation_by_tod_{morning/day/evening/night}` | EMA×4 | 0.5 | `EXPECTATION` | `tick(state_level, tod)` с TOD-filter |
| `expectation_vec` | VectorEMA | [0.5,0.5,0.5] | `EXPECTATION_VEC` | `tick(vector)` → `vector` |
| `hrv_baseline_by_tod_{morning/day/evening/night}` | EMA×4, seed_on_first | 0.0 | `HRV_BASELINE` | `hrv_update(coherence, tod)` с TOD-filter |

⚠ **Два decay на одну метрику** — `dopamine` имеет одну decay от engagement (0.95) и другую от feedback (0.9). Сегодняшний код тоже так работает — использует разные константы в разных методах. В registry этого можно добиться через `decay_override` в payload (каждое событие несёт свою). Проще: extractor возвращает `(signal, override)`-tuple, fire_event подхватывает. Делаем так.

### 5.2. `Neurochem.metrics` — 4 метрики

| Name | Type | Initial | Decay | Listens events |
|---|---|---|---|---|
| `dopamine` | EMA | 0.5 | `NEURO_DOPAMINE` | `graph_update(d)` → `d` (when not None) |
| `serotonin` | EMA | 0.5 | `NEURO_SEROTONIN` | `graph_update(w_change)` → `1 - std(w_change)` |
| `norepinephrine` | EMA | 0.5 | `NEURO_NOREPINEPHRINE` | `graph_update(weights)` → `entropy_normalized(weights)` |
| `self_expectation_vec` | VectorEMA | [0.5,0.5,0.5] | `SELF_EXPECTATION` | `tick(vector)` → `vector` |

Extractors для serotonin/norepinephrine — с вычислением внутри (std / entropy). Это OK, extractor = pure function.

**`record_outcome` остаётся bespoke** — там direct mutation `_dopamine_ema.value += RPE_GAIN * rpe`. Через registry: `neurochem.metrics.get("dopamine").value += ...`.

### 5.3. `ProtectiveFreeze.metrics` — 4 метрики

| Name | Type | Initial | Decay/TC | Listens events |
|---|---|---|---|---|
| `conflict_accumulator` | EMA | 0.0 | `NEURO_CONFLICT_ACCUMULATOR` | `conflict_update(d, serotonin)` → `max(0, d-TAU_STABLE) * (1-serotonin)` |
| `imbalance_pressure` | EMA | 0.0 | TC `IMBALANCE` | `feed_tick(imbalance, dt)` → `(abs(imbalance), dt)` |
| `sync_error_fast` | EMA | 0.0 | TC `SYNC_EMA_FAST` | `feed_tick(sync_err, dt)` → `(sync_err/1.732, dt)` |
| `sync_error_slow` | EMA | 0.0 | TC `SYNC_EMA_SLOW` | `feed_tick(sync_err, dt)` → `(sync_err/1.732, dt)` |

`silence_pressure` **не** в registry — это linear ramp, не EMA. Остаётся как `self.silence_pressure: float` с методами `feed_tick_silence(dt)` + `add_silence_pressure(delta)`.

`active: bool` тоже не в registry — это state machine, не baseline.

### 5.4. Event catalog (сводно)

| Event | Fired from | Subscribers | Payload |
|---|---|---|---|
| `hrv_update` | `assistant` (sensor push), `cognitive_loop._sync_seeking_from_hrv` | UserState: serotonin, NE, hrv_baseline_* | coherence, stress, rmssd, activity, tod |
| `engagement` | `assistant._prepare_request`, `_process_chat_response` | UserState: dopamine | signal (default 0.65) |
| `feedback` | `assistant._apply_feedback` | UserState: dopamine, valence (+ bespoke burnout/streak) | kind, dopamine_signal, valence_signal |
| `chat_sentiment` | `_process_chat_response` | UserState: valence | sentiment ∈ [-1, 1] |
| `plan_completion` | `cognitive_loop._sync_seeking_from_goals` | UserState: agency | completed, planned |
| `energy` | `assistant._prepare_request` | UserState: burnout | decisions_today, max_budget |
| `tick` | Инлайн из `update_from_*` methods для UserState (в конце); `cognitive_loop._advance_tick` для Neurochem | UserState: expectation, expectation_by_tod, expectation_vec. Neurochem: self_expectation_vec | vector, state_level, tod, _decay_override |
| `graph_update` | `Neurochem.update()` call-sites (в `horizon.update_neurochem`) | Neurochem: dopamine, serotonin, NE | d, w_change, weights |
| `conflict_update` | `ProtectiveFreeze.update()` — остаётся явный метод | ProtectiveFreeze: conflict_accumulator | d, serotonin |
| `feed_tick` | `cognitive_loop._advance_tick` | ProtectiveFreeze: imbalance, sync_fast, sync_slow | sync_err, imbalance, dt |

**Итого: 10 типов событий, 21 EMA (13 + 4 + 4).**

---

## 6. Что остаётся bespoke (не входит в registry)

| Элемент | Где | Причина |
|---|---|---|
| `long_reserve: float` | UserState | Dual-pool legacy, уйдёт в capacity migration (Фаза после B) |
| `hrv_coherence/stress/rmssd` passthrough | UserState | UI-зеркала последнего замера, не EMA |
| `activity_magnitude: float` | UserState | Passthrough акселерометра, не EMA |
| `_feedback_counts: dict` | UserState | Counter для streak-bias |
| `_surprise_boost_remaining: int` | UserState | Counter для fast-decay override |
| `_last_input_ts / _last_user_surprise_ts` | UserState | Timestamps, не EMA |
| `_delta_history: list`, `recent_rpe: float` | Neurochem | Rolling window + последнее значение |
| `RPE bump` (`_dopamine_ema.value += ...`) | Neurochem.record_outcome | Additive, не EMA feed |
| `silence_pressure: float` | ProtectiveFreeze | Linear ramp |
| `active: bool` | ProtectiveFreeze | State machine |
| `streak-bias valence` | UserState.update_from_feedback | Discrete conditional |
| `burnout += 0.05` на reject | UserState.update_from_feedback | Discrete additive |

**Правило на будущее:** если новое поле это pure `x = decay*x + (1-decay)*signal` — в registry. Всё остальное — bespoke.

---

## 7. План миграции по шагам

### Шаг 1 (≈1ч) — создать `src/metrics.py`

Написать `MetricRegistry` по API из § 3. Минимум тестов: register → fire_event → value; register c TOD-filter → fire_event с правильным tod → обновляет только нужный suffix; register c time-constant extractor → (signal, dt)-tuple работает; to_dict/load roundtrip.

### Шаг 2 (≈2ч) — identity-check harness (ДО миграции)

До начала рефакторинга **зафиксировать эталон**. Написать тест `tests/test_metric_identity.py`:

1. Создать `UserState() / Neurochem() / ProtectiveFreeze()` в default state
2. Прогнать фиксированный event-sequence (hardcoded list of `(method, args)`-tuples):
   - `update_from_hrv(0.6, 0.3, 40, 0.2)` × 5
   - `update_from_engagement(0.65)` × 10
   - `update_from_feedback("accepted")` × 3
   - `update_from_feedback("rejected")` × 2
   - `update_from_chat_sentiment(0.4)` × 5
   - `update_from_plan_completion(3, 5)`
   - `update_from_energy(20)`
   - `tick_expectation()` × 10
   - `neurochem.update(d=0.4, w_change=[0.1,-0.05,0.2], weights=[0.3,0.4,0.3])` × 5
   - `neurochem.tick_expectation()` × 3
   - `freeze.update(d=0.7, serotonin=0.4)` × 2
   - `freeze.feed_tick(dt=60, sync_err=0.5, imbalance=0.3)` × 20
3. Снять snapshot: `vector(), expectation_vec, hrv_baseline_by_tod, neurochem.vector(), self_imbalance, display_burnout, sync_error_ema_slow` — всё с округлением до 6 знаков
4. Хардкодить snapshot в тест как expected

После каждого шага миграции (3-6) тест должен проходить **бит-в-бит**.

### Шаг 3 (≈1ч) — мигрировать Neurochem

Самое простое — уже на EMA objects. Добавить `self.metrics = _build_neurochem_registry()` в `__init__`. `_build_neurochem_registry()` регистрирует 4 EMA, возвращает registry. Hidden `_dopamine_ema / _serotonin_ema / _norepinephrine_ema / _expectation_vec_ema` заменить на `self.metrics.get("dopamine")`, etc. (либо оставить как alias-properties на первом проходе — удалить в конце фазы).

`update()` → `self.metrics.fire_event("graph_update", d=d, w_change=w_change, weights=weights)`.

`tick_expectation()` → `self.metrics.fire_event("tick", vector=self.vector())`.

`record_outcome()` — `self.metrics.get("dopamine").value += RPE_GAIN * rpe` (direct mutation остаётся).

`to_dict / from_dict` — делегируют в `self.metrics.to_dict / load`.

Identity-check должен пройти.

### Шаг 4 (≈1ч) — мигрировать ProtectiveFreeze

4 EMA уже на objects. Всё как в Шаге 3. `silence_pressure` остаётся отдельно. `feed_tick` → `fire_event("feed_tick", sync_err=s, imbalance=i, dt=dt)`.

### Шаг 5 (≈3-4ч) — мигрировать UserState (самая большая часть)

Здесь нужно **завести 6 новых EMA** для dopamine/serotonin/NE/valence/burnout/agency (сейчас inline скаляры). 

Порядок внутри шага:

1. Заменить `self.dopamine: float = 0.5` на регистрацию EMA в `self.metrics`. Property `dopamine` читает registry
2. Тот же подход для serotonin/NE/valence/burnout/agency
3. Мигрировать 4 predictive EMA (expectation*, hrv_baseline*) — они уже EMA objects, просто перенос в registry
4. Каждый `update_from_*` → `self.metrics.fire_event(...)` + оставшиеся bespoke side-effects
5. `tick_expectation` → `fire_event("tick", vector=..., state_level=..., tod=..., _decay_override=...)`
6. `vector()` → `self.metrics.vector(["dopamine", "serotonin", "norepinephrine"])`
7. `to_dict/from_dict` — делегирование

Identity-check должен пройти после каждого подшага (1, 1+2, 1+2+3, ...).

### Шаг 6 (≈1ч) — call-sites

Все 7 call-sites `update_from_*` остаются как есть — методы UserState/Neurochem ещё существуют (они теперь тонкие обёртки вокруг `fire_event`). Это сохраняет backward-compat. В конце Фазы B (когда будет Signal dispatcher) можно будет переводить call-sites на прямой `fire_event`, а `update_from_*` методы удалить.

**Итого Фазы A:** ≈9-11ч. В пределах оценки 8-12ч из simplification-plan.

---

## 8. Identity-check protocol — что проверяем

После каждого шага миграции один тест:

```bash
pytest tests/test_metric_identity.py -v
```

Тест делает:
1. Fresh UserState/Neurochem/ProtectiveFreeze
2. Прогон 60+ событий из fixed sequence (см. Шаг 2)
3. `assert us.vector() == expected_vector` (с tolerance 1e-6)
4. `assert us.expectation_vec == expected_vec`
5. `assert round(neurochem.self_imbalance, 6) == expected`
6. `assert round(freeze.display_burnout, 6) == expected`
7. `assert round(freeze.sync_error_ema_slow, 6) == expected`
8. to_dict snapshot сравнивается с хардкоженным JSON

Если хоть один не сошёлся — миграция неверная, откат к предыдущему коммиту.

**Дополнительно:** после Шага 5 запустить приложение, залогинить один real session (/assist, несколько accept/reject, HRV push), сравнить `GET /assist/prime-directive` до и после — `mean_ema_slow` должен совпадать.

---

## 9. Открытые вопросы (решить перед кодингом)

1. **Два decay на одну метрику** (dopamine получает 0.95 от engagement, 0.9 от feedback). Сейчас предложено через `(signal, override)`-tuple extractor. Альтернатива — разделить на две метрики `dopamine_engagement_ema` и `dopamine_feedback_ema`, читать через `max()` или similar. Простое решение через override достаточно? **Мой ответ: да, extractor-override честнее и ближе к существующей семантике.**

2. **`_surprise_boost_remaining` декремент** — сейчас в `tick_expectation`. Остаётся там же, до `fire_event`. **Мой ответ: да, counter-логика не в registry.**

3. **`tick_expectation` автоматический vs явный?** Сейчас вызывается автоматически из `update_from_hrv / feedback / energy`. Оставить как есть (bespoke методы сами дёргают после fire_event) или переделать через «каждый fire_event триггерит tick»? **Мой ответ: оставить как сейчас.** Автоматизация через registry добавит implicit coupling, разные типы событий не должны триггерить tick одинаково (например, `engagement` сейчас НЕ триггерит `tick_expectation` — это валидно).

4. **`Neurochem.update()` — сохранять или полностью через `fire_event`?** Предложено: сохранить как обёртку (`def update(...): self.metrics.fire_event("graph_update", ...)`). В call-sites (`horizon.update_neurochem`) пока не трогаем. **Мой ответ: да, тонкая обёртка.**

5. **Lab-scratch (OQ #4)** — если в будущем появится scratch Neurochem, каждый будет со своим registry автоматически (per-class ownership). **Мой ответ: Фаза A не блокирует и не решает OQ #4.**

6. **Тестирование (Шаг 2) — где хранить fixture?** `tests/fixtures/metric_replay.json` с фиксированной последовательностью, или inline в тесте как Python list? **Мой ответ: inline в тесте.** Файл отделяет тест от ожиданий; inline — всё в одном месте для чтения.

---

## 10. Main takeaway

- Инфраструктура `ema.py` закрывает 80% Фазы A. Остаётся тонкий registry-слой.
- 21 EMA в сумме (13 user + 4 neuro + 4 freeze)
- 10 типов событий
- 12 side-effects остаются bespoke (правило: registry — только EMA-дрейф)
- Identity-check протокол гарантирует 1:1 семантику до/после
- Оценка: 9-11ч, вписывается в 8-12ч
- **Не решаем в Фазе A:** Signal dispatcher (Фаза B), graph-first (Фаза C), capacity migration (после B), Lab-scratch (OQ #4), новые метрики/формулы

---

## Связанные docs

- [simplification-plan.md](simplification-plan.md) — стратегия из которой эта фаза
- [TODO.md](TODO.md) — заморожен до завершения Фаз A+B
- [src/ema.py](../src/ema.py) — существующая инфраструктура EMA/Decays/TimeConsts
- [docs/friston-loop.md](../docs/friston-loop.md) — predictive layer семантика (expectation/surprise)
