# Activity log — ground-truth слой «что я сейчас делаю»

## Зачем

Baddle до этого знал **скаляры** (HRV, DA/S/NE, daily_remaining) и
**события** (tick, feedback, goal lifecycle), но не знал одной простой
вещи: **во что юзер реально тратит время**. Из-за этого:

- `decision_cost` начисляется только при `/assist/feedback` — реальный
  2h митинг без Baddle не считается.
- `named_state` Voronoi выводится из neuro-скаляров → «meditation» может
  всплыть пока юзер бежит на митинг.
- Morning briefing без субстанции: «долгий резерв 75%, открытых целей 3» —
  но что *вчера-то было* — система не знает.
- Pattern detector из TODO («3 четверга подряд пропустил завтрак →
  crash») без activity-лога неоткуда брать substrate.

Activity log — append-only event stream ручных context-свитчей. Идея
взята из прототипа [Time Player/v2](file:///C:/Users/Volk/Desktop/Projects/Time%20Player/v2/index.html)
(Telegram Mini App): **Начать → (ввод названия) → активная задача →
«Следующая» (переключение) → «Стоп»**. Шаблоны (Обед / Совещание / Пауза)
для быстрого переключения.

## Data model

Файл: `activity.jsonl` (append-only, как `goals.jsonl` / `state_graph.jsonl`).

```jsonc
{"action":"start",  "id":"abc123...", "ts":1713456789.12,
 "name":"Рефактор assistant.py", "category":"work",
 "workspace":"main", "node_index":42}
{"action":"stop",   "id":"abc123...", "ts":1713460389.5,
 "reason":"switch"}                          // "switch" | "manual" | "auto"
{"action":"update", "id":"abc123...",
 "fields":{"name":"...", "category":"..."}}
```

**Replay → current state:** начинать с пустой мапы, применить события
по ts. Активная задача = последний `start` без последующего `stop` для
того же id. Нулевое допущение: одна активная задача за раз.

## Три контура замкнутости

1. **Event log** — `activity.jsonl` (источник истины).
2. **Content-графа** — на `start` создаётся нода `type=activity` в
   текущем workspace'е с полями `activity_id`, `activity_category`,
   `activity_ts_start`. На `stop` нода обновляется: `activity_ts_end`,
   `activity_duration_s`, `activity_done=true`. Это даёт визуальную
   «нитку дня» прямо в графе.
3. **UserState** (будущее, см. TODO «Category → energy cost multiplier»)
   — periodic tick пока задача активна вычитает из `daily_remaining` по
   таблице category→cost_per_min.

## Мини-маппинг science

| Слой           | Сейчас было              | С activity-логом               |
|----------------|--------------------------|--------------------------------|
| Ground truth   | неявный (через HRV+chat) | явный ручной лог               |
| Energy model   | per-decision             | per-category × time            |
| State inference| Voronoi от skaлярoв      | +prior от текущей категории    |
| Morning recap  | «3 открытых цели»        | «вчера 4ч кода, 2 митинга»     |

Близко к подходу Pentland & Eagle, *Reality Mining* — явные
behavioural-tags над sensor-скалярами дают на порядок лучше
intervention-precision, чем только сенсоры.

## API

Все endpoints в `src/assistant.py` рядом с `/profile/*` и `/goals/*`.

```
GET  /activity/active   → {active: {...} | null, templates: [...]}
POST /activity/start    {name, category?, workspace?} → {id, node_index}
POST /activity/stop     {reason?}  → {stopped: {...} | null}
POST /activity/update   {id, fields:{name?, category?}}
GET  /activity/today    → {total_tracked_s/h, by_category, top_names, switches}
GET  /activity/history  ?limit=100 → [...]
```

**Поведение `/activity/start` при уже активной задаче:** автостоп текущей
со `stop_reason='switch'`, начало новой (это поведение кнопки
«Следующая» в прототипе Time Player).

## UI

Виджет `#activity-bar` в baddle-табе, между symbiosis-панелью и HRV
sim-панелью. Состояния:

- **idle**: `[⚪ Нет активной задачи  00:00:00  сегодня 2.1ч · 6 задач]
  [＋ Начать] [шаблоны...]`
- **input**: `[⚪ Нет активной задачи] [<input placeholder="Что делаешь?">] [OK]`
- **active**: `[🟢 Рефактор assistant.py  00:23:45] [↻ Следующая] [⏹]`

Шаблоны приходят из `activity_log.get_templates()` — сейчас дефолт
(Код / Совещание / Ответ / Обед / Пауза), в будущем из
`profile.context.activity_templates`.

Таймер тикает локально через `setInterval(1000)`. Раз в 30с виджет
дёргает `/activity/active` + `/activity/today` для синхронизации.

## Morning briefing

В `CognitiveLoop._build_morning_briefing_text()` добавлена строка
вчерашнего аггрегата:

```
«Вчера: 4.2ч (work 3.1ч, food 0.5ч) · 12 переключ.»
```

Если задач не было — строка не добавляется (первый день использования).

## Известные open-вопросы (см. TODO «Activity log»)

- Category → `daily_remaining` multiplier ещё не подключён — tick
  вычитает энергию только на `/assist/feedback`.
- Edit start/end time в UI нет — сейчас только name/category.
- Sleep duration из gap-между-днями не выводится (блокер из daily
  viability).
- Отдельный timeline-view (горизонтальная лента дня) — только в графе
  как type=activity ноды.

## Пересечения с существующим

- `_detect_category` в assistant.py — держит 5-категорийную модель
  (food/work/health/social/learning), ту же что profile. `activity_log.
  detect_category` — расширенный keyword-set (обед/совещание/код/пауза).
  Обе функции маленькие — не объединял чтобы не ломать classify-cache.
- `goals_store` — параллельная линия: goals это «что я *хочу* решить»,
  activity это «что я *сейчас делаю*». Связка через category и, в будущем,
  через goal_id у activity-события (планируется).
- `state_graph` — остаётся собственным контуром (тики neurochem).
  Activity log не пишет в state_graph (разные гранулярности).

## Roadmap (в TODO.md)

- TMA-обёртка фронта (push-уведомления через Telegram вместо браузера).
- Category → energy cost multiplier (tick-hook).
- Activity timeline view (отдельная вкладка поверх дня).
- Pattern detector subscribes → weekday × category паттерны.
- Sleep из idle-gap между днями.

---

**Навигация:** [← Static storage](static-storage-design.md)  ·  [Индекс](README.md)  ·  [Следующее: Ontology →](ontology.md)
