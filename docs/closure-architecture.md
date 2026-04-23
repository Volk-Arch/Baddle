# Closure architecture — как все инструменты замыкаются

Любой ввод (chat, taskplayer, modal, системное наблюдение) попадает в
один event log (`goals.jsonl`) через единую интерпретацию намерения.
Инструменты перестают быть параллельными и становятся view'ами над
общим состоянием.

---

## Три вида цели в одном хранилище

`goals.jsonl` хранит события трёх kind'ов:

| Kind | Что это | Закрывается? | Трекается через |
|---|---|---|---|
| **oneshot** | Обычная цель — закрыть проект, купить что-то | да | tick эмитит `stable` → archive_solved |
| **recurring** | Привычка — «пить воду 4 раза/день» | нет | `record_instance` events |
| **constraint** | Граница — «не ем орехи», «не работать после 23» | нет | `record_violation` events |

Replay по log → текущий state: `instances`, `violations`, `status` /
`completed_at` / `abandoned_at`. Append-only, никогда не мутирует
старые записи — прогресс и streak'и считаются чистым replay'ем.

---

## Четыре входа → одно namespace

```
   CHAT            TASKPLAYER         MODAL               OBSERVATION
  (assist)        (activity_log)      (UI)            (patterns/checkins)
     │                 │                │                    │
     ▼                 ▼                ▼                    ▼
 intent_router    try_match_        кнопки            collect_
  (2-level LLM)   recurring        +1 / −1 / +×N     suggestions()
                  + detect_
                  constraint
     │                 │                │                    │
     └─────────────────┴────────────────┴────────────────────┘
                              │
                              ▼
                    ┌──────────────────┐
                    │  goals.jsonl     │
                    │  append-only     │
                    └──────────────────┘
                              │
                              ▼
                    ┌──────────────────┐
                    │   _replay()      │
                    │ → current state  │
                    │ → progress       │
                    │ → lagging        │
                    │ → violations     │
                    └──────────────────┘
```

---

## Intent router — двухуровневый LLM

Первый LLM-call классифицирует сообщение в top-kind. Второй (если
нужно) — в subtype. Для `chat` второй call пропускается.

**Level 1:** `task` / `fact` / `constraint_event` / `chat` / `command`

**Level 2:**

| Top | Subtype | Действие |
|---|---|---|
| task | new_goal / new_recurring / new_constraint | Draft card `intent_confirm` |
| task | question | Обычный execute_deep |
| fact | instance + matched goal_id | `record_instance(goal_id)` + ack |
| fact | activity | `start_activity()` + match recurring |
| fact | thought | Node в graph |
| constraint_event | violation | `scan_message_for_violations` |
| chat | — | Свободный LLM-ответ |

Кэш LRU с TTL — одинаковые сообщения не тратят токены повторно.

**Ключевое различение** task vs fact: task = будущее/желаемое («хочу
бегать», «как»), fact = текущее/прошедшее («начал тренировку», «поел»).

---

## Двусторонняя связка chat ↔ taskplayer

| Направление | Триггер | Что происходит |
|---|---|---|
| chat → taskplayer | fact/activity + extract_activity_name | `start_activity("Тренировка")` + notice в chat |
| taskplayer → goals | `try_match_recurring_instance("Обед")` | +1 instance recurring «покушать 3 раза» |
| taskplayer → constraint | `try_detect_constraint_violation("Пиво")` | violation на «не пью» |
| chat → instance | fact/instance + matched_goal_id | record_instance напрямую, без execute_deep |
| chat → violation | `scan_message_for_violations` | record_violation на matched constraint |

Все пути используют **те же** LLM-классификаторы через intent_router.
UI рендерит `instance_ack`, `activity_started`, `constraint_violation`
карточки — каждая с кнопкой отмены.

---

## Plans ↔ Recurring link

Plans и recurring goals разные по роли, но связаны через опциональный
`goal_id`:

| Что | Смысл |
|---|---|
| **Plan** (plans.jsonl) | Time-boxed событие: «встреча 14:00», «тренировка пн в 7:00» |
| **Recurring goal** (goals.jsonl) | Частота без времени: «3 раза в неделю» |
| **plan.goal_id** | Link — `complete_plan` авто-вызывает `record_instance(goal_id)` |

UI «План дня» показывает unified view: plans с временами сверху
(time-sorted), recurring progress за сегодня снизу (с кнопкой `+1`),
разделитель «— привычки —». `+1` в любом месте обновляет оба view'а.

---

## Observation → Suggestion

Система детектит паттерны и **предлагает** draft цели через тот же
`intent_confirm` card что у router'а.

**Четыре источника detect'а:**

| Источник | Триггер | Что предложит |
|---|---|---|
| **patterns** | skip_breakfast / heavy_work / habit_anomaly | Recurring (добавить недостающее) или constraint |
| **checkins streak** | stress ≥ 70 / energy ≤ 30 / surprise ≤ −0.5 за 7 дней | Recurring восстановительная или constraint |
| **state/activity** | 3+ work-сессий > 2ч подряд | Constraint «ограничить блок работы» |
| **weekly aggregate** | this_week vs prev_week (checkins + activity + recurring adherence) | ОДНА change на неделю |

**Единый LLM-draft формат:**

```
KIND: recurring|constraint
TEXT: <короткий текст>
FREQ: <n> / <day|week>       # для recurring
POLARITY: avoid|prefer        # для constraint
```

Throttle: раз в сутки + max 2 карточки/день + dedup по `draft.text`
(два источника с одинаковой идеей = одна карточка).

`GET /suggestions/pending` — синхронный путь «что ты мне сейчас
предложишь?», без 24ч цикла.

---

## Solved archive → RAG

Архивные цели (snapshot при goal-resolved) доступны через
`find_similar_solved(query, top_k=3, min_similarity=0.55)`. Cosine
similarity по goal-text embedding'у, возвращает
`{snapshot_ref, goal_text, final_synthesis, similarity}`.

В `/assist` это инжектится в `profile_hint` — LLM видит похожие прошлые
задачи и строит ответ с учётом опыта. Неявный RAG без отдельного индекса.

---

## Единая draft-карточка `intent_confirm`

Один UI-компонент обслуживает все пути создания recurring/constraint/goal:

```
┌────────────────────────────────────┐
│ ♻ Создать привычку?                │
│ 💡 Потому что: <trigger>           │
│ пить воду каждый час · health      │
│                                     │
│         [Нет] [Изменить] [Да]      │
└────────────────────────────────────┘
```

Источники card'ов: `router/new_recurring`, `router/new_constraint`,
`observation_suggestion`. **Да** → `POST /goals/confirm-draft` →
создание goal через `add_goal(kind, schedule/polarity)`. **Изменить**
→ форма в 🎯 Цели с preload'ом. **Нет** → удаление.

---

## Background hooks

Фоновые checks через общий `_throttled()`:

| Hook | Интервал | Что |
|---|---|---|
| `_check_recurring_lag` | 30 мин | Recurring с отставанием → alert |
| `_check_observation_suggestions` | 24ч | Draft suggestions |
| `_check_dmn_*` | 10мин / 30мин / 60мин | Pump / deep / converge / cross-graph |
| `_check_night_cycle` | 24ч | Scout + REM + consolidation |
| `_check_heartbeat` | 5 мин | State_graph snapshot |

Полная карта — [alerts-and-cycles.md](alerts-and-cycles.md). DMN/Scout
детально — [dmn-scout-design.md](dmn-scout-design.md).

---

## Что это даёт end-to-end

Любой ввод превращается в **один из 5 потоков:**

1. **Быстрое действие** (fact/instance, activity, violation) —
   запись события, 1-2 сек, без execute_deep
2. **Создание цели** (task/new_*) — draft card, 1-2 сек + подтверждение
3. **Глубокое исследование** (task/question) — execute_deep, 10+ сек
4. **Свободный чат** — 1 простой LLM call
5. **Системное наблюдение** → suggestion → возможное новое goal/constraint

Средняя стоимость: 1-2 LLM calls на сообщение юзера. Полный execute_deep
зовётся только когда нужно **думать**, не в ответ на «поел» или
«начал тренировку».

---

## Где в коде

- `src/intent_router.py` — 2-level classifier + draft card builder
- `src/goals_store.py` — kind/schedule/polarity + instances/violations
- `src/recurring.py` — progress, lag, context summary, LLM scanner
- `src/plans.py` — time events + goal_id link
- `src/activity_log.py` — taskplayer + try_match / try_detect
- `src/suggestions.py` — 4 источника → draft pipeline
- `src/solved_archive.py` — RAG-lite через cosine
- `src/cognitive_loop.py` — `_check_observation_suggestions`,
  `_check_recurring_lag` + DMN checks

---

**Навигация:** [← Storage layout](storage-layout.md) · [Индекс](README.md) · Конец пути ✓
