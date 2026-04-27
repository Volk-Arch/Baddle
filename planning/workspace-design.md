# Workspace — implementation план

> Концепция: [docs/workspace.md](../docs/workspace.md). Этот файл — sub-waves для cleanup-plan W14.

---

## Принцип реализации

Workspace = **scope над графом**, не отдельный store. Все операции графа (`distinct`, scout, SmartDC, consolidation) работают над workspace-нодами без изменений. Реализация:

1. Существующая нода графа получает поля `scope: "workspace" | "graph"`, `expires_at: float | None`.
2. Источники вызывают `workspace.add(...)` → создаётся нода с `scope="workspace"`.
3. Periodic processing: pump/SmartDC/consolidation запускаются над workspace-нодами при триггерах.
4. `workspace.select(now)` → convergence rule → top-K кандидатов.
5. `workspace.commit(selected)` → меняет `scope="graph"`, убирает `expires_at`. И push в chat history.

Этот подход **не требует** нового storage layer. Только новые поля на нодах + helper functions.

---

## Sub-waves W14

### W14.1 — Scope primitive (~3-4ч)

**Файл:** `src/workspace.py` (~150 LOC)

**Что:**
- `add(source, kind, text, urgency, expires_at=None, accumulate=False, dedup_key=None, metadata=None) -> int` — создаёт ноду графа с `scope="workspace"` через `record_action(actor="baddle", scope="workspace")`. Возвращает `node_idx`.
- `list_pending() -> list[node]` — все ноды с `scope == "workspace"` and `now < expires_at`.
- `select(now, max_emit=1) -> list[node]` — convergence rule (drop expired → immediate-flag → counter-wave penalty → budget → urgency-sort → top-K).
- `commit(node_indices) -> None` — меняет `scope="graph"`, удаляет `expires_at`, пишет в `data/chat_log.jsonl` для UI.

**Изменения в graph_logic.py:**
- `_make_node` принимает `scope` (default `"graph"`) и `expires_at` (default `None`).

**Tests:** unit tests на add/select/commit semantics, dedup, expiry, counter-wave penalty, budget.

**Identity:** существующие тесты должны остаться зелёными (workspace ortogonal к LTM operations).

### W14.2 — Migrate /assist reply через workspace (~1-2ч)

`/assist` route: текущий `record_action(actor="baddle", action_kind="assist_reply", ...)` → `workspace.add(source="assist_reply", accumulate=False, urgency=1.0)` → `workspace.commit(workspace.select(now))`.

User message — через `workspace.add(source="user_msg", accumulate=False, urgency=1.0)`. Один path для всего что попадает в chat history.

**Verify:** smoke test — один user message → один baddle reply в graph (как было). Identity sequence для chat flow.

### W14.3 — Migrate alerts через workspace (~2-3ч)

`Dispatcher.dispatch()` сейчас возвращает emitted Signals → `_add_alert(sig.content)`. Меняем: emitted → `workspace.add(source="alert", kind="alert", ...)`. UI читает alerts через `workspace.list_committed_recent(kind="alert")` или unified `/chat/recent`.

**Decision point:** оставить ли Dispatcher отдельно для UI overlay banners или полностью мигрировать. Решить по результатам W14.1 prototype — если workspace справляется с budget/dedup, Dispatcher может стать `dispatch() = workspace.add` wrapper.

### W14.4 — Migrate briefings + scout (~2-3ч)

- `_build_morning_briefing_text/sections` — результат → `workspace.add(source="brief_morning", accumulate=True, urgency=0.6, expires_at=now+3600)`.
- `_build_weekly_summary` — то же для `source="brief_weekly"`.
- Scout / dmn-bridge: в `_advance_tick` после нахождения значимого моста → `workspace.add(source="scout", accumulate=True, urgency=0.4, expires_at=now+3600)`.

После migration `_recent_bridges` deque может быть удалена — её роль переходит к workspace `scope="workspace"` filter.

### W14.5 — Cross-кандидатная обработка (~2-3ч)

**Trigger rule:** при `workspace.add()` если `count(scope="workspace", source=X)` > `THRESHOLD_SIMILAR_CANDIDATES` (default 3) — запустить cross-processing:

- 3+ sync_seeking similar по тону → `pump.scout(workspace_subset)` ищет общий паттерн → если bridge_quality > 0.5 → новый кандидат `source="insight_pattern"` с urgency = max(input urgencies) + 0.1, и старые помечаются `superseded_by`.
- 5+ observation_suggestion overlap by topic → `consolidation._collapse_cluster_to_node(workspace_subset)` → один summary-кандидат.
- 2+ alerts overlapping by time/topic → SmartDC выбирает один с резонансом к текущему `r.user.mode/balance`.

Это **активная обработка** между генерацией и broadcast'ом — главное отличие workspace от plain queue.

**Risk:** infinite loop (новый кандидат сам триггерит new processing). Защита — флаг `_synthesized_from` на новом кандидате, исключает из дальнейших cross-операций.

### W14.6 — Decompose assistant.py (~3-5ч)

После W14.2-4 `_assist` route и `_alerts` aggregator упрощаются. Time для split:

- `src/routes/chat.py` — /assist, /assist/feedback, /assist/state, /chat/recent (~700)
- `src/routes/goals.py` — все goals/recurring/constraints (~280)
- `src/routes/activity.py` — activity_log endpoints (~230)
- `src/routes/plans.py` (~130), `src/routes/checkins.py` (~70), `src/routes/profile.py` (~150)
- `src/routes/briefings.py` — morning/weekly (~330)
- `src/routes/misc.py` — sensors/patterns/debug/decompose (~250)

`assistant.py` 3105 → ~150 (Flask blueprint setup).

### W14.7 — Decompose cognitive_loop.py (~2-3ч)

После migration `_check_*` детекторов в workspace.add — _cognitive_loop сильно упрощается:

- `cognitive_loop.py` — main loop + `_advance_tick` (~1200, ужмётся ещё после W11 #2 NAND consolidation)
- `bookkeeping.py` — `_check_heartbeat` / `_check_graph_flush` / `_check_activity_cost` / `_check_cognitive_load_update` (~400)
- `briefings.py` — `_build_morning_briefing_*` / `_build_current_state_signature` (~500)

DMN/REM heavy work уже идёт в W11 #3 (`pump_logic` + `consolidation` → `dmn.py`).

### W14.8 — STM→LTM consolidation (NREM-like, ~2-3ч)

Ночной cycle (`consolidation.py`): прогон workspace-нод (`scope="workspace"`, expired или near-expiry):

- Если used in synthesis (referenced by another committed node) → promote: `scope="graph"`, `expires_at=None`. STM → LTM.
- Если accumulated supporting evidence (Beta-prior alpha+beta > threshold) → promote.
- Иначе expire/archive по hebbian-принципам как сейчас.

Это закрывает [TODO Backlog #11](TODO.md#пакет-память-и-pruning) «Оперативная vs долговременная память» — без новой подсистемы, через scope-promotion.

### W14.9 — LTM activation на user input (recall, ~2-3ч)

Дневной режим: при каждом user-message (и опционально на assist reply prep):

- Embedding нового message → similarity search через existing `nodes_near()` или embedding RAG.
- Top-K LTM-нод (filtered by `confidence > 0.6` AND `evidence_count > N` — «толстые идеи»).
- Каждая → `workspace.add(source="ltm_recall", scope="workspace", expires_at=now+15min, references=[ltm_node_idx])`.
- В `select()` они конкурируют с другими кандидатами; SmartDC может выбрать наиболее resonant с текущим состоянием.

**Note:** activation = создание reference-ноды, не копия. Оригинал в LTM не страдает.

**Reuse:** `execute_deep`-RAG логика подходит — частично переиспользовать.

**Risk:** thrashing если каждое user-message подтягивает 10 нод → workspace переполнен. Cap K=3, dedup с уже активными ltm_recall.

### W14.10 — REM scout deep bridges (ночь, ~3-4ч)

Ночной cycle (после W14.8): второй проход по полному графу:

- Берём **давно-неактивированные** ноды (`touched_at` старше N дней) — это «забытые» концепты.
- Берём **толстые активные** core-ноды (high confidence, frequent access).
- `pump_logic.scout()` ищет мосты между ними. Это **divergent generation** в самом графе.
- Найденные bridges с `bridge_quality > 0.5` → `workspace.add(source="rem_insight", scope="workspace", expires_at=next_morning+24h, urgency=0.5-0.7)`.
- Утренний briefing (W14.4) включает их как раздел «Ночные находки» если quality высокая.

Это закрывает [TODO Tier 2 «META-вопросы — ночная генерация что ты не заметил»](TODO.md). Естественное следствие workspace + scout-over-LTM.

**Performance:** REM scout — heavy embedding ops. Cap по времени или по количеству бandidates per night. Запускать только когда `_idle_multiplier > THRESHOLD` (юзер реально неактивен — не работает в 3 утра).

---

## Order и риски

**Порядок:** W14.1 → W14.2 (smallest scope) → W14.3-4 (parallel possible) → W14.5 (cross-processing) → W14.6-7 (decompose) → W14.8 (NREM consolidation) → W14.9 (LTM activation/recall) → W14.10 (REM deep bridges, after .8 для использования promoted ноды).

**Hot path:** workspace вызывается на каждом /assist + tick. Performance check после W14.1.

**Behaviour drift:** alerts могут задержаться на ~5s (буфер vs immediate). UX-наблюдение нужно. Есть `accumulate=False` flag для critical, default immediate для existing alerts.

**Rollback:** scope-flag — additive change. Если что-то ломается — `scope="graph"` works как сейчас, workspace-функции могут быть выключены через config.

---

## Open questions (из docs/workspace.md)

1. Workspace vs Dispatcher — раздельно или унифицировать. Решить после W14.1.
2. `accumulate` hardcoded vs `r.user.mode` driven.
3. Persistence overhead (jsonl на каждый add/select/commit).
4. Cross-processing trigger — counter-based или periodic.
5. Pruning policy для expired без commit.

---

## Estimate

Total ~22-30ч от prototype до полной миграции + decomposition + sleep cycles. Не одна сессия — sequence из 10 wave'ов с зелёным baseline после каждой.
