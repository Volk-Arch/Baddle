# Предиктивная user-модель

> Пользователь для Baddle — не тег, а **физическая система** со своей
> энергетикой, ожиданиями и предсказуемой траекторией состояний.
> `UserState` зеркалит `Neurochem` системы, но кормится другими сигналами:
> тело, ввод, обратная связь. Симбиоз двух — в [symbiosis-design.md](symbiosis-design.md).

Этот документ описывает пять расширений `UserState` поверх базовых
скаляров (dopamine / serotonin / norepinephrine):

1. **Signed surprise** — ошибка со знаком (недооценил или переоценил)
2. **Valence** — отдельная ось «приятно ↔ неприятно»
3. **Activity zone** — связка HRV и движения в 4 зоны
4. **Named states** — 10 именованных регионов вместо скаляров
5. **Dual-pool energy** — две энергетические шкалы: день и долгий резерв
6. **Decision cost by mode** — разные режимы стоят разного
7. **Симулятор дня** — «если сделаю X, Y, Z — что к вечеру»

---

## 1. Signed surprise — ошибка со знаком

Раньше `sync_error = ‖user − system‖` считался как L2-норма — всегда
положительное число. Направление было потеряно: «недооценил юзера» и
«переоценил» давали одинаковое значение.

Теперь это разделено. `UserState` хранит четыре связанных скаляра:

| Поле | Смысл |
|------|-------|
| `expectation` | Медленный baseline: куда я привык ожидать что попадёт юзер |
| `reality` | Текущий сырой уровень активации (среднее D и S) |
| `surprise` | Знаковая разница: `reality − expectation` ∈ [−1, 1] |
| `imbalance` | Абсолютная величина surprise: «насколько сильно сбился с курса» |

**Интерпретация знака:**
- `surprise > 0` — реальность превзошла ожидания (подъём, как «благодарность»)
- `surprise < 0` — реальность ниже ожиданий (спад, как «разочарование»)
- `|surprise|` большое — мозг «включает» сознание, чтобы разобраться

Механика обновления: `expectation` — EMA (экспоненциальное сглаживание)
со спадом 0.98, то есть живёт ~50 обновлений. `tick_expectation()`
вызывается автоматически после каждого `update_from_*` (HRV, timing,
сообщение, feedback, энергия).

---

## 2. Valence — ось приятно/неприятно

Возбуждение (norepinephrine, dopamine) и валентность — **разные оси**.
Одинаково высокое возбуждение может быть любопытством (+valence) или
стрессом (−valence). Их нельзя сводить в одну шкалу без потери смысла.

`UserState.valence ∈ [−1, 1]` — отдельный EMA-скаляр. Источники сигнала:

| Событие | Вклад | Спад |
|---------|-------|------|
| `accepted` feedback | +0.7 | 0.9 |
| `rejected` feedback | −0.7 | 0.9 |
| Быстрый ввод (<30с разрыв) | +0.2 | тихая радость «хочется ещё» |
| Долгая пауза (>5 мин) | −0.2 | лёгкая отстранённость |

**Streak bias при отказах:** если rejects превышают accepts на 3+,
валентность понижается дополнительно пропорционально перекосу. Это ловит
ситуацию «серия подряд не понравилось» — одного отказа недостаточно,
серии хватает чтобы сказать «что-то идёт не так».

HRV сюда не кормит. HRV — про тело, valence — про ощущение. Разные
источники, не смешивать. Сохраняется через `to_dict/from_dict`.

---

## 3. Activity zone — HRV × движение

HRV один не различает покой и движение: низкая когерентность может быть
стрессом сидящего юзера или нормальным состоянием во время бега. Чтобы
отличать, читаем акселерометр как **независимый канал** и комбинируем
с HRV-когерентностью.

`UserState.activity_magnitude` — скаляр движения (magnitude вектора
ускорения). Комбинация с когерентностью даёт четыре зоны:

| Движется? | HRV-ок? | Зона | Смысл |
|-----------|---------|------|-------|
| нет | да | 🟢 `recovery` | Здоровое восстановление |
| нет | нет | 🟡 `stress_rest` | Беспокойство в покое |
| да | да | 🔵 `healthy_load` | Здоровая нагрузка, flow-like |
| да | нет | 🔴 `overload` | Перегрузка, тормози |

Пороги: `active = activity_magnitude ≥ 0.5`, `hrv_ok = coherence ≥ 0.5`.

### Откуда physical signal

- **Polar H10 (когда BLE подключён):** accelerometer через BLE →
  `|accel| − g` magnitude → push в `hrv_manager.update_activity(mag)`.
- **Симулятор (сейчас):** слайдер «Activity» (0-3) в HRV-панели, либо
  `POST /hrv/simulate {activity: 1.2}`.

### Влияние на named_state

Раньше ось активации (A) считалась как `mean(dopamine, norepinephrine)` —
чисто когнитивное возбуждение. Лежащий юзер с высоким дофамином (например
после сильного feedback) попадал в «flow» — это неверно физиологически.

Теперь: `A = 0.7·cog_arousal + 0.3·min(1, activity/2)`. Движение даёт до
+0.3 к активации. Бегущий юзер не может оказаться в «медитации» по
скалярам D/NE — тело вето.

### Alerts по зоне

`/assist/alerts` генерит предупреждения только для негативных зон:
- `type: zone_overload` — при `active + !hrv_ok` — severity `warning`
- `type: zone_stress_rest` — при `!active + !hrv_ok` — severity `info`

Recovery и healthy_load — позитивные состояния, alerts не нужны.

---

## 4. Named states — 10 регионов в (T, A)-пространстве

Вместо того чтобы отдавать юзеру голые скаляры «D=0.7 S=0.4 NE=0.3»,
маппим две производные — `T` (tone, валентность) и `A` (activation) —
на именованные регионы. Их 10, распределены по плоскости как Voronoi:

| Имя | T | A | Когда |
|-----|---|---|-------|
| inspiration | 0.90 | 0.95 | творческий подъём |
| flow | 0.85 | 0.90 | оптимальная вовлечённость |
| curiosity | 0.48 | 0.82 | активное исследование |
| gratitude | 0.64 | 0.58 | реальность превзошла ожидания |
| neutral | 0.50 | 0.50 | баланс |
| meditation | 0.43 | 0.21 | спокойствие без реакции |
| apathy | 0.35 | 0.25 | низкая вовлечённость |
| stress | 0.30 | 0.85 | напряжение |
| disappointment | 0.26 | 0.40 | ожидания не оправдались |
| burnout | 0.10 | 0.40 | критическое истощение |

**Маппинг Baddle-скаляров на оси:**
- `T ≈ serotonin` — валентность, стабильность
- `A ≈ 0.7·mean(D, NE) + 0.3·activity` — когнитивное возбуждение плюс движение

`UserState.named_state` — `@property`, возвращает dict ближайшей точки:
`{key, label, advice, distance, coord}`. Это **состояние самого юзера**.
Не путать с `sync_regime` (FLOW / REST / PROTECT / CONFESS) — тот про
симбиоз с системой, 4 состояния. Тут разрешение выше — 10 регионов.

UI забирает полную карту через `GET /assist/named-states` для рендера.

---

## 5. Dual-pool energy — день и долгий резерв

Одной энергетической шкалы недостаточно. Реально их две:
- **Дневная энергия** (0..100) — быстрый пул, восстанавливается за ночь
- **Долгий резерв** (0..2000) — медленный пул, копит долгосрочный износ

Burnout — это не «дневная на нуле» (это просто усталость), а **истощение
долгого резерва** при хронической перегрузке несколько недель подряд.

### Как тратится

```
debit_energy(cost, daily_remaining):
    если daily_remaining ≥ cost и daily_remaining ≥ 20:
        → весь cost уходит из daily
    если daily_remaining < 20:
        → daily покрывает максимум (daily_remaining)
        → остаток + штраф 30% идёт из long_reserve
        → long_used = overflow + cost · 0.3
```

Штраф 30% за работу на пустом баке — биологическая интуиция: сложнее
думать уставшим. Энергия дороже когда день уже прошёл.

### Как восстанавливается

Вызывается при полуночном reset:
- `sleep_recovery = 90 · hrv_recovery`
- `rest_bonus = 20 · hrv_recovery`
- `long_reserve += sleep_recovery + rest_bonus`

Без HRV — берём дефолт `hrv_recovery = 0.7` (средний сон).

### Персистентность

- `daily_spent` — в `user_state.json`, сумма потраченного сегодня
- `long_reserve` — в `UserState.to_dict()`, стартует с 1500

---

## 6. Decision cost by mode — разные режимы стоят разного

Простое дело стоит мало (3 единицы), сложное сравнение — много (12).
Таблица в `_MODE_COST` ([src/assistant.py](../src/assistant.py)):

| Категория | Режимы | Cost |
|-----------|--------|------|
| simple | free, scout, fan | 3 |
| moderate-light | rhythm | 4 |
| moderate | vector, horizon | 6 |
| moderate-heavy | bayes, race | 7 |
| complex (AND) | builder, pipeline, cascade, scales | 10 |
| critical (XOR) | tournament, dispute | 12 |

На каждое решение `_log_decision()` берёт `cost = _decision_cost(mode_id)`,
вызывает `user.debit_energy(cost, daily_remaining)` и пишет в
`state.history` поля `cost / daily_used / long_used` для аудита.

Цифры калиброваны по интуиции, не по данным. Подстраивать можно.

---

## 7. Симулятор дня — «если сделаю X, Y, Z»

`POST /assist/simulate-day` отвечает на вопрос «что будет к концу дня
если я выполню этот план?» — полезно для:
- «Хватит ли энергии на завтрашний день?»
- «Если запланирую 3 tournament и 2 dispute, не сожгусь?»
- Визуализации burnout_risk при разных сценариях

**Как работает:** делает клон `UserState` через `to_dict/from_dict`,
прогоняет план через `debit_energy` — **живое состояние не меняется**.

### Запрос

```json
{
  "plan": [{"mode": "tournament"}, {"mode": "fan"}, ...],
  "hrv_recovery": 0.7
}
```

### Ответ

Суммарные числа плюс помешагавая раскладка:

```json
{
  "plan_size": 9,
  "total_cost": 102,
  "total_daily_used": 94,
  "total_long_used": 15.4,
  "steps": [
    {"mode": "tournament", "cost": 12,
     "daily_remaining_after": 88, "long_reserve_after": 1500},
    ...
  ],
  "end_of_day": {
    "daily_remaining": 0,
    "long_reserve": 1494.4,
    "burnout_risk": 0.253,
    "predicted_named_state": {"key": "neutral", "label": "Нейтральное",
                              "advice": "..."},
    "dopamine": 0.5, "serotonin": 0.5
  }
}
```

---

## Персистентность

Всё хранится в `data/user_state.json`:

- `daily_spent`, `decisions_today`, `last_reset_date` — session-level
- `user_state_dump` — сериализация `UserState`:
  ```
  {dopamine, serotonin, norepinephrine, burnout,
   expectation, surprise, valence, long_reserve,
   activity_magnitude, hrv: {...}}
  ```

**Загрузка** (`_load_state()`): читаем файл, зовём
`set_user_state(UserState.from_dict(user_state_dump))`. Восстанавливает
expectation, valence, long_reserve между сессиями — continuity.

**Сохранение** (`_save_state(state)`): сериализуем текущий UserState
перед записью JSON. Паттерн на каждый вызов: `_log_decision` →
`_save_state`.

---

## Ограничения

- **Expectation единая** — не различает контекст (работа vs отношения).
  Мультиконтекстный baseline — будущая работа.
- **Симулятор только на один день.** Прогноз на N дней с чередующимися
  recovery nights — TODO.
- **Costs жёсткие.** Можно скейлить от сложности tick'а (число подзадач),
  сейчас фикс по режиму.
- **Circadian baseline drift** — дофамин утром / серотонин вечером —
  не реализован.

---

## Файлы

- [src/user_state.py](../src/user_state.py) — `UserState` с signed surprise,
  valence, activity_zone, dual-pool, named_state, `debit_energy`,
  `recover_long_reserve`
- [src/user_state_map.py](../src/user_state_map.py) — 10 Voronoi-регионов
- [src/assistant.py](../src/assistant.py):
  - `_MODE_COST` + `_decision_cost()`
  - `_load_state` / `_save_state` — персистентность через `user_state_dump`
  - `_ensure_daily_reset` — полуночное восстановление long_reserve
  - `_compute_energy` — dual-pool snapshot
  - `_log_decision` — mode-взвешенный debit
  - Эндпоинты `/assist/simulate-day`, `/assist/named-states`

---

**Навигация:** [← Symbiosis](symbiosis-design.md)  ·  [Индекс](README.md)  ·  [Следующее: HRV →](hrv-design.md)
