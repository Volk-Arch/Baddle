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

DMN/REM heavy work остаётся отдельно: `pump.py` (mental operator, day+night) и `consolidation.py` (night housekeeping) — разная семантика, склейка отвергнута. См. cleanup-plan W11 #3.

### W14.8 — Sequential integration (NREM + emergent REM в одном проходе, ~3-4ч)

**Ключевая переработка:** не два отдельных cycle'а (NREM consolidation + REM scout), а **один пошаговый pass** по workspace-нодам где insight'ы **emerge** из integration. Дешевле и точнее resonance с memory replay в hippocampus.

`consolidation.py` ночной cycle (когда `_idle_multiplier > threshold`):

```python
for node N in workspace_nodes_by_quality_desc():
    if quality(N) < threshold:
        archive(N); continue

    similar = nodes_near(N.embedding, k=20)  # один cheap call

    if similar[0].distance < MERGE_TH:
        # Близкое — merge: link N → similar[0], increment evidence
        link(N, similar[0]); evidence_bump(similar[0])
        # N не promotes сама — она УСИЛИВАЕТ existing
    else:
        mid = [s for s in similar if MERGE_TH <= s.distance < RELATED_TH]
        if mid:
            # ← Insight emergence: mid-distance связи которых ещё нет
            for m in mid:
                if not edge_exists(N, m):
                    add_edge(N, m, type="related_to")
            if very_resonant(N, mid):
                # отдельный insight-кандидат для morning briefing
                workspace.add(source="overnight_insight",
                              expires_at=next_morning+24h,
                              references=[N.idx, *[m.idx for m in mid]])

        promote(N)  # scope="graph", expires_at=None

    # Process noted queries (если N имела "need context: X")
    for query in N.notes.get("queries", []):
        answer = nodes_near(query.embedding, k=3)
        workspace.add(source="ltm_recall_overnight",
                      expires_at=next_morning+12h,
                      content=answer)
```

Это закрывает:
- [TODO Backlog #11](TODO.md#пакет-память-и-pruning) — STM/LTM transfer через scope mutation
- [TODO Backlog #12](TODO.md#пакет-память-и-pruning) — pruning через merge-on-close
- [TODO Tier 2 «META-вопросы — ночная генерация»](TODO.md) — emergent insight'ы из mid-distance edges

Без отдельного REM scout — он **не нужен** как separate pass. Insight'ы emerge естественно при integration.

**Performance:** один embedding search per workspace-нода. Если 50 нод за день → 50 cheap searches за ночь. В отличие от broad scout по всему графу — это **много дешевле**.

**Cap:** time budget на ночной pass (например max 5 мин). Если workspace переполнен — приоритет high-quality нод, остальные archive с меткой "недо-integrated".

### W14.10 — Cross-batch REM scout (~2-3ч)

После W14.8 phase 1 (integration), Phase 2: scout между сегодняшними promoted-нодами + random sample давнего LTM.

```python
today_batch = nodes_promoted_this_night  # из W14.8

# pairs внутри batch — cross-batch insight'ы
for pair in random.sample(combinations(today_batch, 2), k=N):
    bridge = pump.scout(pair[0], pair[1])
    if bridge and bridge.quality > 0.5:
        workspace.add(source="cross_batch_insight",
                      expires_at=next_morning + 24h,
                      references=list(pair))

# pairs (today_batch × random_old_LTM) — remote associations (REM-style)
random_old = sample(old_ltm_nodes, k=N, where=touched_at_old)
for new in today_batch:
    for old in random_old[:3]:
        bridge = pump.scout(new, old)
        if bridge and bridge.quality > 0.6:
            workspace.add(source="remote_association",
                          expires_at=next_morning + 24h,
                          references=[new, old])
```

**Почему** Phase 1 этого не делает: в W14.8 каждая новая нода смотрит в LTM (close + mid-distance), но **между сегодняшними** новыми связи не ищутся. И связи к **давно неактивированным** old LTM (low touched_at) тоже пропускаются — sequential search идёт через `nodes_near` который typically returns recent.

**Cap:** total time budget на Phase 2 (например 2 мин), max N pairs.

**Performance:** небольшой today_batch (типично 10-30 нод за день) × small old_sample → manageable. Heavy ops только на found bridges.

### W14.11 — Synaptic homeostasis (~1-2ч) — **astrocyte-pattern**

Параллель с biological astrocytes (queue.txt 2026-04-28): non-neuronal слой который делает housekeeping ночью (глимфатическая clean-up). У нас — **отдельный async loop** от main cognitive_loop, не блокирует neurons-слой. Investigate возможность разделить:
- **Neurons слой** = main cognitive_loop tick + workspace integration
- **Glia слой** = ночной homeostasis + REM scout (W14.10) + cleanup

Это даёт architectural separation of concerns: active processing vs passive maintenance. Не обязательно сейчас (W14.11 как single function работает), но research note для будущей декомпозиции.

После W14.10 Phase 3: global rebalancing confidence на LTM.

```python
DECAY_FACTOR = 0.95          # ночное ослабление
RESTORE_TOUCHED = 1 / 0.95   # для touched_today nodes — net stable
ARCHIVE_THRESHOLD = 0.05      # ниже этой confidence → archive

for node in graph.nodes:
    if node.scope != "graph":
        continue
    node.confidence *= DECAY_FACTOR
    if node.touched_today or node.committed_today:
        node.confidence *= RESTORE_TOUCHED
    if node.confidence < ARCHIVE_THRESHOLD:
        archive(node)  # soft delete или move в data/archive/
```

**Эффект:** rarely-touched ноды медленно decay → archive когда падают ниже threshold. Frequently-touched стабильны (decay × restore ≈ 1.0). Это **предотвращает раздувание графа** при долгой работе.

**Связь с existing hebbian decay** в `consolidation.py` — там per-node, on read access. Synaptic homeostasis — **batch global** на ВСЕХ LTM, один раз за ночь. Дополняет, не замещает.

**Calibration:** DECAY_FACTOR подобрать под use rate. 0.95/night = ~half-life 14 дней для untouched нод. Если archive слишком агрессивный — поднять до 0.97. Adjusting через 1-2 мес use наблюдений.

**Risk:** confidence потеряет ground truth если decay incorrectly tuned. Mitigation: сохранять `confidence_at_promote` отдельным полем, чтобы можно было восстановить если decay over-aggressive.

### W14.9 — Lazy LTM recall queue (~1-2ч)

Дневной режим: workspace **не делает** broad recall на каждое user-message. Hot path остаётся cheap (in-memory operations only).

Вместо — **opt-in note pattern**:
- При commit committed-нода может содержать `notes.queries: ["need context about X"]` если LLM-response generation определила что нужен deep context, но не получила (saved tokens).
- Эти queries — input для ночного W14.8 processing.
- Утром answers ждут в workspace (`source="ltm_recall_overnight"`).

**Где вешаются queries:**
- `execute_deep` если max_tokens cap'нул retrieval — note query.
- assist response с low-confidence (LLM «не уверен» по surprise indicator) — note.
- explicit user request «уточни X в моих заметках» — синхронный recall (только этот case делает immediate LTM hit).

Это превращает дневной режим в **request pattern**: «нужно бы это» → ночью отвечается. Хот-path остаётся для bayesian + chem updates без RAG overhead.

**Risk:** delayed response — если юзер ждёт context immediate, а получит утром. Mitigation: explicit queries (`/assist/recall?q=...`) делаются immediate.

---

## Order и риски

**Порядок:** W14.1 → W14.2 (smallest scope) → W14.3-4 (parallel possible) → W14.5 (cross-processing in workspace) → W14.6-7 (decompose) → W14.9 (lazy queue infra) → W14.8 (sequential integration phase 1) → W14.10 (cross-batch + remote associations phase 2) → W14.11 (synaptic homeostasis phase 3).

**Биологические параллели:**
- W14.8 ≈ NREM consolidation + memory replay (Wilson & McNaughton 1994)
- W14.10 ≈ REM remote associations (Walker, Stickgold)
- W14.11 ≈ Synaptic homeostasis (Tononi & Cirelli 2014)

Эти параллели описательные — не цель имитировать мозг. Просто оказывается что resource allocation для LLM-based system с asymmetric workloads совпадает с тем что природа нашла за миллионы лет.

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

Total ~22-30ч от prototype до полной миграции + decomposition + 3-фазный sleep cycle. Не одна сессия — sequence из 11 wave'ов с зелёным baseline после каждой.
