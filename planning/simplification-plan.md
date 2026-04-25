# Strategy: 6 правил для Baddle

> Документ — **стратегический**, не история. Хранит **что делаем и чего не делаем**.
>
> Решение автора (2026-04-23): принять **тотальное упрощение** как фазу. 6 правил §4 — каркас в котором новая фича стоит копейки.
>
> Где история — `memory/project_session_*.md` + `git log`. Где текущая реализация подсистем — [docs/](../docs/). Что осталось делать — [TODO.md](TODO.md).

## Сделанные фазы (краткая навигация)

Строка на фазу — для понимания где что искать. Подробности в коде и git log.

- **Phase A — MetricRegistry** (2026-04-24). `src/metrics.py` + 21 EMA в трёх registries (UserState/Neurochem/ProtectiveFreeze). Реализовано Правило 2 §4. Identity bit-identical через 10 тестов. — реализация в [src/metrics.py](../src/metrics.py); описана в Правиле 2 ниже.
- **Phase B — Signal dispatcher** (2026-04-25). `src/signals.py` + `src/detectors.py` (13 pure-function детекторов с urgency-эвристиками). 21 bookkeeping `_check_*` в `cognitive_loop.py` сжаты на ~600 строк. Реализовано Правило 1 §4. — реализация в [src/signals.py](../src/signals.py), [src/detectors.py](../src/detectors.py).
- **Phase C — 3-zone capacity** (2026-04-25). dual-pool `daily_spent + long_reserve` (legacy 0..100 + 0..2000) → `capacity_zone` (green/yellow/red) из 3 параллельных контуров (физио/эмо/когн). — описание в [docs/capacity-design.md](../docs/capacity-design.md).
- **Phase D — РГК-коллапс** (2026-04-25). 5-axis chem (DA/5HT/NE/ACh/GABA) + balance() formula + R/C bit + adapter pattern UserState/Neurochem/PF поверх `src/rgk.py`. Реализовано Правило 6 §4. — описание в [docs/neurochem-design.md § Пять модуляторов](../docs/neurochem-design.md), теория в [rgk-spec.md](rgk-spec.md).

---

## 1. Контекст: почему эта тема вообще возникла

После серии feature-burst сессий TODO раздулся до ~260 строк, ~80+ пунктов, из которых реальная оценка кодинга — **150-200 часов работы**. Проект начал выглядеть как «собрать 20 подсистем параллельно», каждую bespoke. Три честных наблюдения:

1. **Docs стали опережать код намеренно.** Написание целевой `capacity-design.md` создало долг в 20+ часов миграции. То же с резонансной моделью — 3 новых code-changes появились из переоптики.
2. **Прайм-директива не применялась к TODO.** Объявили `sync_error` единственной метрикой ценности, но 80+ пунктов не фильтровались вопросом «реально ли снизит sync_error за 2 мес use?».
3. **Каждая идея добавлялась, старые удалялись редко.** Workspace-cleanup был исключением (−1030 строк, −2 doc'а, −3 подсистемы). Обычно наоборот: каждая session заканчивалась новыми пунктами.

При текущем паттерне за 6-12 месяцев получается **~20000 строк кода** и **28+ документов**. Решение: остановиться, собрать core-правила, упростить существующее, **только потом** добавлять новое.

---

## 2. Честная оценка текущего TODO по уровням функциональности

Без consolidation (каждая фича bespoke):

### Tier 1 — «Честный минимум» (~35-40ч, 1-2 месяца)

Что делаем:
- **Capacity миграция** — docs обещают 3-контурную модель, код использует dual-pool. Долг, закрывать.
- **PolarH10Adapter** — реальное тело, не симулятор. Базовый сенсор.
- **Constraint expansion через LLM** — закрывает баг «8B Q4 предложила кефир как замену молока».
- **Throttle urgency-scaling** — после 2 нед данных из `throttle_drops.jsonl`.
- **UX полировка** — один chat (убрать legacy entry), specialized card renders для fan/rhythm, patterns auto-abandon.

**Что получается:** Baddle ощущает реальное тело через Polar, имеет честную 3-контурную capacity, docs совпадают с кодом, 3-4 user-visible UX улучшения. Daily-use ready, нет скрытого долга.

### Tier 2 — «Хорошо оборудованный» (~80-100ч, 3-4 месяца)

Tier 1 +
- Резонансный пакет: aperture, frequency_regime, focus_residue, дыхательный режим, prompt preset.
- **RAG в `execute_deep`** (#15 из бэклога — «самая правильная из списка»): retrieval similar past nodes/outcomes во всех reasoning-путях, не только pump/DMN.
- **Рассел 2D** (Valence × Arousal): UI-карта состояния, morning briefing.
- **AppleWatchAdapter** — sparse HR через HealthKit/Shortcuts.
- **HRV-аналитика** — VO2max тест, HRR (heart rate recovery).
- META-вопросы ночная генерация, plan.create_from_text, еда-предложения.

**Что получается:** Нейро-ассистент. Видит три слоя усталости, предлагает breathing по контексту, помнит прошлое во всех reasoning-путях, показывает эмоциональное состояние 2D-картой, ловит recovery metrics.

### Tier 3 — «Полное видение» (~150-200ч, 6-12 месяцев)

Tier 2 +
- Event-based HRV compression (sensor_readings → hrv_events.jsonl).
- Autonomic strain detector (хронический симпатический перегруз).
- Priority queue dispatcher (step #3 из throttle).
- **Tool-use слой**: calendar / weather / web.search / file / rag + permission model.
- OQ resolutions: personal capacity prior, память как ключ настройки (если решим).
- Task tracker полный: backlog, auto-scheduling через capacity-зоны, калибровка оценки через surprise_at_start.

**Что получается:** Full life-assistant. Действует в мире через tools, имеет per-user priors, автопланирует день через capacity, детектит хронические перегрузки ВНС. **AHI** (Artificial Human Interface) из world-model.md.

### Что не получается даже в Tier 3

- Не становится conscious (явно в world-model: «Не сознание. Не попытка сделать AI живым»).
- Не становится терапевтом (граница: не называет эмоции).
- Не становится mass-market (single-user local tool).
- Не становится multi-user (каскад зеркал ломается — один человек, одно зеркало).

---

## 3. Проблема: каждая фича как отдельная подсистема

Почему 20000 строк. Честный взгляд на то что происходит в TODO:

- **21 check-функция** в `cognitive_loop.py`, каждая — своя cascade из preconditions → throttle → quiet-after-other → emit. Свои константы (`SYNC_SEEKING_INTERVAL`, `RECURRING_LAG_INTERVAL`, `SUGGESTIONS_MAX_PER_DAY`, `PLAN_REMINDER_MINUTES`, `BRIEFING_INTERVAL`...). Свой throttle logger. Повторение паттерна 21 раз.
- **20+ `update_from_*` методов** в `UserState` / `Neurochem` / `CognitiveState`, каждый со своим decay, call-site, bootstrapping. Update-логика разбросана по 5 файлам.
- **6 параллельных хранилищ** — `graph.json`, `goals.jsonl`, `activity.jsonl`, `checkins.jsonl`, `recurring` в profile, `patterns.jsonl`. Каждое со своим replay, мутациями, read-API.

Структурно большая часть TODO — выражения **5-6 простых правил**, которые мы не вынесли как абстракции.

---

## 4. Шесть правил на которые всё коллапсирует

### Правило 1 — Любое событие к юзеру это `Signal(type, urgency, content, expires_at)`

**Сейчас:** 21 check-функция, каждая bespoke cascade.

**Было бы:** 21 детектор вида `detect() -> Optional[Signal]`. Один dispatcher с attention-budget. Новая фича = 15-20 строк детектора.

```python
# Один dispatcher-цикл
candidates = []
for detector in DETECTORS:
    sig = detector()
    if sig:
        candidates.append(sig)

# Фильтр expired, сортировка по urgency, budget gate
candidates = [s for s in candidates if s.expires_at > now]
candidates.sort(key=lambda s: -s.urgency)
for sig in candidates[:ATTENTION_BUDGET_PER_WINDOW]:
    emit(sig)
```

**Сколько коллапсирует:**
- Throttle steps #2 и #3 (urgency-scaled + priority dispatcher) — **встроено**
- `_log_throttle_drop` — становится естественным output'ом dispatcher'а
- `SYNC_SEEKING_INTERVAL`, `RECURRING_LAG_INTERVAL`, `BRIEFING_INTERVAL` и остальные 15+ throttle-констант — **умирают**
- `SYNC_SEEKING_QUIET_AFTER_OTHER` cascade — **умирает** (attention-budget делает то же)
- `SUGGESTIONS_MAX_PER_DAY` — становится просто urgency-sort top-K
- `patterns auto-abandon` (TODO) — бесплатно через `expires_at`
- Counterfactual honesty (уже сделано) — dispatcher сам решает через budget
- 21 check-cascade в `cognitive_loop.py` — 21 детектор-функция

### Правило 2 — Любая производная метрика это `EMA(source_event, decay)`

**Сейчас:** `update_from_hrv`, `update_from_engagement`, `update_from_feedback`, `update_from_message`, `update_from_plan_completion`, `tick_expectation`, `tick_valence`, ...

**Было бы:** declarative registry:

```python
METRICS = {
    "agency":         EMA(source="plan_completion", decay=0.95),
    "dopamine":       EMA(source="distinct_d",      decay=0.9),
    "valence":        EMA(source="sentiment",       decay=0.92),
    "cognitive_load": EMA(source="activity_event",  decay=0.85),
    "serotonin":      EMA(source="weight_stability", decay=0.95),
    # ... 20+ штук
}

# Event fires → registry updates
def fire_event(event_type, payload):
    for name, ema in METRICS.items():
        if ema.listens_to(event_type):
            ema.update(payload)
```

`vector()` просто читает:

```python
def vector():
    return [METRICS[name].value for name in ["dopamine", "serotonin", "norepinephrine"]]
```

**Сколько коллапсирует:**
- TODO-пункт «Все формулы в один файл для наглядности» — **встроено**
- Capacity миграция становится **declarative 5-строчной** вместо 30-item migration
- `frequency_regime` из резонансной модели — **одна строка** в registry
- Любой новый derived field — **одна строка**
- 20+ `update_from_*` методов — **единый update_metrics(event)**
- Scattered decay constants — **в одном registry**

### Правило 3 — Любое знание это нода графа, любая связь через `distinct()`

**Сейчас:** 6 параллельных хранилищ.

**Было бы:** всё ноды графа с type-фильтрами:

```python
record(type="goal",            content="Снизить стресс", refs=[])
record(type="goal_instance",   content="выпил воды",     refs=[goal_id])
record(type="constraint",      content="не ем лактозу",  refs=[])
record(type="pattern",         content="пропуск завтрака → crash", refs=[...])
record(type="checkin",         content={energy:60, ...}, refs=[])
record(type="activity",        content="Обед",           refs=[])
```

Queries через:
```python
nodes_where(type="goal", status="open")
nodes_where(type="constraint", polarity="avoid")
nodes_near(embedding=query_vec, type="pattern", k=5)
```

**Сколько коллапсирует:**
- Пакет «Всё через граф» из бэклога (#3, #5, #6, #7, #13) — **реализуется или отвергается cleanly**
- `goals_store.py` (~300 строк) — **умирает или сильно сокращается**
- `recurring.py` (~400 строк) — **становится фильтром над графом**
- Большая часть `activity_log.py` — **node-filter + category detection**
- `patterns.jsonl` — **тоже ноды**

**⚠ Это правило спорное.** В бэклоге я сам писал:
> Goals имеют transactional семантику (atomic `record_instance`) — граф append-only, при crash half-states. Поиск «вчерашние 3 instance» = O(log N) traversal вместо O(1) dict lookup. Компромисс: `goal_instance` как узел **с ref** в goals_store, не замена store'а.

Теряется atomic-update для goals. Оставляем как **условное** правило — если примем, рефакторинг значительный; если не примем, хранилища остаются параллельными, но это ок.

### Правило 4 — `distinct()` единственный примитив рассуждения

Уже так (`docs/nand-architecture.md`). 4 операции (SmartDC, Pump, Novelty, Embedding-first) — надстройки над `distinct`. Тут сжимать нечего, просто **держим дисциплину**: не писать cosine_similarity руками мимо `distinct`.

### Правило 5 — PE единственный драйвер поведения системы

Уже так (`docs/friston-loop.md`). 5 каналов PE → max → `imbalance_pressure` → замедление циклов через `_idle_multiplier` → интенсивность alerts. Всё автономное поведение — отсюда. Новые caналы добавляются, но структура та же.

### Правило 6 — Состояние = один резонатор, всё остальное его проекции

Уже в docs (`resonance-model.md`). CognitiveState.precision, Neurochem.γ, HRV-ширина, aperture — проекции одного. В коде это уже так работает, просто без явного имени. Держим дисциплину: не плодить parallel state-контейнеры.

### Правило 7 — Не давить, а инвертировать

Добавлено 2026-04-24 после расширения резонансной модели (см. [resonance-model § Две роли одного резонатора](../docs/resonance-model.md)).

**Суть:** когда система детектит «плохой паттерн» у юзера (спираль тревоги, выгорание, руминация, деструктивная волна), первичный соблазн — усилить давление (больше alerts, morализация, push harder). Это **всегда ошибка**. Давление против устойчивого аттрактора включает отрицательную обратную связь: система упруго возвращается в исходное состояние как только давление снимается, паттерн не ломается, а уходит в тень и становится жёстче.

**Правильный приём — генерация контрволны** (фазовый сдвиг на 180°) или рассинхронизация задержкой, или смена несущей. Три способа без давления:

- **Инверсия частоты** — генерировать сигнал, противоположный фундаментальной гармонике паттерна (парадоксальная интенция Франкла, вопрос-перевёртыш, абсурд как взлом автопилота).
- **Рассинхронизация задержкой** — вставить паузу в цикл обратной связи, чтобы волна возвращалась не в фазе усиления, а в фазе гашения. В коде: `suppress observation_suggestion` при активной сессии, `SYNC_SEEKING_QUIET_AFTER_OTHER`, hebbian decay без обращений.
- **Смена несущей** — сменить канал/тон/модальность так, чтобы старый аттрактор в новой среде физически не поддерживался. В коде: выбор тона sync_seeking (caring / curious / reference / simple), переключение mode в depth engine.

**Критерий для новых детекторов/Signal'ов:** если мой сигнал усиливает паттерн юзера (даже через негатив — осуждение, моральное давление, повторение «ты должен») — я давлю, это отрицательная обратная связь. Если мой сигнал разрывает резонанс через инверсию, паузу или смену несущей — я корректирую, система переходит в новый аттрактор.

**Вписывается в Правило 1** (Signal dispatcher) как свойство urgency-computation: детектор с высоким urgency должен предлагать **контрволну**, не усиление. Если urgency растёт через «громче, чаще, жёстче» — это bespoke-костыль, а не Signal. Проверка при review: каждый proactive check должен явно отвечать, какой из трёх способов без-давления он реализует.

**Где уже применяется в коде:**
- `detect_observation_suggestions` silent skip при `last_input < 10min` — пауза (рассинхронизация)
- `detect_sync_seeking tone` choice (через `_generate_sync_seeking_message`) — смена несущей
- `ProtectiveFreeze` при конфликтах — прекращение обновлений, чтобы не давить на юзера ошибкой предсказания
- `sync_regime` FLOW → PROTECT/CONFESS — явная инверсия тактики взаимодействия

---

---

## 7. Tradeoffs честно

### Плюсы

- **Проект ~5k строк вместо 20k.** В 3-4 раза меньше кода поддерживать.
- **Новая фича = декларация, не подсистема.** Bar на добавление низкий, цикл добавить/попробовать/удалить быстрый.
- **Docs сжимаются.** 6 правил ≡ 6 контрактов. Книга перестаёт быть 28 глав, становится ~10-12 глав с чёткой структурой (Foundations → 6 правил → их применения).
- **Тестируемость.** 6 контрактов = 6 test-suites. Bespoke-путь = 21 test-suite плюс интеграционные cascades.
- **Легче онбордить нового разработчика (или себя через год).** «Вот 6 правил, всё остальное — их применения» vs «вот 28 docs, каждый про свою подсистему».

### Минусы

- **25-30ч рефакторинга без видимых фич.** Требует дисциплины не добавлять фичи в этот период.
- **Risk неправильной абстракции.** 6 правил — моя гипотеза. Если на практике детекторы не влезают в единый `Signal(type, urgency, content, expires_at)` — придётся расширять Signal или разъединять dispatcher.
- **Дисциплина после.** Абстракция помогает только если её соблюдают. Через год легко начать писать bespoke cascade снова. Нужно правило «новая фича = 1 из 6 или стоп, думай».
- **Меньше артефактов проекта.** Для Habr-статьи больше скучнее — «сделал чистую архитектуру» vs «построил 28 подсистем».
- **Теряется некоторая гибкость в краях.** Bespoke cascade может учесть нетипичный edge-case легче чем декларативный dispatcher.

### Что делает эту инвестицию разумной именно сейчас

1. **Workspace-removal показал паттерн.** Уже знаем как делать consolidation (−1030 строк за одну сессию). Мышца тренирована.
2. **Capacity миграция впереди** — делать её bespoke = +20ч долга, делать её declarative после Фазы A = 5-7ч. Разница в 3x покрывает инвестицию в Фазу A сама по себе.
3. **Throttle steps #2 и #3 бы делались.** Строя Signal dispatcher мы реализуем оба step'а одновременно. Иначе — 18ч дополнительно.
4. **Капитал понимания растёт.** После месяца bespoke-фичей знание систем разложено по 21 месту. После consolidation — централизовано, восстанавливается быстрее при возврате к проекту.

---

## 8. Дисциплина — ключевой risk

Абстракция работает только если **соблюдается всегда**. Полу-абстракция хуже consistent bespoke, потому что дают иллюзию структуры без её гарантий.

### Рабочее правило после consolidation

**Любая новая фича проходит через фильтр:**
1. Это новый детектор в dispatcher? — OK, написать как Signal-producer.
2. Это новый derived metric? — OK, добавить в MetricRegistry одной строкой.
3. Это новый тип ноды графа? — OK, через `record(type=...)`.
4. Это что-то что не влезает? — **СТОП**. Или оно поверх 1-3 (тогда reformulate), или оно 7-е правило (тогда редкий случай, серьёзно думать прежде чем добавлять).

### Что делать если правило не влезает

Если через 3+ месяца появляется реальная фича которая **честно не укладывается** в 6 правил — это означает что **6-е правило неполно**. Тогда расширяем осознанно:
- Документируем почему не влезло.
- Обсуждаем что добавить: 7-е правило? изменить одно из 6?
- Не делаем bespoke escape hatch «на этот раз».

Если пропускаем этот rigor — через год снова TODO на 80 пунктов.

---

## 5. Tradeoffs честно

### Плюсы

- **Проект ~5k строк вместо 20k.** В 3-4 раза меньше кода поддерживать (фактически — see [TODO.md § 🧹 Cleanup](TODO.md) для реалистичного target после Phase E-I).
- **Новая фича = декларация, не подсистема.** Bar на добавление низкий, цикл добавить/попробовать/удалить быстрый.
- **Docs сжимаются.** 6 правил ≡ 6 контрактов.
- **Тестируемость.** 6 контрактов = 6 test-suites. Bespoke-путь = 21 test-suite плюс интеграционные cascades.
- **Легче онбордить нового разработчика (или себя через год).** «Вот 6 правил, всё остальное — их применения» vs «вот 28 docs, каждый про свою подсистему».

### Минусы

- **25-30ч рефакторинга без видимых фич.** Требует дисциплины не добавлять фичи в этот период.
- **Risk неправильной абстракции.** 6 правил — гипотеза. Если на практике детекторы не влезают в единый Signal — придётся расширять Signal.
- **Дисциплина после.** Абстракция помогает только если её соблюдают. Через год легко начать писать bespoke cascade снова.
- **Меньше артефактов проекта.** Для Habr-статьи скучнее — «сделал чистую архитектуру» vs «построил 28 подсистем».
- **Adapter overhead.** Phase D показал — facades поверх _rgk дают +500 строк boilerplate, чтобы убрать нужны Phase E-I (см. [TODO.md § 🧹 Cleanup](TODO.md)).

---

## 6. Дисциплина — ключевой risk

Абстракция работает только если **соблюдается всегда**. Полу-абстракция хуже consistent bespoke, потому что даёт иллюзию структуры без её гарантий.

### Рабочее правило: фильтр для новых фич

**Любая новая фича проходит через фильтр:**
1. Это новый детектор в dispatcher? — OK, написать как Signal-producer (Правило 1).
2. Это новый derived metric? — OK, добавить в `_rgk` как extractor функцию или новый axis в Resonator (Правило 2).
3. Это новый тип ноды графа? — OK, через `record(type=...)` (Правило 3).
4. Это новый chem axis? — OK, добавить в `Resonator.__init__` + `balance()` formula coverage + 1 feeder вызов (Правило 6).
5. Это что-то что не влезает? — **СТОП**. Или оно поверх 1-4 (тогда reformulate), или оно 8-е правило (редкий случай, серьёзно думать).

### Что делать если правило не влезает

Если появляется реальная фича которая **честно не укладывается** в правила — это означает что правила **неполны**. Тогда расширяем осознанно:
- Документируем почему не влезло.
- Обсуждаем что добавить: новое правило? изменить существующее?
- Не делаем bespoke escape hatch «на этот раз».

Если пропускаем этот rigor — через год снова TODO на 80 пунктов.

---

## Связанные docs

- [TODO.md](TODO.md) — что осталось делать
- [docs/neurochem-design.md § 5-axis](../docs/neurochem-design.md) — Правило 6 в коде после Phase D
- [TODO.md § 🧹 Cleanup](TODO.md) — опциональный line-count cleanup (Phase E-I)
- [rgk-spec.md](rgk-spec.md) — теоретическая модель РГК
- [resonance-code-changes.md](resonance-code-changes.md), [breathing-mode.md](breathing-mode.md), [resonance-prompt-preset.md](resonance-prompt-preset.md) — Tier 2 specs
- [docs/world-model.md](../docs/world-model.md), [docs/resonance-model.md](../docs/resonance-model.md), [docs/nand-architecture.md](../docs/nand-architecture.md), [docs/friston-loop.md](../docs/friston-loop.md) — теоретический каркас
