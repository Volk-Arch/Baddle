# Power — единая метрика сложности и нагрузки

Любое действие в Baddle — задача, ответ на сообщение, прочтение briefing'а, выполнение чек-ина — имеет **энергетическую стоимость**. Сейчас она моделируется отдельными скалярами: `estimated_complexity` для tasks, `cognitive_load_today` для capacity, `urgency` для signals, hardcoded `budget=5/hour` для dispatcher. Все они описывают **одно** явление, но без общей основы.

Power — единая метрика, выводящая эти скаляры из одной физически корректной формулы. Не product-feature, а **дисциплина измерения**: одна формула для всего, одни оси, одна калибровка.

---

## Формула

База — мощность из физики (`Работа / Время`), интерпретированная через свободную энергию Фристона. Полный вид:

```
Power = (U × V × P × interest_factor × chem_modulator)
```

Покомпонентно (всё в безразмерных шкалах для composability):

| Component | Что моделирует | Диапазон | Источник |
|---|---|---|---|
| **U** (uncertainty) | Энтропия процесса — сколько неизвестных требует prediction | 1–5 | LLM-классификация при создании задачи / similar tasks из history |
| **V** (volume) | Работа — количество шагов / переключений / энергии для completion | 1–5 | LLM-оценка / explicit user input |
| **P** (time pressure) | Темпоральная точность — насколько сжаты сроки | 1–N (≥1) | `max(1, (T_norm / T_actual)^γ)`, γ ≈ 1.5–2.0 |
| **interest_factor** | Engagement multiplier — как сильно motivation либо снижает, либо повышает cost | 0.5–1.5 | Explicit user rating + auto from valence/DA recent state |
| **chem_modulator** | РГК-state эффект на subjective ability | 0.7–1.5 | Function of (DA, ACh, NE, valence, balance) |

**Формула P** (из chat 2026-04-27):
```
P = max(1, (T_norm / T_actual)^γ)
```
Если времени достаточно — `P = 1` (нет давления). Если сжато — `P > 1` нелинейно.

**Open-ended цели** (без дедлайна) → `T_actual = ∞` → `P = 1`. Это **правильно**: цель «изучить React» не давит сама по себе, давит конкретная задача внутри неё.

---

## Векторность

3 контура capacity ([capacity-design.md](capacity-design.md)) уже даны: `phys / affect / cogload`. Power должна быть **вектор** по этим контурам, а не скаляр:

```
Power = (P_phys, P_affect, P_cogload)
```

Готовка курицы по-французски:
- `P_phys` ≈ 2.5 (моторика, многозадачность on stove)
- `P_affect` ≈ 1.0 (нет emotional load если рутина)
- `P_cogload` ≈ 1.8 (planning, timing, parallel processes)

Программирование сложного фичера:
- `P_phys` ≈ 0.3 (только сидение)
- `P_affect` ≈ 1.5 (frustration на bugs)
- `P_cogload` ≈ 4.0 (deep mental work)

**Класcификация контуров** для задачи происходит при создании через LLM (категория задачи + типовой профиль). Уточняется через action_memory accumulated patterns.

Это **естественно соединяет** Power с capacity-зонами: можно проверять `P_cogload ≤ available_cogload` отдельно от `P_phys`. Юзер в красной зоне по cogload, но зелёной по phys — может разгрузить голову через моторику.

---

## Chem-modulator: РГК-state эффект на subjective cost

Объективная задача и субъективная стоимость её выполнения **расходятся** в зависимости от состояния. РГК-axes дают chem-modulator:

| Axis state | Эффект на cost |
|---|---|
| High DA (motivation, gain↑) | Cost ниже — work «легче» в flow |
| High valence (positive) | Cost ниже — emotional fit |
| High ACh (plasticity) | U-cost ниже — open для нового, learning mode |
| High NE (aperture narrow, stress) | V-cost ниже (фокус на одном), но P-cost выше |
| Low DA + low valence | Cost выше — resistance, procrastination |
| balance() < 0.5 (гипостабильность) | Все cost'ы выше — общая вялость |
| balance() > 1.5 (гиперрезонанс) | V-cost ниже но риск burnout — дешёвый сейчас, дорогой завтра |

Function:
```python
def chem_modulator(rgk):
    base = 1.0
    base *= (1 - 0.2 * rgk.user.gain.value)        # DA → cost lower
    base *= (1 + 0.3 * (1 - rgk.valence.value))    # neg valence → cost higher
    if rgk.user.balance() < 0.5:
        base *= 1.2                                  # apathy
    if rgk.user.balance() > 1.5:
        base *= 0.8                                  # mania (sub-cost)
    return clamp(base, 0.7, 1.5)
```

Конкретные коэффициенты — за калибровку через 1-2 мес use.

---

## Динамика — live tracking

Static estimation (при создании) даёт expected. Real нагрузка проявляется **в процессе** через progress-tracking:

```
при start задачи:
    snapshot rgk_state, surprise_at_start, time_started
    expected_total_power = (U × V × P × ...)

каждые 5 мин (heartbeat):
    progress_estimate = LLM или explicit input ([0, 1])
    elapsed = now - time_started
    expected_progress = elapsed / T_norm
    drift = progress_estimate / expected_progress

    если drift > 1.2: задача идёт быстрее ожидаемого → effective_P снижается
    если drift < 0.8: медленнее → effective_P растёт
    update workspace candidate metadata с current_power

при done / switch / abandon:
    actual_total_time = elapsed_at_stop
    actual_total_power = recompute с actual time
    surprise_delta = actual_total_power - expected_total_power
    record в task event log для calibration
```

Это **continuous Friston** на уровне самой задачи: prediction (estimate at create) → observation (live progress) → update model для будущих оценок. Та же math что у нас уже есть в chemistry RPE.

---

## Calibration loop — bias accumulation

После каждого `done` задачи:
```
actual_complexity = surprise_at_stop - surprise_at_start (через activity_log)
bias = actual_complexity / estimated_complexity

обновить bias_per_category[category] EMA с decay 0.9:
    bias_per_category[c] = 0.9 * bias_per_category[c] + 0.1 * bias

при следующей оценке задачи в category c:
    formula_estimated = U × V × P × ...
    final_estimated = formula_estimated × bias_per_category[c]
```

После месяца use bias_per_category отражает реальные patterns:
- `cooking` bias = 1.4 → user систематически **недооценивает** сложность готовки
- `coding` bias = 0.7 → **переоценивает** сложность кода

Auto-adjust до того как юзер увидит estimate. Это **седьмая ось action memory** — не «какой тон работает», а «насколько user точен в самооценке».

---

## Interest factor

Два источника:

**1. Explicit input при создании** (опционально):
- 🚫 «Не хочу делать вообще» → 1.5 (resistance multiplier)
- 😐 «Нейтрально, нужно» → 1.0
- 🙂 «Интересно» → 0.85
- 🔥 «Очень хочу» → 0.7

**2. Auto inference из state**:
- Recent valence для category > 0.5 (positive history) → interest ↑
- Recent DA при работе с similar tasks → interest ↑
- Frequent abandonment в category → interest ↓

Combine: `interest_factor = explicit if provided else auto`.

**Почему важно:** interest — не косметический параметр. High interest сглаживает все cost'ы (flow state в Csikszentmihalyi terms). Low interest — multiplier на resistance, real cost растёт через procrastination + context switching. Без interest formula систематически переоценивает «легко и интересно» tasks и недооценивает «нужно но скучно».

---

## Closure с dispatcher budget

Сейчас `Dispatcher.budget_per_window = 5/hour` — arbitrary константа. По физике:

```
budget_per_window = available_capacity_now
cost(candidate) = power_per_candidate × time_to_attend

dispatch:
    while remaining_budget >= top_candidate.cost:
        emit(top_candidate)
        remaining_budget -= top_candidate.cost
```

Это закрывает gap «budget arbitrary vs capacity actual»:
- В зелёной зоне `available_capacity` высокое → больше signals пропускается
- В красной зоне → почти все droped (защита overload)
- Counter-wave (mode='C') пенализирует push-style cost (existing logic уже здесь)

Power becomes **derived** от РГК-state, не хардкод.

---

## Storage = task lifecycle events

Из [task-tracker-design.md](task-tracker-design.md): `data/tasks.jsonl` append-only. Под Power каждый event несёт полный state:

```jsonl
{"ts": ..., "action": "create", "id": ..., "text": ...,
 "category": ..., "U": 3, "V": 4, "P": 1.5, "interest": 1.0,
 "chem_modulator_at_create": 1.1, "estimated_power": 19.8}
{"ts": ..., "action": "start", "id": ..., "rgk_snapshot": {...},
 "surprise_at_start": 0.42}
{"ts": ..., "action": "progress", "id": ..., "progress": 0.4,
 "elapsed_min": 25, "drift": 0.85}
{"ts": ..., "action": "done", "id": ..., "actual_total_min": 80,
 "surprise_at_stop": 0.65, "actual_power": 26.0}
```

Replay даёт текущий backlog + accumulated bias coefficients. Без модификаций других хранилищ.

---

## Унификация: что было vs что становится

| Было (disconnected) | После (Power-derived) |
|---|---|
| `estimated_complexity ∈ [0, 1]` arbitrary | `Power = U × V × P × interest × chem_modulator` |
| `cognitive_load_today` (6-observable formula) | Sum of P_cogload over today's tasks/events |
| `decisions_today` плоский counter → burnout | Sum of all event Powers → burnout |
| `urgency ∈ [0, 1]` для signals | Power per signal candidate (cogload контур) |
| `dispatcher.budget = 5/hour` | `available_capacity_now` derived |
| 6 контуров capacity disconnected | 3 контура × Power vector |

Не «новая подсистема» — **переинтерпретация existing скаляров через одну ось**. Старый код работает, formula добавляется поверх как **explanatory layer**.

---

## Прогностическая сила

Если formula корректна — наблюдаемые corollary:

1. **HRV recovery × overnight insights** (W14.10 REM scout): хороший сон → больший workspace integration → утром `available_capacity` выше → больше signals пропускается.

2. **Bias coefficient ≠ 1 per category** — predictable patterns user'а. После 2 мес use каждый user имеет свой bias-vector (cooking = 1.3, coding = 0.8, ...).

3. **Workspace overflow** = sum of P_total > available_capacity. Detector → suggest defer one task / break batch in 2 days. Auto-scheduling в morning briefing.

4. **balance() out of corridor** correlates с increased chem_modulator → subjective cost spike → больше abandons / context switches. Validation через `data/prime_directive.jsonl`.

---

## Связано

- [task-tracker-design.md](task-tracker-design.md) — storage layer (tasks.jsonl), создание/выполнение задач
- [capacity-design.md](capacity-design.md) — 3 контура (phys/affect/cogload) — Power vector mapping
- [rgk-spec.md](rgk-spec.md) — РГК-axes (DA/5HT/NE/ACh/GABA) → chem_modulator
- [friston-loop.md](friston-loop.md) — prediction error как driver автономного поведения; Power = power demand prediction
- [action-memory-design.md](action-memory-design.md) — calibration через accumulated bias-coefficient
- [workspace.md](workspace.md) — batch-planning через cumulative power vs capacity (W14.5 cross-обработка)
- [planning/power-implementation.md](../planning/power-implementation.md) — implementation план

---

## Что это **не**

- Не точная shipping-формула. Коэффициенты (γ, chem-веса, interest mapping) — за калибровкой через 1-2 мес use.
- Не product feature «оценка сложности» в UI. Это measure под капотом, который влияет на dispatcher / scheduling / workspace, но юзер видит только последствия (briefing предложил 3 задачи вместо 5; alert не отправлен в красной зоне).
- Не замена estimated_complexity вручную. Юзер может установить U/V/interest explicitly; chem_modulator + P считаются автоматически.
