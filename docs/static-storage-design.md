# Static storage — профиль, цели, архив решений

До этого Baddle была **только динамика**: tick, sync, neurochem, REM,
consolidation. Всё что система знала о юзере существовало эфемерно —
в нодах графа, которые умирали при reset/switch. Статики не было.

Этот слой закрывает gap. Три хранилища:
- **User Profile** — кто ты
- **Goals Store** — что решаешь
- **Solved Archive** — как решал

Плюс замкнутый цикл uncertainty-learning: если профиль пустой по теме
вопроса — система спросит и запомнит.

---

## User Profile

Файл `user_profile.json` — не append-only, полный snapshot (маленький,
редко меняется, atomic rewrite норм).

**5 фиксированных категорий:** food / work / health / social / learning.
Каждая содержит `preferences` (что нравится) и `constraints` (что
избегать). Плюс `context` (произвольный key-value: profession, wake_hour,
sleep_hour, tz).

5 категорий — компромисс универсальность × простота. Больше → юзер
теряется. Меньше → всё падает в general и pattern-mining ломается.
Покрывают ~90% daily decisions.

**API:** `load_profile / save_profile`, `add_item / remove_item`,
`set_context`, `is_category_empty` (для uncertainty trigger),
`profile_summary_for_prompt` (text для LLM-инжекции),
`parse_category_answer` (LLM-разбор user-ответа на clarify-вопрос).

---

## Goals Store

Файл `goals.jsonl` — append-only event log (как state_graph). Goals
часто обновляются, нужен audit → event log проще чем snapshot.

**Три вида цели:** `oneshot` (обычная, закрывается complete/abandon),
`recurring` (привычка, копит instance events), `constraint` (граница,
копит violation events). Детали — [closure-architecture.md](closure-architecture.md).

**События:** `create` / `complete` / `abandon` / `update` / `instance` /
`violation`. Current state replay'ится через `_replay()`. Status
lifecycle: `open → (done | abandoned)`. Recurring и constraint всегда
`open`.

**API:** `add_goal(text, mode, workspace, kind, schedule, polarity) → id`,
`record_instance`, `record_violation`, `complete_goal`, `abandon_goal`,
`update_goal`, `list_goals(status, workspace, category, limit)`,
`goal_stats()`.

**Progress helpers** (`src/recurring.py`): `get_progress` (done_today /
expected_by_now / lag / period), `list_lagging`, `list_constraint_status`,
`build_active_context_summary` (текст для LLM-prompt инжектится в /assist).

**Lifecycle hook:** `/graph/add` с `node_type=goal` автоматически зовёт
`add_goal()` и сохраняет `goal_id` в ноде. `tick_nand` при STOP CHECK
когда goal resolved → `archive_solved()` + `complete_goal()`, маркирует
нод `_goal_completed=True` (идемпотентность).

---

## Solved Archive

Каталог `graphs/<workspace>/solved/{snapshot_ref}.json` — per-workspace,
один файл на решённую задачу. Снапшот живёт рядом с графом откуда
пришла цель.

**Что внутри:**
- `goal` — id, text, workspace, reason, archived_at
- `graph_snapshot` — полный `_graph["nodes"] + edges + meta`
- `state_trace` — последние 50 state_graph entries (контекст для replay)
- `final_synthesis` — последняя нода с confidence ≥ 0.8 (автодетект)

Snapshot_ref в goals.jsonl **ссылкой**, не inline — архив весит
десятки KB, не раздуваем event log.

**API:** `archive_solved`, `load_solved`, `list_solved`.

Юзер через UI (Goals tab) видит список завершённых, клик показывает
контекст — как думал, какие ноды были, какие решения принимал.

---

## Profile-aware flow

Главное: статика активно **участвует в каждом запросе**.

Юзер пишет «хочу покушать»:

1. `_detect_category(message) → "food"` (keyword match — быстро, без LLM)
2. `is_category_empty("food")`?
   - **ДА** → `profile_clarify` card: «расскажи что любишь/избегаешь».
     Юзер отвечает → `parse_category_answer` (LLM) → profile.food.*
     → авто-retry оригинала
   - **НЕТ** → `profile_summary_for_prompt` =
     `"Профиль: ест=[здоровое]; не ест=[орехи, молоко]"`
3. `classify_intent_llm(message, profile_hint)` → mode=tournament
4. `execute_via_zones(..., profile_hint)` → LLM в system-части получает
   «учитывай эти предпочтения и ограничения» → 3 рецепта без орехов и
   молока
5. Юзер выбирает → feedback → UserState.valence ↑

**Category detection сейчас keyword-match** (~50 слов для 5 категорий,
покрывает ~90%). Если не сработало — profile_hint пустой, normal flow,
никаких misclass errors. Можно расширить на LLM если accuracy станет
проблемой.

---

## Uncertainty-driven profile learning

Первый раз юзер спрашивает про еду — профиль пуст, assistant **не
выполняет** запрос, а возвращает `profile_clarify` card:

> 👤 «Чтобы помочь лучше, мне нужно знать твои предпочтения и
> ограничения в категории «Еда». Расскажи кратко: что любишь, чего
> избегаешь?»

UI показывает textarea + кнопки Сохранить / Пропустить.

При Сохранить: `POST /profile/learn` → `parse_category_answer`
(LLM разбирает в preferences + constraints) → `add_item` на каждый
элемент → frontend авто-retry оригинала. Теперь profile не пустой,
execute работает с constraints.

**Fallback** если LLM недоступна: простой split по запятым + проверка
на markers (`не `, `без `, `no `) → распределяет в prefs/constraints.

---

## Жизненный пример: замкнутый цикл

**День 1.** Profile пуст. «Хочу покушать» → clarify → «не ем орехи,
люблю курицу и овощи» → parse → profile сохранён → автопоток → 3 блюда
с курицей/овощами без орехов → выбор, feedback, valence ↑.

**День 2.** «Что приготовить вечером» → profile НЕ пуст → hint
инжектится → мгновенный ответ из 3 опций. Без повторного clarify.

**День 30.** «Хочу новое» → LLM с хинтом генерит 5 рецептов уважающих
constraint орехов → rejected на один → valence слегка падает →
следующий раз LLM аккуратнее.

Замкнулось: state + profile + goals + LLM → рекомендация → feedback →
state.

---

## UI

**Profile modal** (👤) — 5 секций по категориям с preferences (зелёные
chips) + constraints (оранжевые), `+/×` на каждом, inline input.

**Goals modal** (🎯) — summary (total / open / done / abandoned +
completion_rate + avg_time), список Открытые (actions ✓ / ×), список
Завершённые (последние 15 с ref на snapshot).

**In-chat card** `profile_clarify` — styled как question card
(фиолетовый border) с textarea + Сохранить/Пропустить.

---

## Где в коде

- `src/user_profile.py` — Profile
- `src/goals_store.py` — Goals event log
- `src/solved_archive.py` — Archive
- `src/assistant.py` — endpoints + profile-aware flow + uncertainty trigger
- `src/assistant_exec.py` — profile_hint injection в execute
- `src/graph_routes.py` — hook на `/graph/add` goal type
- `src/tick_nand.py` — STOP CHECK → archive + complete

**Открыто:** inventory для еды (сейчас работает без — LLM знает
constraints), LLM-based category detection (keyword покрывает 90%),
solved-archive visualization (UI показывает список, но не SVG replay
графа), cross-goal patterns («когда решаю X — обычно abandoned»),
goal hierarchy (parent_goal_id).

---

**Навигация:** [← Episodic memory](episodic-memory.md) · [Индекс](README.md) · [Следующее: Activity log →](activity-log-design.md)
