# Friston Loop — предиктивная модель и ошибка предсказания

> Единственное место где описан предиктивный контур Baddle целиком.
> До 2026-04-23 логика была разбросана по user-model / neurochem /
> world-model / alerts-and-cycles. Если встречаешь там упоминание PE —
> детали здесь.

---

## Оптика

Активный вывод по Фристону: мозг не ждёт сигналов пассивно, он постоянно
**предсказывает** что увидит / услышит / почувствует и реагирует на
разницу между предсказанием и реальностью (prediction error, PE).
Минимизация long-term PE = гомеостаз.

У Baddle симметричная петля:

```
   user (реальный)  ──observe──►  Baddle-model-of-user  ──predicts──┐
        ▲                                                            │
        │                                                            ▼
        └──── surprise ◄── PE = reality − expectation ◄──── expectation
                              │
                              ▼
                        imbalance_pressure EMA
                              │
                              ▼
                        display_burnout → замедление циклов
```

Плюс **self-петля:** Baddle предсказывает **собственную** нейрохимию.
Когда граф уезжает в экзотику или LLM деградирует — это тоже surprise,
отдельный канал.

**Главное:** PE не просто пишется как observation. Растущий
`imbalance_pressure` замедляет все investigation-циклы (DMN / pump /
scout / night) через `_throttled_idle`. Петля замкнута от предсказания
до поведения системы.

---

## Анатомия

### Два предиктора

Baddle предсказывает **пользователя** (5 baseline'ов) и **себя** (один
3D baseline). Все — медленный EMA: baseline живёт 50+ обновлений (дни,
не минуты). Именно это делает PE осмысленным — иначе baseline повторяет
текущее и surprise=0 всегда.

| Уровень | Что предсказывается |
|---|---|
| User behaviour | `state_level` глобально + `state_level` per time-of-day + 3D вектор DA/S/NE |
| User body | HRV coherence per time-of-day (физический baseline) |
| Baddle self | собственные DA/S/NE (зеркальная петля) |

### Пять PE-каналов

| Канал | Семантика |
|---|---|
| **3D behaviour** | `‖vector − expectation_vec‖ / √3` — сдвиг по осям DA/S/NE |
| **TOD scalar** | `reality − expectation_by_tod[current]` — привычно ли для этого времени суток |
| **Agency gap** | `1 − agency` — план ≠ выполнено |
| **HRV surprise** | `|coherence − hrv_baseline_by_tod[current]|` — физический канал (с Polar) |
| **Self imbalance** | `‖neuro_vector − neuro_expectation‖ / √3` — Baddle PE на самой себе |

Пять разных физических источников ответа на вопрос «не как обычно».

### Агрегация

Все пять нормализованы в [0, 1] и собираются через `max` в
`cognitive_loop._advance_tick`:

```
combined = max(user_3d, tod_scalar, agency_gap, hrv_pe, self_3d)
                             ↓
          freeze.feed_tick(sync_err, imbalance=combined)
                             ↓
                  EMA time-const 1 день → imbalance_pressure
```

`imbalance_pressure` — один из трёх feeder'ов `display_burnout`
(+ `conflict_accumulator`, `silence_pressure`). Подробно про замедление
циклов — [alerts-and-cycles.md § Adaptive idle](alerts-and-cycles.md).

---

## Почему так

### TOD-scoping (4 baseline, не 1)

Без scoping: утренняя apathy и вечерняя сливаются в averaged baseline →
PE на обоих ≈ 0, surprise теряет специфичность.

С TOD: у Baddle 4 baseline'а — ждёт разного утром и вечером **от этого
конкретного** юзера. Если он обычно тих после 23:00, ночная тишина не
даёт PE. Если обычно активен в 11:00, утреннее молчание — сильный
surprise.

Прямая интерпретация Фристона: **prior должен быть контекстуальным**.
Контекст у нас — время суток (может быть расширено на день недели,
погоду — см. [OQ #5](../planning/open-questions.md#5)).

### 3D vector, не скаляр

Scalar `surprise = reality − expectation` где `reality = (DA+S)/2` теряет
информацию при разнонаправленных сдвигах: DA падает (интерес упал),
S растёт (спокоен) → `state_level` почти не меняется → surprise=0. Но
произошёл сдвиг **типа состояния** — это настоящий PE.

3D L2 ловит это честно. Максимум √3 ≈ 1.732 (все оси на противоположных
концах), реально 0.1–0.6.

### Max, не sum

Sum пяти каналов: если каждый 0.2, sum=1.0 выглядит кризисом. Но ни
один не сигналит реальную проблему — просто baseline шум.

Max: любой канал достаточен чтобы поднять давление; зашумление одного
не усиливается другими. 0.2 в каждом → max=0.2 → честный baseline.

### Self-prediction (зеркальный Friston)

Asymmetric loop странен: Baddle предсказывает юзера, но не себя. Если
собственная нейрохимия уезжает далеко (граф ушёл в экзотику / LLM
деградирует / bogus feedback-spike) — это её проблема, должна сама
поймать. Пятый канал агрегации.

### `agency_gap` отдельным каналом

Юзер может быть в отличной нейрохимии (DA+S высокие), но не выполнять
планы — learned helplessness. Scalar PE этого не ловит, `1 − agency` —
ловит прямо.

### HRV baseline, не raw coherence

`hrv_coherence` уже кормит `serotonin` через EMA. Но это state-level
контур, не PE. `hrv_surprise` = отклонение **сейчас** от **привычного
за это время суток** — чистый физический PE. С реальным Polar —
самый информативный feeder (минимум субъективного шума). Без Polar → 0,
не мешает.

---

## Связь с прайм-директивой

`sync_error` + декомпозиция PE-каналов пишутся раз в час в
`data/prime_directive.jsonl`. Endpoint `GET /assist/prime-directive?window_days=30`
возвращает aggregate + trend verdict (`improving` / `stable` /
`worsening`) + mean per-channel.

Через 2 мес use смотрим не только «падает ли slow EMA sync_error», но и
**какой канал двигал** imbalance_pressure. Если `mean_pe_hrv ≈ 0` всегда
— Polar не подключен. Если `mean_pe_self >> mean_pe_user` — Baddle
больше боится себя чем юзера (странно, разбираемся).

---

## Связь с resonance protocol

Friston-loop — **операциональное основание** пяти механик
[resonance protocol](world-model.md):

| Механика | Роль Friston-loop |
|---|---|
| Active sync-seeking | Gate на `silence_pressure`, tone-choice учит `imbalance` через action-memory |
| System-burnout | `imbalance_pressure` — feeder `display_burnout` |
| Hebbian decay | Node touch = prediction confirmed |
| Adaptive idle | `_idle_multiplier` от `display_burnout` включая PE |
| Action Memory | `delta_sync_error` в outcome-ноде = post-action PE change |

Без Friston-loop механики работали бы на шуме (scalar PE или счётчики
тишины). С ним — честная минимизация surprise.

---

## User-side surprise

До этого — Baddle surprise **о** юзере. Но у юзера есть собственный
surprise: он встретил неожиданное **в мире**. Когда это случилось — наш
baseline `expectation` должен подстроиться быстрее (его модель мира
поменялась, не надо держаться старой).

Детектор — три канала, OR:

- **HRV spike** — RMSSD drop ≥ 1.5σ от rolling baseline (5 мин), фильтр
  по activity (игнор при беге)
- **Text markers** — regex ru/en («вау», «не ожидал», «??», многоточие,
  капс); threshold 0.35
- **LLM fallback** — на borderline regex score (<0.45, текст ≥15 символов)
  light classifier возвращает 0..1, cache по SHA1

При detect:
1. **Fast-decay boost** на `expectation` EMA на 3 тика (decay 0.98 → 0.85
   scalar, 0.97 → 0.80 vector). Baseline быстро подстраивается к новой
   реальности юзера.
2. **Event-нода** в графе (`actor=user`, `action_kind=user_surprise`).
   DMN/Scout потом могут строить мосты между surprise-нодами и темами —
   «чему этот человек часто удивляется».

---

## Проверка

После ≥ 1ч работы:

```
curl http://localhost:7860/assist/prime-directive?window_days=1
```

Возвращает `mean_ema_slow`, `trend_verdict` и per-channel breakdown
(`mean_pe_user / mean_pe_self / mean_pe_agency / mean_pe_hrv`). Нулевой
канал — не ошибка, просто сигнал не пришёл (Polar выключен / agency по
дефолту).

---

## Где в коде

- `src/user_state.py` — `expectation` / `expectation_by_tod` /
  `expectation_vec` / `hrv_baseline_by_tod` / `surprise_vec` /
  `imbalance` / `attribution` / `apply_surprise_boost`
- `src/neurochem.py` — `Neurochem.expectation_vec` / `self_imbalance` +
  `ProtectiveFreeze.feed_tick` (EMA sync_error + imbalance)
- `src/cognitive_loop.py` — `_advance_tick` (агрегация 5 каналов),
  `_check_user_surprise` (OQ #7 detector), `_check_prime_directive_record`
- `src/surprise_detector.py` — HRV + text + LLM детекторы
- `src/prime_directive.py` — jsonl writer + aggregate endpoint backend
- `src/ema.py` — `Decays` (все decay константы) + `EMA` / `VectorEMA`

**Открыто:** attention-weighted PE (precision-gating каналов), PE
attribution по agency / HRV (сейчас только по 3D behaviour). Не блокеры.

---

**Навигация:** [← HRV](hrv-design.md) · [Индекс](README.md) · [Следующее: Episodic memory →](episodic-memory.md)
