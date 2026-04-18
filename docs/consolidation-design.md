# Консолидация — забывание как фича

> Граф не должен расти линейно в количестве тиков. Слабая старая информация
> должна уходить, освобождая внимание. Это не баг памяти, это её работа.
> Биологический аналог — memory consolidation во время медленного сна:
> недавно активные связи укрепляются, неактивные тают.

## Два процесса

### 1. Content-graph pruning

Удаляет **слабые старые орфанные** ноды из `_graph["nodes"]`.

**Кандидат должен одновременно:**
- `type ∈ {hypothesis, thought}` (facts/goals/actions/evidence/questions — никогда)
- `depth >= 0` (topic-root ноды depth=-1 защищены)
- `confidence < 0.3` (по умолчанию; параметр)
- `last_accessed > 30 дней` (по умолчанию; fallback на `created_at`)
- **НЕ** в `subgoals` какой-либо цели
- **НЕТ** входящих directed-рёбер от goal / fact / action
- **НЕТ** evidence-нод указывающих на неё (`evidence_target`)

Удаление через `graph_logic._remove_node(idx)`, который ремапит все рёбра
и embedding-кэш. Обрабатывается от конца массива к началу — индексы не
сдвигаются до обработки.

### 2. State-graph archiving

Переносит tick-снапшоты старше `retain_days` (14 по умолчанию) из
`state_graph.jsonl` в `state_graph.archive.jsonl` (append). Основной файл
переписывается атомарно через `.tmp` rename.

**Парент-цепочка переживает архив:** хэши старых entries продолжают
существовать, просто в другом файле. Последний retained entry чейнится
через `parent=<hash>` на архивный entry — цепочка валидна, но для чтения
истории через `read_all()` архив игнорируется по умолчанию (cold storage).

Без архива `state_graph.jsonl` вырос бы в гигабайт за месяцы активной
работы. С архивом — тонкий файл на 14 дней, все компакты движений доступны
для эпизодической памяти.

## Триггеры

**Вручную:** `POST /graph/consolidate`

```json
{
  "dry_run": true,
  "confidence_threshold": 0.3,
  "content_age_days": 30,
  "state_retain_days": 14
}
```

Возвращает `{content: {...}, state: {...}}` summary. `dry_run=true`
показывает что УДАЛИЛОСЬ БЫ, без изменений.

**Автоматически:** `CognitiveLoop._check_consolidation()` — раз в 24ч
когда NE низкое (sleep-like). Генерит alert в `/assist/alerts`:

```
Консолидация: удалено N слабых нод, архивировано M tick-записей.
```

## Что защищено

Консолидация **никогда** не трогает:

| Категория | Почему |
|-----------|--------|
| goal, fact, action, evidence, question | структурные, не «мысли» |
| topic-roots (depth=-1) | базовая структура workspace'а |
| Subgoals активной цели | часть текущей задачи |
| Ноды на которые указывает directed от goal | goal-зависимые |
| Ноды с evidence_target от них | гипотеза поддерживается evidence |
| Свежие (< threshold days) | не успели стать «мусором» |
| confidence ≥ threshold | уже подтверждённые |

## Почему именно такой дизайн

**Почему не LRU кэш?** Память — не ёмкость. Консолидация удаляет **нерелевантное**, не «самое старое». Старая `goal = "написать диплом"` с субголами
остаётся даже через год.

**Почему архив а не удаление state_graph?** Git-аудит → детерминистический
replay. Архив холодный, но читаемый. Если нужно доказать что Baddle
действительно прошла через N состояний 3 месяца назад — JSONL там.

**Почему раз в 24ч?** Биологически — slow-wave sleep цикл консолидации.
Прагматически — ежедневный sanity check без overhead.

**Почему NE gate?** Не стоит чистить граф пока юзер активно взаимодействует.
Консолидация — ночная работа, не прерывание.

## Что не реализовано

- **Ротация архива.** `state_graph.archive.jsonl` растёт монотонно. Нужна
  стратегия: quarterly rotation, gzip compress, или cut-off по возрасту.
- **Lineage-aware prune.** Ноды созданные через collapse (`collapsed_from`
  непустой) могут нести информацию от удалённых предков — сейчас не
  учитывается в решении о прунинге.
- **Strengthening on access.** `last_accessed` обновляется рутинно, но нет
  механизма «укрепления» confidence при повторных попаданиях в tick'и —
  это дополнительная сторона консолидации (LTP аналог).

## Файлы

- [src/consolidation.py](../src/consolidation.py) — `consolidate_content_graph`, `consolidate_state_graph`, `consolidate_all`
- [src/graph_routes.py](../src/graph_routes.py) `/graph/consolidate` endpoint
- [src/cognitive_loop.py](../src/cognitive_loop.py) `_check_consolidation` nightly hook
