# Storage layout — где что лежит

Baddle хранит три ортогональных слоя данных:

| Слой | Папка | Что | Scope |
|------|-------|-----|-------|
| **Person** | `data/` | Настройки + состояние тела + привычки + цели | один-на-юзера (singleton) |
| **Workspace** | `graphs/<ws>/` | Граф мыслей + history tick'ов + архив решённых задач | per-проект |
| **Registry** | `workspaces/index.json` | Список воркспейсов + cross-edges | один-на-юзера |

Под каждый слой — отдельные обоснования, ниже.

---

## 1. `data/` — person-level data

> Всё что идентифицирует **тебя как человека**, не контекст конкретной задачи.
> Если бы Baddle мигрировал на облако с multi-user, каждый юзер имел бы
> свой `data/`. Сейчас single-user, поэтому один.

```
data/
  settings.json           # API config (url/key/model, neural defaults, depth knobs)
  user_state.json         # live UserState: energy, dopamine/serotonin/NE, HRV
  user_profile.json       # preferences & constraints (food, health, work, ...)
  goals.jsonl             # append-only event log всех целей (created/completed/abandoned)
  activity.jsonl          # append-only manual activity tracker
  checkins.jsonl          # append-only subjective signal (mood, focus, энергия ручной ввод)
  patterns.jsonl          # detected anomalies (weekday×category)
  plans.jsonl             # events + recurring habits
  roles.json              # persona list for /graph/think (user-editable; seeded from defaults)
  templates.json          # chat prompt templates (user-editable; seeded from defaults)
  state_graph.jsonl       # fallback state_graph для StateGraph без base_dir
  state_embeddings.jsonl  # его embedding cache
  state_graph.archive.jsonl  # consolidation archive
```

**Характер данных:** append-only или atomic-replace, mostly JSON/JSONL.
**Git:** полностью gitignored через `data/` в `.gitignore`. Никогда не
коммитим — это личные данные.

**Seed:** `roles.json` и `templates.json` при первом запуске пишутся из
`src/defaults.py` (inline константы). Юзер может править — не перетрутся.
После `/data/reset` дефолты снова запишутся на следующем старте.

---

## 2. `graphs/<ws>/` — workspace-level data

> **Один workspace = одна тема/проект/контекст работы.** «main» дефолтный,
> юзер может создать «work», «personal», «research», etc. Граф мыслей,
> история тиков и архив решений — **per-workspace**, потому что разные
> темы не пересекаются семантически (мысли о карьере ≠ мысли о рецептах).

```
graphs/
  main/
    graph.json              # nodes + edges + meta + embeddings (content-graph)
    state_graph.jsonl       # append-only history: каждый tick = одна запись
    state_embeddings.jsonl  # lazy embedding cache для query_similar
    meta.json               # title, tags, created, last_active
    solved/
      1776504512_<goal>_xxx.json  # archived goal snapshots
  work/
    ...
  personal/
    ...
```

**Что живёт где, детально:**

- **`graph.json`** — текущий content-graph: ноды (hypothesis/evidence/goal/
  synthesis), рёбра (directed/manual_links/manual_unlinks), meta, embeddings
  (параллельный массив). Atomic replace на save. Dirty-flushed через
  `_check_ws_flush` (каждые 2 мин) + при `switch()`.

- **`state_graph.jsonl`** — **кто-что-когда** системы. Append-only лог
  тиков: `{hash, parent, ts, action, phase, content_touched, state_snapshot,
  reason, graph_id}`. Источник истины для DMN walks, meta_tick analyze_tail,
  Markov transitions. См. [state-graph-design](state-graph-design.md) не
  существует — смотри комменты в [src/state_graph.py](../src/state_graph.py).

- **`state_embeddings.jsonl`** — ленивый кеш embedding'ов state_nodes.
  Считаются при первом `query_similar` / `ensure_embedding(entry)`.

- **`meta.json`** — лёгкий метадата workspace'а: title, tags, created,
  last_active. Считывается `WorkspaceManager.list_workspaces()`.

- **`solved/<snapshot_ref>.json`** — когда tick эмитит `action=stable,
  reason=GOAL REACHED`, `archive_solved()` пишет полный snapshot: копия
  графа + tail state_graph + final_synthesis. Юзер может открыть в
  архиве целей (🎯 modal → Завершённые).

**Git:** `graphs/*` gitignored, кроме `graphs/.gitkeep` чтобы директория
существовала в репо. Per-workspace — это строго user data.

**Swap семантика:** `WorkspaceManager.switch(ws_id)` делает:
1. Flush текущего `_graph` (RAM) в `graphs/<current>/graph.json`
2. Load `graphs/<target>/graph.json` в RAM
3. Rebind global `StateGraph` на `graphs/<target>/` base_dir

UserState/CognitiveState/profile — **не** переключаются, это person-level.

---

## 3. `workspaces/` — registry

```
workspaces/
  index.json    # { active_id, workspaces: {ws_id: meta}, cross_edges: [] }
```

**`cross_edges`** — "serendipity bridges": node-пары из **разных**
workspace'ов которые семантически близки (embedding distance в узком
окне). Создаются DMN'ом (`_check_dmn_cross_graph` каждый час), через
`WorkspaceManager.find_serendipity_bridges()`. Используются в meta-graph
(графе-над-графами) для визуализации связей между темами.

**Git:** `workspaces/*` gitignored кроме `.gitkeep`.

---

## Потоки данных (data flow)

```
┌─────────── Person (data/) ───────────┐
│  settings  user_state  user_profile  │
│  goals     activity    checkins      │
│  patterns  plans                     │
└──────────┬────────────────┬──────────┘
           │                │
    reads settings   reads profile
           │                │
           ▼                ▼
      API backend    execute_deep (chat)
           │                │
           ▼                ▼
┌─────── Workspace (graphs/<ws>/) ─────┐
│  graph.json         ← writes nodes   │
│  state_graph.jsonl  ← writes ticks   │
│  state_embeddings   ← lazy cache     │
│  solved/*.json      ← goal snapshots │
└──────────┬────────────────────────────┘
           │
    reads by DMN cross-scan
           ▼
┌─── Registry (workspaces/index.json) ─┐
│  cross_edges: [                      │
│    {from_graph: "work", from_node: 3,│
│     to_graph: "personal", ...}       │
│  ]                                   │
└──────────────────────────────────────┘
```

---

## Когда что перечитывается

| Файл | Перечитывается |
|------|----------------|
| `data/settings.json` | при import `api_backend` + каждый write через `/settings` POST |
| `data/user_state.json` | atomic load/save на каждый `_get_context()`; lock через `_state_lock` |
| `data/user_profile.json` | per-request в `/profile/*` endpoints, кеш не держим |
| `data/goals.jsonl` | `list_goals()` стримом читает весь файл (rotate при >2MB) |
| `data/roles.json`, `templates.json` | на каждый `/roles`, `/templates` endpoint |
| `graphs/<ws>/graph.json` | один раз на старте (bootstrap), во время работы живёт в RAM `_graph` dict |
| `graphs/<ws>/state_graph.jsonl` | append per tick; read через `read_all()` / `tail(n)` |
| `graphs/<ws>/solved/*.json` | scan на `list_solved()` (для 🎯 Archive modal) |
| `workspaces/index.json` | при import `workspace` + каждый CRUD (create/switch/delete) |

---

## Сброс (reset)

`POST /data/reset` (с body `{"confirm":"RESET"}`) удаляет:
- Всё в `data/` кроме `settings.json`, `roles.json`, `templates.json`
- Все `graphs/<ws>/` папки
- `workspaces/index.json`

Сохраняет:
- `data/settings.json` (API config)
- `data/roles.json` + `data/templates.json` (перезапишутся из defaults
  если удалить и рестартнуть)
- Исходный код (разумеется)

После reset рекомендуется рестарт процесса — runtime singletons (`_graph`,
`CognitiveState`, `UserState`) пересоздадутся; без рестарта endpoint всё
равно резетит их через `reset_graph()` + `set_global_state(CognitiveState())`,
но workspace manager и прочее проще перезапустить.

---

## Навигация

[← Full cycle](full-cycle.md) · [Индекс](README.md) · [Cross-graph →](cross-graph-design.md)
