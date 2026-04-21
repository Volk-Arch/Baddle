# Tick Cycle — автономный цикл мышления

Tick — один атомарный шаг мышления: сгенерируй / объедини / углуби /
проверь. Какой именно — решает CognitiveState на основе текущего
состояния графа. Аналогия: один вдох-выдох мышления. Autorun = серия
вдохов.

---

## Фазы

```
GENERATE   — пакет идей (novelty-checked)
MERGE      — объединить похожее, не тратить работу на дубли
ELABORATE  — углубить уникальные, добавить evidence
DOUBT      — Smart DC на углублённые но непроверенные
GENERATE+  — всё проверено? искать пробелы (META)
SYNTHESIZE — ничего нового → стабильно → финальный итог
```

Порядок не жёсткий. Horizon выбирает фазу по policy weights — успех
растит вес, провал снижает. Не round-robin, система адаптируется.

---

## Классификация нод перед tick'ом

| Категория | Критерий | Зачем |
|---|---|---|
| **bare** | Нет evidence, не verified, не collapsed_from | Нужен elaborate |
| **unverified** | confidence < stable_threshold | Кандидаты для doubt |
| **verified** | confidence ≥ stable_threshold | Готовы |
| **doubt_candidates** | unverified минус bare | Doubt только после elaborate |

**Bare ноды не идут в doubt.** Сначала elaborate (добавить аргументы),
потом doubt. SmartDC на голой гипотезе без контекста — слабая проверка.

---

## Выбор фазы

`select_phase(available)` смотрит доступные фазы и выбирает по
наибольшему policy weight. Фаза доступна если есть работа: generate —
если гипотез < min и ещё не генерили; merge — если есть группа похожих;
elaborate — если есть bare ноды; doubt — если есть doubt_candidates.

Если ничего не доступно: META («что упустил?», с контекстом verified)
или SYNTHESIZE (граф стабилен, цикл завершён).

---

## Выбор цели

Какую ноду обрабатывать (`_pick_target`):
- BFS-расстояние до goal через взвешенные рёбра — ближе к цели =
  приоритетнее
- Каждый 3-й вызов — случайный выбор (разнообразие, не застревать)
- Без goal — наименее уверенная нода

---

## Horizon integration

Каждый tick возвращает `horizon_params` (temp, top_k, novelty_threshold
для LLM) и `horizon_metrics` (precision, state, policy weights для
UI overlay). Autorun после SmartDC отправляет feedback:
`surprise = 1 − confidence` → `horizon.update(surprise)` → precision
корректируется → следующий tick с новыми параметрами.

---

## NAND-emergent — единственный путь

Classic tick с `if primitive == "xor"` удалён. Все 14 режимов проходят
через **один tick** (`tick_emergent` в `src/tick_nand.py`). Логика
возникает из зон distinct:

- `d < τ_in` → CONFIRM-зона → collapse (merge)
- `τ_in ≤ d ≤ τ_out` → EXPLORE-зона → pump / elaborate
- `d > τ_out` → CONFLICT-зона → smartdc (doubt)

**Emergent compare:** несколько verified + conflict_pairs между ними
→ `action = "compare"` (LLM-judge выбирает лучший).

**Scout / DMN:** pump между furthest-pair, запись bridge.

### Stop conditions

Единая функция `should_stop()`, не зависит от primitive:

- `d(goal, best_verified) < τ_in` → цель достигнута
- Convergence: 3+ verified, avg confidence > 85%, нет pending
- Novelty exhaustion: precision > 0.85 и нет работы

**Для goals с subgoals — AND vs OR эмерджентно по `avg_d`:**

| avg_d | Семантика | Правило |
|---|---|---|
| ≤ τ_in | Subgoals близки (альтернативы: React/Vue/Svelte) | **OR**: первый verified хватит |
| ≥ τ_out | Subgoals разнесены (части целого: frontend/backend/db) | **AND**: все должны быть verified |
| promежуточное | Не резолвим, продолжаем | — |

Режим (tournament / builder / pipeline) не задаёт это явно — distinct
между subgoals сам показывает характер задачи.

### Mode как preset

Поля `primitive` / `strategy` / `goal_type` удалены из `modes.py`. Mode
— компактный кортеж `(name, name_en, goals_count, fields, ..., preset)`.
`preset` (precision, policy, target_surprise) читается через
`get_mode(mode_id)`, `create_horizon(mode_id)` забирает оттуда. Runtime
не свитчится на mode — логика эмерджентна из distinct-зон.

---

## Pause-on-question

Tick эмитит `action: "ask"` когда:
- `sync_error > 0.6` (система не понимает юзера), ИЛИ
- `NE < 0.35` + много unverified (блуждание в неопределённости)

Autorun в `graph.js` ловит это, показывает alert и останавливается.
Юзер отвечает → NE spike + answer становится нодой.

---

## Camera mode — сенсорная депривация

Если `cs.llm_disabled == True`, tick пропускает generate / elaborate /
smartdc (требуют LLM) и работает только на distinct между существующими
нодами: collapse / compare / pump. Найти паттерны в том что уже есть.

---

## Merge lineage

Merge отслеживает происхождение: `collapsed_from: [3, 5, 7]`.
`_filter_lineage` не даёт объединять ноды с общим происхождением —
предотвращает «перемалывание» одного материала. Группировка сначала
по embedding clusters, fallback по topic.

---

## Multi-goal

Для режимов с `goals_count: "2+"` (AND/OR/XOR-like) при создании goal
multiline текст разбивается: первая строка = goal text, остальные =
hypothesis-ноды (subgoals). Goal хранит `subgoals: [idx1, idx2, ...]`.
`tick_emergent` фильтрует classify только по subgoal нодам.

---

## Защита от циклов

- `_generated` flag — не генерировать заново если набрали min_hyp
- Merge всегда `no_merge=false` в autorun (originals удаляются)
- SmartDC всегда `replace` mode (не создаёт дочерние ноды)
- `meta_count < max_meta` — максимум 2 META-запроса за цикл

---

## Hook в state-граф

После каждого emit в state_graph.jsonl добавляется запись с action /
phase / content_touched / полным snapshot'ом CognitiveState. Git-аудит
+ эпизодическая память в одной структуре —
[episodic-memory.md](episodic-memory.md).

---

## Где в коде

- `src/tick_nand.py` — `tick_emergent()` (единственный tick engine)
- `src/cognitive_loop.py` — `CognitiveLoop.tick_foreground()` для
  `/graph/tick` + фоновый thread
- `src/thinking.py` — helpers (`classify_nodes`, `_find_similar_group`,
  `_pick_target`, `_pick_distant_pair`)
- `src/horizon.py` — `select_phase`, `update`, `to_llm_params`,
  `apply_to_bayes`
- `src/state_graph.py` — hook на каждый tick emit
- `/graph/tick` endpoint → `loop.tick_foreground()`
- `static/js/graph.js` — autorun с обработкой `action: "ask"`

---

**Навигация:** [← Конус (метафора + ритм)](cone-design.md) · [Индекс](README.md) · [Следующее: Horizon →](horizon-design.md)
