# Полный цикл Baddle — статика + динамика

> Все остальные design-доки описывают **куски**. Этот — как всё собирается
> в один работающий организм. Читать если: 1) хочешь понять что зачем,
> 2) планируешь новую фичу и не знаешь куда её приземлять, 3) вернулся
> в проект после паузы и нужен быстрый re-boot.

---

## Два слоя

Всё что делает Baddle делится на **статику** (что ты есть и что уже
сделал) и **динамику** (как ты думаешь прямо сейчас).

```
┌─────────────────────────── СТАТИКА ───────────────────────────┐
│                                                                │
│  USER PROFILE           GOALS STORE          SOLVED ARCHIVE    │
│  (who you are)          (what you solve)     (how you solved)  │
│  data/user_profile.json data/goals.jsonl     graphs/<ws>/solved/ │
│                                                                │
│  • 5 категорий          • append-only        • snapshot графа  │
│  • preferences          • create/done/ab     • state_trace     │
│  • constraints          • stats aggregate    • final_synthesis │
│  • context (wake/tz)                                           │
│                                                                │
└────────────────────────────────────────────────────────────────┘
                              ▲ ▼
┌─────────────────────────── ДИНАМИКА ──────────────────────────┐
│                                                                │
│     USER STATE                   SYSTEM STATE                  │
│     (your now)                   (baddle's now)                │
│                                                                │
│  dopamine ← feedback        dopamine ← distinct(d) в tick      │
│  serotonin ← HRV coherence  serotonin ← ΔW стабильность        │
│  norepinephrine ← HRV str   norepinephrine ← H(W) энтропия     │
│  burnout ← rejects          freeze.accumulator ← d·(1−S)       │
│  valence ← feedback signed  recent_rpe ← |Δposterior|          │
│  expectation ← EMA reality  maturity ← verifieds++             │
│  long_reserve ← energy cost sync_error = ‖user−system‖         │
│                                                                │
│  CONTENT GRAPH              COGNITIVE LOOP                     │
│  ноды с embedding'ами       DMN 10m / state_walk 20m /         │
│  distinct-matrix в тике     night cycle 24h / briefing 24h     │
│  NAND zones: CONFIRM/       tick_foreground при /graph/tick    │
│  EXPLORE/CONFLICT                                              │
│                                                                │
│  STATE GRAPH                META-TICK                          │
│  append-only Git-аудит      паттерны в tail(20)                │
│  каждый tick → запись       stuck/rejection/rpe/monotony       │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

**Статика** хранится и меняется редко (когда явно добавляешь/решаешь).
**Динамика** живёт каждую секунду — апдейт от сигналов, от тика, от
ночного цикла. **Стрелка туда-сюда:** статика читается в LLM-промпт,
динамика при резолве пишется в статику (goals/archive), valence/
neurochem реагирует на feedback.

---

## Прайм-директива

Одна метрика меряет всё: **sync_error = ‖UserState − SystemState‖** в
4-мерном нейрохимическом пространстве (dopamine/serotonin/norepinephrine/
burnout). Любая фича оценивается тем, **снижает ли она рассинхрон с
конкретным юзером**. Фича не снижает — низкий приоритет, даже если
архитектурно красиво.

Отсюда `sync_regime`: FLOW / REST / PROTECT / CONFESS — 4 режима
адаптивного поведения. См. [symbiosis-design.md](symbiosis-design.md).

---

## Data flow: один запрос от юзера

```
Юзер пишет: "Хочу покушать что-то простое"
  │
  ▼
/assist endpoint
  │
  ├─ cs.inject_ne(0.4)                        [dynamic: system arousal ↑]
  ├─ user.update_from_timing()                [dynamic: user engagement]
  ├─ user.update_from_message(text)           [dynamic: user stability]
  ├─ user.update_from_energy(decisions_today) [dynamic: user burnout]
  │
  ├─ _detect_category("Хочу покушать") → "food"     [static: category]
  │
  ├─ is_category_empty("food")?
  │     ├─ ДА → profile_clarify card
  │     │       юзер отвечает → parse_category_answer (LLM)
  │     │       → profile.food.preferences/constraints append
  │     │       → auto-retry оригинального message
  │     │
  │     └─ НЕТ → profile_summary = "ест: овощи; избег.: орехи"  [static read]
  │
  ├─ recurring_ctx = build_active_context_summary()   [active habits/constraints]
  │     → "покушать 3/день (1/3 сегодня) · не орехи"
  │
  ├─ find_similar_solved(message) — RAG по архиву    [past syntheses]
  │     → инжектится в profile_hint если sim ≥ 0.6
  │
  ├─ intent_router.route(message) — 2-level LLM      [NEW: fast-path prefilter]
  │     ↓
  │     kind ∈ {task/fact/constraint_event/chat}
  │     │
  │     ├─ fact/instance → record_instance, return   [~1.5s, no execute_deep]
  │     ├─ fact/activity → start_activity + match    [~1.5s]
  │     ├─ task/new_*    → intent_confirm card       [draft с кнопками]
  │     ├─ chat          → свободный LLM ответ       [~1.5s]
  │     └─ fallthrough → classify_intent_llm → execute_deep
  │
  ├─ classify_intent_llm(message, state_hint, profile_hint)  [если router не решил]
  │     ↓
  │     mode=tournament, intent=direct, confidence=0.85      [dynamic LLM]
  │
  ├─ execute_via_zones(message, mode, profile_hint)
  │     ↓
  │     LLM генерит 5 идей С УЧЁТОМ constraints + recurring + RAG  [dynamic LLM]
  │     distinct-matrix → zones CONFIRM/EXPLORE/CONFLICT          [dynamic]
  │     _render_card → tournament → LLM-judge выбирает           [dynamic LLM]
  │
  └─ scan_message_for_violations(message)            [после execute]
        ↓
        LLM проверяет нарушил ли юзер активные constraints
        если да → record_violation + constraint_violation card
  │
  ├─ _log_decision(mode_id="tournament")
  │     ↓
  │     user.debit_energy(cost=12, daily_remaining)              [dynamic: pool]
  │     state.history append                                      [static: log]
  │
  ▼
Response: cards=[{type: "comparison", options, winner}]
  │
  ▼
Юзер жмёт 👎 rejected
  │
  ▼
/assist/feedback
  │
  ├─ cs.update_neurochem(d=0.8)          [dynamic: burnout ↑, dopamine via RPE]
  ├─ user.update_from_feedback("rejected")
  │     ↓
  │     user.dopamine ↓, burnout ↑, valence ↓, streak bias     [dynamic]
  │
  ▼
Всё. Следующий запрос будет в новом sync_regime.
```

---

## Жизненный цикл goal'а

Статика + динамика работают вместе через весь путь цели:

```
1. CREATE
   POST /graph/add {node_type:"goal", mode:"tournament", text:...}
     ↓
   [static] goals_store.add_goal() → goal_id → вписан в node
   [dynamic] goal-нода в _graph["nodes"] с subgoals

2. PROCESS (пока открытая)
   юзер пишет сообщения, делает ticks, добавляет evidence
     ↓
   [dynamic] confidence subgoals растёт через Bayes updates
   [dynamic] каждый tick → state_graph entry (append)
   [dynamic] neurochem.maturity чуть-чуть ↑ при каждом crossing 0.8

3. RESOLVE (тик эмитит GOAL REACHED)
   should_stop() → True (через distinct-зоны или convergence)
     ↓
   [dynamic] cs.note_verified() → maturity ↑
   [static]  solved_archive.archive_solved(...) → snapshot_ref
   [static]  goals_store.complete_goal(id, reason, snapshot_ref)
   [dynamic] node._goal_completed=True (идемпотентность)

4. RETROSPECT
   UI 🎯 Goals → archive → клик → load_solved(ref)
     ↓
   показывает goal_text + final_synthesis + nodes + state_trace
```

Что осталось в live-графе — временно. Что в solved archive — permanent.

---

## Ночной цикл (24h)

Пока юзер спит, единый проход (в прошлом было 3 параллельных):

```
CognitiveLoop._check_night_cycle():
  │
  ├─ 1. Scout Pump+Save    ← новый bridge-мост между далёкими нодами
  │                          (persisted в граф как новая hypothesis)
  │
  ├─ 2. REM Emotional      ← state_nodes с |rpe|>0.15 из tail(100)
  │                          → Pump между их content_touched парами
  │                          «эмоционально-насыщенные эпизоды пере-
  │                          обрабатываются поверх удивлявших нод»
  │
  ├─ 3. REM Creative       ← пары distinct(emb)<0.2 + BFS-path≥3
  │                          → manual_link. «Далёкие но близкие»:
  │                          ноды разных областей с одной семантикой
  │
  └─ 4. Consolidation      ← прунинг weak-old-orphans + архив
                             state_graph (>14d → .archive.jsonl)
```

Плюс независимо работают:
- **DMN 10m** — пробные pump без save, alerts если quality>0.5
- **state_walk 20m** — embed current state-sig → query_similar в state_graph
- **daily_briefing 24h** (после wake_hour) — утренний alert

---

## Три контура замкнутости

### Информационный
```
message → classify → execute (LLM + graph) → card → feedback → neurochem
   ↑                                                                ↓
   └────────── profile/state read при каждом запросе ──────────────┘
```

### Физиологический (UserState)
```
HRV coherence → user.serotonin
HRV stress → user.norepinephrine       ─┐
Accelerometer → user.activity_magnitude │
Feedback accept/reject → user.dopamine  ├─→ sync_error ‖user−sys‖ → regime
Timing gap → user.dopamine, valence     │                ↓
Decision cost → user.burnout, long     ─┘         advice + alert
                                         │
  + activity_zone (4 региона) ──────────┘── zone-specific alerts
    (recovery/stress_rest/healthy_load/overload)
```

### Диалоговый + uncertainty learning
```
профиль пуст в food → "что любишь/избегаешь?" → LLM-parse → profile append
       ↑                                                            │
       └────── следующий раз НЕ переспрашивает ──────────────────────┘
```

---

## Где что живёт (быстрый файл-index)

### Static
- [src/user_profile.py](../src/user_profile.py) — load/save, add_item, parse_category_answer. Файл: `user_profile.json`
- [src/goals_store.py](../src/goals_store.py) — add_goal/complete/abandon/replay/stats. Файл: `goals.jsonl`
- [src/solved_archive.py](../src/solved_archive.py) — archive_solved/load/list. Каталог per-workspace: `graphs/<ws>/solved/{ref}.json`

### Dynamic — состояние
- [src/user_state.py](../src/user_state.py) — UserState + Voronoi named_state + signed surprise + valence + dual-pool
- [src/neurochem.py](../src/neurochem.py) — Neurochem (D/S/NE) + ProtectiveFreeze + RPE
- [src/horizon.py](../src/horizon.py) — CognitiveState (композиция) + maturity drift + derived sync_error/regime
- [src/user_state_map.py](../src/user_state_map.py) — 10 Voronoi регионов

### Dynamic — работа
- [src/cognitive_loop.py](../src/cognitive_loop.py) — единый фон: DMN / state_walk / night_cycle / briefing + tick_foreground
- [src/tick_nand.py](../src/tick_nand.py) — NAND emergent tick: distinct-matrix → zones → action
- [src/meta_tick.py](../src/meta_tick.py) — 5 паттернов в state_graph tail → policy nudge / emit action
- [src/modes.py](../src/modes.py) — 14 modes как compact tuples + should_stop
- [src/assistant_exec.py](../src/assistant_exec.py) — execute_via_zones + profile_hint injection
- [src/assistant.py](../src/assistant.py) — /assist + classify cache + profile-aware flow + uncertainty trigger
- [src/graph_logic.py](../src/graph_logic.py) — граф + sample_in_embedding_space + _bayesian_update_distinct
- [src/state_graph.py](../src/state_graph.py) — append-only Git-аудит
- [src/consolidation.py](../src/consolidation.py) — прунинг + архив
- [src/cross_graph.py](../src/cross_graph.py) — seed_from_history
- [src/workspace.py](../src/workspace.py) — multi-graph
- [src/pump_logic.py](../src/pump_logic.py) — bridge между двумя идеями
- [src/hrv_manager.py](../src/hrv_manager.py), [src/hrv_metrics.py](../src/hrv_metrics.py) — HRV

### UI
- [templates/index.html](../templates/index.html) — всё UI
- [static/js/assistant.js](../static/js/assistant.js) — chat + panels
- [static/js/graph.js](../static/js/graph.js) — graph viz + autorun
- [static/js/modes.js](../static/js/modes.js) — selector
- [static/css/style.css](../static/css/style.css)

---

## Тонкие места

**1. Race conditions при параллельных /assist:** Сейчас не обработаны.
Два одновременных запроса могут дважды списать energy (хотя дедуп в
classify cache и `_log_decision` прощает).

**2. goals.jsonl не архивируется:** Растёт монотонно. Консолидация
archive-ит state_graph но не goals. Для лет-пользования нужна ротация.

**3. cognitive_loop.tick_foreground не шарит state с Flask thread:** 
Синхронно дергается в request-контексте. `_graph` — модульная
синглтон-переменная, Thread-safe только через locks; пока только
`graph_lock` в `_add_node`/`_remove_node`, остальное полагается на GIL.

**4. Embedding-кэш не персистится между рестартами:** Content-graph
embeddings в `_graph["embeddings"]` только в памяти. Текущая сессия —
всё пересчитывается при старте. State_graph embeddings — да, в
`state_embeddings.jsonl`.

**5. Profile learning через LLM-parse может галлюцинировать:**
`parse_category_answer` просит LLM выдать PREF: / AVOID: строки. Если
LLM промажет формат — fallback на простой split, может классифицировать
неточно. Юзер видит результат в 👤 и может вручную поправить.

**6. Multi-workspace + UserState:** UserState глобальный per-person.
При switch'е workspace UserState **не меняется** — это by design (HRV
один на человека), но preferences тоже одни (профиль не per-workspace).
Если нужны different preferences для work vs personal — сейчас не
поддерживается.

---

## Рекомендуемый cadence для чтения

| Когда | Что читать |
|-------|-----------|
| Первый раз | [README.md](../README.md) → [PITCH.md](PITCH.md) → этот файл |
| «Как думает Baddle?» | [tick-design.md](tick-design.md) → [nand-architecture.md](nand-architecture.md) |
| «Как адаптируется?» | [horizon-design.md](horizon-design.md) → [neurochem-design.md](neurochem-design.md) |
| «Как симбиоз с юзером?» | [symbiosis-design.md](symbiosis-design.md) → [user-model-design.md](user-model-design.md) |
| «Как помнит меня?» | [static-storage-design.md](static-storage-design.md) |
| «Как работает ночью?» | [consolidation-design.md](consolidation-design.md) + REM-блок в TODO |
| «Как находит инсайты?» | [pump-design.md](pump-design.md) + [meta-tick-design.md](meta-tick-design.md) |
| Before commit | `TODO.md` секция ⬇ СДЕЛАНО — там полный список проверяемых кейсов |

---

**Навигация:** [← Epilogue](epilogue.md)  ·  [Индекс](README.md)  ·  [Следующее: NAND архитектура →](nand-architecture.md)
