# Baddle docs — путеводитель

Документы читаются как книга: каждая глава опирается на предыдущую,
каждый документ раскрывает термин, нужный следующему. Полный проход —
≈ 4-5 часов, можно по одной главе в день. 21 основный doc в reading
order + вспомогательные (reference / context) + deprecated.

---

## В 5 минут — mental model

Если у вас нет 4 часов, прочтите этот раздел и [glossary](#словарь) внизу.

**Что Baddle делает.** Локальный второй мозг для одного человека. Замечает закономерности, ищет связи, замедляется когда юзер устал. Без облака, без gamification, без «вернись».

**Mental model — три концепции:**

1. **Каскад зеркал.** Реальность → человек → Baddle. Человек настроен на мир, Baddle — на конкретного человека. Метрика качества зеркала — `sync_error = ‖предсказание − реальное действие‖`. Это **прайм-директива**, измеряется и логируется ([world-model.md](world-model.md)).

2. **РГК — резонатор как substrate.** Не «контейнер state», а **физическая модель**: 5 chem-параметров (DA/5-HT/NE/ACh/GABA) + R/C bit + balance() формула. Всё остальное (capacity-зоны, sync_regime, named_state) — **проекции** одного резонатора через `project(domain)`. Каскад зеркал = **пара** связанных резонаторов (user mirror + system mirror) ([rgk-spec.md](rgk-spec.md), [neurochem-design.md](neurochem-design.md)).

3. **7 правил как проекции одной модели.** Любая фича — выражение одного из них. Сигнал → Signal/Dispatcher. Метрика → EMA. Знание → нода графа. Рассуждение → distinct(). Автономное поведение → PE. State → резонатор. Не давить → R/C инверсия ([architecture-rules.md](architecture-rules.md)).

**Bottom-up инкрементальная разработка.** Проект не спроектирован сверху — concepts накопились (нейрохимия + Bayes + конус + HRV + Friston), потом коллапсировали в РГК как unifying frame. То же продолжается: `workspace` (STM/LTM scope), `Power` (единая метрика нагрузки) — добавляются через scope над графом и derived метрики, без новых подсистем.

**Главное соглашение.** Если новая фича не укладывается в одно из 7 правил — это сигнал что либо фича другой природы (стоп, переосмыслить), либо правил не хватает. Полу-абстракция хуже consistent bespoke.

---

## Как читать

**🆕 Первый раз.** Главы 1 + 2 (2 часа). После — можешь объяснить проект
другу и понять зачем он существует.

**🔄 Вернулся после паузы.** Сразу [full-cycle.md](full-cycle.md) (20 мин) —
свёрнутый обзор всей системы. Дальше — в нужный раздел.

**🔬 Разбираюсь в коде.** Foundation — пара абзацев. Основное время
в Cognitive Layer → Knowledge Structures → Implementation (3-4 часа).
Держи `src/` рядом. Карта файлов — в [TECH_README § Карта src/](TECH_README.md#карта-src).

**🧠 Хочу понять theory.** [foundation](foundation.md) → [world-model](world-model.md) →
[resonance-model](resonance-model.md) → [rgk-spec](rgk-spec.md) →
[friston-loop](friston-loop.md) (~2 часа). Параллельно — [rgk-spec § Testable claims](rgk-spec.md#testable-claims) для понимания
**что измеряется** и **что бы опровергло модель**. Это не просто метафоры — конкретные
hypotheses с validation paths через `data/prime_directive.jsonl` и accumulated patterns.

### Quick paths

| Вопрос | Путь |
|---|---|
| «Как думает Baddle?» | [tick](tick-design.md) → [nand](nand-architecture.md) → [horizon](horizon-design.md) |
| «Как адаптируется?» | [horizon](horizon-design.md) → [neurochem](neurochem-design.md) → [symbiosis](symbiosis-design.md) |
| «Что такое симбиоз?» | [symbiosis](symbiosis-design.md) → [user-model](user-model-design.md) → [hrv](hrv-design.md) |
| «Как помнит?» | [episodic-memory](episodic-memory.md) → [storage](storage.md) |
| «Как находит инсайты?» | [thinking-operations](thinking-operations.md) — SmartDC / Pump / Novelty / Embedding-first |
| «Как работает 24/7?» | [dmn-scout](dmn-scout-design.md) |
| «Что в фоне дёргается?» | [alerts-and-cycles](alerts-and-cycles.md) — 21 check + alert types |
| «Prediction error?» | [friston-loop](friston-loop.md) — 2 предиктора, 5 PE-каналов, прайм-директива |
| «Как измерить что работает?» | [friston-loop § Прайм-директива](friston-loop.md#связь-с-прайм-директивой) → `/assist/prime-directive` |
| «Место Baddle относительно меня?» | [world-model](world-model.md) — каскад зеркал |
| «Резонансная оптика / единый словарь?» | [resonance-model](resonance-model.md) — mapping «концепт ↔ Baddle ↔ код» |
| «Что такое РГК?» | [rgk-spec](rgk-spec.md) — спецификация физической модели + mapping к коду |
| «Divergence / convergence как универсальный паттерн?» | [universe-as-git](universe-as-git.md) Глава 8 — память, обучение, творчество |
| «Какие принципы дизайна?» | [architecture-rules](architecture-rules.md) — 7 правил + фильтр для новых фич |
| «STM/LTM, рабочая память?» | [workspace](workspace.md) — scope над графом + дневной/ночной циклы |
| «Сложность, нагрузка, capacity budget?» | [power](power.md) — единая метрика через `Power = U×V×P×interest×chem_modulator` |
| «Как другой юзер быстро попадёт в узор?» | [synchronization](synchronization.md) — resonance transfer через аналогии (coarse → fine) |
| «Что не решено?» | [planning/TODO § Открытые вопросы](../planning/TODO.md) |

---

## Главы

### 🌍 Глава 1 — Foundation (25 мин)
*Зачем Baddle существует.*

1. [foundation.md](foundation.md) *(25 мин)* — пять проектов сошлись через `prediction error`. Origin story + четыре слоя стека (MindBalance + Тамагочи + Time Player + HRV).

### 🧠 Глава 2 — Core concepts (100 мин)
*Архитектура мышления на фундаментальном уровне.*

2. [full-cycle.md](full-cycle.md) *(20 мин)* — полный цикл: статика + динамика, data flow одного запроса, lifecycle goal'а, три контура замкнутости.
3. [architecture-rules.md](architecture-rules.md) *(15 мин)* — 7 правил архитектуры (Signal / EMA / Graph / distinct / PE / Resonator / Counter-wave) + фильтр для новых фич. Meta-обзор поверх остальных глав.
4. [nand-architecture.md](nand-architecture.md) *(30 мин)* — весь Baddle на одном примитиве `distinct()`. Эмерджентная логика, Git-версионирование графа.
5. [cone-design.md](cone-design.md) *(15 мин)* — байесовский конус: 5 операций вокруг prediction error + универсальный ритм divergence/convergence, резонансные параметры конуса.
6. [friston-loop.md](friston-loop.md) *(20 мин)* — **canonical PE layer**: 2 предиктора (user/self), 5 PE-каналов, `imbalance_pressure` aggregate, прайм-директива через `prime_directive.jsonl`. Читается рано — все последующие главы опираются на эту оптику.

### 💭 Глава 3 — Cognitive layer (115 мин)
*Состояние системы во времени: что ей хорошо, что плохо.*

7. [tick-design.md](tick-design.md) *(15 мин)* — атомарный шаг мышления, фазы tick_nand, emergent через NAND.
8. [horizon-design.md](horizon-design.md) *(20 мин)* — `CognitiveState`: precision, policy, γ, T, семь когнитивных состояний.
9. [neurochem-design.md](neurochem-design.md) *(15 мин)* — dopamine / serotonin / norepinephrine, формулы EMA, RPE, ProtectiveFreeze.
10. [symbiosis-design.md](symbiosis-design.md) *(15 мин)* — двойной state-вектор USER ↕ SYSTEM, `sync_error` как прайм-директива, 4 режима (flow/rest/protect/confess), таблица трёх схем классификации состояния.
11. [user-model-design.md](user-model-design.md) *(20 мин)* — `UserState`: surprise, 10 named_states (Voronoi), day simulator.
12. [hrv-design.md](hrv-design.md) *(20 мин)* — HRV как физический вход: coherence → θ/φ конуса, 4 activity zones, ВНС как переключатель несущей.
13. [capacity-design.md](capacity-design.md) *(20 мин)* — три контура нагрузки: физио / эмо / когн. Capacity через зоны, дневная метрика через observable, decision gate через компоненты.
14. [episodic-memory.md](episodic-memory.md) *(30 мин)* — жизнь системы: state-graph → meta-tick → consolidation. Хеббовское крепление, ночная консолидация, детерминистический replay. Относится к когнитивному слою, хоть и работает с данными.

### 📚 Глава 4 — Knowledge structures (75 мин)
*Как Baddle помнит.*

15. [storage.md](storage.md) *(30 мин)* — physical layout (`data/` + `graphs/`) + content (profile / goals / solved archive) + sensor stream + reset. Замкнутый цикл с uncertainty-learning.
16. [activity-log-design.md](activity-log-design.md) *(15 мин)* — `activity.jsonl`, 3 контура (event log / content graph / UserState), category → energy.
17. [task-tracker-design.md](task-tracker-design.md) *(20 мин)* — задачный слой: backlog с оценкой сложности, auto-scheduling в план дня через capacity-зону, возврат незавершённого.
18. [ontology.md](ontology.md) *(10 мин)* — **reference**: схемы всех data-файлов. Держи под рукой когда пишешь код.

### 🔧 Глава 5 — Implementation (85 мин)
*Как всё собрано в единую систему.*

19. [thinking-operations.md](thinking-operations.md) *(40 мин)* — 4 атомные операции на графе: SmartDC (диалектика), Pump (скрытые мосты), Novelty (фильтр повторов), Embedding-first (мышление без слов).
20. [dmn-scout-design.md](dmn-scout-design.md) *(25 мин)* — фоновое сознание 24/7: 4 DMN-check + night cycle (Scout + REM + Consolidation) + heartbeat substrate.
21. [closure-architecture.md](closure-architecture.md) *(20 мин)* — как замкнуты инструменты: intent router, recurring / constraints, plan↔goal link, observation → suggestion, RAG.

---

## Карта зависимостей

```
      [foundation]
            ↓
       [full-cycle]
                        ↓
                [nand-architecture]
                        ↓
                  [cone-design]
                        ↓
                 [friston-loop]   ← canonical PE layer
                        ↓
                   [tick-design]
                        ↓
           [horizon] ←→ [neurochem]
                ↓            ↓
       [symbiosis] ←→ [user-model] → [hrv]
                ↓            ↓            ↓
                        [capacity]
                             ↓
                     [episodic-memory]
                             ↓
                  [storage] → [activity-log] → [task-tracker] → [ontology]
                             ↓
             [thinking-operations] → [dmn-scout] → [closure-architecture]
```

**Обратные стрелки** (user-model → symbiosis) означают: понимание
углубляется если прочитать оба. `friston-loop` поднят рано — без него
последующие главы ссылаются на prediction error, каналы PE и
imbalance_pressure без контекста.

---

## Вспомогательные (вне глав)

Эти documents не в reading order, но нужны по ходу работы:

| Doc | Зачем |
|---|---|
| [world-model.md](world-model.md) | Оптика проекта: каскад зеркал + resonance protocol + 5 механик. Содержит **canonical mapping** «внешние словари ↔ Baddle ↔ код» |
| [resonance-model.md](resonance-model.md) | Резонансная рамка: 5 аксиом + волна/частота/чистота как единый словарь для существующих механик. Интерпретирующий слой над world-model |
| [rgk-spec.md](rgk-spec.md) | **Спецификация физической модели РГК.** Аксиомы + математическое выражение + маппинг к коду. Стратегический документ — корни большинства последующих правил |
| [universe-as-git.md](universe-as-git.md) | Универсальный паттерн divergence ↔ convergence (Глава 8 — память, творчество). Корни workspace/Power концепций |
| [workspace.md](workspace.md) | Рабочая память (STM) между divergent generation и graph (LTM). Scope над графом, дневной/ночной циклы, NREM/REM/homeostasis параллели |
| [power.md](power.md) | Единая метрика сложности/нагрузки. `Power = U × V × P × interest × chem_modulator` через 3 контура capacity. Унифицирует estimated_complexity / cognitive_load / urgency / dispatcher.budget |
| [synchronization.md](synchronization.md) | Resonance transfer — как новый юзер быстро попадает в узор через analogies (coarse → fine). Direction для ответа на Origin question (foundation § Origin) |
| [positioning.md](positioning.md) | AHI как когнитивный шлюз (manifesto). Почему прослойка между человеком и AGI обязательна — даже при зрелом AGI. 4 структурные причины (исполнение vs вероятность, bandwidth ~50 бит/с, регуляторика, эпистемологический зазор). Корни Habr-статьи |
| [alerts-and-cycles.md](alerts-and-cycles.md) | Полная карта фоновых check'ов + alert types + throttling math |
| [action-memory-design.md](action-memory-design.md) | Действия / outcomes как ноды графа |
| [TECH_README.md](TECH_README.md) | Технический обзор (параллельно с этим index'ом) |
| [mockup.html](mockup.html) | Интерактивный мокап UI |

**Планирование** — [../planning/](../planning/):
[TODO.md](../planning/TODO.md) (все задачи + открытые архитектурные вопросы),
[breathing-mode.md](../planning/breathing-mode.md) (guided дыхательная сессия),
[resonance-prompt-preset.md](../planning/resonance-prompt-preset.md) (chat UI 🔵/🔴 preset).

---

## Соглашения

- Все docs на русском. `inline code` = имя файла / функции / переменной из `src/`.
- Science-mapping (Friston, DMN, Hebb, Bayes) — **LLM context** для быстрого re-boot сессии. Не ornamental, не удалять.
- Единственный source-of-truth для ошибки предсказания (PE) — [friston-loop.md](friston-loop.md). Остальные doc'и на эту тему оставляют stub pointer на него.

---

## Как растить docs

**Принцип:** `docs/` описывают **реальность** — как код работает сейчас, narrative-стиль, без temporal language («после Phase D», «ранее», «расширено»). `planning/` описывает **намерения** — что осталось делать, design specs для не-реализованных фич. Если код изменился — обновляется секция в docs, а не добавляется «после X». История — `git log` + `memory/project_session_*.md`.

Когда добавляешь фичу:

1. Напиши design-doc в подходящей главе (не создавай лишний файл если тема естественно ложится в существующий).
2. Добавь ссылку в этот index с временем чтения.
3. Обнови [ontology.md](ontology.md) если меняется формат данных.
4. Обнови [../planning/TODO.md](../planning/TODO.md) если не всё закрыто.

Docs-дерево растёт как книга, а не как свалка.

---

## Словарь

Ключевые термины проекта в одну строку. При первой встрече в коде или docs — смотри сюда.

**РГК** — Резонансно-Генерирующий Контур. Substrate state+dynamics. Пара связанных резонаторов (user mirror + system mirror) + 5-axis chem (DA/5HT/NE/ACh/GABA) + R/C bit + balance(). [src/rgk.py](../src/rgk.py).

**Каскад зеркал** — реальность → человек → Baddle. Метафора оптики проекта: Baddle настроен на одного юзера через двойной резонатор. См. [world-model.md](world-model.md).

**balance()** — `(DA × NE × ACh) / (5HT × GABA)`. Корридор `[0.3, 1.5]` = здоровый резонанс. >1.5 гиперрезонанс/мания, <0.5 гипостабильность/апатия.

**R/C mode** — bit на резонаторе. R (resonance) — пассивный приёмник. C (counter-wave) — активный генератор инверсной волны для разрыва деструктивного аттрактора. Hysteresis 0.15/0.08. См. [resonance-model.md § Две роли](resonance-model.md).

**Прайм-директива** — `sync_error = ‖user_vec − system_vec‖`. Единственная метрика «работает ли». EMA fast/slow в [data/prime_directive.jsonl](../data/), endpoint `/assist/prime-directive`. См. [friston-loop § Прайм-директива](friston-loop.md).

**Counter-wave** — Правило 7. Инверсия паттерна вместо давления. Three modes of inversion: смена частоты / задержка / смена несущей.

**distinct(a, b)** — единственный примитив рассуждения ∈ [0, 1]. Все операции над знанием (SmartDC, Pump, Novelty) надстройки. Зоны `τ_in=0.3`, `τ_out=0.7`. Правило 4.

**SmartDC** — диалектика гипотезы: thesis vs antithesis vs neutral → synthesis с числовой confidence. Использует distinct() во всех путях. [thinking-operations.md](thinking-operations.md).

**Pump** — поиск скрытых мостов. `scout(A, B)` находит общую ось между двумя далёкими нодами графа. Background-цикл DMN.

**DMN** — Default Mode Network. Continuous фоновый pump между idle тиками. Эмерджентные insight'ы на 4 уровнях глубины.

**γ (gamma)** — `2.0 + 3.0 · NE · (1 − 5HT)`. Чувствительность Bayes-обновления. Спокойный режим → ~2.0, ищущий → ~5.0.

**Capacity** — резерв энергии в 3 контурах: phys / affect / cogload. Зоны green / yellow / red. Decision gate в `_assist`. [capacity-design.md](capacity-design.md).

**Workspace** — рабочая память (STM). Scope над графом с `expires_at`. Дневной режим — cheap in-memory; ночной — sequential integration с LTM. См. [workspace.md](workspace.md).

**Power** — единая метрика сложности/нагрузки. `U × V × P × interest × chem_modulator`. Унифицирует estimated_complexity / cognitive_load / urgency / budget. Vector по 3 контурам capacity. См. [power.md](power.md).

**ProtectiveFreeze** *(deprecated)* — был отдельный класс, удалён в B5 W3. Pressure-layer (conflict_accumulator + silence_pressure + imbalance_pressure + sync_error EMA + freeze_active flag) живёт в `_rgk` напрямую.

**Neurochem** *(deprecated)* — был отдельный класс, удалён в B5 W4. System chem (gain/hyst/aperture/plasticity/damping) живёт в `_rgk.system` напрямую.

**UserState** — backward-compat shim над `_rgk.user`. Production использует `_rgk.user.X.value` или `_rgk.project("user_state")`.

**Workspace = STM, Граф = LTM.** Перенос через scope mutation в ночном `consolidation` cycle.
