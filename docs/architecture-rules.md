# Архитектурные правила Baddle

> Семь правил, на которые проект коллапсирует. Каждая фича — выражение одного из них; новый тип логики — расширение конкретного правила, не отдельная подсистема.

---

## Зачем правила

При feature-burst разработке проект растёт как 20+ параллельных подсистем — каждая со своим throttle, decay, persistence. Структурно большая часть TODO — выражения 5-7 простых паттернов, которые проще вынести как абстракции один раз.

Аудит реальности (2026-04-25): 60% `src/` — каркас семи правил, 39% — inherent IO (104 Flask routes + persistence), ~10% — bespoke pockets подлежащие cleanup. Реалистичный floor проекта ~22-23k LOC. Правила не дают «5k LOC silver bullet», они дают **низкий barrier на новую фичу** (добавить декларацию, не строить подсистему) и **читаемую архитектуру** (один контракт = один файл, не разбросано по 5 файлам).

---

## Правило 1 — Любое событие к юзеру это Signal

**Контракт:** `Signal(type, urgency, content, expires_at, dedup_key, source)`. Детектор — pure-function `(ctx) -> Optional[Signal]`. Один dispatcher с attention-budget per window решает что эмитить, что дропнуть в `data/throttle_drops.jsonl`.

**Каркас:** [src/signals.py](../src/signals.py) — `Dispatcher` + `Signal`. [src/detectors.py](../src/detectors.py) — 13 pure-function детекторов с urgency-эвристиками. Подключение: `cognitive_loop._loop` → собирает кандидаты → `dispatcher.dispatch(candidates, now, user_mode=...)` → `_add_alert` для emitted.

**Что коллапсируется:** 21 throttle-каскад, 15+ `*_INTERVAL` констант, `_log_throttle_drop`, `SUGGESTIONS_MAX_PER_DAY`, per-detector quiet logic — всё либо встроено в dispatcher (budget/dedup/expires), либо умирает.

**Связано:** [alerts-and-cycles.md](alerts-and-cycles.md) — полная карта детекторов и throttling math.

---

## Правило 2 — Любая производная метрика это EMA

**Контракт:** `EMA(initial, decay=...)` или `EMA(initial, time_const=...)`. Один primitive, один файл констант (`Decays`/`TimeConsts`).

**Каркас:** [src/ema.py](../src/ema.py) — `EMA`/`VectorEMA` + `Decays`/`TimeConsts`. После Phase D `Resonator` ([src/rgk.py](../src/rgk.py)) хранит 5 chem-параметров как EMA-атрибуты, feeders explicit (`feed_acetylcholine(novelty, boost=False)`), без event-routing layer.

**Что коллапсируется:** 20+ `update_from_*` методов → explicit `feed_*` методы на Resonator/UserState. Scattered decay constants → один `Decays` namespace. 3D legacy vector (DA, 5HT, NE) → `Resonator.vector()`. Capacity (3 параллельных контура) — derived поверх Resonator.

**Связано:** [neurochem-design.md](neurochem-design.md) — реализация пяти модуляторов.

---

## Правило 3 — Любое знание это нода графа

**Контракт:** `record(type, content, refs=[])` — нода с типом и ссылками. Поиск через `nodes_where(type=...)`/`nodes_near(embedding=...)`. Связи через `distinct()` (см. Правило 4).

**Каркас:** [src/graph_logic.py](../src/graph_logic.py) — операции над графом. [src/graph_store.py](../src/graph_store.py) — persist. Производные: `state_graph` (история тиков), `solved_archive` (RAG over solved goals), `consolidation` (REM/прорастание).

**Что коллапсируется (условно — правило спорное):** часть `goals_store.py` / `recurring.py` / `activity_log.py` / `patterns.jsonl` могла бы стать filter'ами над графом. Trade-off: goals имеют transactional семантику (atomic `record_instance`) — append-only граф при crash даёт half-states. Компромисс — `goal_instance` как нода с ref в goals_store, не замена store'а. Параллельные хранилища допустимы там, где transactional семантика реально нужна.

**Связано:** [ontology.md](ontology.md) — схемы node-типов.

---

## Правило 4 — distinct() единственный примитив рассуждения

**Контракт:** `d = distinct(A, B) ∈ [0, 1]` — мера различия двух идей. Все операции (SmartDC, Pump, Novelty, Embedding-first) — надстройки над `distinct`. Зоны (`τ_in=0.3`, `τ_out=0.7`) разбивают d на согласие / исследование / конфликт.

**Каркас:** [src/main.py:distinct_decision](../src/main.py) + [src/tick_nand.py](../src/tick_nand.py). Адаптивная чувствительность `γ = 2.0 + 3.0·NE·(1−S)` — байесовское обновление в логарифмическом пространстве: `new_logit = old_logit − γ·d + log(prior)` → softmax с температурой T (адаптивна по KL между тиками).

**Дисциплина:** не писать cosine_similarity мимо `distinct()`. Один путь к мере различия — один файл констант — один трейс отладки.

**Связано:** [nand-architecture.md](nand-architecture.md) — почему один примитив + три зоны вместо булевой логики.

---

## Правило 5 — PE единственный драйвер автономного поведения

**Контракт:** 5 PE-каналов (`user_pe`, `agency_gap`, `hrv_pe`, `self_pe`, plus optional channels) → max → `imbalance_pressure` (EMA) → `_idle_multiplier` (замедляет циклы при перегрузе) и intensity для alerts. Всё что система делает «сама» — отсюда.

**Каркас:** агрегация в [cognitive_loop._advance_tick](../src/cognitive_loop.py) — `combined_imbalance = max(user_pe, self_pe, agency_gap, hrv_pe)`. ProtectiveFreeze получает feed_tick + sync_error EMA. `imbalance_pressure` peak'ит когда модель stale → idle slowdown сохраняет budget.

**Что коллапсируется:** мета-controller decisions (когда замедлить, когда гасить, когда тревожить) — все из одной величины. Новые PE-каналы добавляются в max, поведение наследуется.

**Связано:** [friston-loop.md](friston-loop.md) — canonical PE layer; «отсутствующий объект» (PE=0 при молчании юзера) → замкнутый контур → руминация.

---

## Правило 6 — Состояние = один резонатор, всё остальное проекции

**Контракт:** один `Resonator` (5 chem axes — gain/hyst/aperture/plasticity/damping = DA/5HT/NE/ACh/GABA + R/C bit + balance()). `РГК = два связанных резонатора` (user mirror + system mirror) + auxiliary state (valence/agency/burnout, predictive baselines, pressure accumulators). Всё остальное — проекции через `project(domain)`.

**Каркас:** [src/rgk.py](../src/rgk.py) — `Resonator` + `РГК` + `project()`. Singleton `get_global_rgk()` (B0): production bootstrap shares один объект между UserState/Neurochem/ProtectiveFreeze (каскад зеркал — один резонатор, не три). UserState/Neurochem facades остаются как thin proxies через `@property`.

**Балансовая формула:** `balance = (DA · NE · ACh) / (5HT · GABA)`. Корридор `[0.3, 1.5]` маркирует здоровый резонанс. Diagnostic скаляр для долгосрочного здоровья системы.

**Дисциплина:** не плодить parallel state-контейнеры. Если новое поле — это chem axis, расширяем Resonator. Если derived от chem — projector в `RGK.project()`. Если bespoke (sensor passthrough, aggregate) — поле РГК, но прямое чтение/запись через property proxy.

**Связано:** [neurochem-design.md](neurochem-design.md) — 5 модуляторов и balance(). [resonance-model.md § 5 axioms](resonance-model.md) — теоретическая основа.

---

## Правило 7 — Не давить, а инвертировать

**Контракт:** R/C bit на резонаторе через гистерезис (THETA_ACT=0.15 / THETA_REC=0.08). Mode `R` (passive resonance) — пассивный приёмник, следует за полем. Mode `C` (counter-wave) — генератор инверсной волны, разрывает деструктивный аттрактор. При `user.mode == 'C'` push-style сигналы (sync_seeking, recurring_lag, observation_suggestion, morning_briefing) понижают urgency на 0.3 — не усиливают давление.

**Каркас:** `Resonator.update_mode(perturbation)` — гистерезисный flip. `cognitive_loop._advance_tick` вызывает каждый tick с `sync_err` (user mirror) и `combined_imbalance` (system mirror). `signals.py` определяет `COUNTER_WAVE_PUSH_TYPES`; `Dispatcher.dispatch()` принимает `user_mode='R'/'C'` параметр.

**Три способа инверсии (без давления):**
- **Инверсия частоты** — генерировать сигнал противоположный фундаментальной гармонике паттерна (парадоксальная интенция Франкла).
- **Рассинхронизация задержкой** — пауза в цикле обратной связи. `suppress observation_suggestion` при `last_input < 10min`.
- **Смена несущей** — сменить канал/тон/модальность так, чтобы старый аттрактор физически не поддерживался. Tone choice в `_generate_sync_seeking_message` (caring/curious/reference/simple).

**Критерий для новых детекторов:** если сигнал усиливает паттерн юзера (через осуждение, моральное давление, повтор «ты должен») — давит, отрицательная обратная связь. Если разрывает резонанс через инверсию/паузу/смену несущей — корректирует, система переходит в новый аттрактор.

**Связано:** [resonance-model.md § Две роли одного резонатора](resonance-model.md). Аналоги в психотерапии: парадоксальная интенция Франкла, парадоксальное предписание Вацлавика, «Po» де Боно.

---

## Фильтр для новых фич

Любая новая фича проходит через filter:

1. **Это новый детектор в dispatcher?** — OK, написать как Signal-producer (Правило 1).
2. **Это новый derived metric?** — OK, EMA-атрибут на Resonator/UserState + 1 feeder (Правило 2).
3. **Это новый тип ноды графа?** — OK, через `record(type=...)` (Правило 3).
4. **Это новый chem axis?** — OK, добавить в `Resonator.__init__` + `balance()` coverage + 1 feeder (Правило 6).
5. **Это что-то что не влезает?** — **СТОП**. Или поверх 1-4 (тогда reformulate), или 8-е правило (редкий случай — серьёзно думать прежде чем добавлять).

Если через 3+ месяца появляется реальная фича которая **честно не укладывается** — расширяем осознанно: документируем почему не влезло, обсуждаем что добавить (новое правило? изменить существующее?), не делаем bespoke escape hatch «на этот раз». Полу-абстракция хуже consistent bespoke — даёт иллюзию структуры без её гарантий.

---

## Главное

Новая фича = декларация, не подсистема. Каркас один. Если правила соблюдаются — barrier на add/try/remove низкий, цикл итераций быстрый. Если нет — через год снова TODO на 80 пунктов.

---

**Навигация:** [Индекс](README.md) · [nand-architecture](nand-architecture.md) · [resonance-model](resonance-model.md)
