# Ontology — формат всех данных Baddle

Эта страница описывает **что хранится где** и в каком формате. Используется
как reference когда добавляешь новые поля или пишешь интеграцию.

Принцип: **append-only JSONL** для всего что меняется во времени,
**single JSON** для snapshot-состояний.

**Где физически:** person-level данные в `data/`, per-workspace графы
в `graphs/<ws>/`, workspace registry в `workspaces/index.json`. Layout
целиком → [storage-layout.md](storage-layout.md).

---

## 1. `user_state.json` (snapshot, не append-only)

Хранит сессию ассистента + serialized `UserState`.

```jsonc
{
  "decisions_today": 12,
  "daily_spent": 38.5,          // сколько energy потрачено сегодня
  "last_reset_date": "2026-04-18",
  "last_interaction": 1776528912.4,
  "total_decisions": 234,
  "streaks": {"утренний_код": 5, "бег": 3},
  "history": [...],              // last 100 interactions (trimmed)
  "last_briefing_ts": 1776510400.0,  // для dedup briefing между рестартами
  "user_state_dump": {           // → UserState.from_dict() на старте
    "dopamine": 0.5,
    "serotonin": 0.5,
    "norepinephrine": 0.5,
    "burnout": 0.02,
    "valence": 0.1,
    "expectation": 0.5,
    "reality": 0.5,
    "surprise": 0.0,
    "imbalance": 0.0,
    "long_reserve": 1500.0,
    "activity_magnitude": 0.0,
    "activity_zone": {"key":"recovery", "emoji":"🟢", "label":"..."},
    "named_state": {"key":"neutral", "label":"Нейтральное", "advice":"..."},
    "hrv": {"coherence":0.7, "stress":0.3, "rmssd":42.0},
    "last_sleep_duration_h": 7.2   // выводится из activity log или check-in
  }
}
```

---

## 2. `user_profile.json` (snapshot)

Preferences + constraints по 5 категориям + произвольный context.

```jsonc
{
  "categories": {
    "food":     {"preferences": ["здоровое"], "constraints": ["не ем орехи"]},
    "work":     {"preferences": [...], "constraints": [...]},
    "health":   {...},
    "social":   {...},
    "learning": {...}
  },
  "context": {
    "wake_hour": 7,
    "sleep_hour": 23,
    "profession": "разработчик",
    "hrv_autostart": true,
    "activity_templates": [...]   // опционально override дефолтов
  },
  "updated_at": 1776528912.0
}
```

---

## 3. `goals.jsonl` (append-only event log)

Каждая строка — одно событие. Current state = replay.

```jsonc
{"action":"create", "id":"abc123", "workspace":"main", "text":"...",
 "mode":"horizon", "priority":null, "deadline":null, "category":"work",
 "ts":1776520000.0}
{"action":"complete", "id":"abc123", "reason":"goal reached",
 "snapshot_ref":"sol_001", "energy_pct":0.4, "ts":1776520800.0}
{"action":"abandon", "id":"xyz789", "reason":"нерелевантно", "ts":...}
{"action":"update", "id":"abc123", "fields":{"priority":3}, "ts":...}
```

Ротация: `rotate_if_needed()` gzip'ит события старше 120 дней для
завершённых goal'ов в `archives/goals-{YYYYMMDD}.jsonl.gz`.

---

## 4. `activity.jsonl` (append-only, трекер «что я делаю»)

```jsonc
{"action":"start", "id":"act01", "ts":..., "name":"Код",
 "category":"work", "workspace":"main", "node_index":12}
{"action":"update", "id":"act01", "fields":{"node_index":12}}
{"action":"stop", "id":"act01", "reason":"switch"|"manual"|"auto",
 "ts":...}
{"action":"update", "id":"act01",
 "fields":{"name":"Рефактор","started_at":...,"stopped_at":...}}
{"action":"delete", "id":"act01"}   // soft delete — replay пропускает
```

Replay строит `{id → {name, category, started_at, stopped_at, duration_s,
status, node_index}}`.

---

## 5. `plans.jsonl` (append-only, карта будущего)

Oneshot events + recurring habits в одной модели.

```jsonc
{"action":"create", "id":"p01", "name":"Митинг", "category":"work",
 "ts_start":1776540000.0, "ts_end":null,
 "recurring":null,                          // oneshot
 "expected_difficulty":3, "note":"", "ts":...}
{"action":"create", "id":"p02", "name":"Завтрак",
 "recurring":{"days":[0,1,2,3,4,5,6], "time":"08:00"},
 "expected_difficulty":1, "ts":...}          // recurring habit
{"action":"complete", "id":"p02", "for_date":"2026-04-18",
 "actual_ts":..., "actual_difficulty":2, "note":"", "ts":...}
{"action":"skip", "id":"p02", "for_date":"2026-04-18",
 "reason":"проспал", "ts":...}
{"action":"update", "id":"p01", "fields":{"ts_start":...}}
{"action":"delete", "id":"p01"}
```

Replay выдаёт `{id → {..., completions:[...], skips:[...], status}}`.
`schedule_for_day(date)` разворачивает recurring под конкретный день.

---

## 6. `checkins.jsonl` (append-only, subjective state)

Ручной ввод energy/focus/stress + expected/reality.

```jsonc
{"action":"checkin", "ts":...,
 "energy":75, "focus":60, "stress":30,
 "expected":1, "reality":0,              // ∈ [-2, +2]
 "note":"продуктивный день но устал"}
```

Derived: `surprise = reality - expected`. Кормит `UserState.surprise` и
`long_reserve` через `apply_to_user_state(entry)`.

---

## 7. `patterns.jsonl` (append-only, detected anomalies)

Пишется раз в сутки в `night_cycle` через `patterns.detect_all()`.

```jsonc
{"kind":"skip_breakfast", "category":"food", "window":"morning",
 "weekday":3, "count":3, "baseline_rate":0.8,
 "weekday_label_ru":"четверг",
 "hint_ru":"Последние 3 четверга пропускал завтрак...",
 "detected_at":1776510400.0}
{"kind":"heavy_work_day", "category":"work", "weekday":1,
 "count":4, "mean_minutes_day":180, "hint_ru":"...", "detected_at":...}
{"kind":"habit_anomaly", "plan_id":"p02", "habit_name":"Бег",
 "skipped":5, "completed":2, "total":7, "skip_rate":0.71,
 "hint_ru":"Habit «Бег» пропущен 5 из 7 раз...", "detected_at":...}
```

---

## 8. `state_graph.jsonl` (append-only, аудит жизни системы)

Каждый action системы — одна строка с hash-chain (tamper-evident).

```jsonc
{"hash":"abc123def456", "parent":"prev...",
 "timestamp":"2026-04-18T18:00:00+00:00",
 "action":"tick"|"assist"|"feedback"|"heartbeat"|"night_cycle"|...,
 "phase":"NAND"|"background"|"REM"|...,
 "user_initiated":false,
 "content_touched":[12, 15],         // node indices
 "state_snapshot":{...},             // зависит от action
 "state_origin":"1_rest"|"1_held",
 "rpe":0.2,                          // reward prediction error (tick)
 "user_feedback":null,               // accepted|rejected|ignored
 "reason":"heartbeat · act:Код(15m) · plans:2/4",
 "graph_id":"main"}
```

Отдельный поток `heartbeat` (раз в 5 мин) пишет снапшот всех стримов —
это substrate для DMN/state_walk когда юзер idle.

---

## 9. `state_embeddings.jsonl` (append-only, vector index)

Один к одному со `state_graph.jsonl` — для similarity-search в
`DMN walks` / `state_walk`.

```jsonc
{"hash":"abc123def456", "embedding":[0.01, 0.02, ...]}
```

---

## 10. `workspaces/{ws_id}/graph.json` (snapshot на workspace)

Content graph активного workspace (nodes + edges + embeddings).

```jsonc
{
  "nodes": [
    {"id":0, "text":"...", "embedding":[0.01, ...],
     "entropy":{"avg":0.0,"unc":0.0},
     "depth":0, "topic":"", "confidence":0.5,
     "type":"thought"|"goal"|"hypothesis"|"fact"|"activity",
     "rendered":true,
     "last_accessed":"2026-04-18T18:00:00+00:00",
     "goal_id":"abc123",              // если type=goal → connect к goals_store
     "activity_id":"act01",           // если type=activity → connect к activity_log
     "activity_category":"work",
     "activity_ts_start":...,
     "activity_ts_end":...,
     "activity_duration_s":...,
     "activity_done":true,
     "subgoals":[1,2,3],
     "mode":"horizon"                 // если goal
    }
  ],
  "edges": {
    "manual_links":[[0,1]],
    "manual_unlinks":[],
    "directed":[[0,1]]
  },
  "meta": {
    "topic":"",
    "hub_nodes":[0,5],
    "mode":"horizon"
  },
  "embeddings": [[0.01,...], [0.02,...], ...],  // параллельно nodes
  "tp_overrides": {...},
  "_horizon": {...}                              // optional cached state
}
```

---

## 11. `workspaces/index.json`

```jsonc
{
  "active_id":"main",
  "workspaces":{
    "main":{"id":"main","title":"Main","tags":[],
            "created":"2026-04-18T...","last_active":"..."},
    "research":{...}
  },
  "cross_edges":[
    {"from_graph":"main","from_node":12,
     "to_graph":"research","to_node":3, "d":0.18}
  ]
}
```

---

## 12. `solved/{snapshot_ref}.json` (per-goal archive)

Snapshot графа + state_trace + final_synthesis при `complete_goal()`.

```jsonc
{
  "ref":"sol_001", "goal_text":"...",
  "workspace":"main", "completed_at":...,
  "final_synthesis":"...",
  "state_trace":[...],
  "nodes":[...], "edges":{...}
}
```

---

## 13. `settings.json` (API config + neural + depth)

```jsonc
{
  "api_url":"http://localhost:1234",
  "api_key":"",
  "api_model":"qwen/qwen3-8b",
  "embedding_model":"text-embedding-nomic-embed-text-v1.5",
  "local_ctx":32768,
  // Neural defaults — общие для baddle chat + DMN + graph tab.
  // Читаются через api_backend.get_neural_defaults().
  "neural_temp": 0.7,
  "neural_top_k": 40,
  "neural_threshold": 0.91,
  "neural_novelty": 0.85,
  "neural_max_tokens": 3000,
  "neural_seed": -1,
  // Depth knobs — сколько циклов мышления на каждом уровне.
  // Читаются через api_backend.get_depth_defaults() / get_mode_depth().
  "deep_chat_steps": 3,                 // global fallback
  "deep_mode_steps": {                  // per-mode override
    "horizon": 5, "bayes": 7, "tournament": 3, "dispute": 4,
    "builder": 4, "pipeline": 4, "cascade": 3, "scales": 3,
    "race": 2, "fan": 3, "scout": 3, "vector": 3, "free": 3
  },
  "deep_diversity_min": 0.30,           // diversity guard threshold
  "dmn_converge_max_steps": 100,
  "dmn_converge_stall_window": 12,
  "dmn_converge_max_wall_s": 900,
  "live_bayes": false
}
```

### Новые типы нод content-графа:
- `type="synthesis"` — результат `force_synthesize_top()` (финальный
  collapse после autorun loop). Confidence = avg(top_N source nodes).
- `evidence_polarity: "pro"|"con"|"why"|"how"` — маркировка evidence-нод
  при comparative/dialectical deep pipeline.
- `evidence_target: int` — индекс hypothesis к которой привязан evidence.
- `diversity_seed: true` — нода добавлена diversity guard'ом через pump
  между ближайшей парой при слипшемся brainstorm'е.

---

## Schema-additions policy

Single-path, без миграций и legacy кода. Правила эволюции схем:

1. **Additions only** — добавление новых полей не ломает прошлые записи.
2. **Значения по умолчанию в коде** — при чтении, не через backfill-скрипт.
3. **Breaking schema changes** → **новый файл** с `v2`-суффиксом + явный
   импорт из старого при старте (если нужен). Без grace period — single
   path значит один формат.
4. **Rename** = одномоментный: переименовываем поле во всех readers + писателях
   в одном commit'е. Старые записи получают default при чтении.

---

## Cross-references

- `goal_id` на content-ноде → `goals.jsonl` (id == goal_id).
- `activity_id` на content-ноде → `activity.jsonl` (id == activity_id).
- `snapshot_ref` в goals → `solved/{ref}.json`.
- `hash` в `state_embeddings.jsonl` ↔ `state_graph.jsonl`.
- `plan_id` в patterns (`habit_anomaly`) → `plans.jsonl`.

---

**Навигация:** [← Activity log](activity-log-design.md)  ·  [Индекс](README.md)  ·  [Следующее: SmartDC →](smartdc-design.md)
