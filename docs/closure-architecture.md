# Closure architecture — как все инструменты замыкаются

> Любой ввод (chat, taskplayer, modal, системное наблюдение) попадает
> в один event log — `goals.jsonl` — через единую интерпретацию намерения.
> Инструменты перестают быть параллельными и становятся view'ами над
> общим состоянием.

Эта страница — обзор того **как соединены слои**. Если тебя интересуют
отдельные компоненты — см.
[intent_router](../src/intent_router.py), [recurring](../src/recurring.py),
[suggestions](../src/suggestions.py), [plans](../src/plans.py),
[solved_archive](../src/solved_archive.py).

---

## Модель: три вида цели в одном хранилище

`goals.jsonl` хранит события всех трёх kind'ов. Тип определяется при
`add_goal(kind=...)`:

| Kind | Что это | Закрывается? | Трекается через |
|------|---------|--------------|------------------|
| **oneshot** | Обычная цель — закрыть проект, купить что-то | да — complete/abandon | tick эмитит `stable` → archive_solved |
| **recurring** | Привычка с частотой — «пить воду 4 раза/день» | нет (всегда open) | `record_instance` events |
| **constraint** | Граница — «не ем орехи», «не работать после 23» | нет | `record_violation` events |

Replay по log → текущий state включает:
- `instances: [{ts, note}]` — выполнения recurring
- `violations: [{ts, note, detected}]` — нарушения constraint
- `status, completed_at, abandoned_at` — для oneshot

`record_instance(goal_id)` и `record_violation(goal_id)` — append-only,
никогда не мутируют старые записи. Это позволяет считать прогресс
(`get_progress`) и streak'и (`list_lagging`) чистым replay'ем.

---

## Четыре входа → одно namespace

```
        CHAT            TASKPLAYER         MODAL               OBSERVATION
       (assist)         (activity_log)     (UI)                (patterns/checkins/state)
           │                 │                │                      │
           ▼                 ▼                ▼                      ▼
   ┌──────────────┐   ┌────────────┐  ┌────────────┐   ┌──────────────────┐
   │ intent_router│   │ try_match_ │  │ кнопки     │   │ collect_         │
   │ (2-level LLM)│   │ recurring_ │  │ +1 / −1    │   │ suggestions()    │
   │              │   │ instance() │  │ +×N        │   │ (pattern +       │
   │              │   │ + try_     │  │            │   │ checkin + stress)│
   │              │   │ detect_    │  │            │   │                  │
   │              │   │ constraint_│  │            │   │                  │
   │              │   │ violation()│  │            │   │                  │
   └──────┬───────┘   └─────┬──────┘  └─────┬──────┘   └────────┬─────────┘
          │                 │               │                   │
          └─────────────────┴───────────────┴───────────────────┘
                                     │
                                     ▼
                           ┌────────────────────┐
                           │   goals.jsonl      │
                           │   (append-only     │
                           │    event log)      │
                           └────────────────────┘
                                     │
                                     ▼
                           ┌────────────────────┐
                           │    _replay()       │
                           │ → current state    │
                           │ → get_progress()   │
                           │ → list_lagging()   │
                           │ → list_constraint_ │
                           │   status()         │
                           └────────────────────┘
```

---

## Intent router — двухуровневый LLM

[`src/intent_router.py`](../src/intent_router.py). Первый LLM-call
классифицирует сообщение в top-kind, второй (если нужно) — в subtype.
Для `chat` второй call пропускается.

**Level 1:** `task` / `fact` / `constraint_event` / `chat` / `command`

**Level 2:**

| Top | Subtype | Действие |
|-----|---------|----------|
| `task` | `new_goal` / `new_recurring` / `new_constraint` | Draft card `intent_confirm` |
| `task` | `question` | Обычный execute_deep |
| `fact` | `instance` + matched goal_id | `record_instance(goal_id)` + ack |
| `fact` | `activity` | `start_activity()` + match recurring |
| `fact` | `thought` | Node в graph |
| `constraint_event` | `violation` | `scan_message_for_violations` |
| `chat` | — | Простой свободный LLM ответ |

**Кэш:** LRU 100, TTL 5 мин. Одинаковые сообщения не тратят токены
повторно.

**Ключевое различение task vs fact:**
- task = будущее/желаемое («хочу бегать», «как»)
- fact = текущее/прошедшее («начал тренировку», «поел»)

---

## Двусторонняя связка chat ↔ taskplayer

| Направление | Триггер | Что происходит |
|-------------|---------|---------------|
| chat → taskplayer | `fact/activity` + extract_activity_name | `start_activity("Тренировка")` + notice в chat |
| taskplayer → goals | `try_match_recurring_instance("Обед")` | +1 instance recurring «покушать 3 раза» |
| taskplayer → constraint | `try_detect_constraint_violation("Пиво")` | violation на constraint «не пью» |
| chat → instance | `fact/instance` + matched_goal_id | record_instance напрямую, без execute_deep |
| chat → violation | `scan_message_for_violations` | record_violation на matched constraint |

Все пути используют **те же** LLM-классификаторы через intent_router.
UI рендерит `instance_ack`, `activity_started`, `constraint_violation`
карточки — каждая с кнопкой отмены.

---

## Plans ↔ Recurring link

Plans и recurring goals — **разные по роли**, но связаны через
опциональный `goal_id`:

| Что | Смысл |
|-----|-------|
| **Plan** (plans.jsonl) | Time-boxed событие: «встреча 14:00», «тренировка пн в 7:00» |
| **Recurring goal** (goals.jsonl) | Частота без конкретного времени: «3 раза в неделю» |
| **plan.goal_id** | Link — `complete_plan` авто-вызывает `record_instance(goal_id)` |

UI «План дня» (plan-panel) показывает unified view:
- Plans с временами сверху (time-sorted)
- Recurring progress за сегодня снизу (с кнопкой `+1`)
- Разделитель «— привычки —»
- `+1` в любом месте обновляет **оба** view'а (plan-panel + goals modal)

Deprecated: `plans.recurring=True` больше не создаётся через UI. Для
привычек используется 🎯 → Привычки. Back-compat чтения сохранён.

---

## Observation → Suggestion

Система не просто детектит паттерны, но **предлагает** draft цели.
Тот же card type `intent_confirm` что у router'а — юзер жмёт Да/
Изменить/Нет. См. [`src/suggestions.py`](../src/suggestions.py).

### Четыре источника detect'а

| Источник | Триггер | Что suggest'ит |
|----------|---------|----------------|
| **patterns** | skip_breakfast / heavy_work_day / habit_anomaly | Обычно recurring (добавить недостающее) или constraint (ограничить перегруз) |
| **checkins streak** | stress ≥ 70 / energy ≤ 30 / surprise ≤ −0.5 за 7 дней | Recurring (восстановительная привычка) или constraint |
| **state/activity** | 3+ work-сессий >2ч подряд | Constraint «ограничить блок работы» |
| **weekly aggregate** | Сравнение this_week vs prev_week (checkins + activity hours + recurring adherence) | ОДНА change на следующую неделю — recurring/constraint/abandon |

### Единый LLM-draft формат

```
KIND: recurring|constraint
TEXT: <короткий текст цели>
FREQ: <n> / <day|week>       # для recurring
POLARITY: avoid|prefer        # для constraint
```

Парсер `_parse_draft_response` tolerant к регистру и русским ключам.

### Throttle

- `_check_observation_suggestions` — раз в сутки
- Max 2 карточки в день (`SUGGESTIONS_MAX_PER_DAY`)
- Dedup по `draft.text` — два источника с одинаковой идеей = одна карточка
- Alert `observation_suggestion` → UI рендерит через `assistRenderCard`

### On-demand

`GET /suggestions/pending` — синхронный путь «что ты мне сейчас
предложишь?», без ожидания 24h cycle.

---

## Solved archive → RAG

Архивные цели (snapshot при goal-resolved) перестали быть забытыми.
`find_similar_solved(query_text, top_k=3, min_similarity=0.55)`:

1. Считает embedding query'а
2. Для каждого архива берёт embedding goal-text (из cached в snapshot или
   на лету)
3. Cosine similarity → top-K
4. Возвращает `{snapshot_ref, goal_text, final_synthesis, similarity}`

В `/assist` это инжектится в `profile_hint`:

```
Похожие решённые раньше задачи (для контекста):
  — «как выбрать ноутбук» (sim 0.78): [final_synthesis...]
  — «что подарить маме» (sim 0.62): [final_synthesis...]
```

LLM видит эти заметки и строит ответ с учётом прошлого опыта —
неявный RAG без отдельного индекса.

---

## Единая draft-карточка `intent_confirm`

Один UI-компонент обслуживает **все** пути создания recurring/
constraint/goal:

```
┌────────────────────────────────────┐
│ ♻ Создать привычку?                │
│ 💡 Потому что: <trigger>           │
│ пить воду каждый час · health      │
│                                     │
│         [Нет] [Изменить] [Да]      │
└────────────────────────────────────┘
```

Источники card'ов:
- **router/new_recurring** — юзер явно сказал «хочу X каждый день»
- **router/new_constraint** — «хочу перестать Y»
- **observation_suggestion** — система заметила паттерн и предлагает

Кнопка **Да** → `POST /goals/confirm-draft` → создание goal через
`add_goal(kind, schedule/polarity)`. **Изменить** → открывает форму
в 🎯 Цели с preload'ом. **Нет** → удаление карточки.

---

## Workspace scoping

**Практический workflow и user guide** → [workspace-design.md](workspace-design.md)
(секция «User flow»).

`intent_router`, `build_active_context_summary`, `scan_message_for_violations`,
`try_match_recurring_instance`, `try_detect_constraint_violation` —
все принимают optional `workspace` параметр. Если указан:

- `list_recurring(workspace="work")` — только цели с `workspace="work"` +
  глобальные (без `workspace` поля — видны во всех контекстах)
- LLM видит только релевантные recurring в subtype-classifier'е
- Cache router'а key'ируется `"{workspace}::{message_key}"` — одно
  сообщение в разных ws даёт разные результаты

Практически: в workspace=work юзер пишет «сделал стендап» → матчится
только к work-recurring. То же сообщение в workspace=personal не
триггерит work-цель.

Передача workspace в chain:
```
/assist → workspace = get_workspace_manager().active_id
  → intent_router.route(msg, workspace)
  → _classify_subtype_fact(..., recurring_list=filtered_by_ws)
  → match в scoped списке
```

Для `/activity/start` — аналогично, workspace = active workspace при
вызове.

---

## Hooks в cognitive_loop (background)

Фон проходит по нескольким детекторам через один общий `_throttled()`:

| Hook | Интервал | Что делает |
|------|----------|-----------|
| `_check_recurring_lag` | 30 мин | Recurring с отставанием → alert |
| `_check_observation_suggestions` | 24h | Draft suggestions → alert |
| `_check_dmn_continuous` | 10 мин | Pump bridges between graph nodes |
| `_check_dmn_deep_research` | 30 мин | Autonomous deep research open-goal |
| `_check_dmn_converge` | 60 мин | Server-side tick-autorun до stable |
| `_check_dmn_cross_graph` | 60 мин | Serendipity bridges между workspaces |
| `_check_night_cycle` | 24h | Scout + REM + consolidation |
| `_check_heartbeat` | 5 мин | State_graph snapshot |

Каждый бросает alerts в очередь → UI polling каждые ~10с → рендер
в chat.

---

## Что это даёт end-to-end

Любой ввод юзера превращается в **один из 5 потоков**:

1. **Быстрое действие** (fact/instance, fact/activity, violation) —
   запись события, 1-2 сек, без execute_deep
2. **Создание цели** (task/new_*) — draft card, 1-2 сек + подтверждение
3. **Глубокое исследование** (task/question) — execute_deep, 10+ сек
4. **Свободный чат** (chat) — 1 простой LLM call
5. **Системное наблюдение** → observation_suggestion → возможное
   новое goal/constraint

Средняя стоимость: 1-2 LLM calls на сообщение юзера (router + optional
subtype). Полный execute_deep зовётся только когда нужно **думать**,
не в ответ на «поел» или «начал тренировку».

---

## Файлы

- [src/intent_router.py](../src/intent_router.py) — 2-level classifier + draft card builder
- [src/goals_store.py](../src/goals_store.py) — kind/schedule/polarity + instances/violations
- [src/recurring.py](../src/recurring.py) — progress, lag, context summary, LLM scanner
- [src/plans.py](../src/plans.py) — time events + goal_id link
- [src/activity_log.py](../src/activity_log.py) — taskplayer + try_match + try_detect
- [src/suggestions.py](../src/suggestions.py) — 3 источника → draft pipeline
- [src/solved_archive.py](../src/solved_archive.py) — RAG-lite через cosine
- [src/cognitive_loop.py](../src/cognitive_loop.py) — `_check_observation_suggestions`,
  `_check_recurring_lag`

---

**Навигация:** [← Storage layout](storage-layout.md) · [Индекс](README.md)
