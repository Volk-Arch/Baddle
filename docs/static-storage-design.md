# Static storage layer — профиль, цели, архив решений

> До этого Baddle была **только динамика**: tick, sync, neurochem, REM,
> consolidation. Всё что Baddle знала о юзере существовало эфемерно — в
> нодах графа, которые умирали при reset/switch. Статики не было.
>
> Этот слой закрывает gap. Три хранилища: user profile (кто ты), goals
> store (что решаешь), solved archive (как решал). Плюс замкнутый цикл
> uncertainty-learning: если профиль пустой по теме вопроса — система
> спросит и запомнит.

## Три хранилища

### 1. User Profile ([src/user_profile.py](../src/user_profile.py))

**Файл:** `user_profile.json` — не append-only, полный snapshot.

**Структура:**
```json
{
  "categories": {
    "food":     {"preferences": [...], "constraints": [...]},
    "work":     {"preferences": [...], "constraints": [...]},
    "health":   {"preferences": [...], "constraints": [...]},
    "social":   {"preferences": [...], "constraints": [...]},
    "learning": {"preferences": [...], "constraints": [...]}
  },
  "context": {
    "profession": "разработчик",
    "wake_hour": 7,
    "sleep_hour": 23,
    "tz": "UTC+3"
  },
  "updated_at": 1776503...
}
```

5 категорий зафиксированы: `food / work / health / social / learning`.
Каждая содержит два массива: `preferences` (что нравится) и `constraints`
(что избегать). Context — произвольный key-value.

**API:**
- `load_profile() / save_profile(p)`
- `add_item(cat, kind, text)` / `remove_item(cat, kind, text)`
- `set_context(key, value)`
- `is_category_empty(cat)` — используется для uncertainty-trigger
- `profile_summary_for_prompt(cats, lang)` — text для LLM-инжекции:
  `"Профиль юзера: [Еда] · нрав.: здоровое питание · избег.: не ем орехи"`
- `parse_category_answer(text, cat, lang)` — LLM-разбор user-ответа на
  profile_clarify-вопрос в `{preferences, constraints}`

### 2. Goals Store ([src/goals_store.py](../src/goals_store.py))

**Файл:** `goals.jsonl` — append-only event log (как state_graph).

**События:**
```json
{"action": "create", "id", "workspace", "text", "mode", "priority",
 "deadline", "category", "ts"}
{"action": "complete", "id", "reason", "snapshot_ref", "ts"}
{"action": "abandon",  "id", "reason", "ts"}
{"action": "update",   "id", "fields": {...}, "ts"}
```

Current state replay'ится из event log через `_replay()`. Status lifecycle:
`open → (done | abandoned)`. Update переcсчитывает поля (priority/deadline/category).

**API:**
- `add_goal(text, mode, workspace, priority, deadline, category) → id`
- `complete_goal(id, reason, snapshot_ref)`
- `abandon_goal(id, reason)`
- `update_goal(id, fields)`
- `list_goals(status, workspace, category, limit)` — current state
- `goal_stats()` — completion_rate, avg_time_to_done, by_mode, by_category

**Lifecycle hook:**
- `/graph/add` с `node_type=goal` → автоматически вызывает `add_goal()`,
  сохраняет `goal_id` в самой ноде
- `tick_nand` STOP CHECK когда goal resolved → `archive_solved()` +
  `complete_goal()`, маркирует node `_goal_completed=True` чтобы
  повторный tick не дублировал

### 3. Solved Archive ([src/solved_archive.py](../src/solved_archive.py))

**Каталог:** `solved/{snapshot_ref}.json`, один файл на решённую задачу.

**Payload:**
```json
{
  "snapshot_ref": "1776503659_abc123_xyz",
  "goal": {"id", "text", "workspace", "reason", "archived_at"},
  "graph_snapshot": {"nodes": [...], "edges": {...}, "meta": {...}},
  "state_trace": [...],      // последние 50 state_graph entries
  "final_synthesis": {"text", "confidence", "idx"}  // highest-conf нода
}
```

Когда tick эмитит `action=stable` с `reason="GOAL REACHED..."`:
- Копируется весь `_graph["nodes"]` + edges + meta
- Последние 50 state_graph entries (context для replay)
- Detected final_synthesis = последняя нода с confidence ≥ 0.8
- snapshot_ref возвращается в goal-event для связи

**API:**
- `archive_solved(goal_id, goal_text, workspace, reason) → snapshot_ref`
- `load_solved(snapshot_ref)`
- `list_solved(limit)` — архивный индекс с summary

Юзер через UI (Goals tab) видит список завершённых задач, клик показывает
полный контекст: как думал, какие ноды были, какие решения принимались.

## Profile-aware flow (замкнутый цикл)

Это главное: статика теперь **активно участвует** в каждом запросе.

```
Юзер: «хочу покушать»
  ↓
_detect_category(message) → "food"  (keyword match)
  ↓
is_category_empty("food")?
  ├─ ДА → profile_clarify card: «расскажи что любишь / избегаешь»
  │      User отвечает → parse_category_answer (LLM) → profile.food.*
  │      автоматически повторяет оригинальный запрос
  │
  └─ НЕТ → profile_summary_for_prompt(["food"]) →
           "Профиль: ест=[здоровое]; не ест=[орехи, молоко]"
             ↓
     classify_intent_llm(message, profile_hint=...)  → mode=tournament
     execute_via_zones(..., profile_hint=...)        → LLM инжектит
                                                       constraints в system
             ↓
     3 рекомендации блюд — без орехов, без молока, здоровые
             ↓
     User выбирает #2 → feedback → UserState.valence ↑
```

### Что инжектится куда

- **classify_intent_llm**: `profile_hint` в user-part prompt'а → помогает
  LLM правильно выбрать mode (например tournament vs fan).
- **execute_via_zones → brainstorm prompt**: `profile_hint` добавляется в
  system-часть как `"Учитывай эти предпочтения и ограничения в ответе"`
  → LLM генерирует идеи с учётом constraints.

### Category detection

Сейчас — keyword match (`_CATEGORY_KEYWORDS` dict в assistant.py) со
словарями «еда / работа / здоровье / социальное / обучение». Быстро, без
LLM. Если не подошло ничего — `None` → profile_hint пустой, normal flow.

Можно расширить до LLM-based detection (объединив с classify в один вызов)
если accuracy станет проблемой.

## Uncertainty-driven profile learning

Первый раз юзер спрашивает про еду — профиль пуст, assistant **не
выполняет** запрос, а возвращает `profile_clarify` card:

```
👤 "Чтобы помочь лучше, мне нужно знать твои предпочтения и ограничения
    в категории «Еда / питание». Расскажи кратко: что любишь, чего избегаешь?"
```

UI показывает textarea + кнопки **Сохранить** / **Пропустить**.

При «Сохранить»:
- `POST /profile/learn {category, answer, original_message}`
- `parse_category_answer` — LLM-разбор в `{preferences, constraints}`
- `add_item` на каждый элемент
- Frontend авто-ретраит оригинальный запрос (ставит в input + send) —
  теперь profile не пустой, execute работает с constraints

Fallback если LLM недоступна в parse: простой split по запятым +
проверка на отрицательные markers (`не `, `без `, `no `) → распределяет
в preferences/constraints.

## UI (👤 + 🎯 в neurochem-панели)

**Profile modal (👤):**
- 5 секций по категориям с preferences (зелёные chips) + constraints
  (оранжевые chips)
- `+/×` на каждом chip — add/remove через endpoints
- Inline input для новых items

**Goals modal (🎯):**
- Summary строка: total/open/done/abandoned + completion_rate + avg_time
- Список **Открытые** — текст + mode + workspace + date, actions ✓ (complete) / × (abandon)
- Список **Завершённые / архив** — последние 15 solved с ref на snapshot

**In-chat card (`type=profile_clarify`):**
- Styled как notion-ish question card (фиолетовый border)
- Textarea + Сохранить/Пропустить

## Почему так, а не иначе

**Почему 5 фиксированных категорий?** Универсальность × простота. Больше
→ юзер теряется. Меньше → всё падает в general и pattern-mining ломается.
5 покрывают 90% daily decisions.

**Почему JSONL events для goals, а JSON snapshot для profile?**
- Profile маленький, редко меняется — atomic rewrite норм
- Goals часто обновляются, имеют audit requirement → event log логичнее.
  Плюс replay делается легко, нет race conditions на write.

**Почему snapshot_ref в goals.jsonl а не inline?** Архив весит десятки
KB (весь граф + state trace). Не хотим раздувать goals.jsonl × тысячи
записей. Ссылка лёгкая; архив подгружается при запросе.

**Почему keyword category detection а не LLM?** Первый запрос делается
быстро (< 50ms против 500-2000ms LLM). Если keyword не сработал — системе
просто нет hint, обычный flow. Никаких misclass errors.

## Жизненный пример: «хочу покушать» в замкнутом цикле

День 1. Profile пуст. Юзер: «хочу покушать».
- detect_category → food
- food пуст → profile_clarify: «расскажи что любишь/избегаешь»
- Юзер: «не ем орехи, люблю курицу и овощи»
- parse → `preferences=[люблю курицу и овощи]`, `constraints=[не ем орехи]`
- profile сохраняется, оригинальный message повторяется
- execute_via_zones с profile_hint → 3 варианта с курицей/овощами, без орехов
- Юзер выбирает, feedback, valence растёт

День 2. Юзер: «что приготовить вечером».
- detect_category → food
- food НЕ пуст → profile_hint = "нрав.: курица, овощи; избег.: орехи"
- classify → tournament, execute с хинтом → мгновенный ответ из 3 опций
- Нет повторного clarify — система помнит

День 30. Юзер: «хочу попробовать новое».
- detect_category → food
- LLM с хинтом генерирует 5 новых рецептов, уважающих constraint орехов
- Юзер принимает `rejected` на один → UserState.valence слегка падает →
  следующий раз LLM будет аккуратнее

Замкнулось: state + profile + goals + LLM → рекомендация → feedback → state.

## Что не сделано (остаётся в TODO)

- **Inventory для еды** — если захотим «из холодильника». Сейчас работает
  без: LLM знает food constraints, предлагает общие идеи блюд.
- **LLM-based category detection** — если keyword match слабоват. Сейчас
  5 категорий × ~10 слов = 50 keywords, покрывают ~90% cases.
- **Solved archive visualization** — UI показывает список, но не SVG
  replay графа решения. Можно переиспользовать существующий graph-svg
  рендер + state-trace timeline.
- **Cross-goal patterns** — «когда решаю X из food — обычно кончается
  abandoned» — это meta-tick уровня goals. Пока нет.
- **Goal hierarchy / parent_goal_id** — сейчас goals плоские. subgoals
  на node-уровне (в `_graph.nodes[i].subgoals`), но в goals_store не
  реплицируются.

## Файлы

- [src/user_profile.py](../src/user_profile.py)
- [src/goals_store.py](../src/goals_store.py)
- [src/solved_archive.py](../src/solved_archive.py)
- [src/assistant.py](../src/assistant.py) — endpoints + profile-aware flow + uncertainty trigger
- [src/assistant_exec.py](../src/assistant_exec.py) — profile_hint в execute
- [src/graph_routes.py](../src/graph_routes.py) — hook на `/graph/add` goal type
- [src/tick_nand.py](../src/tick_nand.py) — STOP CHECK → archive + complete
- [templates/index.html](../templates/index.html) — Profile/Goals modals + buttons
- [static/css/style.css](../static/css/style.css) — profile/goals/profile_clarify styles
- [static/js/assistant.js](../static/js/assistant.js) — modal logic + card renderer

---

**Навигация:** [← Meta-tick](meta-tick-design.md)  ·  [Индекс](README.md)  ·  [Следующее: Activity log →](activity-log-design.md)
