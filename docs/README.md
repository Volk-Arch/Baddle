# Baddle docs — путеводитель

Это 27 документов. Если читать подряд в случайном порядке — половина
терминов будет непонятна. Этот index даёт **порядок чтения как книгу**:
каждая глава строится на предыдущей, каждый документ раскрывает термин
который нужен следующему.

**Общее время:** ≈ 6-7 часов для полного прохода от «что это?» до
«готов работать с кодом». Можно читать по одной главе в день —
5 дней × 1-2 часа.

---

## 🎯 Как пользоваться этим index'ом

Три входа в зависимости от роли:

### 🆕 Первый раз вижу Baddle
Читай главы **Foundation → Core Concepts** (2 часа). Этого хватит
чтобы объяснить проект другу и понять зачем он существует.

### 🔄 Вернулся после паузы
Начни с [full-cycle.md](full-cycle.md) (20 мин) — там свёрнутый
обзор всей системы. Потом прыгай в нужный раздел по теме которую
трогаешь.

### 🔬 Хочу разобраться в математике / алгоритмах
Foundation пропусти (пара абзацев). Основное время в
**Cognitive Layer → Knowledge Structures → Implementation**
(4-5 часов). Держи рядом `src/` — каждый doc ссылается на файлы.

### Quick paths по темам
| Вопрос | Путь |
|--------|------|
| «Как думает Baddle?» | [tick](tick-design.md) → [nand](nand-architecture.md) → [horizon](horizon-design.md) |
| «Как адаптируется?» | [horizon](horizon-design.md) → [neurochem](neurochem-design.md) → [symbiosis](symbiosis-design.md) |
| «Что такое симбиоз?» | [symbiosis](symbiosis-design.md) → [user-model](user-model-design.md) → [hrv](hrv-design.md) |
| «Как помнит?» | [state-graph](state-graph-design.md) → [meta-tick](meta-tick-design.md) → [static-storage](static-storage-design.md) |
| «Как находит инсайты?» | [pump](pump-design.md) → [smartdc](smartdc-design.md) → [embedding-first](embedding-first-design.md) |
| «Как думать без языка?» | [embedding-first](embedding-first-design.md) → [novelty](novelty-design.md) |
| «Как работать 24/7?» | [tick](tick-design.md) → [state-graph](state-graph-design.md) → DMN/Scout в [full-cycle](full-cycle.md) |

---

## 📖 Главы (логический порядок чтения)

### 🌍 Глава 1 — Foundation (40 мин)
**Зачем это?** Понять проблему и зачем Baddle существует.

1. **[PITCH.md](PITCH.md)** *(15 мин)* — что это, проблема 35k decisions/day, решение через augmented intelligence.
2. **[origin-story.md](origin-story.md)** *(10 мин)* — 5 разных проектов которые сошлись в одну систему через `prediction error`.
3. **[life-assistant-design.md](life-assistant-design.md)** *(15 мин)* — четыре слоя стека: MindBalance + Tамагочи + Time Player + HRV.
4. **[epilogue.md](epilogue.md)** *(5 мин)* — философия: «система — зеркало твоего мышления».

**После этой главы ты знаешь:** *почему* Baddle нужен и *что* из себя представляет снаружи.

---

### 🧠 Глава 2 — Core Concepts (65 мин)
**Зачем это?** Архитектура мышления: как эта штука в принципе работает.

5. **[full-cycle.md](full-cycle.md)** *(20 мин)* — полный цикл: статика (profile/goals) + динамика (tick/DMN/symbiosis), data flow для одного запроса, lifecycle goal'а, three контура замкнутости.
6. **[nand-architecture.md](nand-architecture.md)** *(30 мин)* — весь Baddle построен на одном примитиве `distinct()`. Эмерджентная логика, Git-версионирование графа.
7. **[cone-design.md](cone-design.md)** *(15 мин)* — байесовский конус как метафора: 5 операций вокруг оси prediction error.
8. **[convergence-divergence.md](convergence-divergence.md)** *(5-10 мин)* — теоретический фундамент: паттерн сходится/расходится во вселенной.

**После этой главы ты знаешь:** *как* Baddle думает на фундаментальном уровне.

---

### 💭 Глава 3 — Cognitive Layer (105 мин)
**Зачем это?** Состояние системы во времени: что ей хорошо, что плохо.

9. **[tick-design.md](tick-design.md)** *(15 мин)* — атомарный шаг мышления, фазы tick_nand, emergent логика через NAND.
10. **[horizon-design.md](horizon-design.md)** *(20 мин)* — `CognitiveState`: precision, policy, γ, T, семь когнитивных состояний с пресетами.
11. **[neurochem-design.md](neurochem-design.md)** *(15 мин)* — dopamine/serotonin/norepinephrine/burnout, формулы EMA, RPE, ProtectiveFreeze.
12. **[symbiosis-design.md](symbiosis-design.md)** *(15 мин)* — двойной state-вектор USER ↕ SYSTEM, `sync_error` как прайм-директива, 4 режима (flow/rest/protect/confess).
13. **[user-model-design.md](user-model-design.md)** *(20 мин)* — расширенный `UserState`: signed prediction error, dual-pool energy (daily + long_reserve), 10 named_states (Voronoi), day simulator.
14. **[hrv-design.md](hrv-design.md)** *(20 мин)* — HRV как физический вход: coherence → θ/φ конуса, 4 activity zones (HRV × движение), полярные источники данных.

**После этой главы ты знаешь:** *какое состояние* у Baddle в каждый момент и почему.

---

### 📚 Глава 4 — Knowledge Structures (95 мин)
**Зачем это?** Как Baddle помнит: графы, логи, файлы.

15. **[state-graph-design.md](state-graph-design.md)** *(20 мин)* — второй граф (append-only JSONL), hash-chain аудит жизни системы, эпизодическая память через similarity-search.
16. **[consolidation-design.md](consolidation-design.md)** *(10 мин)* — забывание как фича: prune слабых нод + archive старых state_graph.
17. **[meta-tick-design.md](meta-tick-design.md)** *(10 мин)* — tick второго порядка: 5 паттернов в tail state_graph → policy_nudge.
18. **[static-storage-design.md](static-storage-design.md)** *(25 мин)* — user profile (5 категорий × preferences/constraints), goals store (event log), solved archive (snapshot графа на goal-resolve), uncertainty-driven learning.
19. **[activity-log-design.md](activity-log-design.md)** *(15 мин)* — ground-truth слой: `activity.jsonl`, 3 контура (event log / content graph / UserState), category → energy.
20. **[ontology.md](ontology.md)** *(10 мин)* — **reference**: схемы всех 13 data-файлов (user_state, profile, goals, activity, plans, checkins, patterns, state_graph, state_embeddings, workspaces/, solved/, settings). Держи под рукой когда пишешь код.

**После этой главы ты знаешь:** *как* Baddle помнит и *где* лежат данные.

---

### 🔧 Глава 5 — Implementation (85 мин)
**Зачем это?** Компоненты для поиска инсайтов и continuity.

21. **[smartdc-design.md](smartdc-design.md)** *(15 мин)* — диалектическая верификация: 3 полюса (thesis / antithesis / synthesis) + embedding-анализ confidence.
22. **[pump-design.md](pump-design.md)** *(20 мин)* — поиск мостов между далёкими нодами: облака → оси → smartdc. Это сердце Scout/DMN.
23. **[embedding-first-design.md](embedding-first-design.md)** *(15 мин)* — мышление без слов: perturbation в embedding space, lazy render текста.
24. **[novelty-design.md](novelty-design.md)** *(10 мин)* — фильтр повторов через similarity + rephrase-before-reject.
25. **[cross-graph-design.md](cross-graph-design.md)** *(10 мин)* — continuity между сессиями: seed-ноды при switch, перенос embedding'ов.
26. **[workspace-design.md](workspace-design.md)** *(15 мин)* — multi-graph архитектура: WorkspaceManager, cross-graph edges (серендипити), meta-graph.
27. **[storage-layout.md](storage-layout.md)** *(10 мин)* — где что лежит на диске: `data/` vs `graphs/<ws>/` vs `workspaces/`, data flow, reset.

**После этой главы ты знаешь:** *как* Baddle находит связи и *как* всё собрано в единую систему.

---

## 🗺 Карта зависимостей (кто-на-кого опирается)

```
  [PITCH] → [origin-story] → [life-assistant]
                                    ↓
                               [full-cycle]
                                    ↓
                           [nand-architecture]
                              ↙      ↓      ↘
                      [tick]   [cone]   [state-graph]
                        ↓                      ↓
                  [horizon] ←→ [neurochem]  [consolidation]
                        ↓            ↓          ↓
                  [symbiosis] ←→ [user-model] [meta-tick]
                        ↓            ↓
                                 [hrv]    [static-storage]
                                             ↓
                                      [activity-log]
                                             ↓
                                        [ontology]
                                             ↓
                              [smartdc] [pump] [embedding-first]
                                  ↓       ↓         ↓
                              [novelty] [cross-graph] [workspace]
```

Обратные стрелки (user-model → symbiosis) означают: понимание
улучшается, если прочитать оба.

---

## 🕳 Известные пробелы (будут закрыты)

Эти темы сейчас раскиданы по нескольким документам вместо отдельных
целостных описаний:

- **DMN / Scout** — упоминается в [full-cycle](full-cycle.md),
  [state-graph](state-graph-design.md), [meta-tick](meta-tick-design.md),
  но нет объединённого описания как «фоновое сознание 24/7 + heartbeat
  substrate». Планируется `dmn-scout-design.md`.
- **Energy model** — разбросан по [life-assistant](life-assistant-design.md),
  [user-model](user-model-design.md), [activity-log](activity-log-design.md).
  Dual-pool + cascading tax + category cost — нет единого места.
  Планируется `energy-design.md`.
- **Plans + checkins** — сейчас описаны частично в
  [activity-log](activity-log-design.md) и [static-storage](static-storage-design.md),
  но нет отдельного design-doc'а. Планируется `plans-checkins-design.md`.

---

## 📐 Соглашения

- Все docs на русском (кроме кода в примерах).
- `inline code` = имя файла / функции / переменной из `src/`.
- Каждый design-doc имеет секцию «**Проверка**» с API-вызовом который
  подтверждает что фича работает. Это дублирует тесты в
  [../TESTS.md](../TESTS.md).
- Science-mapping в docs это **LLM context** для быстрого re-boot
  после паузы, не ornamental — не удалять.

---

## 🔄 Что дальше

Когда добавляешь новую фичу:
1. Напиши design-doc в подходящей главе.
2. Добавь ссылку в этот README с временем чтения.
3. Обнови [../TESTS.md](../TESTS.md) с user-кейсом + API-проверкой.
4. Обнови [ontology.md](ontology.md) если меняется формат данных.
5. Обнови [../TODO.md](../TODO.md) если не всё закрыто.

Таким образом docs-дерево растёт как книга, а не как свалка.
