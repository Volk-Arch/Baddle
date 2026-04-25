# Baddle docs — путеводитель

Документы читаются как книга: каждая глава опирается на предыдущую,
каждый документ раскрывает термин, нужный следующему. Полный проход —
≈ 4-5 часов, можно по одной главе в день. 21 основный doc в reading
order + вспомогательные (reference / context) + deprecated.

---

## Как читать

**🆕 Первый раз.** Главы 1 + 2 (2 часа). После — можешь объяснить проект
другу и понять зачем он существует.

**🔄 Вернулся после паузы.** Сразу [full-cycle.md](full-cycle.md) (20 мин) —
свёрнутый обзор всей системы. Дальше — в нужный раздел.

**🔬 Разбираюсь в коде.** Foundation — пара абзацев. Основное время
в Cognitive Layer → Knowledge Structures → Implementation (3-4 часа).
Держи `src/` рядом.

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
| «Какие принципы дизайна?» | [architecture-rules](architecture-rules.md) — 7 правил + фильтр для новых фич |
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
| [alerts-and-cycles.md](alerts-and-cycles.md) | Полная карта фоновых check'ов + alert types + throttling math |
| [action-memory-design.md](action-memory-design.md) | Действия / outcomes как ноды графа |
| [TECH_README.md](TECH_README.md) | Технический обзор (параллельно с этим index'ом) |
| [mockup.html](mockup.html) | Интерактивный мокап UI |

**Планирование** — [../planning/](../planning/):
[TODO.md](../planning/TODO.md) (все задачи + открытые архитектурные вопросы),
[cleanup-plan.md](../planning/cleanup-plan.md) (Track A + B оставшегося cleanup),
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
