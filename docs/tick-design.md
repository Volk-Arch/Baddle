# Tick Cycle — автономный цикл мышления

## Идея

Tick — один шаг автономного мышления. Не "запусти всё и жди результат", а **один атомарный шаг**: сгенерируй / объедини / углуби / проверь. Какой именно шаг — решает CognitiveHorizon на основе текущего состояния графа.

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

## Dispatcher по примитивам

tick() читает `primitive`, `strategy`, `goal_type` из goal ноды и маршрутизирует:

### Stop conditions (`check_stop()` в modes.py)

| goal_type | Условие остановки |
|-----------|------------------|
| finite | Все hypotheses verified ИЛИ avg confidence > 85% |
| repeatable | Цикл завершён → snapshot |
| open | precision > 0.85 и нет bare/unverified → diminishing returns |
| None (free/scout) | Никогда не останавливается автоматически |

### Примитивы

| Primitive | Логика |
|-----------|--------|
| **none** (Scout, Free) | Текущее поведение, без stop condition |
| **focus** (Vector, Rhythm, Horizon) | Одна цель, check_stop() по goal_type |
| **or** | Первая verified hypothesis → стоп |
| **xor comparative** | Все doubted → "ready to compare" |
| **xor dialectical** | Пропускает elaborate, сразу doubt |
| **and unordered** | Все должны быть verified, любой порядок |
| **and seq** | Только первая unverified нода обрабатывается |
| **and priority** | По расстоянию до goal (через _pick_target) |
| **and balance** | Round-robin (random каждый 3-й) |
| **bayes** | Ручной режим: гипотеза + наблюдения → Bayes update. Run = LLM подсказывает наблюдения |

### Multi-goal (subgoals)

Для режимов с `goals_count: "2+"` (AND, OR, XOR): при создании goal ноды multiline текст разбивается на строки. Первая строка = goal text, остальные = hypothesis ноды (subgoals). Goal хранит `subgoals: [idx1, idx2, ...]`.

tick() фильтрует classify только по subgoal нодам — stop conditions и dispatcher работают с отфильтрованными списками.

## Защита от циклов

- `_generated` flag: не генерировать заново если уже набрали min_hyp
- merge всегда `no_merge: false` в autorun (originals удаляются)
- SmartDC всегда `replace` mode (не создаёт дочерние ноды)
- `meta_count < max_meta`: максимум 2 мета-запроса за цикл

## Файлы

- `src/thinking.py` — `tick()`, `classify_nodes()`, `_find_similar_group()`, `_pick_target()`
- `src/horizon.py` — `select_phase()`, `update()`, `to_llm_params()`
- `src/graph_routes.py` — autorun loop вызывает tick и исполняет actions
