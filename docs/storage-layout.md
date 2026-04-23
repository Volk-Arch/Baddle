# Storage layout — где что лежит

Baddle хранит два слоя данных плюс сенсорный поток:

| Слой | Папка | Что | Scope |
|---|---|---|---|
| **Person** | `data/` | Настройки + состояние тела + привычки + цели + sensor stream | один-на-юзера |
| **Graph** | `graphs/main/` | Граф мыслей + история тиков + архив | один на пользователя |

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

**state_graph.jsonl** — кто-что-когда системы. Источник истины для DMN walks, meta_tick `analyze_tail`, Markov transitions. Полный дизайн — [episodic-memory.md](episodic-memory.md).

**solved/** — когда tick эмитит `action=stable, reason=GOAL REACHED`, `archive_solved()` пишет полный snapshot (копия графа + tail state_graph + final_synthesis). Юзер открывает в 🎯 → Завершённые.

`graphs/*` gitignored кроме `graphs/.gitkeep`.

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

**Где в коде:**
- [src/sensor_stream.py](../src/sensor_stream.py) — SensorStream + SensorReading, взвешенный агрегат, persist
- [src/sensor_adapters.py](../src/sensor_adapters.py) — адаптеры Polar / Apple
- Endpoints `/sensor/readings`, `/sensor/aggregate` в [graph_routes.py](../src/graph_routes.py)

Физиологическая интерпретация сигналов — в [hrv-design.md](hrv-design.md).

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

**Навигация:** [← DMN / Scout](dmn-scout-design.md) · [Индекс](README.md) · [Closure architecture →](closure-architecture.md)
