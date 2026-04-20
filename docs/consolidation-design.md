# Консолидация — забывание как фича

> Граф не должен расти линейно в количестве тиков. Слабая старая информация
> должна уходить, освобождая внимание. Это не баг памяти, это её работа.
> Биологический аналог — memory consolidation во время медленного сна:
> недавно активные связи укрепляются, неактивные тают.

## Три процесса

Ночной цикл идёт строго по порядку **decay → prune → archive**. Decay готовит
почву (снижает confidence у неиспользуемых) — prune собирает то что опустилось
под порог — archive выгребает старые tick-снапшоты из state_graph.

### 1. Hebbian decay (confidence ↓ без обращений)

**Добавлено 2026-04-20 в рамках [resonance protocol](world-model.md)** —
механика #1 «Node confidence decay без access». Принцип:

- Каждое реальное обращение к ноде (`elaborate` / `smartdc` / участие в
  `pump` / `navigate` / `render-node` / `add-evidence`) вызывает
  `graph_logic.touch_node(idx)`. Он обновляет `last_accessed = now` **и**
  добавляет `+0.02` к `confidence` (capped at 1.0). Это hebbian: **использованная
  связь крепнет**.
- Раз в сутки (в ночном цикле) `consolidation.decay_unused_nodes` проходит
  по всем `hypothesis/thought` с `last_accessed > 1 день назад` и снижает
  `confidence` на `DECAY_PER_RUN = 0.005`. Минимум — `0.05` (не ниже), чтобы
  ноды могли ожить при случайном пересечении.

**Соотношение параметров (подобрано мягко — редкие живые мысли не должны
срываться в архив при недельной паузе юзера):**

| Событие | Δ confidence |
|---|---|
| Обращение через `touch_node()` | +0.02 |
| Сутки без обращений | −0.005 |
| Безубыточно | ≥ 1 обращение / 4 дня |
| От стартовых 0.8 до порога prune 0.3 | ~100 дней без обращений |
| Свежая нода (< 1 дня) | неприкосновенна |
| Ниже 0.05 | замораживается, не падает |

Это чисто операционально: *«мысль которая часто всплывает — крепнет,
неиспользуемая — тихо гаснет и в итоге уходит в prune»*. Никто
ничего не решает руками, только частота использования.

**Параметры в `src/consolidation.py`:**

```python
DECAY_PER_RUN = 0.005         # сколько снимать за прогон
DECAY_MIN_CONFIDENCE = 0.05   # пол — ниже не опустится
DECAY_GRACE_DAYS = 1.0        # свежие не трогаем первые сутки
```

Подобраны под daily nightly cycle. Если частота изменится — нужна
пропорциональная калибровка.

### 2. Content-graph pruning

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

### 3. State-graph archiving

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
Ночной цикл: Scout +мост · ... · decay N / прунинг M / архив K
```

Цифры отдельные: `decayed` = сколько нод опустили на шаг (не удалили),
`pruned` = сколько удалили (из тех что опустились под threshold от decay),
`archived` = сколько tick-записей переехало в archive.

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
- **Калибровка `DECAY_PER_RUN` под реальные данные.** Текущие значения
  (boost 0.02 / decay 0.01) выбраны умозрительно под ежедневный цикл —
  нужна неделя реальной работы чтобы увидеть распределение обращений
  к нодам и подстроить (см. [open-questions #1](open-questions.md) про
  подбор параметров после измерения).

## Файлы

- [src/consolidation.py](../src/consolidation.py) — `decay_unused_nodes`,
  `consolidate_content_graph`, `consolidate_state_graph`, `consolidate_all`
- [src/graph_logic.py](../src/graph_logic.py) — `touch_node`, `touch_nodes`
  (hebbian boost on access)
- [src/graph_routes.py](../src/graph_routes.py) — `/graph/consolidate` endpoint
  + интеграция `touch_node` во все node-access endpoints (elaborate / smartdc /
  pump / expand / navigate / render-node / add-evidence)
- [src/pump_logic.py](../src/pump_logic.py) — `touch_node` для обеих node_a /
  node_b в начале pump'а
- [src/cognitive_loop.py](../src/cognitive_loop.py) — `_check_consolidation`
  nightly hook (decay → prune → archive)

---

**Навигация:** [← State graph](state-graph-design.md)  ·  [Индекс](README.md)  ·  [Следующее: Meta-tick →](meta-tick-design.md)
