# Workspace — implementation план

> Концепция: [docs/workspace.md](../docs/workspace.md). Этот файл — sub-waves для cleanup-plan W14.
>
> **Sync 2026-04-29:** документ обновлён под реальную имплементацию W14.1-6.
> Изменения от изначального плана документированы inline (помечены **«Реальность:»**).

---

## Принцип реализации

Workspace = **scope над графом**, не отдельный store. Все операции графа (`distinct`, scout, SmartDC, consolidation) работают над workspace-нодами без изменений. Реализация:

1. Существующая нода графа получает поля `scope: "workspace" | "graph" | "archived"`, `expires_at: float | None`.
2. Источники вызывают `workspace.add(...)` или `workspace.record_committed(...)` → создаётся нода с `scope="workspace"` либо `scope="graph"` соответственно.
3. Periodic processing: `_check_workspace_select` (5 мин) делает select+commit accumulating нод; `_check_workspace_cleanup` (10 мин) archives expired.
4. `workspace.select(now)` → convergence rule → idxs для commit.
5. `workspace.commit(selected)` → меняет `scope="graph"`, убирает `expires_at`. UI читает committed actions через `list_recent_alerts`/`list_recent_bridges` graph queries (не отдельный chat_log.jsonl). См. **«Реальность W14.1»** ниже.

Этот подход **не требует** нового storage layer. Только новые поля на нодах + helper functions.

---

## Sub-waves W14

### W14.1 — Scope primitive (~3-4ч) ✅ done 2026-04-29

**Файл:** `src/memory/workspace.py` (~280 LOC; W18 Phase 4 место `src/memory/`, не корень).

**Что:**
- `add(actor, action_kind, text, urgency=0.5, ttl_seconds=3600, accumulate=True, dedup_key=None, context=None, extras=None) -> int` — создаёт ноду графа с `scope="workspace"` через `record_action(actor=..., scope="workspace")`. Returns `node_idx`.
- `record_committed(actor, action_kind, text, urgency, accumulate=False, ...) -> Optional[int]` — convenience helper: `add() + commit() + log on failure`. Используется для immediate publication (chat msgs / alerts / briefings / bridges).
- `list_pending(now=None) -> list[node]` — все ноды с `scope == "workspace"` and `now < expires_at`.
- `select(now, max_emit=1) -> list[int]` — convergence rule (drop expired → immediate path для `accumulate=False` → counter-wave penalty `−0.3` для PUSH_KINDS при mode=`C` → urgency-sort → top-K).
- `commit(node_indices) -> int` — меняет `scope="graph"`, удаляет `expires_at`, ставит `committed_at=now`. Returns count.
- `archive_expired(now=None) -> int` — workspace ноды с истёкшим TTL → `scope="archived"` (для post-hoc analysis).
- `synthesize_similar(node_idxs) -> Optional[int]` + `_maybe_cross_process(action_kind)` — cross-processing trigger (W14.5).
- `list_recent_alerts(since_ts) -> list[node]` — UI poll path graph query (committed `actor='baddle'` + `severity` field + `committed_at > since_ts`).
- `list_recent_bridges(since_ts=0, limit=10) -> list[node]` — committed bridge actions (`action_kind` ∈ `BRIDGE_KINDS`).

**Реальность W14.1 — отклонения от изначального плана:**

1. **`source` field → `action_kind` taxonomy.** Изначально планировался отдельный `source` field как идентификатор источника. Заменён на единую Action Memory taxonomy (`action_kind`): user_chat / baddle_reply / brief_morning / sync_seeking / observation_suggestion / dmn_bridge / scout_bridge / etc. Согласует с Правилом 6 architecture-rules — единый taxonomy событий, без parallel taxonomy.

2. **`actor` расширен с `"baddle"` до `"baddle"|"user"`.** Изначально workspace был только для baddle-side decisions. Реальность: `user_chat` тоже идёт через workspace для cross-processing над user msgs (3 повторяющихся sync_seeking → один insight). Single path для всех events.

3. **`commit()` не пишет в `data/chat_log.jsonl`.** Изначально планировался parallel jsonl для UI delivery. Реальность: scope mutation only — UI читает committed actions через `list_recent_alerts` graph query. Single source of truth (graph). Параллельный `chat_history.jsonl` остаётся для UI presentation (msg + card formatting), но это complementary layer (не event substrate). См. [src/chat_history.py module docstring](../src/chat_history.py).

4. **Bonus: `archive_expired()` + periodic check.** Не было в изначальном плане; добавлен для предотвращения накопления expired workspace nodes. `_check_workspace_cleanup` (10 мин) в cognitive_loop tick.

**Изменения в graph_logic.py:**
- `_make_node` принимает `scope` (default `"graph"`) и `expires_at` (default `None`).
- `record_action` принимает `scope` + `expires_at` kwargs.
- `_ensure_node_fields` добавляет `setdefault("scope", "graph")` + `setdefault("expires_at", None)` для legacy nodes.

**Tests:** 23 unit tests в `tests/test_workspace.py` (add/dedup/list_pending/select-immediate/select-top-K/select-expired/counter-wave on+off/commit/archive_expired/record_committed/synthesize/cross-process trigger/list_recent_alerts/list_recent_bridges/severity inheritance).

**Identity:** существующие тесты остались зелёными (492 → 535 после полного W14).

### W14.2 — Migrate /assist reply через workspace (~1-2ч) ✅ done 2026-04-29

`/assist` route: `workspace.record_committed(actor="baddle", action_kind="baddle_reply", ...)` (immediate) + `link_chat_continuation(idx)`.

User message: `workspace.record_committed(actor="user", action_kind="user_chat", context={sentiment, ...}, ...)` (immediate). Один path для всего что попадает в граф+chat.

**Реальность:** оба пути используют helper `record_committed` (W14 cleanup commit), который consolidates 5 callsites одного паттерна `add+commit+log`. См. cleanup-plan W14 cleanup.

**Verify:** identity 492 passed после migration.

### W14.3 — Migrate alerts через workspace (~2-3ч) ✅ done 2026-04-29 + W14.5c-state

`Dispatcher.dispatch()` returns emitted Signals → `_emit_alert(sig, now)` helper в cognitive_loop. Helper splits на два path по `sig.accumulating: bool`:

- **Accumulating** (`sig.accumulating=True`): `workspace.add(accumulate=True)`, без commit. Накапливается, проходит cross-processing если 3+ similar. Periodic `_check_workspace_select` (5 мин) делает emission через convergence rule.
- **Immediate** (default): `workspace.record_committed(accumulate=False)` — для critical alerts (capacity_red, regime_protect, plan_reminder) + state indicators (regime/capacity/coherence/zone, добавлены в W14.5c-state).

UI читает alerts через `workspace.list_recent_alerts(since_ts)` с in-memory cursor `loop._last_alerts_poll_ts`.

**Реальность — Decision point closure:** Dispatcher **сохранён** как pre-emit gate. Hybrid: Dispatcher применяет counter-wave/budget/dedup/expired для non-accumulating; accumulating bypass'ят counter-wave+budget (workspace.select их применяет на pending), но keep dedup+expired в Dispatcher (window-based dedup для всех Signals).

Computed-on-the-fly блок в `/assist/alerts` (regime/capacity/zone из current state) **удалён** в W14.5c-state — заменён 3 detector'ами (`detect_regime_state`/`detect_capacity_red_state`/`detect_activity_zone`). Все события идут через единый detector → Signal → Dispatcher → workspace path.

`_alerts_queue` + `_add_alert` + `get_alerts` **удалены** в W14.5c-2.

### W14.4 — Migrate briefings + bridges (~2-3ч) ✅ done 2026-04-29 + W14.5c-3

**Briefings: immediate publication, не accumulating.**

- `_build_morning_briefing_text/sections` — результат → `workspace.record_committed(actor="baddle", action_kind="brief_morning", accumulate=False, ttl=24h, extras={sections_count, recovery_pct, lang})`.
- `_build_weekly_summary` — то же для `action_kind="brief_weekly"` с `ttl=7d`.

**Реальность — отклонение от изначального плана:** изначально `accumulate=True, urgency=0.6, ttl=3600` (накопление через select cycle). Реальность — `accumulate=False, immediate commit`. Briefings = explicit user POST request (`/assist/morning`, `/assist/weekly`), не background generation. Если позже добавим auto-morning brief (system-initiated без user request) — вернёмся к accumulate=True path.

**Scout/DMN bridges: immediate, не accumulating.**

- `_record_baddle_action(action_kind in BRIDGE_KINDS, text, extras={quality, source})` через `workspace.record_committed`. Используется в night cycle scout, DMN deep research, converge loop pump, converge forced synthesis, DMN tick — 5 sites unified в W14.5c-3.

**Реальность — отклонение:** изначально `accumulate=True, urgency=0.4, ttl=3600` (накопление мостов в workspace, выбор через select). Реальность: bridges produced через explicit `pump.scout` calls (не detector chain) — нет потока кандидатов для накопления. immediate commit adequate.

**TODO для W14.5+:** если в будущем bridges станут потоком (например background scout каждые 5 мин с регулярными hits), переключить на accumulating path. Это разблокирует cross-processing над bridges (3 similar bridges → 1 synthesized insight). Сейчас пропуск.

**`_recent_bridges` deque удалена** в W14.5c-3 — replaced by `workspace.list_recent_bridges` graph query. 3 missing `_record_baddle_action` calls (DMN deep / converge_pump / converge_synthesis) добавлены — все bridges теперь в graph.

### W14.5 — Cross-кандидатная обработка (~2-3ч) ✅ done 2026-04-29 (a/b/c)

**Trigger rule:** при `workspace.add()` (только если `accumulate=True`) если `count(scope="workspace", action_kind=X)` ≥ `THRESHOLD_SIMILAR_CANDIDATES=3` — запустить `_maybe_cross_process(action_kind)` → `synthesize_similar(node_idxs)`.

**Реальность — отклонение от изначального плана:**

Изначально per-kind strategies:
- 3+ sync_seeking similar по тону → `pump.scout(workspace_subset)` → bridge_quality > 0.5 → `source="insight_pattern"`
- 5+ observation_suggestion overlap by topic → `consolidation._collapse_cluster_to_node(workspace_subset)` → summary
- 2+ alerts overlapping by time/topic → SmartDC выбирает резонансный

Реальность — **generic `synthesize_similar`** для всех kinds (text concatenation, urgency=max+0.1, severity inherited из first source). Per-kind strategies **deferred to W14.5+**: будущая работа добавит pump.scout/collapse/SmartDC переключение по `action_kind`. Сейчас generic text-aggregation — proof-of-concept.

**Loop protection** через `synthesized_from` + `superseded_by` filters в `_maybe_cross_process` candidates pool. Recursion невозможна.

**Real source:** `observation_suggestion` (W14.5b) — единственный accumulating source сейчас (через `Signal.accumulating=True`). Остальные idle до switch on accumulating path.

**TODO для W14.5+:**
1. LLM-based synthesis (pump.scout/collapse/SmartDC) per `action_kind`.
2. Bridges accumulating (если bridges станут потоком, см. W14.4).
3. Дополнительные accumulating sources (sync_seeking accumulating + cross_process).

### W14.6 — Decompose assistant.py (~3-5ч) ✅ done 2026-04-29

**Реальность:** `assistant.py` 2964 → 55 LOC (bootstrap-shell). Better than spec target ~150. Split на 8 routes модулей в `src/io/routes/` (W18 Phase 4 ontology placement, не корень `src/routes/`):

- `src/io/routes/chat.py` (1163 LOC) — /assist + classify_intent + 4 fastpaths + /assist/{state,feedback,camera,status,history,prime-directive,bookmark} + /assist/chat/{history,append,clear} + /loop/{start,stop,status}
- `src/io/routes/goals.py` (238 LOC) — все /goals/* + /goals/solved/* + _push_event_to_chat helper
- `src/io/routes/activity.py` (246 LOC) — /activity/* + _sync_activity_to_graph
- `src/io/routes/plans.py` (110 LOC) — /plan/*
- `src/io/routes/checkins.py` (84 LOC) — /checkin/*
- `src/io/routes/profile.py` (79 LOC) — /profile/*
- `src/io/routes/briefings.py` (419 LOC) — /assist/morning + /assist/weekly + /assist/alerts
- `src/io/routes/misc.py` (475 LOC) — /patterns/* + /sensor/* + /debug/* + /assist/decompose + /graph/assist

Plus `src/io/state.py` (273 LOC) — state helpers (extracted из assistant.py 47-247): _load_state/_save_state/_get_context/_capacity_reason_text/_today_date/_ensure_daily_reset/_log_decision/_response_for_mode/_detect_category/_push_event_to_chat.

`assistant.py` остаётся как **bootstrap shell** (55 LOC): re-exports из io.state + import io.routes (trigger blueprint registration). Backward-compat для `from src.assistant import _load_state` (cognitive_loop / detectors / assistant_exec / tests) + `from src.assistant import assistant_bp` (ui.py) + `from src.assistant import get_hrv_manager` (test_capacity monkeypatch).

### W14.7 — Decompose cognitive_loop.py (~2-3ч) ⏳ pending

После migration `_check_*` детекторов в workspace.add — _cognitive_loop сильно упрощается:

- `cognitive_loop.py` — main loop + `_advance_tick` (~1200, ужмётся ещё после W11 #2 NAND consolidation)
- `bookkeeping.py` — `_check_heartbeat` / `_check_graph_flush` / `_check_activity_cost` / `_check_cognitive_load_update` (~400)
- `briefings.py` — `_build_morning_briefing_*` / `_build_current_state_signature` (~500)

DMN/REM heavy work остаётся отдельно: `pump.py` (mental operator, day+night) и `consolidation.py` (night housekeeping) — разная семантика, склейка отвергнута. См. cleanup-plan W11 #3.

**Pending state:** `process/cognitive_loop.py` ещё ~2670 LOC. После W14.7 → ~1200 main + 400 bookkeeping + 500 briefings.

### W14.8 — Sequential integration (NREM + emergent REM в одном проходе, ~3-4ч) ⏳ pending

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

### W14.10 — Cross-batch REM scout (~2-3ч) ⏳ pending

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

### W14.11 — Synaptic homeostasis (~1-2ч) — **astrocyte-pattern** ⏳ pending

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

### W14.9 — Lazy LTM recall queue (~1-2ч) ⏳ pending

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
