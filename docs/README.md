# Baddle docs — путеводитель

28 документов. Читаются как книга: каждая глава опирается на предыдущую,
каждый документ раскрывает термин нужный следующему. Полный проход —
≈ 4-5 часов, можно по одной главе в день. 22 основных doc'а в reading
order + 6 вспомогательных (reference / context).

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
| «Как помнит?» | [episodic-memory](episodic-memory.md) → [static-storage](static-storage-design.md) |
| «Как находит инсайты?» | [thinking-operations](thinking-operations.md) — SmartDC / Pump / Novelty / Embedding-first |
| «Как работает 24/7?» | [dmn-scout](dmn-scout-design.md) |
| «Что в фоне дёргается?» | [alerts-and-cycles](alerts-and-cycles.md) — 21 check + alert types |
| «Prediction error?» | [friston-loop](friston-loop.md) — 2 предиктора, 5 PE-каналов, прайм-директива |
| «Как измерить что работает?» | [friston-loop § Прайм-директива](friston-loop.md#связь-с-прайм-директивой) → `/assist/prime-directive` |
| «Место Baddle относительно меня?» | [world-model](world-model.md) — каскад зеркал |
| «Что не решено?» | [planning/TODO § Открытые вопросы](../planning/TODO.md) |

---

## Главы

### 🌍 Глава 1 — Foundation (25 мин)
*Зачем Baddle существует.*

1. [origin-story.md](origin-story.md) *(10 мин)* — пять проектов сошлись через `prediction error`.
2. [life-assistant-design.md](life-assistant-design.md) *(15 мин)* — четыре слоя стека: MindBalance + Тамагочи + Time Player + HRV.

### 🧠 Глава 2 — Core concepts (65 мин)
*Архитектура мышления на фундаментальном уровне.*

3. [full-cycle.md](full-cycle.md) *(20 мин)* — полный цикл: статика + динамика, data flow одного запроса, lifecycle goal'а, три контура замкнутости.
4. [nand-architecture.md](nand-architecture.md) *(30 мин)* — весь Baddle на одном примитиве `distinct()`. Эмерджентная логика, Git-версионирование графа.
5. [cone-design.md](cone-design.md) *(15 мин)* — байесовский конус: 5 операций вокруг prediction error + универсальный ритм divergence/convergence.

### 💭 Глава 3 — Cognitive layer (110 мин)
*Состояние системы во времени: что ей хорошо, что плохо.*

6. [tick-design.md](tick-design.md) *(15 мин)* — атомарный шаг мышления, фазы tick_nand, emergent через NAND.
7. [horizon-design.md](horizon-design.md) *(20 мин)* — `CognitiveState`: precision, policy, γ, T, семь когнитивных состояний.
8. [neurochem-design.md](neurochem-design.md) *(15 мин)* — dopamine / serotonin / norepinephrine, формулы EMA, RPE, ProtectiveFreeze.
9. [symbiosis-design.md](symbiosis-design.md) *(15 мин)* — двойной state-вектор USER ↕ SYSTEM, `sync_error` как прайм-директива, 4 режима (flow/rest/protect/confess).
10. [user-model-design.md](user-model-design.md) *(20 мин)* — `UserState`: surprise, dual-pool energy, 10 named_states (Voronoi), day simulator.
11. [hrv-design.md](hrv-design.md) *(20 мин)* — HRV как физический вход: coherence → θ/φ конуса, 4 activity zones, источники данных.
12. [friston-loop.md](friston-loop.md) *(20 мин)* — **canonical PE layer**: 2 предиктора (user/self), 5 PE-каналов, `imbalance_pressure` aggregate, прайм-директива через `prime_directive.jsonl`.

### 📚 Глава 4 — Knowledge structures (80 мин)
*Как Baddle помнит.*

13. [episodic-memory.md](episodic-memory.md) *(30 мин)* — жизнь системы: state-graph → meta-tick → consolidation. Один pipeline doc.
14. [static-storage-design.md](static-storage-design.md) *(25 мин)* — profile / goals / solved archive. Замкнутый цикл с uncertainty-learning.
15. [activity-log-design.md](activity-log-design.md) *(15 мин)* — `activity.jsonl`, 3 контура (event log / content graph / UserState), category → energy.
16. [ontology.md](ontology.md) *(10 мин)* — **reference**: схемы всех 13 data-файлов. Держи под рукой когда пишешь код.

### 🔧 Глава 5 — Implementation (90 мин)
*Как всё собрано в единую систему.*

17. [thinking-operations.md](thinking-operations.md) *(40 мин)* — 4 атомные операции на графе: SmartDC (диалектика), Pump (скрытые мосты), Novelty (фильтр повторов), Embedding-first (мышление без слов).
18. [dmn-scout-design.md](dmn-scout-design.md) *(25 мин)* — фоновое сознание 24/7: 4 DMN-check + night cycle (Scout + REM + Consolidation) + heartbeat substrate.
19. [cross-graph-design.md](cross-graph-design.md) *(10 мин)* — continuity между сессиями: seed-ноды при switch, перенос embedding'ов.
20. [workspace-design.md](workspace-design.md) *(15 мин)* — multi-graph: WorkspaceManager, cross-graph edges (серендипити), meta-graph.
21. [storage-layout.md](storage-layout.md) *(10 мин)* — где что лежит на диске: `data/` / `graphs/<ws>/` / `workspaces/`, data flow, reset.
22. [closure-architecture.md](closure-architecture.md) *(20 мин)* — как замкнуты инструменты: intent router, recurring / constraints, plan↔goal link, observation → suggestion, RAG.

---

## Карта зависимостей

```
  [origin-story] → [life-assistant]
                        ↓
                   [full-cycle]
                                    ↓
                           [nand-architecture]
                                    ↓
                               [cone-design]
                                    ↓
                              [tick-design]
                                    ↓
                  [horizon] ←→ [neurochem]
                       ↓            ↓
              [symbiosis] ←→ [user-model] → [hrv]
                       ↓            ↓            ↓
                          [friston-loop]   ← canonical PE layer
                                    ↓
                         [episodic-memory]
                                    ↓
                     [static-storage] → [activity-log] → [ontology]
                                    ↓
                    [thinking-operations] → [dmn-scout]
                                    ↓                ↓
                          [cross-graph] → [workspace]
                                    ↓
                     [storage-layout] → [closure-architecture]
```

**Обратные стрелки** (user-model → symbiosis) означают: понимание
углубляется если прочитать оба.

---

## Вспомогательные (вне глав)

Эти documents не в reading order, но нужны по ходу работы:

| Doc | Зачем |
|---|---|
| [world-model.md](world-model.md) | Оптика проекта: каскад зеркал + resonance protocol + 5 механик |
| [alerts-and-cycles.md](alerts-and-cycles.md) | Полная карта 21 check + alert types + throttling math |
| [action-memory-design.md](action-memory-design.md) | Действия / outcomes как ноды графа |
| [TECH_README.md](TECH_README.md) | Технический обзор (параллельно с этим index'ом) |
| [mockup.html](mockup.html) | Интерактивный мокап UI |

**Планирование** — [../planning/](../planning/):
[TODO.md](../planning/TODO.md) (все задачи + открытые архитектурные вопросы + кристаллизация),
[ui-split-plan.md](../planning/ui-split-plan.md) (UI split Baddle vs Graph Lab).

---

## Соглашения

- Все docs на русском. `inline code` = имя файла / функции / переменной из `src/`.
- Science-mapping (Friston, DMN, Hebb, Bayes) — **LLM context** для быстрого re-boot сессии. Не ornamental, не удалять.
- Единственный source-of-truth для ошибки предсказания (PE) — [friston-loop.md](friston-loop.md). Остальные doc'и на эту тему оставляют stub pointer на него.

---

## Как растить docs

Когда добавляешь фичу:

1. Напиши design-doc в подходящей главе (не создавай лишний файл если тема естественно ложится в существующий).
2. Добавь ссылку в этот index с временем чтения.
3. Обнови [ontology.md](ontology.md) если меняется формат данных.
4. Обнови [../planning/TODO.md](../planning/TODO.md) если не всё закрыто.

Docs-дерево растёт как книга, а не как свалка.
