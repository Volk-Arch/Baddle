# Tick Cycle — автономный цикл мышления

## Идея

Tick — один шаг автономного мышления. Не "запусти всё и жди результат", а **один атомарный шаг**: сгенерируй / объедини / углуби / проверь. Какой именно шаг — решает CognitiveState на основе текущего состояния графа.

Аналогия: один вдох-выдох мышления. Autorun = серия вдохов.

## Фазы

```
GENERATE   — пакет идей (novelty-checked)
MERGE      — объединить похожее, не тратить работу на дубли
ELABORATE  — углубить уникальные, добавить evidence
DOUBT      — Smart DC на углублённые но непроверенные
GENERATE+  — всё проверено? искать пробелы (META)
SYNTHESIZE — ничего нового → стабильно → финальный итог
```

Порядок не жёсткий — Horizon выбирает фазу по весам (policy weights).

## Классификация нод

Перед каждым tick граф классифицируется (`classify_nodes`):

| Категория | Критерий | Зачем |
|-----------|----------|-------|
| **bare** | Нет evidence, не verified, не collapsed_from | Нужен elaborate |
| **unverified** | confidence < stable_threshold | Кандидаты для doubt |
| **verified** | confidence ≥ stable_threshold | Готовы |
| **doubt_candidates** | unverified минус bare | Doubt только после elaborate |

Ключевое решение: **bare ноды не идут в doubt**. Сначала elaborate (добавить аргументы), потом doubt (проверить). SmartDC на голой гипотезе без контекста — слабая проверка.

## Выбор фазы

```python
available = {
    "generate": len(hypotheses) < min_hyp and not generated,
    "merge":    есть группа похожих,
    "elaborate": есть bare ноды,
    "doubt":    есть doubt_candidates,
}
phase = horizon.select_phase(available)
```

Horizon выбирает из доступных фаз по policy weights. Вес фазы растёт если она дала результат, падает если нет. Это не round-robin — система адаптируется.

Если ни одна фаза не доступна:
1. **META** (если verified ≥ 3 и meta_count < max_meta): "что упустил?" с контекстом уже проверенного
2. **SYNTHESIZE**: граф стабилен, цикл завершён

## Merge: lineage tracking

Merge не просто объединяет — отслеживает происхождение:
- `collapsed_from: [3, 5, 7]` — из каких нод синтезирован
- `_filter_lineage()` не даёт объединять ноды с общим происхождением
- Предотвращает "перемалывание" одного и того же материала

Группировка: сначала по embedding clusters (семантика), fallback по topic.

## Выбор цели

`_pick_target()` — какую ноду обрабатывать:
- BFS-расстояние до goal через взвешенные рёбра (ближе к цели = приоритетнее)
- Каждый 3-й вызов — случайный выбор (разнообразие, не застревать)
- Без goal: наименее уверенная нода

## Horizon integration

Каждый результат tick содержит:
- `horizon_params`: temperature, top_k, novelty_threshold для LLM
- `horizon_metrics`: precision, state, policy_weights для UI overlay

Autorun после SmartDC отправляет feedback:
```
surprise = 1 - confidence
→ horizon.update(surprise) 
→ precision корректируется
→ следующий tick использует новые параметры
```

## NAND-emergent — единственный путь

Classic tick с `if primitive == "xor"` удалён. Все 14 режимов проходят через
**один tick** (`tick_emergent` в `src/tick_nand.py`). Логика возникает из зон
distinct:

```
Compute distinct matrix on hypothesis pairs (за O(n²) с embeddings):
  d < τ_in    → CONFIRM-zone  → collapse (merge)
  τ_in ≤ d ≤ τ_out → EXPLORE-zone → pump / elaborate
  d > τ_out   → CONFLICT-zone → smartdc (doubt)

Emergent compare:
  Несколько verified + conflict_pairs между ними → action = "compare"
  (LLM-judge выбирает лучший)

Scout / DMN (mode_id == "scout"):
  Pump между furthest-pair, записать bridge
```

### Stop conditions (`should_stop()` в modes.py)

Единая функция, не зависит от primitive. См. `docs/nand-architecture.md`:

| Case | Условие остановки |
|------|------------------|
| 1 | Goal с subgoals: distinct-зона решает AND vs OR (детали ниже) |
| 2 | `d(goal, best_verified) < τ_in` → цель достигнута |
| 3 | Convergence: 3+ verified, avg confidence > 85%, нет pending |
| 4 | Novelty exhaustion: precision > 0.85 и нет работы |

**Case 1 подробно (emergent AND/OR через avg_d между subgoals):**

| avg_d | Семантика | Правило |
|-------|-----------|---------|
| `avg_d ≤ τ_in` | Subgoals СЕМАНТИЧЕСКИ БЛИЗКИ (альтернативы одного, React/Vue/Svelte) | **OR**: первый verified хватит |
| `avg_d ≥ τ_out` | Subgoals РАЗНЕСЕНЫ (части целого, frontend/backend/db) | **AND**: все должны быть verified |
| `τ_in < avg_d < τ_out` | Промежуточная зона | НЕ резолвим, продолжаем |

Это эмерджентная семантика — режим (tournament/builder/pipeline) не
задаёт её явно, distinct между subgoals сам показывает характер задачи.

### Mode как preset

Поля `primitive`/`strategy`/`goal_type` удалены из `modes.py`. Mode —
компактный кортеж `(name, name_en, goals_count, fields, placeholder*,
intro*, renderer_style, preset)`. `preset` читается из одного источника
истины через `get_mode(mode_id)`; `create_horizon(mode_id)` забирает
`(precision, policy, target_surprise)` оттуда. Runtime не свитчится на
mode — логика эмерджентна из distinct-зон.

### Pause-on-question

Tick может эмитить `action: "ask"` когда:
- `sync_error > 0.6` (система не понимает юзера), ИЛИ
- `NE < 0.35` + много unverified (система блуждает в неопределённости)

Autorun в `graph.js` ловит это, делает `fetch /graph/assist` (запрос вопроса),
показывает через alert и останавливается. Юзер отвечает → NE spike + answer
становится нодой.

### Camera mode — сенсорная депривация

Если `cs.llm_disabled == True`, tick пропускает generate/elaborate/smartdc
(они требуют LLM-вызов) и работает только на distinct между существующими
нодами: collapse / compare / pump. Режим «сенсорной депривации» — найти
паттерны в том что уже есть.

### Multi-goal (subgoals)

Для режимов с `goals_count: "2+"` (AND/OR/XOR-like): при создании goal ноды
multiline текст разбивается на строки. Первая строка = goal text, остальные =
hypothesis-ноды (subgoals). Goal хранит `subgoals: [idx1, idx2, ...]`.

tick_emergent фильтрует classify только по subgoal нодам.

### Hook в State-граф

После каждого emit результата, в state_graph.jsonl добавляется запись:
`{action, phase, content_touched, state_snapshot (CognitiveState), state_origin}`.
Это Git-аудит + эпизодическая память в одной структуре (см.
[state-graph-design.md](state-graph-design.md)).

## Защита от циклов

- `_generated` flag: не генерировать заново если уже набрали min_hyp
- merge всегда `no_merge: false` в autorun (originals удаляются)
- SmartDC всегда `replace` mode (не создаёт дочерние ноды)
- `meta_count < max_meta`: максимум 2 мета-запроса за цикл

## Файлы

- `src/tick_nand.py` — `tick_emergent()` (единственный tick engine)
- `src/cognitive_loop.py` — `CognitiveLoop` с `tick_foreground()` для `/graph/tick`
  + фоновый thread (Scout/DMN/NE decay/HRV alerts). Общий NE-бюджет с
  foreground через `last_foreground_tick` timestamp
- `src/thinking.py` — helpers: `classify_nodes`, `_find_similar_group`, `_pick_target`, `_pick_distant_pair`, `_tick_force_collapse`
- `src/horizon.py` — `CognitiveState` (`select_phase`, `update`, `to_llm_params`, `apply_to_bayes`, и все neurochem методы)
- `src/state_graph.py` — `StateGraph` с hook'ом на каждый tick emit
- `src/graph_routes.py` — `/graph/tick` эндпоинт → делегирует в `loop.tick_foreground()`
- `static/js/graph.js` — autorun с обработкой `action: "ask"` (pause-on-question) и cone viz

---

**Навигация:** [← Divergence/Convergence](convergence-divergence.md)  ·  [Индекс](README.md)  ·  [Следующее: Horizon →](horizon-design.md)
