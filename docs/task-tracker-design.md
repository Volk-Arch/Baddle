# Task Tracker — backlog и auto-scheduling

Расширение taskplayer'а: от «что делаю прямо сейчас» к полноценному **задачному слою**. Backlog задач с оценкой сложности, автоматическое планирование на день через capacity-зону, отслеживание прогресса с возвратом незавершённого в очередь.

---

## Идея

Сейчас taskplayer ([activity-log-design.md](activity-log-design.md)) умеет только «сейчас». Жмёшь **Старт** → Baddle знает что ты делаешь X. **Следующая** → переключил контекст. **Стоп** → закрыл. Всё. После стопа — задача исчезает из поля зрения.

Чего не хватает: **списка того что надо сделать**, который живёт между сессиями. Планирование собственных задач, оценка сложности при создании, автоматическая расстановка по дням с учётом [capacity-зоны](capacity-design.md), возврат незавершённого в очередь. Это классический to-do backlog, но **интегрированный с остальной Baddle**.

Отличие от существующих слоёв:

| Слой | Что в нём | Горизонт |
|---|---|---|
| **Цели** (goals) | Долгосрочные направления («изучить React») | недели–месяцы |
| **Привычки** (recurring) | Циклические действия («покушать 3 раза») | каждый день |
| **Ограничения** (constraints) | Чего избегать («без лактозы») | всегда |
| **План** (plans) | События на фиксированное время («встреча среда 11:00») | минуты–часы |
| **Трекер** (activity_log) | Что идёт прямо сейчас | секунды–часы |
| **Задачи** (новое) | Конкретная работа с оценкой сложности | часы–дни |

Задачный слой — **промежуточное звено** между целью («изучить React») и трекером («сейчас пишу тест компонента»). Один конкретный work-item с оцениваемой сложностью и явным состоянием завершён/не завершён/перенесён.

---

## Структура задачи

Поля:

- **Текст** — что именно сделать.
- **Категория** — одна из пяти из user_profile: work / health / social / learning / food (список расширяемый).
- **Оценённая сложность** (estimated_complexity ∈ [0, 1]) — субъективная оценка «тяжести» при создании. 0.2 ~ рутинное, 0.5 ~ обычная работа, 0.8 ~ требует концентрации.
- **Оценённая длительность** (estimated_duration_min) — опционально, сколько минут займёт.
- **Дедлайн** — опционально, timestamp.
- **Родительская цель** (parent_goal_id) — опционально, связь с goal из [storage](storage.md).
- **Статус** — `backlog` (в очереди), `scheduled` (поставлена на сегодня), `in_progress` (запущена через trackер), `done` (закрыта), `deferred` (перенесена), `abandoned` (отменена).
- **Флаг touched_today** — нажата хотя бы раз сегодня, но не завершена.

Все поля, кроме текста и статуса, опциональны. Минимальная задача — просто текст, Baddle помогает довести до более структурированного вида через LLM-parse.

---

## Auto-scheduling — Baddle ставит задачи в план дня

Утренний briefing ([alerts-and-cycles § morning_briefing](alerts-and-cycles.md)) предлагает 2-4 задачи из backlog на сегодня. Алгоритм matching'а:

1. **Проверка капасити.** [Capacity-зона](capacity-design.md) по трём контурам. В красной зоне — ни одной задачи, только рекомендация отдохнуть.
2. **Дедлайны.** Задачи с дедлайном в течение 48 часов — приоритет.
3. **Сложность под зону.** Зелёная зона — до 4 задач любой сложности. Жёлтая — до 2 задач со средней сложностью (≤ 0.5). Красная — только light (≤ 0.3), и не больше одной.
4. **Диверсификация категорий.** Не предлагать все 4 задачи из одной категории — если есть остальные, разбавить.
5. **Перенесённые и touched_today** — приоритетнее свежего backlog (продолжить начатое, если всё ещё актуально).

Подтверждения **не требуется**: предложение показывается как часть briefing'а, юзер может запустить кнопкой из карточки или проигнорировать. Незапущенные к вечеру автоматически возвращаются в backlog без пометки — не создаётся давления «не выполнил».

---

## Progress tracking через activity_log

Когда задача запускается через кнопку из briefing-карточки:
1. Статус задачи меняется на `in_progress`.
2. Вызывается `activity_log.start_activity(name=task.text, category=task.category, _task_id=id)`.
3. Задача оживает в taskplayer как обычная активность.

Дальше два сценария:

**Завершил работу.** Жмёшь **Стоп** в taskplayer → activity останавливается, задача помечается `done`. В evening retro попадает в «сделано сегодня».

**Прервал, не закончил.** Тип (а) switch на другую задачу через **Следующую**, (б) день кончился с активной задачей. В обоих случаях:
- В активити-логе фиксируется stop с reason `switch` или `day_end`.
- Задача в backlog переводится из `in_progress` в `backlog`, но получает флаг `touched_today`.
- В evening retro выводится: «работал 45 минут над X, не закончил; продолжить завтра?».

Флаг `touched_today` сбрасывается на следующий день в полночь. В auto-scheduling на завтра — приоритет перед свежим backlog.

---

## Калибровка сложности

Оценка сложности при создании — субъективная. Через месяц use'а — данные для сравнения:

- **`estimated_complexity`** (что юзер думал когда создавал) — сохраняется в задаче.
- **`surprise_at_start`** (фактическая PE в момент запуска, см. [capacity-design](capacity-design.md)) — снимается activity_log'ом.

Разница `surprise_at_start − estimated_complexity` = ошибка оценки. На месяце накапливается bias — недооценивает или переоценивает. Коррекционный коэффициент в auto-scheduling («этот юзер систематически занижает сложность в 1.4 раза — reality-check перед подтверждением»).

Это прямое расширение [Action Memory](action-memory-design.md) на уровне типов задач: не просто «какой тон sync_seeking работает», а «какую сложность этот юзер реально держит».

---

## UI — расширенный taskplayer

Текущий taskplayer: одна активная задача + кнопки Старт / Следующая / Стоп.

Расширение:
- **Панель backlog** — свёрнутый список с фильтром по категории / дедлайну, раскрывается по клику.
- **Briefing-карточка** в чат-ленте утром: «Сегодня предлагаю: X, Y, Z». Каждая задача — запускаемая кнопка.
- **Evening retro**: «Сделано сегодня: A. Работал но не закончил: B (45 мин)». Кнопка «продолжить завтра» на B.
- **Индикатор touched_today** в списке backlog — точка или цвет, чтобы было видно что задача активна.

Главный принцип: **нет давления**. Нет streak-счётчика «дней подряд с выполненной задачей». Нет push-напоминаний «ты не начал X!». Нет gamification за продуктивность. Ушёл на день — Baddle молча перенёс всё в backlog.

---

# Реализация

## Хранилище

Отдельный файл `data/tasks.jsonl` — append-only, как `activity.jsonl`. Replay даёт текущее состояние backlog.

Типы событий (action):
- `create` — новая задача. `{ts, action: "create", id, text, category, estimated_complexity?, estimated_duration_min?, deadline?, parent_goal_id?}`.
- `schedule` — переведена в `scheduled` на конкретный день. `{ts, action: "schedule", id, for_date: "YYYY-MM-DD"}`.
- `start` — запущена в трекер. `{ts, action: "start", id, activity_id}` (activity_id — ссылка в activity_log).
- `done` — завершена. `{ts, action: "done", id}`.
- `defer` — перенесена. `{ts, action: "defer", id, new_deadline?}`.
- `abandon` — отменена. `{ts, action: "abandon", id, reason?}`.
- `touch_reset` — сброс флага touched_today (полуночный cron). `{ts, action: "touch_reset"}`.

Replay в памяти восстанавливает `dict[task_id, task_state]`. Как в `activity_log._replay`.

Схема задачи детально — [ontology.md](ontology.md) § tasks.

## Новый модуль

`src/tasks.py` по аналогии с `activity_log.py`:

- `add_task(text, category?, estimated_complexity?, estimated_duration_min?, deadline?, parent_goal_id?) -> id` — создаёт.
- `schedule_task(id, for_date)` — помещает в план дня.
- `start_task(id) -> activity_id` — запускает через `activity_log.start_activity` с перекрёстной ссылкой.
- `complete_task(id)` / `defer_task(id, new_deadline?)` / `abandon_task(id, reason?)`.
- `list_tasks(status?, category?, deadline_before?)` — фильтрованный список.
- `list_backlog_for_day(date) -> list` — candidate'ы для auto-scheduling.
- `get_touched_today() -> list` — для evening retro.

## Связь с cognitive_loop

- `_check_daily_briefing` (утренний alert) расширяется: вызывает `list_backlog_for_day` + matching algorithm по capacity-зоне + включает 2-4 задачи в briefing-карточку.
- `_check_evening_retro` расширяется: включает done / touched / deferred секцию.
- Новый check `_check_touch_reset` (раз в сутки в полночь) — сброс `touched_today`.

## Связь с activity_log

- `start_activity` принимает новый опциональный `_task_id`. При наличии — активность линкуется с задачей.
- `stop_activity` при `reason ∈ {switch, day_end}` вызывает `tasks.touch(task_id)` если связанная задача ещё не done.
- `stop_activity` при `reason == "manual"` если связанная задача есть — предлагает её закрыть (через UI confirmation).

## Endpoints

- `POST /tasks/add` — создать.
- `POST /tasks/{id}/schedule` — поставить на день.
- `POST /tasks/{id}/start` — запустить (трекер стартует автоматически).
- `POST /tasks/{id}/done`, `/tasks/{id}/defer`, `/tasks/{id}/abandon`.
- `GET /tasks?status=backlog&category=work` — список с фильтрами.
- `GET /tasks/backlog-for-day?date=2026-04-26` — candidates для briefing.

## Auto-scheduling matching

Псевдокод алгоритма:

```
candidates = list_backlog_for_day(today)
# фильтры: deadline < 48h → приоритет; touched_today → приоритет; deferred → приоритет

zone = capacity.current_zone()
if zone == "red":
    return []   # только рекомендация отдохнуть

max_complexity = {green: 1.0, yellow: 0.5, red: 0.3}[zone]
max_count      = {green: 4,   yellow: 2,   red: 1  }[zone]

filtered = [t for t in candidates if t.estimated_complexity <= max_complexity]
filtered = diversify_by_category(filtered, target=max_count)
return filtered[:max_count]
```

## Миграция

Нет breaking change — новый слой, существующие (goals, recurring, activity_log, plans) не трогаются. Путь:

1. `src/tasks.py` + endpoints + UI-панель backlog.
2. Briefing расширяется с auto-scheduling. Изначально — простой алгоритм без капасити (только deadline + touched).
3. Когда [capacity-design.md](capacity-design.md) войдёт в фазу 2 — matching начинает использовать капасити-зону.
4. Калибровка сложности включается в фазе 3 капасити (когда накоплены surprise_at_start данные).

---

**Навигация:** [← Activity log](activity-log-design.md) · [Индекс](README.md) · [Следующее: Ontology →](ontology.md)
