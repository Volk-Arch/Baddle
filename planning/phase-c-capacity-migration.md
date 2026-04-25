# Фаза C — Capacity migration: спецификация перед кодингом

> Зафиксировано 2026-04-25 после завершения Фаз A+B. Документ — **дизайн**. Цель: dual-pool legacy (`daily_spent` 0..100 + `long_reserve` 0..2000) → 3-контурная capacity-zone модель из [docs/capacity-design.md](../docs/capacity-design.md).

---

## 1. Контекст

`docs/capacity-design.md` описывает целевую модель: три параллельных контура (физио/эмо/когнитивный) с capacity-zone (green/yellow/red) как decision gate. Код всё ещё на dual-pool — это **долг**, docs опережают.

**Простота миграции после Фаз A+B:**
- EMA для `serotonin/dopamine/burnout/hrv_coherence` уже в `MetricRegistry` (Фаза A) — формулы `phys_ok`/`affect_ok` это просто чтение метрик с порогами
- Никаких alert-cascade: capacity показывает zone в UI + используется как gate в `assistant.py`, не emit'ит signals
- Cognitive load — новая ось, декларативная формула из 5 observable, считается раз в 5 мин в bookkeeping check

Net cost (до consolidation было бы 16-22ч bespoke): **~8-10ч**.

---

## 2. Что НЕ делаем

- Не меняем UI radically — убираем энергобар, добавляем 3 мини-индикатора 🟢🟡🔴 (Физо/Эмо/Когн); сохраняем shape API для других UI-блоков
- Не убираем `LOW_ENERGY_THRESHOLD` сразу — оставляем как fallback пока не убедимся в zone-based gate
- Не вызываем 1-monthly use данные для калибровки коэффициентов — стартуем с docs-defaults, крутим через 2 нед
- Не трогаем activity_log structure — добавляем 2 поля (surprise_at_start, surprise_delta), не переделываем
- Не делаем per-user capacity prior (OQ #1) — это после миграции, не часть её

---

## 3. Целевые поля и методы

### `UserState` (новые)

```python
self.day_summary: dict = {}     # {YYYY-MM-DD: {tasks_started, tasks_completed,
                                #                context_switches, complexity_sum,
                                #                progress_delta, engagement_mean,
                                #                cognitive_load}}
self.cognitive_load_today: float = 0.0   # live aggregate, [0, 1]
```

### `UserState` (новые методы / properties)

```python
@property
def capacity_zone(self) -> str:
    """green | yellow | red — derived из 3 ok-индикаторов."""

@property
def capacity_reason(self) -> list[str]:
    """Причины не-зелёной зоны: ["hrv_coherence_low", "cogload_high", ...]."""

def rollover_day(self, hrv_recovery: Optional[float] = None) -> None:
    """Полуночный reset: persist day_summary[yesterday], обнулить
    cognitive_load_today, hrv-recovery вызывает long_reserve recovery (legacy
    fallback пока не удалили дуал-пул).

    Заменяет _ensure_daily_reset → recover_long_reserve flow.
    """

def update_cognitive_load(self) -> None:
    """Пересчитать cognitive_load_today из активности + сегодняшнего day_summary.
    Вызывается из cognitive_loop._check_cognitive_load_update раз в 5 мин."""
```

### `activity_log` (новые поля)

```python
# В start_activity():
"surprise_at_start": float        # |UserState.surprise_vec| на момент старта

# В stop_activity():
"surprise_delta": float           # current_surprise - surprise_at_start
```

### `cognitive_loop` (новый bookkeeping check)

```python
def _check_cognitive_load_update(self):
    """Раз в 5 мин пересчитать UserState.cognitive_load_today."""
    if not self._throttled("_last_cognitive_load_update", 300):
        return
    get_user_state().update_cognitive_load()
```

Добавляется в `_loop()` рядом с другими bookkeeping-проверками.

### Pure formulas (декларативно)

```python
# В user_state.py или новый src/formulas.py если будет (TODO)

def compute_cognitive_load(day_summary_today: dict, progress_delta: float) -> float:
    """6 observable → [0, 1] нормализованная нагрузка дня.
    Per docs/capacity-design.md §Формулы.
    """
    return clamp(0.0, 1.0,
        0.20 * normalize(day_summary_today.get("tasks_started", 0), cap=8)
      + 0.30 * normalize(day_summary_today.get("context_switches", 0), cap=10)
      + 0.30 * normalize(day_summary_today.get("complexity_sum", 0.0), cap=3.0)
      - 0.25 * normalize(day_summary_today.get("tasks_completed", 0), cap=5)
      - 0.25 * max(0.0, progress_delta)
    )

def compute_capacity_indicators(user_state) -> dict:
    """Booleans + reasons. Reads EMA через registry."""
    coherence = user_state.metrics.value("hrv_coherence")  # требует registry-добавления
    burnout = user_state.metrics.value("burnout")
    serotonin = user_state.metrics.value("serotonin")
    dopamine = user_state.metrics.value("dopamine")
    cogload = user_state.cognitive_load_today

    return {
        "phys_ok":    coherence > 0.5 and burnout < 0.3,
        "affect_ok":  serotonin > 0.4 and dopamine > 0.35,
        "cogload_ok": cogload < 0.6,
        # +reasons если fail
    }

def compute_capacity_zone(indicators: dict) -> str:
    """green/yellow/red из числа выполненных условий."""
    n_ok = sum([indicators["phys_ok"], indicators["affect_ok"],
                indicators["cogload_ok"]])
    if n_ok == 3:    return "green"
    if n_ok == 2:    return "yellow"
    return "red"
```

### EMA addition в registry

`hrv_coherence` сейчас живёт как passthrough в `UserState.hrv_coherence` (raw last value, не EMA). Capacity нужен `hrv_coherence_ema_slow`. Решение:

Вариант A — Use existing `serotonin` (которая EMA от coherence уже): `phys_ok = serotonin > 0.5 AND burnout < 0.3`. Per docs serotonin питается coherence через USER_SEROTONIN_HRV decay — это уже slow EMA по coherence. **Принимаем.**

Тогда:
```python
phys_ok = serotonin_ema > 0.5 AND burnout_ema < 0.3   # переиспользуем существующее
```

Не добавляем новых EMA в registry — все нужные уже есть.

---

## 4. Migration plan по шагам

### Шаг 1 (≈1.5ч) — UserState поля + методы

`src/user_state.py`:
1. Добавить `self.day_summary = {}` и `self.cognitive_load_today = 0.0` в `__init__`
2. Добавить properties `capacity_zone`, `capacity_reason`
3. Добавить методы `rollover_day(hrv_recovery)`, `update_cognitive_load()`
4. Добавить pure-function helpers `compute_cognitive_load`, `compute_capacity_indicators`, `compute_capacity_zone` (module-level)
5. Обновить `to_dict / from_dict` — persist day_summary + cognitive_load_today

Tests: 5-6 unit-тестов на формулы и properties.

### Шаг 2 (≈1ч) — activity_log surprise tracking

`src/activity_log.py`:
1. В `start_activity()` снимать `surprise_at_start = abs(get_user_state().imbalance)`
2. В `stop_activity()` снимать `surprise_delta = current_surprise - surprise_at_start`
3. Добавить эти поля в JSONL serialize

Tests: 2-3 unit-тестов (start с известной surprise, stop проверяет delta).

### Шаг 3 (≈1ч) — cognitive_loop bookkeeping check

`src/cognitive_loop.py`:
1. Добавить `_check_cognitive_load_update` рядом с другими bookkeeping (action_outcomes, hrv_push, etc)
2. Добавить `_last_cognitive_load_update` field
3. Вызвать в `_loop()` body
4. Метод вызывает `get_user_state().update_cognitive_load()` который читает activity_log.day_summary + sync_error_slow → пересчитывает

Tests: integration test что update_cognitive_load обновляет field.

### Шаг 4 (≈2ч) — assistant.py decision gate

Самая инвазивная часть. `src/assistant.py`:
1. **Daily reset:** `_ensure_daily_reset` → `rollover_day()` (вместо `recover_long_reserve`)
2. **Decision logging:** убрать `_log_decision()` debit_energy call. Cost больше не списывается с pool — только записывается в activity (через cognitive_loop._check_activity_cost уже работает).
3. **Energy warning gate (line 792-805):** заменить `energy.energy < 20` на `capacity_zone == "red"` + explanation из `capacity_reason`
4. **`_compute_energy`:** заменить или удалить. Если UI всё ещё хочет energy dict → возвращать compatibility shape (заполняем zero-friendly defaults).
5. **`/assist/simulate-day`:** переписать через zone-prediction (предсказать сколько активностей в день → cognitive_load_today прогноз → zone)

Tests: 3-4 integration tests на decision gate в каждой зоне.

### Шаг 5 (≈1ч) — UI 3 мини-бара

`templates/index.html`:
1. Удалить energy + reserve bars (lines 41-64)
2. Добавить 3 мини-индикатора 🟢🟡🔴 (Физо/Эмо/Когн)
3. Каждый показывает текущий статус ok/fail (не value, а boolean ✓/✗)

`static/js/assistant.js`:
1. Удалить или адаптировать `resetEnergy()` (POST → no-op endpoint или удалить кнопку)
2. `assistUpdateHeader()` обновляется чтобы рендерить 3 индикатора из `capacity` dict в response
3. Backend `/assist` endpoint возвращает `capacity: {phys_ok, affect_ok, cogload_ok, zone, reason}` рядом с `user_state`

Tests: smoke render (manual visual + JS lint pass).

### Шаг 6 (≈1.5ч) — Удалить dual-pool legacy

Когда новый gate работает в production (1-2 дня smoke):
1. Удалить `daily_spent`, `long_reserve` поля в state dict (assistant.py)
2. Удалить `LONG_RESERVE_MAX`, `LONG_RESERVE_DEFAULT`, `DAILY_ENERGY_MAX`, `LONG_RESERVE_TAP_THRESHOLD` константы (user_state.py)
3. Удалить `debit_energy`, `recover_long_reserve`, `energy_snapshot` (user_state.py) или адаптировать под compat-shim
4. Удалить `_MODE_COST` table + `_decision_cost` (assistant.py)
5. Удалить `LOW_ENERGY_THRESHOLD` + `HEAVY_MODES` (cognitive_loop.py) — gate теперь через capacity_zone
6. Удалить `/user_state/reset-energy` endpoint
7. Cleanup тестов на dual-pool если есть

### Итого: ~8-10ч

| Шаг | Что | Время |
|---|---|---|
| 1 | UserState fields/methods + helpers | 1.5ч |
| 2 | activity_log surprise tracking | 1ч |
| 3 | cognitive_loop _check_cognitive_load_update | 1ч |
| 4 | assistant.py decision gate refactor | 2ч |
| 5 | UI 3-индикатора | 1.5ч |
| 6 | Cleanup dual-pool legacy | 1.5ч |
| Total | | **8.5ч** |

---

## 5. Backward compat / breaking changes

### Что ломается

- `/assist` response shape: убираем `energy.{daily, long_reserve, burnout_risk, decisions_today}`. Заменяем на `capacity.{phys_ok, affect_ok, cogload_ok, zone, reason[]}`.
- UI energy bar исчезает. Если juzer обновит страницу — увидит новый header.
- `/user_state/reset-energy` POST endpoint удаляется. Старый JS reset-кнопка должна быть удалена в Шаге 5.

### Что НЕ ломается

- Все остальные API endpoints (graph, /assist/state metrics, alerts) — не трогаем
- Phase A/B identity тесты продолжают работать
- Dispatcher + детекторы не трогаем
- `low_energy_heavy` детектор продолжает работать (он использует assistant._get_context.energy.energy которое мы оставим в compat-форме до Шага 6, потом замапить на capacity-based)

### Compat-shim для transition

В Шаге 4: можно оставить `energy_snapshot()` возвращать derived shape:
```python
def energy_snapshot(self, decisions_today: int) -> dict:
    """Compat-shim: derived из capacity. Удаляется в Шаге 6."""
    is_red = self.capacity_zone == "red"
    return {
        "decisions_today": decisions_today,
        "energy": 100 if not is_red else 20,   # backward-compat number
        "burnout_risk": ... ,
    }
```

Пока тесты/legacy-checks ожидают energy dict — выдаём derived. В Шаге 6 удаляем.

---

## 6. Identity testing strategy

В отличие от Фазы A (bit-identical) и B (semantic identity), Фаза C **меняет семантику** — gate перестаёт быть `daily<20 → reject`, становится zone-based. Identity тестов нет; вместо них:

1. **Property-based:** zone-функция должна возвращать корректное значение для всех 8 комбинаций ok-indicators (2³). 8 unit-тестов.
2. **Formula tests:** `compute_cognitive_load` тесты на boundary values (zero load, max load, completions reduce load).
3. **Activity surprise tracking:** start → stop → assert `surprise_delta` matches expected.
4. **Integration:** mock activity events, проверить cognitive_load_today растёт корректно за день.
5. **Regression:** существующие 175 тестов продолжают passing.

---

## 7. Открытые вопросы (решить перед кодингом)

### Q1. Использовать `serotonin` EMA или добавить отдельный `hrv_coherence_ema`?

Per § 3 — переиспользуем `serotonin` (она уже EMA от coherence через USER_SEROTONIN_HRV). 
**Мой ответ: да.** Меньше метрик → проще модель. Если через 2 нед окажется что serotonin ловит больше чем только coherence (через checkin focus, например) — добавим отдельный slow-EMA. Не сейчас.

### Q2. `cognitive_load_today` — поле или derived property?

Поле: писать вычисленное значение каждые 5 мин в `_check_cognitive_load_update`. Property: вычислять on-demand при чтении.
**Мой ответ: поле.** Property требует чтения activity_log на каждое чтение — дорого. 5-минутный update достаточно свеж.

### Q3. `progress_delta` — где хранить и как считать?

Per docs: `sync_error_slow в 23:59 минус sync_error_slow в 00:01`. Нужна snapshot в начале дня.
**Мой ответ:** добавить `sync_error_at_dawn: float` в `day_summary[today]` при `rollover_day()` в полночь. `progress_delta = sync_error_slow_now - day_summary[today]["sync_error_at_dawn"]`. Простое поле.

### Q4. Compat-shim для UI или сразу breaking change?

Compat-shim добавляет работу (Шаг 6 удаляет). Direct breaking change проще, но если что-то пропустим — ломается UI.
**Мой ответ: short compat-shim.** Шаг 4 → 5 → 6: API возвращает `capacity` рядом с `energy` в Шагах 4-5, удаляет `energy` в Шаге 6. UI обновляется в Шаге 5 (читает оба, использует capacity). Безопасный transition.

### Q5. _MODE_COST table — удалить совсем или оставить как hint?

Cost-предсказание используется в `/assist/simulate-day`. После миграции simulate-day предсказывает zones из плана активностей, не из mode complexity.
**Мой ответ: удалить.** Per docs §7 — таблица cost умирает. simulate-day переписывается на zone forecasting.

### Q6. Cognitive_load_today persistence — file или in-memory?

День сбрасывается в полночь (`rollover_day()` сохраняет в `day_summary[yesterday]`, обнуляет today). Между рестартами процесса в течение дня — теряется.
**Мой ответ:** persist в `user_state.json` (через `to_dict / from_dict`). Cheap, защищает от разрыва дня при рестарте.

---

## 8. Что остаётся bespoke (out of capacity scope)

| Элемент | Где | Почему |
|---|---|---|
| `activity_magnitude` (accelerometer) | UserState | Sensor passthrough, не EMA |
| `activity_zone` (4-zone HRV×activity) | UserState property | Существует, дополняет capacity (не заменяет) |
| `named_state` (Russell V×A) | UserState property | Voronoi map, отдельная семантика |
| `hrv_coherence/stress/rmssd` passthrough | UserState | Last sensor reading |
| `_recent_bridges` | CognitiveLoop | DMN tracking |

---

## 9. Связанные docs

- [docs/capacity-design.md](../docs/capacity-design.md) — целевая модель (полная спецификация формул)
- [simplification-plan.md §6](simplification-plan.md) — оценка 5-7ч (поправили на 8-10ч после инвентаризации)
- [docs/hrv-design.md](../docs/hrv-design.md) — физиологический контур
- [TODO.md § Capacity миграция](TODO.md) — punchlist полей/методов

---

## 10. Main takeaway

- Целевая модель полностью описана в `docs/capacity-design.md`, спека этого документа — план миграции
- ~8-10ч в 6 шагах с safe transition (compat-shim в Шагах 4-5 → cleanup в 6)
- Phase A registry упрощает: переиспользуем `serotonin`/`burnout`/`dopamine` EMA, не добавляем новых
- UI меняется (energy bar → 3 zone indicators), но шаг изолирован (Шаг 5)
- Identity не bit-identical как в Phase A (gate-семантика меняется), но 175 регрессионных тестов остаются passing
- Calibration коэффициентов формулы — отдельный пункт через 2 нед данных, не часть миграции
