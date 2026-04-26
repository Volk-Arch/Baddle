# Storage — где что лежит и как читается

Baddle хранит два слоя данных плюс сенсорный поток:

| Слой | Папка | Что | Scope |
|---|---|---|---|
| **Person** | `data/` | Настройки + состояние тела + привычки + цели + sensor stream | один-на-юзера |
| **Graph** | `graphs/main/` | Граф мыслей + история тиков + архив | один на пользователя |

До статики Baddle была **только динамика**: tick, sync, neurochem, REM, consolidation. Всё что система знала о юзере существовало эфемерно — в нодах графа, которые умирали при reset/switch. Этот слой закрывает gap.

---

## `data/` — person-level

Всё что идентифицирует **тебя как человека**, не контекст задачи. Если бы Baddle мигрировал на multi-user облако — каждый юзер имел бы свой `data/`.

```
data/
  settings.json             # API (url/key/model, neural defaults, depth knobs)
  user_state.json           # live UserState: energy, DA/S/NE, HRV snapshot
  user_profile.json         # preferences & constraints (5 категорий)
  goals.jsonl               # append-only event log целей
  activity.jsonl            # append-only activity tracker
  checkins.jsonl            # append-only subjective signal
  patterns.jsonl            # detected anomalies (weekday × category)
  plans.jsonl               # events + recurring habits
  roles.json                # persona list для /graph/think
  templates.json            # chat prompt templates
  state_graph.jsonl         # fallback state_graph (без base_dir)
  state_embeddings.jsonl    # embedding cache
  state_graph.archive.jsonl # consolidation archive
  prime_directive.jsonl     # sync_error EMA лог для валидации
  sensor_readings.jsonl     # polymorphic sensor stream
```

Формат: append-only JSONL или atomic-replace JSON. Всё gitignored через `data/` в `.gitignore` — личные данные не коммитятся.

**Seed.** `roles.json` и `templates.json` при первом запуске пишутся из `src/defaults.py`. Юзер может править — не перетрутся. После `/data/reset` дефолты запишутся снова.

---

## `graphs/main/` — единый граф

Граф мыслей, история тиков, архив завершённых целей. Один контекст на пользователя.

```
graphs/
  main/
    graph.json              # content-graph: nodes + edges + embeddings + meta
    state_graph.jsonl       # append-only tick log
    state_embeddings.jsonl  # lazy cache
    meta.json               # title, tags, created, last_active
    solved/
      {snapshot_ref}.json   # archived goal snapshots
```

**graph.json** — atomic replace на save. Dirty-flush через фоновый check (каждые 2 мин) + при значимых событиях.

**Node fields (per-node JSON):**
- `id`, `text`, `embedding`, `topic`, `type`, `depth`, `entropy`, `rendered`, `created_at`, `last_accessed` — base.
- `confidence ∈ [0, 1]` — authoritative scalar (mean), управляется `_bayesian_update_distinct` + γ + RPE.
- `alpha`, `beta` — Beta-prior sidecar accumulator (initial total=4 → растёт с каждым `_bump_evidence`). Total = alpha+beta = накопленный evidence weight.
- `confidence_total`, `confidence_ci` — cached derivatives `[ci_lower, ci_upper]` для UI без recompute.
- Action-Memory only (`type=action/outcome`): `actor`, `action_kind`, `context`, `closed`, `outcome_idx`, `linked_action_idx`, `delta_sync_error`, `user_reaction`, `latency_s`.
- Evidence only: `evidence_relation`, `evidence_strength`, `evidence_target`.

Backward-compat: legacy nodes без alpha/beta получают priors derived из confidence (`_confidence_to_alpha_beta`) при первом load через `_ensure_node_fields`.

**state_graph.jsonl** — кто-что-когда системы. Источник истины для DMN walks, meta_tick `analyze_tail`, Markov transitions. Полный дизайн — [episodic-memory.md](episodic-memory.md).

**solved/** — когда tick эмитит `action=stable, reason=GOAL REACHED`, `archive_solved()` пишет полный snapshot (копия графа + tail state_graph + final_synthesis). Юзер открывает в 🎯 → Завершённые.

`graphs/*` gitignored кроме `graphs/.gitkeep`.

---

## User Profile

Файл `user_profile.json` — не append-only, полный snapshot (маленький, редко меняется, atomic rewrite норм).

**5 фиксированных категорий:** food / work / health / social / learning. Каждая содержит `preferences` (что нравится) и `constraints` (что избегать). Плюс `context` (произвольный key-value: profession, wake_hour, sleep_hour, tz).

5 категорий — компромисс универсальность × простота. Больше → юзер теряется. Меньше → всё падает в general и pattern-mining ломается. Покрывают ~90% daily decisions.

**API:** `load_profile / save_profile`, `add_item / remove_item`, `set_context`, `is_category_empty` (для uncertainty trigger), `profile_summary_for_prompt` (text для LLM-инжекции), `parse_category_answer` (LLM-разбор user-ответа на clarify-вопрос).

---

## Goals Store

Файл `goals.jsonl` — append-only event log (как state_graph). Goals часто обновляются, нужен audit → event log проще чем snapshot.

**Три вида цели:** `oneshot` (обычная, закрывается complete/abandon), `recurring` (привычка, копит instance events), `constraint` (граница, копит violation events). Детали — [closure-architecture.md](closure-architecture.md).

**События:** `create` / `complete` / `abandon` / `update` / `instance` / `violation`. Current state replay'ится через `_replay()`. Status lifecycle: `open → (done | abandoned)`. Recurring и constraint всегда `open`.

**API:** `add_goal(text, mode, workspace, kind, schedule, polarity) → id`, `record_instance`, `record_violation`, `complete_goal`, `abandon_goal`, `update_goal`, `list_goals(status, workspace, category, limit)`, `goal_stats()`.

**Progress helpers** (`src/recurring.py`): `get_progress` (done_today / expected_by_now / lag / period), `list_lagging`, `list_constraint_status`, `build_active_context_summary` (текст для LLM-prompt инжектится в /assist).

**Lifecycle hook:** `/graph/add` с `node_type=goal` автоматически зовёт `add_goal()` и сохраняет `goal_id` в ноде. `tick_nand` при STOP CHECK когда goal resolved → `archive_solved()` + `complete_goal()`, маркирует нод `_goal_completed=True` (идемпотентность).

---

## Solved Archive

Каталог `graphs/<workspace>/solved/{snapshot_ref}.json` — per-workspace, один файл на решённую задачу. Снапшот живёт рядом с графом откуда пришла цель.

**Что внутри:**
- `goal` — id, text, workspace, reason, archived_at
- `graph_snapshot` — полный `_graph["nodes"] + edges + meta`
- `state_trace` — последние 50 state_graph entries (контекст для replay)
- `final_synthesis` — последняя нода с confidence ≥ 0.8 (автодетект)

Snapshot_ref в goals.jsonl **ссылкой**, не inline — архив весит десятки KB, не раздуваем event log.

**API:** `archive_solved`, `load_solved`, `list_solved`.

Юзер через UI (Goals tab) видит список завершённых, клик показывает контекст — как думал, какие ноды были, какие решения принимал.

---

## Profile-aware flow

Главное: статика активно **участвует в каждом запросе**.

Юзер пишет «хочу покушать»:

1. `_detect_category(message) → "food"` (keyword match — быстро, без LLM)
2. `is_category_empty("food")`?
   - **ДА** → `profile_clarify` card: «расскажи что любишь/избегаешь». Юзер отвечает → `parse_category_answer` (LLM) → profile.food.* → авто-retry оригинала
   - **НЕТ** → `profile_summary_for_prompt` = `"Профиль: ест=[здоровое]; не ест=[орехи, молоко]"`
3. `classify_intent_llm(message, profile_hint)` → mode=tournament
4. `execute_via_zones(..., profile_hint)` → LLM в system-части получает «учитывай эти предпочтения и ограничения» → 3 рецепта без орехов и молока
5. Юзер выбирает → feedback → UserState.valence ↑

**Category detection сейчас keyword-match** (~50 слов для 5 категорий, покрывает ~90%). Если не сработало — profile_hint пустой, normal flow, никаких misclass errors. Можно расширить на LLM если accuracy станет проблемой.

---

## Uncertainty-driven profile learning

Первый раз юзер спрашивает про еду — профиль пуст, assistant **не выполняет** запрос, а возвращает `profile_clarify` card:

> 👤 «Чтобы помочь лучше, мне нужно знать твои предпочтения и ограничения в категории «Еда». Расскажи кратко: что любишь, чего избегаешь?»

UI показывает textarea + кнопки Сохранить / Пропустить.

При Сохранить: `POST /profile/learn` → `parse_category_answer` (LLM разбирает в preferences + constraints) → `add_item` на каждый элемент → frontend авто-retry оригинала. Теперь profile не пустой, execute работает с constraints.

**Fallback** если LLM недоступна: простой split по запятым + проверка на markers (`не `, `без `, `no `) → распределяет в prefs/constraints.

---

## Жизненный пример: замкнутый цикл

**День 1.** Profile пуст. «Хочу покушать» → clarify → «не ем орехи, люблю курицу и овощи» → parse → profile сохранён → автопоток → 3 блюда с курицей/овощами без орехов → выбор, feedback, valence ↑.

**День 2.** «Что приготовить вечером» → profile НЕ пуст → hint инжектится → мгновенный ответ из 3 опций. Без повторного clarify.

**День 30.** «Хочу новое» → LLM с хинтом генерит 5 рецептов уважающих constraint орехов → rejected на один → valence слегка падает → следующий раз LLM аккуратнее.

Замкнулось: state + profile + goals + LLM → рекомендация → feedback → state.

---

## Sensor stream — полиморфный поток сигналов

Сигналы от тела приходят через единый канал, независимый от конкретного устройства. Центральная абстракция — **SensorStream**, в который любой адаптер пушит **SensorReading** одного из заранее известных видов.

**Запись (SensorReading):** `(ts, source, kind, metrics, confidence)`.

| Вид (kind) | Частота | Что внутри |
|---|---|---|
| `rr` | каждый удар сердца (high-freq) | интервал между ударами в мс |
| `hrv_snapshot` | раз в 15 секунд (агрегат) | RMSSD, когерентность, LF/HF, пульс |
| `activity` | по факту движения | magnitude 0–5 от акселерометра |
| `subjective` | ручной check-in | тон, активация, свободная заметка |

**Источники (source):** Polar H10, симулятор, Apple Watch, manual. У каждого источника свой confidence по умолчанию: polar 1.0, apple 0.8, manual 0.7. Абсолютные значения chest-strap (Polar) и optical (Apple) различаются, поэтому нормализация идёт относительно baseline **каждого источника**.

**Взвешенный агрегат.** Когда одновременно доступны Polar (high-freq) + Apple Watch (sparse) + manual (10 минут назад), финальное значение считается с весом `confidence × exp(−возраст/τ)`: свежее и надёжное получает больший вклад. Это ключевая операция stream'а при multi-source setup.

**Persist:** `data/sensor_readings.jsonl` append-only. RR с high-freq downsample'ятся (каждый 10-й на диск) — иначе гигабайт за день. Ring-buffer сырых RR живёт в памяти для онлайн-метрик.

**Синхронизация с состоянием.** Раз в 15 секунд `_check_hrv_push` в `cognitive_loop` берёт `stream.latest_hrv_aggregate()` и кормит UserState (серотонин, норадреналин, activity_zone). Любой источник влияет на состояние через тот же канал.

Физиологическая интерпретация сигналов — в [hrv-design.md](hrv-design.md).

---

## UI

**Profile modal** (👤) — 5 секций по категориям с preferences (зелёные chips) + constraints (оранжевые), `+/×` на каждом, inline input.

**Goals modal** (🎯) — summary (total / open / done / abandoned + completion_rate + avg_time), список Открытые (actions ✓ / ×), список Завершённые (последние 15 с ref на snapshot).

**In-chat card** `profile_clarify` — styled как question card (фиолетовый border) с textarea + Сохранить/Пропустить.

---

## Потоки данных

```
┌─────────── Person (data/) ────────────┐
│  settings  user_state  user_profile   │
│  goals     activity    checkins       │
│  patterns  plans                      │
└──────────┬─────────────────┬──────────┘
           │                 │
    reads settings     reads profile
           │                 │
           ▼                 ▼
      API backend    execute_deep (chat)
           │                 │
           ▼                 ▼
┌─────── Graph (graphs/main/) ──────────┐
│  graph.json         ← writes nodes    │
│  state_graph.jsonl  ← writes ticks    │
│  state_embeddings   ← lazy cache      │
│  solved/*.json      ← goal snapshots  │
└───────────────────────────────────────┘
```

---

## Когда что перечитывается

| Файл | Перечитывается |
|---|---|
| `data/settings.json` | при import `api_backend` + каждый `/settings` POST |
| `data/user_state.json` | atomic load/save на каждый `_get_context()`; lock через `_state_lock` |
| `data/user_profile.json` | per-request в `/profile/*`, без кэша |
| `data/goals.jsonl` | `list_goals()` стримом (rotate при > 2MB) |
| `data/roles.json`, `templates.json` | на каждый `/roles`, `/templates` endpoint |
| `data/sensor_readings.jsonl` | stream append на каждый sensor reading, read через `latest_hrv_aggregate()` |
| `graphs/main/graph.json` | один раз на старте; RAM — `_graph` dict |
| `graphs/main/state_graph.jsonl` | append per tick; read через `read_all()` / `tail(n)` |
| `graphs/main/solved/*.json` | scan на `list_solved()` |

---

## Reset

`POST /data/reset` с body `{"confirm":"RESET"}` удаляет:
- Всё в `data/` кроме `settings.json`, `roles.json`, `templates.json`
- Папку `graphs/main/`

Сохраняет: `data/settings.json` (API config), `data/roles.json` + `data/templates.json` (перезапишутся из defaults если удалить и рестартнуть).

После reset рекомендуется рестарт процесса — runtime singletons (`_graph`, `CognitiveState`, `UserState`) пересоздадутся. Без рестарта endpoint резетит их через `reset_graph()` + `set_global_state`.

---

## Где в коде

- [src/user_profile.py](../src/user_profile.py) — Profile
- [src/goals_store.py](../src/goals_store.py) — Goals event log
- [src/solved_archive.py](../src/solved_archive.py) — Archive
- [src/sensor_stream.py](../src/sensor_stream.py) — SensorStream + SensorReading, взвешенный агрегат, persist
- [src/sensor_adapters.py](../src/sensor_adapters.py) — адаптеры Polar / Apple
- [src/assistant.py](../src/assistant.py) — endpoints + profile-aware flow + uncertainty trigger
- [src/assistant_exec.py](../src/assistant_exec.py) — profile_hint injection в execute
- [src/graph_routes.py](../src/graph_routes.py) — hook на `/graph/add` goal type, sensor endpoints (`/sensor/readings`, `/sensor/aggregate`)
- [src/tick_nand.py](../src/tick_nand.py) — STOP CHECK → archive + complete

**Открыто:** inventory для еды (сейчас работает без — LLM знает constraints), LLM-based category detection (keyword покрывает 90%), solved-archive visualization (UI показывает список, но не SVG replay графа), cross-goal patterns («когда решаю X — обычно abandoned»), goal hierarchy (parent_goal_id).

---

**Навигация:** [← Episodic memory](episodic-memory.md) · [Индекс](README.md) · [Следующее: Activity log →](activity-log-design.md)
