# Storage layout — где что лежит

Baddle хранит три ортогональных слоя данных:

| Слой | Папка | Что | Scope |
|---|---|---|---|
| **Person** | `data/` | Настройки + состояние тела + привычки + цели | один-на-юзера |
| **Workspace** | `graphs/<ws>/` | Граф мыслей + history тиков + архив | per-проект |
| **Registry** | `workspaces/index.json` | Список воркспейсов + cross-edges | один-на-юзера |

---

## `data/` — person-level

Всё что идентифицирует **тебя как человека**, не контекст задачи. Если
бы Baddle мигрировал на multi-user облако — каждый юзер имел бы свой
`data/`.

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

Формат: append-only JSONL или atomic-replace JSON. Всё gitignored
через `data/` в `.gitignore` — личные данные не коммитятся.

**Seed.** `roles.json` и `templates.json` при первом запуске пишутся
из `src/defaults.py`. Юзер может править — не перетрутся. После
`/data/reset` дефолты запишутся снова.

---

## `graphs/<ws>/` — workspace-level

Один workspace = одна тема/проект/контекст. «main» дефолтный, юзер
может создать «work», «personal», «research». Граф мыслей, история
тиков, архив — **per-workspace**, потому что разные темы не пересекаются
семантически.

```
graphs/
  main/
    graph.json              # content-graph: nodes + edges + embeddings + meta
    state_graph.jsonl       # append-only tick log
    state_embeddings.jsonl  # lazy cache
    meta.json               # title, tags, created, last_active
    solved/
      {snapshot_ref}.json   # archived goal snapshots
  work/ ...
  personal/ ...
```

**graph.json** — atomic replace на save. Dirty-flush через `_check_ws_flush`
(каждые 2 мин) + при `switch()`.

**state_graph.jsonl** — кто-что-когда системы. Источник истины для
DMN walks, meta_tick `analyze_tail`, Markov transitions. Полный
дизайн — [episodic-memory.md](episodic-memory.md).

**solved/** — когда tick эмитит `action=stable, reason=GOAL REACHED`,
`archive_solved()` пишет полный snapshot (копия графа + tail state_graph
+ final_synthesis). Юзер открывает в 🎯 → Завершённые.

`graphs/*` gitignored кроме `graphs/.gitkeep`.

**Swap семантика при switch(ws):** flush текущего `_graph` → load
target в RAM → rebind `StateGraph` singleton на новый base_dir.
UserState / CognitiveState / profile — **не** переключаются
(person-level).

---

## `workspaces/index.json` — registry

```json
{
  "active_id": "main",
  "workspaces": { "main": {...}, "work": {...} },
  "cross_edges": [
    {"from_graph": "work", "from_node": 3,
     "to_graph": "personal", "to_node": 7, "d": 0.18}
  ]
}
```

**cross_edges** — serendipity bridges, node-пары из разных workspaces
семантически близкие в embedding space. Создаются DMN'ом
(`_check_dmn_cross_graph` каждый час). Используются в meta-graph
(граф-над-графами) для визуализации связей между темами.

`workspaces/*` gitignored кроме `.gitkeep`.

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
┌─────── Workspace (graphs/<ws>/) ──────┐
│  graph.json         ← writes nodes    │
│  state_graph.jsonl  ← writes ticks    │
│  state_embeddings   ← lazy cache      │
│  solved/*.json      ← goal snapshots  │
└──────────┬─────────────────────────────┘
           │
    reads DMN cross-scan
           ▼
┌─── Registry (workspaces/index.json) ──┐
│  cross_edges: [...]                   │
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
| `graphs/<ws>/graph.json` | один раз на старте; RAM — `_graph` dict |
| `graphs/<ws>/state_graph.jsonl` | append per tick; read через `read_all()` / `tail(n)` |
| `graphs/<ws>/solved/*.json` | scan на `list_solved()` |
| `workspaces/index.json` | при import `workspace` + каждый CRUD |

---

## Reset

`POST /data/reset` с body `{"confirm":"RESET"}` удаляет:
- Всё в `data/` кроме `settings.json`, `roles.json`, `templates.json`
- Все `graphs/<ws>/` папки
- `workspaces/index.json`

Сохраняет: `data/settings.json` (API config), `data/roles.json` +
`data/templates.json` (перезапишутся из defaults если удалить и
рестартнуть).

После reset рекомендуется рестарт процесса — runtime singletons
(`_graph`, `CognitiveState`, `UserState`) пересоздадутся. Без рестарта
endpoint резетит их через `reset_graph()` + `set_global_state`, но
workspace manager проще перезапустить.

---

**Навигация:** [← Workspace](workspace-design.md) · [Индекс](README.md) · [Closure architecture →](closure-architecture.md)
