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

## Защита от циклов

- `_generated` flag: не генерировать заново если уже набрали min_hyp
- merge всегда `no_merge: false` в autorun (originals удаляются)
- SmartDC всегда `replace` mode (не создаёт дочерние ноды)
- `meta_count < max_meta`: максимум 2 мета-запроса за цикл

## Файлы

- `src/thinking.py` — `tick()`, `classify_nodes()`, `_find_similar_group()`, `_pick_target()`
- `src/horizon.py` — `select_phase()`, `update()`, `to_llm_params()`
- `src/graph_routes.py` — autorun loop вызывает tick и исполняет actions
