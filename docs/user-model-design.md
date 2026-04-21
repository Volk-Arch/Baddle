# Предиктивная user-модель

Пользователь для Baddle — не тег, а **физическая система** со своей
энергетикой, ожиданиями и предсказуемой траекторией состояний. `UserState`
зеркалит `Neurochem` системы, но кормится другими сигналами: тело, ввод,
обратная связь.

Этот doc описывает что **сверх** базовых DA/S/NE живёт в UserState:

1. Surprise / expectation → **[friston-loop.md](friston-loop.md)** (канонический PE-слой)
2. **Valence** — ось «приятно ↔ неприятно»
3. **Activity zone** — 4 зоны из HRV × движение
4. **Named states** — 10 именованных регионов
5. **Dual-pool energy** — день + долгий резерв
6. **Decision cost by mode** — разные режимы стоят разного
7. **Симулятор дня** — «если сделаю X, Y, Z — что к вечеру»

---

## Valence — ось приятно/неприятно

Возбуждение (NE, DA) и валентность — **разные оси**. Одинаково высокое
возбуждение может быть любопытством (+valence) или стрессом (−valence).
В одной шкале теряется смысл.

`UserState.valence ∈ [−1, 1]` — отдельный EMA-скаляр. Источники только
явные, объективные:

- 👍 feedback: +0.7
- 👎 feedback: −0.7
- Streak bias: если rejects превышают accepts на 3+, valence падает
  дополнительно. Одного отказа недостаточно, серии хватает чтобы сказать
  «что-то идёт не так».
- Chat sentiment через light LLM — высокочастотный feeder (каждое
  сообщение)

HRV сюда не кормит — тело про возбуждение, valence про ощущение. Разные
источники, не смешивать.

---

## Activity zone — HRV × движение

HRV один не различает покой и движение: низкая когерентность может быть
стрессом сидящего или нормальным состоянием при беге. Читаем акселерометр
как независимый канал и комбинируем.

| Движется? | HRV-ок? | Зона | Смысл |
|---|---|---|---|
| нет | да | 🟢 recovery | Здоровое восстановление |
| нет | нет | 🟡 stress_rest | Беспокойство в покое |
| да | да | 🔵 healthy_load | Flow-like нагрузка |
| да | нет | 🔴 overload | Перегрузка, тормози |

Пороги: active = `activity_magnitude ≥ 0.5`, hrv_ok = `coherence ≥ 0.5`.

Данные: Polar H10 accelerometer (magnitude без g) или симулятор-слайдер.

Alerts идут только на негативные зоны — recovery и healthy_load
позитивные, тишины хватает.

Влияние на named_state: ось активации `A = 0.7·cog_arousal +
0.3·min(1, activity/2)`. Лежащий юзер с высоким DA не попадает в «flow»
— тело вето.

---

## Named states — 10 регионов в (T, A)-пространстве

Вместо голых скаляров маппим две производные — `T` (tone / valence) и
`A` (activation) — на именованные регионы. Их 10, распределены как
Voronoi:

| Имя | T | A | Когда |
|---|---|---|---|
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

Маппинг: `T ≈ serotonin`, `A ≈ 0.7·mean(D, NE) + 0.3·activity`.

`named_state` — состояние самого юзера. Не путать с `sync_regime`
(FLOW/REST/PROTECT/CONFESS) — тот про симбиоз, 4 лейбла. Здесь
разрешение выше — 10 регионов.

---

## Dual-pool energy — день и долгий резерв

Одной шкалы недостаточно. Две:

- **Дневная энергия** (0..100) — быстрый пул, восстанавливается за ночь
- **Долгий резерв** (0..2000) — медленный пул, копит долгосрочный износ

Burnout — не «дневная на нуле» (это просто усталость), а **истощение
долгого резерва** при хронической перегрузке несколько недель подряд.

**Как тратится.** Пока дневная ≥ 20, весь cost уходит оттуда. Ниже 20 —
дневная покрывает сколько может, остаток плюс штраф 30% идёт из
долгого. Штраф — биологическая интуиция: думать уставшим дороже.

**Как восстанавливается.** Полуночный reset: `long_reserve +=
(sleep_recovery + rest_bonus) · hrv_recovery`. Без HRV — дефолт 0.7
(средний сон).

---

## Decision cost by mode

Простое дело стоит мало, сложное сравнение — много. Цифры
калиброваны по интуиции, можно перенастроить:

| Категория | Режимы | Cost |
|---|---|---|
| simple | free, scout, fan | 3 |
| moderate-light | rhythm | 4 |
| moderate | vector, horizon | 6 |
| moderate-heavy | bayes, race | 7 |
| complex (AND) | builder, pipeline, cascade, scales | 10 |
| critical (XOR) | tournament, dispute | 12 |

На каждое решение делается `user.debit_energy(cost, daily_remaining)`
и в `state.history` пишется аудитный след (cost / daily_used /
long_used).

---

## Симулятор дня

`POST /assist/simulate-day` отвечает «что будет к концу дня если я
выполню этот план?». Полезно для:
- «Хватит ли энергии на завтрашний день?»
- «Если запланирую 3 tournament и 2 dispute, не сожгусь?»
- Визуализации burnout_risk при разных сценариях

**Как работает.** Делает клон UserState через `to_dict/from_dict`,
прогоняет план через `debit_energy` — **живое состояние не меняется**.

**Ответ** содержит суммарные числа (total_cost, total_daily_used,
total_long_used), пошаговую раскладку с `daily_remaining_after` и
`long_reserve_after`, и прогноз end-of-day: `daily_remaining`,
`long_reserve`, `burnout_risk`, `predicted_named_state`.

---

## Персистентность

Всё хранится в `data/user_state.json` — скаляры, expectation, valence,
long_reserve, HRV snapshot. Загружается при старте через
`UserState.from_dict`, сохраняется после каждого `_log_decision`.
Continuity между сессиями — `expectation_by_tod` и `hrv_baseline_by_tod`
переживают рестарт.

---

## Где в коде

- `src/user_state.py` — `UserState` (все 7 расширений), EMA-объекты
  через `src/ema.py`, `debit_energy`, `recover_long_reserve`
- `src/user_state_map.py` — 10 Voronoi-регионов
- `src/assistant.py` — `_MODE_COST`, `_decision_cost`, dual-pool
  persistence через `_load_state`/`_save_state`, endpoints
  `/assist/simulate-day`, `/assist/named-states`

**Открыто:** multi-context expectation (работа vs отношения), N-day
simulator, dynamic cost от сложности tick'а, circadian baseline drift
(DA утром / S вечером).

---

**Навигация:** [← Symbiosis](symbiosis-design.md) · [Индекс](README.md) · [Следующее: HRV →](hrv-design.md)
