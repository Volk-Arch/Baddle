# Activity log — что я сейчас делаю

## Зачем

Baddle знал скаляры (HRV, DA/S/NE, daily_remaining) и события (tick,
feedback, goal lifecycle). Но не знал одной простой вещи: **во что
юзер реально тратит время**. Из-за этого:

- `decision_cost` начислялся только при `/assist/feedback` — реальный
  2-часовой митинг без Baddle не считался
- `named_state` Voronoi выводится из neuro-скаляров → «meditation» может
  всплыть пока юзер бежит на митинг
- Morning briefing без субстанции: «долгий резерв 75%, открытых 3
  цели» — но что вчера было — система не знает
- Pattern detector («3 четверга подряд пропустил завтрак → crash»)
  без activity-лога неоткуда брать substrate

Activity log — append-only event stream ручных context-свитчей. Модель
как у таск-трекера: **Начать → ввод названия → активная задача →
«Следующая» (переключение) → «Стоп»**. Есть шаблоны (Обед / Совещание /
Пауза) для быстрого переключения.

---

## Data model

Файл `activity.jsonl` (append-only, как `goals.jsonl`):

```jsonc
{"action":"start", "id":"abc123", "ts":...,
 "name":"Рефактор assistant.py", "category":"work",
 "workspace":"main", "node_index":42}
{"action":"stop",  "id":"abc123", "ts":..., "reason":"switch"}
{"action":"update","id":"abc123", "fields":{"name":"...", "category":"..."}}
```

Replay → current state: начинаем с пустой мапы, применяем события по
ts. Активная задача = последний `start` без последующего `stop`. Одна
активная за раз.

---

## Три контура замкнутости

1. **Event log** — `activity.jsonl` (источник истины)
2. **Content-граф** — на `start` создаётся нода `type=activity` в
   текущем workspace'е с полями `activity_id`, `activity_category`,
   `activity_ts_start`. На `stop` добавляются `activity_ts_end`,
   `activity_duration_s`, `activity_done=true`. Визуальная «нитка дня»
   в графе.
3. **UserState** (будущее) — periodic tick пока активно вычитает
   `daily_remaining` по таблице category → cost_per_min.

---

## Science-mapping

| Слой | Сейчас было | С activity-логом |
|---|---|---|
| Ground truth | Неявный (через HRV + chat) | Явный ручной лог |
| Energy model | Per-decision | Per-category × time |
| State inference | Voronoi от скаляров | + prior от текущей категории |
| Morning recap | «3 открытых цели» | «вчера 4ч кода, 2 митинга» |

Близко к подходу Pentland & Eagle *Reality Mining* — явные
behavioural-tags над sensor-скалярами дают на порядок лучше
intervention-precision чем только сенсоры.

---

## API

```
GET  /activity/active   → {active, templates}
POST /activity/start    {name, category?, workspace?} → {id, node_index}
POST /activity/stop     {reason?}
POST /activity/update   {id, fields}
GET  /activity/today    → {total_tracked_s/h, by_category, top_names, switches}
GET  /activity/history  ?limit=100
```

При `/activity/start` с уже активной задачей — автостоп текущей со
`stop_reason='switch'`, начало новой. Поведение кнопки «Следующая».

---

## UI

Виджет `#activity-bar` в baddle-табе (между symbiosis и HRV sim).
Состояния:

- **idle:** `[⚪ Нет активной  00:00:00  сегодня 2.1ч · 6 задач]
  [＋ Начать] [шаблоны]`
- **input:** `[<input placeholder="Что делаешь?">] [OK]`
- **active:** `[🟢 Рефактор assistant.py  00:23:45] [↻ Следующая] [⏹]`

Шаблоны из `activity_log.get_templates()` — дефолт (Код / Совещание /
Ответ / Обед / Пауза), в будущем из `profile.context.activity_templates`.

Таймер тикает локально `setInterval(1000)`. Раз в 30с дёргает
`/activity/active` + `/activity/today` для синхронизации.

---

## Morning briefing

В `_build_morning_briefing_text` строка вчерашнего агрегата:

> «Вчера: 4.2ч (work 3.1ч, food 0.5ч) · 12 переключ.»

Если задач не было — строка не добавляется.

---

## Пересечения

- `_detect_category` в assistant.py — 5-категорийная модель
  (food/work/health/social/learning), та же что profile.
  `activity_log.detect_category` — расширенный keyword-set (обед /
  совещание / код / пауза). Обе маленькие — не объединял, чтобы не
  ломать classify-cache.
- `goals_store` — параллельная линия: goals «что хочу решить», activity
  «что сейчас делаю». Связка через category и, в будущем, через
  `goal_id` у activity-события.
- `state_graph` — собственный контур (тики neurochem). Activity log не
  пишет в state_graph (разные гранулярности).

---

## Где в коде

- `src/activity_log.py` — event store, replay, detect_category,
  try_match_recurring_instance / try_detect_constraint_violation
- `src/assistant.py` — `/activity/*` endpoints
- Integration: `_push_event_to_chat`, action-memory hook для
  `user_activity_start/stop`

**Открыто:** Category → energy cost multiplier (tick-hook не подключён),
edit start/end time в UI, sleep duration из gap-между-днями,
отдельный timeline-view, pattern detector подписан на weekday × category.

---

**Навигация:** [← Static storage](static-storage-design.md) · [Индекс](README.md) · [Следующее: Ontology →](ontology.md)
