# Предиктивная user-модель

> Пользователь — не тег «юзер», а **физическая система** со своей
> энергетикой, ожиданиями и предсказуемой траекторией состояний. Baddle
> моделирует это в UserState — симметрично Neurochem системы, но с
> другими источниками сигналов.

Этот документ описывает четыре расширения UserState поверх базовой
симбиоз-архитектуры (см. [symbiosis-design.md](symbiosis-design.md)):
прогнозные ошибки со знаком, именованные состояния, двухпуловая энергетика,
и симулятор дня.

## 1. Signed prediction error

До этого `sync_error = ‖user − system‖` — L2-норма, **всегда положительный
скаляр**. Направление ошибки было потеряно: «система недооценила юзера»
и «переоценила» давали одинаковое |sync_error|.

Теперь UserState хранит:
- `expectation: float` — медленный EMA `state_level` с decay=0.98
  (baseline ожиданий, переживает ~50 обновлений)
- `reality: float` — derived = current `state_level = (D+S)/2`
- `surprise: float` — derived = `reality − expectation`, signed ∈ [−1, 1]
- `imbalance: float` — `|surprise|`, эквивалент MindBalance ID

Сигнал обновляется автоматически через `tick_expectation()` после каждого
`update_from_*` вызова (HRV / timing / message / feedback / energy).

**Интерпретация:**
- `surprise > 0` → реальность превзошла ожидания (эффект как «благодарность»/подъём)
- `surprise < 0` → реальность ниже ожиданий (эффект как «разочарование»/спад)
- `imbalance` большой → активация сознания растёт (см. MindBalance A = energy × (1 + ID · 0.5))

## 2. Named user-states (Voronoi)

[src/user_state_map.py](../src/user_state_map.py) содержит 10 именованных
регионов в (T, A)-пространстве из прототипа Игоря (MindBalance v4):

| Key | T (tone) | A (activation) | Когда |
|-----|----------|----------------|-------|
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

Маппинг Baddle-скаляров:
- `T ≈ serotonin` (валентность, стабильность)
- `A ≈ mean(dopamine, norepinephrine)` (arousal)

`UserState.named_state` — derived @property, возвращает `{key, label,
advice, distance, coord}` ближайшей точки. Это **состояние пользователя
как такового** — не путать с `sync_regime` (FLOW/REST/PROTECT/CONFESS
про симбиоз с системой). Разрешение выше — 10 регионов vs 4.

Эндпоинт `GET /assist/named-states` отдаёт полную карту для UI-рендера.

## 3. Dual-pool energy

MindBalance v2 различал **daily energy (100)** и **total reserve (2000)**:
быстрый пул восстанавливается ночью, медленный копит долгосрочный износ.
Burnout risk — истощение long reserve при хронической перегрузке.

В Baddle:
- `daily_spent: float` — в `user_state.json`, сумма энергии потраченной сегодня
- `long_reserve: float` — в UserState, 0..2000, стартует с 1500, persisted

**Cascade rule** (`UserState.debit_energy(cost, daily_remaining)`):

```
if daily_remaining >= cost and daily_remaining >= 20:
    → весь cost из daily
elif daily_remaining < 20:
    → daily покрывает свой максимум (daily_remaining)
    → long_used = overflow + cost · 0.3  (штраф за работу на пустом баке)
    → long_reserve -= long_used
```

Tax 30% — **энергия дороже когда daily уже на дне**. Это биологическая
интуиция: сложнее думать уставшим.

**Recovery** (`recover_long_reserve(hrv_recovery)`):
- Вызывается при полуночном reset (`_ensure_daily_reset`)
- Amount = `90 · hrv_recovery + 20 · hrv_recovery`
  (sleep_recovery + rest_bonus из MindBalance settings)
- Без HRV → дефолт recovery=0.7 (средний сон)

## 4. Decision cost by mode

[src/assistant.py](../src/assistant.py) `_MODE_COST` таблица:

| Category | Modes | Cost |
|----------|-------|------|
| simple | free, scout, fan | 3 |
| moderate-light | rhythm | 4 |
| moderate | vector, horizon | 6 |
| moderate-heavy | bayes, race | 7 |
| complex (AND) | builder, pipeline, cascade, scales | 10 |
| critical (XOR) | tournament, dispute | 12 |

`_log_decision(state, kind, meta, mode_id, hrv_recovery)` выставляет
`cost = _decision_cost(mode_id)`, вызывает `user.debit_energy(...)`,
записывает в `state.history` с полями `cost / daily_used / long_used`
(для audit).

## 5. Day planning simulator

`POST /assist/simulate-day` — «если я сделаю X, Y, Z сегодня, что будет
к концу дня?»

```json
Request:
{
  "plan": [{"mode": "tournament"}, {"mode": "fan"}, ...],
  "hrv_recovery": 0.7   // optional, иначе live HRV
}

Response:
{
  "plan_size": 9,
  "total_cost": 102,
  "total_daily_used": 94,
  "total_long_used": 15.4,
  "steps": [
    {"mode": "tournament", "cost": 12, "daily_used": 12,
     "long_used": 0, "daily_remaining_after": 88,
     "long_reserve_after": 1500}, ...
  ],
  "end_of_day": {
    "daily_remaining": 0,
    "long_reserve": 1494.4,
    "burnout_risk": 0.253,
    "predicted_named_state": {
      "key": "neutral", "label": "Нейтральное",
      "advice": "..."
    },
    "dopamine": 0.5, "serotonin": 0.5, ...
  }
}
```

Симуляция делает **клон UserState** (via to_dict/from_dict), шагает по
плану через `debit_energy` — живое состояние не меняется. Полезно для:
- «хватит ли мне энергии на завтрашний план»
- «если я запланирую 3 tournament и 2 dispute, не сожгусь ли»
- показа графика burnout_risk при разных сценариях

## Persistence

Всё сохраняется в существующий `user_state.json`:
- `daily_spent`, `decisions_today`, `last_reset_date` — уровень session
- `user_state_dump`: `{dopamine, serotonin, norepinephrine, burnout,
  expectation, long_reserve, hrv: {...}}` — UserState серилиазация

Загрузка: `_load_state()` читает файл и вызывает `set_user_state(
UserState.from_dict(user_state_dump))`. Это восстанавливает expectation
и long_reserve между сессиями — прерываемая continuity.

Сохранение: `_save_state(state)` сериализует текущий UserState перед
записью JSON'а. Pattern: каждый вызов `_log_decision` + `_save_state`
даёт персистентность.

## Ограничения

- **Expectation EMA единая** — не различает контекст (работа vs отношения).
  Мультиконтекстная baseline (как 3 вкладки в MindBalance) — будущая работа.
- **Долгосрочный прогноз на N дней нет**. `/assist/simulate-day` — только
  один день. N-day prognosis с чередующимися recovery nights — TODO.
- **Costs жёсткие**. Можно скейлить от complexity параметров тика (число
  подзадач и т.п.), сейчас фикс.
- **Circadian baseline drift** — dopamine утром/serotonin вечером — не
  реализован.

## Файлы

- [src/user_state.py](../src/user_state.py) — UserState расширен signed
  surprise, dual-pool, named_state, debit_energy, recover_long_reserve
- [src/user_state_map.py](../src/user_state_map.py) — 10 Voronoi regions
- [src/assistant.py](../src/assistant.py):
  - `_MODE_COST` + `_decision_cost()`
  - `_load_state` / `_save_state` — UserState persistence через `user_state_dump`
  - `_ensure_daily_reset` — полуночное восстановление long_reserve
  - `_compute_energy` — dual-pool snapshot
  - `_log_decision` — mode-взвешенный debit
  - `/assist/simulate-day`, `/assist/named-states` endpoints
