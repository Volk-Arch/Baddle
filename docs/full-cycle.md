# Полный цикл Baddle — статика + динамика

Остальные design-доки описывают **куски**. Этот — как всё собирается
в работающий организм. Читать если: хочешь понять что зачем, планируешь
фичу и не знаешь куда приземлять, или вернулся после паузы и нужен
быстрый re-boot.

---

## Два слоя

Всё что делает Baddle делится на **статику** (что ты есть и что уже
сделал) и **динамику** (как ты думаешь прямо сейчас).

```
┌─────────────────────────── СТАТИКА ───────────────────────────┐
│                                                                │
│  USER PROFILE           GOALS STORE          SOLVED ARCHIVE    │
│  (who you are)          (what you solve)     (how you solved)  │
│                                                                │
│  • 5 категорий          • append-only        • snapshot графа  │
│  • preferences          • create/done/abandon • state_trace    │
│  • constraints          • stats aggregate    • final_synthesis │
│  • context (wake/tz)                                           │
└────────────────────────────────────────────────────────────────┘
                              ▲ ▼
┌─────────────────────────── ДИНАМИКА ──────────────────────────┐
│                                                                │
│     USER STATE                   SYSTEM STATE                  │
│     (your now)                   (baddle's now)                │
│                                                                │
│  dopamine ← feedback         dopamine ← distinct(d) в tick     │
│  serotonin ← HRV coherence   serotonin ← ΔW стабильность       │
│  norepinephrine ← HRV stress norepinephrine ← H(W) энтропия    │
│  burnout ← rejects           freeze.accumulator ← d·(1−S)      │
│  valence ← feedback signed   recent_rpe ← |Δposterior|         │
│  expectation ← EMA reality   maturity ← verifieds++            │
│                     sync_error = ‖user − system‖ (3D)          │
│                                                                │
│  CONTENT GRAPH               COGNITIVE LOOP                    │
│  ноды с embedding'ами        DMN 10m / state_walk 20m /        │
│  distinct-matrix в тике      night cycle 24h / briefing 24h    │
│  NAND zones: CONFIRM/        tick_foreground при /graph/tick   │
│  EXPLORE/CONFLICT                                              │
│                                                                │
│  STATE GRAPH                 META-TICK                         │
│  append-only Git-аудит       паттерны в tail(20)               │
│  каждый tick → запись        stuck/rejection/rpe/monotony      │
└────────────────────────────────────────────────────────────────┘
```

**Статика** хранится и меняется редко (когда явно добавляешь / решаешь).
**Динамика** живёт каждую секунду — апдейт от сигналов, от тика, от
ночного цикла. Статика читается в LLM-промпт; динамика при резолве
пишется в статику (goals / archive), valence / neurochem реагируют на
feedback.

---

## Прайм-директива

Одна метрика меряет всё: **sync_error = ‖UserState − SystemState‖**
в 3D нейрохимическом пространстве (DA / S / NE). Фича оценивается по
одному критерию — снижает ли она рассинхрон с конкретным юзером.

Отсюда `sync_regime` ∈ {FLOW / REST / PROTECT / CONFESS} — адаптивное
поведение. Детали — [symbiosis-design.md](symbiosis-design.md). Полный
предиктивный контур — [friston-loop.md](friston-loop.md).

---

## Data flow: один запрос

Юзер пишет сообщение. Что происходит:

1. **/assist** эндпоинт регистрирует вход — `inject_ne(0.4)` поднимает
   arousal системы, `user.register_input()` обновляет timestamp,
   `user.update_from_energy` бьёт по burnout.

2. **Категория** (`_detect_category`) → food/work/health/... Если
   категория профиля пуста → **profile_clarify card** («что любишь /
   избегаешь?»), parse-ответ через LLM → `profile.food.preferences`
   append → auto-retry оригинала.

3. **Контекст сбирается:**
   - `recurring_ctx` — активные привычки/constraints
     («покушать 3/день, 1/3 сегодня, не орехи»)
   - `find_similar_solved` — RAG по архиву past syntheses, инжектится
     если sim ≥ 0.6

4. **Intent router** (2-level LLM) решает: `fact / activity / task /
   chat`. Fact и activity быстрые (~1.5с), task → intent_confirm
   card с draft и кнопками, chat → свободный LLM-ответ. Fallthrough
   — полный `classify_intent_llm` → `execute_deep`.

5. **Execute** через `execute_via_zones(message, mode, profile_hint)`:
   LLM генерит идеи учитывая constraints / recurring / RAG,
   distinct-matrix раскладывает на zones CONFIRM / EXPLORE / CONFLICT,
   `_render_card` рендерит (tournament / builder / dispute / ...).

6. **Post-check:** `scan_message_for_violations` — LLM проверяет
   нарушил ли юзер активные constraints. Если да → `record_violation` +
   constraint_violation card.

7. **`_log_decision`** списывает energy через `user.debit_energy(cost,
   daily_remaining)` — mode-weighted (tournament 12 energy, fan 3, ...).

8. Юзер жмёт 👎 → **/assist/feedback** → `cs.update_neurochem(d=0.8)` +
   `user.update_from_feedback("rejected")`. DA/valence падают, burnout
   растёт, streak bias учитывается. Следующий запрос — в новом
   sync_regime.

---

## Жизненный цикл goal'а

Статика и динамика работают вместе через весь путь цели:

1. **CREATE.** `POST /graph/add {node_type: "goal", mode, text}` →
   goals_store.add_goal (static), goal-нода в графе с subgoals (dynamic).

2. **PROCESS.** Юзер пишет, делает ticks, добавляет evidence. Confidence
   subgoals растёт через Bayes updates. Каждый tick → state_graph entry.
   `maturity` чуть-чуть ↑ при crossing 0.8.

3. **RESOLVE.** `should_stop() → True` (через distinct-зоны или
   convergence) → `cs.note_verified()` → `solved_archive.archive_solved()`
   → `goals_store.complete_goal(id, reason, snapshot_ref)` →
   `node._goal_completed = True` (идемпотентность).

4. **RETROSPECT.** UI 🎯 Goals → archive → клик → `load_solved(ref)`
   показывает goal_text + final_synthesis + nodes + state_trace.

Что осталось в live-графе — временно. Что в solved archive — permanent.

---

## Ночной цикл

Пока юзер спит, единый проход (раньше 3 параллельных):

1. **Scout Pump+Save** — новый bridge-мост между далёкими нодами
   (persisted как новая hypothesis)
2. **REM Emotional** — state_nodes с `|rpe| > 0.15` из tail(100) →
   Pump между их `content_touched` парами («эмоционально насыщенные
   эпизоды пере-обрабатываются поверх удивлявших нод»)
3. **REM Creative** — пары `distinct(emb) < 0.2` + BFS-path ≥ 3 →
   `manual_link`. «Далёкие но близкие» — ноды разных областей с одной
   семантикой
4. **Consolidation** — прунинг weak-old-orphans + архив state_graph
   (> 14d → `.archive.jsonl`)

Плюс независимо: **DMN 10м** (пробные pump без save, alerts если
quality > 0.5), **state_walk 20м** (query_similar в state_graph),
**daily_briefing 24ч** (утренний alert).

Подробно — [dmn-scout-design.md § Night cycle](dmn-scout-design.md#night-cycle--scout--rem--consolidation)
и [episodic-memory.md § Consolidation](episodic-memory.md#consolidation--забывание-как-фича).

---

## Три контура замкнутости

**Информационный:**
```
message → classify → execute (LLM + graph) → card → feedback → neurochem
   ↑                                                                ↓
   └────────── profile/state read при каждом запросе ──────────────┘
```

**Физиологический (UserState):** HRV coherence / stress → S / NE,
accelerometer → activity_magnitude, feedback → dopamine / valence,
timing → dopamine, decision cost → burnout / long_reserve. Итог —
`sync_error → regime` → advice + alert. Плюс `activity_zone` (4 региона)
→ zone-specific alerts.

**Диалоговый + uncertainty learning:**
```
профиль пуст в food → «что любишь?» → LLM-parse → profile append
   ↑                                                    │
   └──── следующий раз НЕ переспрашивает ───────────────┘
```

---

## Тонкие места

- **Race conditions при параллельных /assist** — не обработаны. Два
  одновременных запроса могут дважды списать energy (дедуп в classify
  cache прощает, но не гарантированно).
- **goals.jsonl растёт монотонно.** Consolidation архивит state_graph,
  но не goals. Для лет-пользования нужна ротация.
- **Embedding-кэш content-графа не персистится.** `_graph["embeddings"]`
  только в памяти, пересчитывается при старте. State_graph embeddings —
  да, в `state_embeddings.jsonl`.
- **Profile LLM-parse может галлюцинировать.** Fallback на split если
  LLM промажет формат. Юзер видит результат в 👤 и правит вручную.
- **Multi-workspace + UserState.** UserState глобальный per-person —
  переключение workspace его не меняет (HRV один на человека). Profile
  тоже один — разные preferences для work vs personal не поддерживаются.
- **cognitive_loop.tick_foreground** синхронный в request-контексте
  Flask. Thread-safety через `graph_lock` в `_add_node` / `_remove_node`,
  остальное полагается на GIL.

Реестр workstreams — [planning/TODO.md](../planning/TODO.md) и
[planning/TODO.md](../planning/TODO.md).

---

## Где что живёт

**Static:** `user_profile.py`, `goals_store.py`, `solved_archive.py` →
[static-storage-design.md](static-storage-design.md).

**Dynamic состояние:** `user_state.py` + `neurochem.py` + `horizon.py`
→ [symbiosis-design.md](symbiosis-design.md), [friston-loop.md](friston-loop.md).

**Dynamic работа:** `cognitive_loop.py` + `tick_nand.py` + `thinking.py`
+ `meta_tick.py` → [tick-design.md](tick-design.md),
[dmn-scout-design.md](dmn-scout-design.md), [episodic-memory.md](episodic-memory.md).

**Операции:** `pump_logic.py` + SmartDC + embedding-first →
[thinking-operations.md](thinking-operations.md).

**Graph / persistence:** `graph_logic.py` + `state_graph.py` +
`consolidation.py` + `workspace.py` + `cross_graph.py` →
[nand-architecture.md](nand-architecture.md), [episodic-memory.md](episodic-memory.md),
[workspace-design.md](workspace-design.md).

**UI:** `templates/index.html`, `static/js/{assistant,graph,modes}.js`,
`static/css/style.css`.

Schemas всех data-файлов — [ontology.md](ontology.md).

---

**Навигация:** [← Life Assistant](life-assistant-design.md) · [Индекс](README.md) · [Следующее: NAND архитектура →](nand-architecture.md)
