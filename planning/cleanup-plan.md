# Cleanup plan v2

> После B5 (Track B closed) — следующий раунд. Не feature-burst, а полировка. Без `python -m pytest` зелёного — каждый Wave не стартует.
>
> Связь: [TODO.md](TODO.md), [../docs/architecture-rules.md](../docs/architecture-rules.md), [../docs/rgk-spec.md](../docs/rgk-spec.md), [../examples/ontology-v3.json](../examples/ontology-v3.json).

---

## 🎯 Следующий шаг: W14 + W18 Phase 4 закрыты — выбор waves

**Что закрылось 2026-04-29 (long session, 22 commits):**
- W18 Phase 1 (substrate/ + process/) — два commit
- W14 целиком (1-6): primitive → chat → alerts → briefings → cross-processing →
  state-indicator detectors → split assistant.py
- W18 Phase 4 (io/) — assistant.py 2964 → 55 LOC (bootstrap-shell), 9 routes
  модулей в `src/io/`

535 passed, pyflakes 0. Workspace primitive — единственный path для events
системы → committed graph actions → UI читает graph queries.

**Что дальше** — wave-by-wave впадание продолжается:

- **W14.7 cognitive_loop split** (~2-3ч) — `process/bookkeeping.py` + briefings extract из cognitive_loop. Файл всё ещё ~2670 LOC.
- **W14.8-11 ночной cycle** (~6-9ч итого) — sequential integration NREM (8) + lazy queue (9) + cross-batch REM (10) + synaptic homeostasis (11). Расширения над workspace primitive.
- **W12 part Б jsonl_store primitive** (~1-2ч) → `src/storage/jsonl_store.py`. Лечит дубликат API в goals_store/plans/recurring/activity_log.
- **W16.2 Analogy injection** (~3-3.5ч) → `src/transfer/analogies.py`.
- **W15 Power formula** (~16-22ч) → `src/capacity/power.py`. Большая, не блокер.
- **W11 #5 chat package** (low) → подразумевается W14.6 закрыло путём split на io/routes/chat.py.

**Не tech debt (decided 2026-04-29):**
- `chat_history.py` — UI persistence layer, complementary с workspace. Не trim.
  См. [src/chat_history.py module docstring](../src/chat_history.py).
  Если позже захотим trim — детальный план в
  [planning/chat-history-trim-plan.md](chat-history-trim-plan.md) (~2-2.5ч).

См. подробности в [W18](#w18--file-structure-as-ontology-mirror-meta-wave-ontology-derived) ниже.

---

## Done log

Закрытые waves (детали — git log + memory snapshots):

| Wave | Дата | Кратко |
|---|---|---|
| ✅ W6 quick win | 2026-04-28 | Полный route audit (104 routes); удалены 6 dead routes (`/assist/health`, `/assist/named-states`, `/goals/update`, `/plan`, `/plan/update`, `/suggestions/pending`) + ранее `/assist/chemistry`. Pattern: generic CRUD endpoints мертвы, UI хочет specific actions. Investigate-tier 13 routes ждут W14.1 — см. ниже. |
| ✅ W7 | 2026-04-28 | Docs sync — упоминания удалённых `Neurochem` / `ProtectiveFreeze` / `THETA_*` обновлены на `_rgk` substrate (11 docs files). |
| ✅ W9 | 2026-04-28 | 4 fallback polish: `float(None)` защита, silent `except: pass` → log.debug, `compute_sync_regime` упрощён, `inject_ne(0.4)` vs `(0.3)` mismatch verified intentional. |
| ✅ W11 #1 | 2026-04-28 | `surprise_detector.py` (401 LOC) → `detectors.py` после DETECTORS registry. Module docstring: два контракта в файле. |
| ✅ W11 #2 | 2026-04-28 | NAND triplet (`tick_nand` + `thinking` + `meta_tick` = 857 LOC) → `nand.py` 867 LOC. Удалено generic-имя `thinking.py`. |
| ❌ W11 #3 | 2026-04-28 | DMN склейка `pump_logic + consolidation → dmn.py` отвергнута. Игорь увидел до commit'а: pump = mental operator, consolidation = housekeeping, склейка = false equivalence. Вместо: `pump_logic.py` → `pump.py` rename. |
| ✅ W11 #4 | 2026-04-28 | HRV/sensor quartet (862 LOC) → `src/sensors/{manager,metrics,stream,adapters}.py` package через `git mv`. |
| ✅ W16.1a | 2026-04-28 | `sync_error_wave(rgk)` per axis amplitude breakdown + max_axis + scalar_5d. UI live `Δ AXIS value` indicator. |
| ✅ W16.1 5D | 2026-04-28 | Clean break 3D → 5D (`Resonator.vector()` 5 axes), threshold scale-up под max=√5, identity preserved для скаляров. UI radar pentagon (5 chem-axes). |
| ✅ W16.1b | 2026-04-28 | Phase-aware comparison: `РГК.phase_per_axis()` + поле `phases` в wave. Lazy `_phase_snapshot` (min-age 30s). Per-axis user_velocity / system_velocity / mismatch (signs opposite AND > noise 0.005/s). |
| ✅ W17 | 2026-04-28 | bio_physics compound naming в API: `dopamine` → `dopamine_gain`, etc. (5 axes). 67 src + 6 JS + 21 tests + 9 docs. Self-documenting API. |
| ✅ W18 Phase 1.1 | 2026-04-29 | `src/substrate/` — `git mv` rgk + horizon + user_state. `__init__.py` с re-exports public API. 19 outside-call-sites обновлены массово (`from .rgk` → `from .substrate.rgk`); внутри substrate/ relative imports на siblings сохранены, к outside-substrate (ema, modes, user_state_map, user_dynamics) подняты на `..`. Identity 492 passed. |
| ✅ W18 Phase 1.2 | 2026-04-29 | `src/process/` — `git mv` nand + detectors + signals + cognitive_loop + pump + consolidation. `__init__.py` (Signal, Dispatcher, DETECTORS, tick_emergent, pump, CognitiveLoop). 10 outside files + tests обновлены. consolidation остаётся в process/ — после W14.1 переоценить границу process/memory. Identity 492 passed. |
| ✅ W14.1 | 2026-04-29 | Workspace primitive: `src/memory/workspace.py` (add/list_pending/select/commit/archive_expired). `_make_node` + `record_action` расширены kwargs scope/expires_at. 16 unit tests. Identity 492 → 508. |
| ✅ W14.2 | 2026-04-29 | Chat msgs (user_chat + baddle_reply) через workspace.add+commit в /assist + /assist/chat/append. |
| ✅ W14.3 | 2026-04-29 | Alerts через workspace: extract `_emit_alert(sig, now)` helper, dispatched Signals → workspace.record_committed (+ legacy queue mirror, удалён в W14.5c). |
| ✅ W14.4 | 2026-04-29 | Briefings (morning + weekly) через workspace.record_committed. action_kind `brief_morning`/`brief_weekly` с TTL 24h/7d. |
| ✅ W14 cleanup | 2026-04-29 | DRY helper `record_committed` (5 callsite consolidation), periodic `_check_workspace_cleanup` (archive_expired каждые 10 мин), `link_chat_continuation` scope filter (skip archived). |
| ✅ W14.5a | 2026-04-29 | Cross-processing infrastructure: `synthesize_similar` + auto-trigger в add() при 3+ similar accumulating, loop-protection через `synthesized_from`/`superseded_by`. |
| ✅ W14.5b | 2026-04-29 | observation_suggestion первый real accumulating source. _emit_alert split на immediate/accumulating. WORKSPACE_SELECT_INTERVAL=300 + `_check_workspace_select`. |
| ✅ W14.5c | 2026-04-29 | Full Dispatcher↔Workspace integration: Signal.accumulating field (Dispatcher pass-through, fix double counter-wave); удалены _alerts_queue + _add_alert + get_alerts (UI читает graph через `workspace.list_recent_alerts`); удалён _recent_bridges deque (replaced by `workspace.list_recent_bridges` + 3 missing _record_baddle_action calls). _record_baddle_action унифицирован через record_committed. Synthesized severity inheritance. 4 commits (a/b/c/final). 492 → 526 passed. |
| ✅ W14.5c-state | 2026-04-29 | State-indicator detectors закрыли «design choice» угол: regime/capacity/coherence/zone alerts теперь идут через единый Signal → Dispatcher → workspace path вместо computed-on-the-fly блока в `/assist/alerts`. 3 новых детектора (`detect_regime_state` / `detect_capacity_red_state` / `detect_activity_zone`). DETECTORS list 13 → 16. 526 → 535 passed. |
| ✅ W14.6 split | 2026-04-29 | assistant.py 2964 → 55 LOC (−2909, bootstrap-shell). 7 commits (a/b1/b2-3/b4/b5/c). Routes split на 8 модулей `src/io/routes/`: chat.py 1163, briefings.py 419, misc.py 475, activity.py 246, goals.py 238, plans.py 110, checkins.py 84, profile.py 79. State helpers extract в `src/io/state.py` 273. Backward-compat re-exports через assistant.py. Identity 535 passed. W18 Phase 4 (io/) закрыта. |

---

## Active waves

### W6 — Routes investigate-tier (после W14.1)

Workspace primitive (W14.1+) станет промежуточным хранилищем: компоненты пишут туда → единый append в чат. Часть routes ниже либо мигрируют под workspace API, либо станут не нужны:

| Route | Status | Причина оставить пока |
|---|---|---|
| `/assist/history` | DEAD | Time-series snapshots — может пригодиться для timeline после workspace |
| `/goals/solved/<ref>` | DEAD | Single snapshot retrieval — для UI deep-link на завершённую цель |
| `/patterns`, `/patterns/run` | DEAD | Legacy weekday×category pattern-detector (pre-Signal) — оценить после workspace |
| `/sensor/readings`, `/sensor/aggregate` | DEAD | Wire когда реальные адаптеры (Polar/AppleWatch) появятся |
| `/graph/self`, `/graph/self/similar` | DEAD | History-as-graph view — может уйти в workspace timeline |
| `/graph/actions-timeline` | DEAD | Action Memory timeline — wire в /lab или workspace |
| `/graph/render-node` | DEAD | Partial render — orphan, скорее delete после W14 |
| `/graph/brainstorm-seed` | DEAD | Seed для DMN bridge — internal? |
| `/graph/consolidate`, `/graph/synthesize`, `/graph/tick` | DEAD/STUB | Manual triggers — часть может быть STUB логикой, exp-tier |
| `/graph/horizon-feedback` | USED? | Feedback в Horizon — проверить deeper grep |
| `/graph/compare` | USED? | Diff UI — проверить deeper grep |
| `/hrv/calibrate` | DEAD | Для setup-flow physical device |

**Re-audit когда W14.1 готов** — часть routes исчезнет естественно.

**Dev-only (keep):** `/debug/alerts/trigger-all` — force-run всех alert checkers, curl для отладки.

---

### W8 — rgk-spec strategic gaps (3-5ч, medium risk per item)

[`docs/rgk-spec.md`](../docs/rgk-spec.md) §6 «Расхождения» отмечает 3 не-сделанных пункта. Пост-B5 substrate готов их закрыть.

#### #1. sync_regime 4 режима → R/C projection (1-2ч)

`compute_sync_regime` returns FLOW/REST/PROTECT/CONFESS — но это **проекция** R/C × (user_state, system_state) на 2D. РГК-spec предлагает: `sync_regime = project_regime(R_or_C, user_level, system_level)`.

**Action:** переписать `compute_sync_regime` через `r.user.mode` + `system.mode` + state_levels. 4 strings остаются как UI labels. Логика становится таблицей не if-каскадом.

#### #2. Counter-wave channel selector (2-3ч)

13 детекторов независимо решают «давить или нет». РГК §6 предлагает: `if user_mode == 'C': emit_counter(channel_selector(state))`. Сейчас в W0 я только понизил urgency push-style сигналов на 0.3 при mode='C' — это базовая инверсия.

**Action:** проверить — реально ли 13 детекторов имеют bespoke counter-wave logic, или dispatcher's urgency_penalty достаточно. Возможно single function `select_counter_channel(state) → "text" | "tone" | "silence" | "regime"` уже излишня — Dispatcher делает то же.

**Risk:** может оказаться что existing dispatcher penalty закрывает. Тогда W8.#2 = no-op + закрыть как «уже сделано».

#### #3. balance() как control signal (1-2ч)

balance ∈ [0.3, 1.5] — diagnostic scalar, **не используется** для решений.

**Action:** проверить TODO консьюмеров (Beta-prior consumers — гайдлайн). Возможно candidates: `if r.user.balance() > 1.5` → mute push-сигналы (гиперрезонанс — мания); `if < 0.5` → нежнее тон (апатия). Это extension существующего R/C logic, без новой подсистемы.

**Опционально:** glutamate axis (rgk-spec §4) — **6-я ось**, не реализована. Spec: «Base_amplitude + Propagation, сырая энергия колебаний». Нет clear feeder. Отложить пока balance() не покажет нужду.

---

### W10 — Optional: UserState shim deletion (2-4ч, high risk)

UserState shim 451 LOC — backward-compat для 132 test refs. Production не использует. Если sweep:
- Все `tests/test_capacity.py`, `test_metric_identity.py`, `test_rgk_properties.py`, `test_rgk_consolidation.py` переписать на `РГК()` + helper-фабрику для `set_capacity(rgk, zone, reasons)` etc.
- Удалить class UserState + module-level `get_user_state/set_user_state`
- `compute_sync_error/regime` остаются module-level (принимают rgk)
- Final user_state.py: ~150 LOC (только helpers + sync constants)

**Не приоритет:** identity preserved через test (как mocking surface). Делать только если shim начнёт мешать.

---

### W11 — File consolidation — remaining

#### #5 Chat → src/chat/ package (low)

`chat.py` 65 + `chat_history.py` 189 + `chat_commands.py` 425 = **679 LOC**. Один UI-слой, split по historical reasons. `src/chat/{routes.py, history.py, commands.py}`. Польза не огромна — но naming чище. Opportunistic.

#### #6 Seed → src/seed.py (low effort, low value)

`demo.py` 309 + `defaults.py` 60 = **369 LOC**. Оба про initial bootstrap. Объединить в `seed.py`. Maybe `dev_only` flag чтобы не тащить в prod. Opportunistic.

#### #7 Mental operators → src/operators/ — ❌ отменён 2026-04-29

После онтологии v3 + переписанного W18 эта задача **снимается**. Audit 2026-04-28 показал что pump/elaborate/smartdc/collapse — **не operators, а graph transactions** (think → mutate). Граф v3 размещает их вместе с NAND и детекторами в подгруппе **Process (H.2)**, а не в отдельной `operators/`.

Это разрешает напряжение **без preconditions**: операторы остаются рядом с NAND tick в `src/process/` (после Фазы 1 W18), и эта близость отражает их реальную функцию — все они **части одной операции разворачивания**, а не отдельный концептуальный слой.

**Audit-данные сохранены для истории:** [git log W11 #7 audit] — кому понадобится в будущем если решат вернуться к идее operators/ как отдельной группы.

---

### W12 — Tasks redesign + shared storage primitive (4-6ч)

#### Часть А: добавить `tasks` слой (по spec)

[`docs/task-tracker-design.md`](../docs/task-tracker-design.md) описывает **новое промежуточное звено** между goals (долгосрочные направления) и activity_log (что прямо сейчас). Текущие 6 слоёв (goals / recurring / constraints / plans / activity_log / patterns) — semantically разные, не объединяемые. `tasks` — седьмое звено: «конкретная работа с оценкой сложности».

**Action:**
1. `src/tasks.py` (~250 LOC) по spec §«Реализация»: add_task / schedule_task / start_task (через activity_log link) / complete_task / list_backlog_for_day.
2. `data/tasks.jsonl` append-only.
3. Endpoints `/tasks/add /schedule /start /done /defer /abandon`.
4. `_check_daily_briefing` расширяется: candidate'ы из backlog по capacity-зоне (zone red → 0 task; yellow → ≤2 light; green → ≤4 любых).
5. UI taskplayer panel — backlog + briefing-карточка с кнопками-задачами.

#### Часть Б: shared storage primitive (1-2ч, low risk)

В trio `plans.py` + `recurring.py` + `goals_store.py` (1112 LOC) — каждый имеет **дубликат** API:
- `_append(entry)` — atomic append в jsonl
- `_read_all() -> list[dict]` — read raw events
- `_replay() -> dict[id, state]` — fold events to current state
- file lock + atomic write + rotation

Один `src/jsonl_store.py` (~80 LOC):
```python
class JsonlStore:
    def __init__(self, path, lock): ...
    def append(self, event): ...
    def read_all(self) -> list[dict]: ...
    def replay(self, fold_fn) -> dict: ...
```

Использует: `plans / recurring / goals_store / activity_log / tasks`. Каждый передаёт свой `fold_fn`. Дубликат `_append/_read_all/_replay` исчезает в 5 файлах. Ожидаемый delta: −150..−250 LOC.

**Risk:** атомарность file ops critical (concurrent /assist requests мутируют). Тесты на race conditions обязательны.

#### Часть В: ontology drift (опц)

`docs/ontology.md` § tasks — пока не актуализирован под spec. Sync после реализации.

---

### W13 — Audit assistant.py + cognitive_loop.py (4-8ч, fallback к W14)

**Заменён W14** — оригинальный W13 был «искать magic numbers в больших файлах». W14 это включает + предлагает архитектурное решение для split. Если W14 невозможен/не подходит — fallback к audit-only W13.

Эти два файла = **5733 LOC = 23% всего проекта**. Самые большие, активные, бизнес-логические. Hypothesis: там же скрыты следующие 12 расхождений с РГК (по аналогии с W0 для facade).

#### Что искать (audit аналогично W0)

1. **Magic numbers** — hardcoded значения которые должны быть РГК const'ами или EMA decay'ями.
2. **Inline формулы** — расчёты которые дублируют `_rgk.X` методы.
3. **Прямые state mutations** — `_rgk.user.gain.value = X` вместо `_rgk.u_X(...)`.
4. **Bespoke flows которые могут быть Signal-producers** — Правило 1.
5. **104 routes vs 1 Signal/Dispatcher несоответствие** — архитектурное напряжение.
6. **Переплетение IO с business** — `_load_state` → mutate → `_save_state`.
7. **Legacy code paths** — комментарии «Phase A», «before W3», «pre-Signal».

#### Подход

1. Прогон Explore-агента на оба файла с конкретными вопросами выше
2. По находкам — оценка: trivial fix / wave / architectural redesign
3. Тривиальные → один commit. Wave-tier → отдельные подпункты W13.X.

#### Risk

Эти два файла — **горячий путь** (assistant.py — каждый /assist call; cognitive_loop.py — background loop каждые 5s). Identity test недостаточен — нужны smoke tests или manual /assist runs после изменений.

---

### W14 — Workspace primitive + декомпозиция (статус 2026-04-29)

**Главный архитектурный шаг после B5.** Концепция — [docs/workspace.md](../docs/workspace.md). Implementation план — [workspace-design.md](workspace-design.md).

Идея: **«не ограничиваем систему в действиях, но выбираем из того что она сделала»**. Активное пред-сознательное пространство между divergent generation (детекторы / scout / brief / dmn-bridge / assist reply) и committed graph. Параллель — Global Workspace Theory (Bernard Baars, 1988).

**Реализация:** workspace = **scope над графом**, не отдельный store. Поля `scope: "workspace" | "graph"` + `expires_at` на нодах.

**Asymmetric cost:** дневной режим — cheap (workspace in-memory + bayesian/chem); ночной режим — thoughtful (3 фазы integration). Ночь: NREM replay → REM remote associations → Synaptic homeostasis.

**Sub-waves (11 шагов, статус):**

День — cheap workspace operations:
- ✅ **W14.1** `src/memory/workspace.py` primitive + scope/expires_at fields (done 2026-04-29, +archive_expired bonus)
- ✅ **W14.2** `/assist` + `/assist/chat/append` через workspace (done 2026-04-29)
- ✅ **W14.3** alerts → workspace (done 2026-04-29 + W14.5c-state добавил state-indicator detectors)
- ✅ **W14.4** briefings → workspace (done 2026-04-29; scout/dmn-bridge через _record_baddle_action в W14.5c-3)
- ✅ **W14.5** Cross-processing (synthesize_similar generic + auto-trigger, done 2026-04-29 в a/b/c/final/state)
- ⏳ **W14.9** Lazy LTM recall queue (`notes.queries` pattern + ночной recall) — отдельная wave

Декомпозиция файлов:
- ✅ **W14.6** assistant.py split → `src/io/routes/*.py` (done 2026-04-29: 2964 → 55 LOC bootstrap, 8 route модулей)
- ⏳ **W14.7** cognitive_loop.py split → `bookkeeping.py` + briefings extract (`process/cognitive_loop.py` ещё ~2670 LOC)

Ночь — 3-фазный sleep cycle:
- ⏳ **W14.8** Phase 1 — Sequential integration NREM (workspace nodes → merge/promote/insight emergence)
- ⏳ **W14.10** Phase 2 — Cross-batch REM scout (today_batch × random_old_LTM pump.scout)
- ⏳ **W14.11** Phase 3 — Synaptic homeostasis (DECAY_FACTOR ночное на all LTM)

**Достигнуто 2026-04-29:** assistant.py 2964 → 55 (bootstrap shell), workspace primitive working end-to-end (detector → Signal → Dispatcher → workspace → committed graph → UI graph queries). _alerts_queue + _recent_bridges + ACCUMULATING_ALERT_KINDS const удалены. 535 passed pyflakes 0.

**Осталось:** W14.7 (~2-3ч) + W14.8-11 ночной cycle (~6-9ч). Это extensions над workspace primitive — не блокеры, но завершают полный design.

**Risk (актуальный):** ночной cycle time budget per phase; over-aggressive decay в W14.11.

**Закрывает / разблокирует:** Backlog #11 (STM/LTM) ✅ закрыт через scope mutation, Backlog #12 (pruning) ⏳ ждёт W14.11, W6 investigate-tier ✅ multiple routes теперь legitimate (committed action queries), W16.2 (analogy injection) разблокирован — chat unification done.

---

### W15 — Power: единая метрика сложности/нагрузки (16-22ч)

Концепт: [../docs/power.md](../docs/power.md). Implementation: [power-implementation.md](power-implementation.md).

Эволюция мысли (Игорь, 2026-04-27): «формула сложности задачи... по идее это разница между ожиданием и реальностью по Фристону. А вообще это чистая формула сколько энергии требуется за какой период».

**`Power = U × V × P × interest × chem_modulator`** где `P = max(1, (T_norm/T_actual)^γ)`. Векторно по 3 контурам (phys/affect/cogload).

Унифицирует:
- `estimated_complexity` arbitrary → derived
- `cognitive_load_today` 6-observable → sum P_cogload
- `urgency` arbitrary → power-derived
- `dispatcher.budget = 5/hour` константа → `available_capacity_now`

**7 sub-waves:** primitive → tasks storage (W12 объединён) → live tracking → interest → calibration loop → dispatcher closure → vector capacity check.

**Не блокирует** B5 / W6-11 / W14. Параллельно или после.

W12 (tasks redesign) **поглощается** в W15.2 — реализуем сразу с Power-полями.

---

### W16 — Resonance transfer (sub-waves)

**Главный архитектурный вопрос проекта.** Концепт — [../docs/synchronization.md](../docs/synchronization.md). Мотивация — [../docs/foundation.md § Origin question](../docs/foundation.md#origin-question--главный-вопрос-проекта).

Превращает onboarding нового юзера из 1.5 мес passive accumulation в 1-2 недели active probing. **Каждая задача = стоячая волна = сумма component waves.** 5-axis chem РГК уже = 5 component frequencies — substrate готов.

**Sub-waves:**

#### W16.1 ✅ done — substrate готов

W16.1a (amplitude per axis) + W16.1b (phase-aware) — см. Done log. Spectral diagnosis: где расхождение + в каких axes user ускоряется относительно system.

#### W16.2 — Analogy injection layer (3-3.5ч, **зависит от W14 chat refactor**)

**Сдвиг от первоначального plan'а:** не отдельный onboarding endpoint в Settings (никто туда не зайдёт), а **слой analogy injection** в chat/alert flows — там где deficit замечается. Делать **после** W14 chat unification (промежуточное хранилище куда все компоненты пишут → append в чат), потому что probe = ещё один component с тем же контрактом.

**Concept:** «sync deficit → analogy injection». Всё что блокирует синхронизацию = повод для probe.

| Deficit | Detector | Probe |
|---|---|---|
| **Empty content** ⭐ critical first contact | no goals + empty profile + low activity | «что для тебя сейчас важно? опиши день идеальный» |
| Axis amplitude mismatch | `sync_error_wave.max_value > 0.4` | «вспомни случай когда [target_state]» (axis-specific) |
| Phase mismatch | `_mismatch_count >= 2` | «опиши что сейчас меняется в твоём состоянии» |
| Valence drift | sentiment EMA дрейф | «что радует/беспокоит?» |
| Physical strain | NE high + HRV low + capacity red | «устал? что делал сегодня?» |
| **Dislike-reformulate** | feedback=rejected с rephrase intent | LLM analogy → W16.5 |

**Empty state — самое главное.** Новый юзер пустой → нет goals → Baddle буквально не с чем sync'аться → sync_error_wave всегда максимальный по дефолту.

**Implementation:**

1. **Core engine** (`src/analogies.py`):
   - Question library — 10-15 questions × 8-region map ([user_state_map.py](../src/user_state_map.py)) — single source of truth для target_axes.
   - `pick_analogy(context, rgk_state)` → relevant question по deficit kind.
   - `apply_response(text, target_axes)` → embedding similarity → resonance score → blend `current = α·current + (1−α)·target` с `α = 0.5 · resonance_score`.
   - Rate limiting — 1-2 probe в день, dedup by deficit kind.
   - `data/analogies_log.jsonl` для replay + cross-reference в W16.5.

2. **Detectors:** `detect_empty_state_probe`, `detect_axis_mismatch_probe`, `detect_phase_mismatch_probe`. Valence/physical — opt-in последующие waves.

3. **Signal kind** = новый `analogy_probe` (отличается от `observation_suggestion` — request user content, не feed). Card kind=`analogy_probe` рендерится в чате с textarea + submit.

4. **Endpoint:** `POST /analogy/answer` `{key, text}` → `{target_state, resonance, axes_updated, accepted}`.

5. **Like/Dislike segmentation** на 👎 ([assistant.js:1014](../static/js/assistant.js)):
   - 👎 → modal popup «плохой ответ / надо переформулировать?» (2 кнопки).
   - «плохой» → existing rejected feedback flow.
   - «переформулировать» → log `data/feedback_refinements.jsonl` с reason=`rephrase_requested` + `POST /feedback/refine`. LLM = W16.5.

**Empty state behaviour (mild + повторение):**
- Initial trigger при empty профиле → 1 probe.
- Repeat если still empty → каждые 2-3 дня с другим вопросом.
- Stop после первого goal или 3+ profile entries.

#### W16.3 — Few-shot bias calibration (1-2ч)

При первом task в category — UI с 3-5 examples, linear fit → initial bias_coefficient. **Calib CI band получает физический смысл**: ширина полосы резонанса. Узкий CI = узкая полоса (точная настройка). Переинтерпретация existing Beta-prior infrastructure через wave optics — не новая метрика.

#### W16.4 — Adiabatic adjustment (thin glue)

При больших sync_error_wave[axis] — система генерирует analogies для axis в morning briefing. Закрывает sub-task **«стабильность волны при передаче»** — система проверяет не дрифтует ли её spectrum под user input. После W16.2 (analogy engine) и W16.5 (outbound) — может быть thin glue layer над ними.

#### W16.5 — Outbound LLM analogies (2-3ч, после W16.2)

При feedback=rejected + reason=`rephrase_requested` (segmented dislike из W16.2) → LLM генерит reformulation через user's known patterns (analogies_log + accepted responses). Hook в deep response retry path. Substrate готовится в W16.2 — feedback_refinements jsonl + analogies log.

---

**Главный архитектурный shift после W16:** качество понимания меряется не через perplexity, а через **коэффициент резонанса** (фазовое рассогласование + амплитудная корреляция per axis). Не LLM-фреймворк, **когнитивный резонатор**.

**Validation (Testable claim 8 в rgk-spec):** A/B test 2+ users — Group A passive, Group B onboarding analogies. Если differential ≥3x — подтверждено. Если <1.5x — analogies маргинальны.

**Risk:** **первое реальное трение проекта**. До W16 все insights накладывались без трения. Если works — модель universal. Если нет — переосмысление (что universal vs personal в РГК?).

---

### W18 — File structure as ontology mirror (meta-wave, ontology-derived)

**История:** идея 2026-04-28 — структура файлов порождает сложность реализаций; по закону Конвея структура файлов = mental model. Первоначальный план был software-design intuition (9 групп: substrate / cycle / operators / signals / memory / storage / sensors / io / ui).

**Пересмотр 2026-04-29 после онтологии v3:** появился граф [examples/ontology-v3.json](../examples/ontology-v3.json) с ветвью H «Реализация в Baddle», где Baddle разделён на **5 концептуальных подгрупп** через `realizes` связи к онтологическим узлам. Это даёт **более чистый** план, выведенный из онтологии, а не из software intuition.

**Соответствие подгрупп графа → директории:**

| Граф | Директория | Что внутри |
|---|---|---|
| H.1 Substrate (узлы 56-62) | `src/substrate/` | РГК (rgk.py), 5-axis chem, balance(), R/C, sync_error_wave, phase_per_axis |
| H.2 Process (узлы 63-67) | `src/process/` | NAND tick (nand.py), detectors, signals + dispatcher, cognitive_loop, Friston PE |
| H.3 Memory (узлы 68-72) | `src/memory/` | graph_logic (LTM), workspace.py (W14.1), action_memory, consolidation, state_graph |
| H.4 Transfer (узлы 73-76) | `src/transfer/` | analogies.py (W16.2), feedback_refinements, outbound LLM (W16.5) |
| H.5 Capacity & UI (узлы 77-82) | `src/capacity/` + `src/ui_render/` | capacity 3-zone, power.py (W15), named_state, cone_ui logic, outcome dashboard |

Плюс две технических группы (не из графа, но необходимые):
- `src/sensors/` — уже сделано (W11 #4) — телесный интерфейс
- `src/io/` — Flask routes (HTTP layer)
- `src/storage/` — jsonl primitives (W12 part Б), отделённые от концептуальной memory

**Что изменилось vs первоначальный план:**

1. ❌ **Убрано `cycle/`, `signals/`, `operators/` как отдельные группы.** Граф показывает что они все часть **process** (H.2) — детекторы, сигналы, диспетчер, NAND, cognitive_loop = одна операция разворачивания. Это устраняет проблему W11 #7 false modularity (mental operators).
2. ✅ **Добавлено `transfer/`.** Раньше не было — теперь явная подгруппа для W16 (resonance transfer). Это **новое** в плане, до v3 неочевидное.
3. ✅ **Добавлено `capacity/`.** Раньше capacity жила в `user_state.py`. Теперь — отдельная подгруппа для измерительных метрик (capacity zones, power, named state).
4. ✅ **Разделено `memory/` (концепции) и `storage/` (jsonl primitives).** Memory — что и как помнится; storage — техническая I/O. Это разрешает напряжение W12 part Б.

**Симптомы текущей структуры (что лечится):**
- РГК substrate разбит между `rgk.py` + `horizon.py` + `user_state.py` → собирается в `substrate/`.
- Mental operators (distinct / pump / elaborate / smartdc / collapse) — больше **не отдельная группа**, а часть `process/`. Решает W11 #7 без extract preconditions.
- Storage primitives (`goals_store` + `plans` + `recurring` + `activity_log`) → `storage/` с общим `jsonl_store.py`.
- `assistant.py` 3105 LOC → split по доменам в `io/routes/` + business в соответствующих подсистемах.
- `cognitive_loop.py` 2628 LOC → `process/cognitive_loop.py` + extract bookkeeping/briefings.

**Связь с existing waves (что куда после реализации):**
- W11 #5 (chat package) → `ui_render/chat.py` или `io/routes/chat.py`
- W11 #7 (operators) → **отменяется**: операторы остаются в `process/` рядом с NAND
- W12 part Б (jsonl_store) → `storage/jsonl_store.py`
- W14.1 (workspace primitive) → `memory/workspace.py`
- W14.6 (assistant.py split) → `io/routes/*.py`
- W14.7 (cognitive_loop split) → `process/cognitive_loop.py` + `process/bookkeeping.py`
- W15 (Power) → `capacity/power.py`
- W16.2 (Analogy injection) → `transfer/analogies.py`
- W16.5 (Outbound LLM) → `transfer/outbound.py`

То есть **многие waves становятся частями W18**, не самостоятельными. Это согласует план.

**Готовность по подгруппам:**
- ✅ `sensors/` — done (W11 #4)
- ✅ `substrate/` — done (W18 Phase 1.1, 2026-04-29)
- ✅ `process/` — done (W18 Phase 1.2, 2026-04-29)
- ✅ `io/` — done (W14.6, 2026-04-29) — state.py + 8 routes/{chat,goals,activity,plans,checkins,profile,briefings,misc}
- 🔶 `memory/` — частично (W14, 2026-04-29) — `workspace.py` с полным lifecycle + cross-processing. graph_logic + state_graph пока в `src/` корне, мигрируют после W14.7-11
- ⏳ `transfer/` — ждёт W16.2
- ⏳ `capacity/` — ждёт W15 (Power) + extract из user_state.py
- ⏳ `storage/` — ждёт W12 part Б
- ⏳ `ui_render/` — `chat_history.py` остаётся UI persistence layer (decided 2026-04-29 — complementary с workspace, не trim). Возможно frontend-side helpers в эту подгруппу позже.

**Как это делать (по фазам):**

1. ✅ **Фаза 1 — стабильные подгруппы** (2026-04-29). substrate/ + process/ через `git mv`. 2 commit, identity preserved.
2. 🔶 **Фаза 2 — memory/** (частично 2026-04-29). `workspace.py` создан в W14.1. graph_logic + state_graph + consolidation мигрируют отдельно (W14.7-11 territory).
3. **Фаза 3 — storage/** ждёт W12 part Б. jsonl_store → goals + plans + recurring + activity_log.
4. ✅ **Фаза 4 — io/** (2026-04-29). assistant.py 2964 → 55 LOC. state.py + 8 routes файлов. W14.6 done.
5. **Фаза 5 — capacity + transfer.** capacity/ после W15 (Power), transfer/ после W16.2.

Это **разворачивающийся план**, не «один большой refactor». Каждая фаза — самостоятельная.

**Артефакт когда придёт время:** `planning/file-structure-physics.md` — narrative design doc с rationale per группа + migration plan + соответствие узлам графа.

**Главное преимущество ontology-derived плана:** структура файлов **совпадает** с структурой графа, и каждая директория имеет **онтологическое обоснование** (через `realizes` связи в ветви H). Когда новый разработчик или LLM открывает проект — структура **сама объясняет**, что есть что. Это и есть «mental model для bootstrapping» в чистом виде.

---

## Open углы и design deviations (2026-04-29 audit)

После W14 + W18 Phase 1+4 закрытия — single source of truth для известного
tech debt и documented отклонений от дизайн-документов. Каждый item имеет
explicit статус: «закрыть в wave X» / «осознанно отложен» / «обновить design
под реальность».

### Открытые углы (tech debt)

| # | Угол | Где | Plan |
|---|---|---|---|
| 1 | `consolidation` остаётся в `process/`, не перенесён в `memory/` | `src/process/consolidation.py` | Переоценить после W14.7-11 — если используется только в night cycle (не в process tick), мигрировать в `memory/` |
| 2 | Test coverage для 8 новых io/routes/ файлов не добавлен | `tests/` | Wire smoke tests `tests/test_routes_smoke.py` через Flask test_client при touching любой route. Не блокер пока existing integration tests pass |
| 3 | CLAUDE.md / docs/ не обновлены про новую `src/io/` структуру | docs/architecture-rules.md, docs/foundation.md | Update при следующей docs-sync wave (после W14.7 наверное) |
| 4 | `bookmark` route использует `graph_logic._add_node` напрямую, не через workspace | `src/io/routes/chat.py` /assist/bookmark | Defensible (insight_bookmark = explicit user mark, не event) — но inconsistent с другими routes. При W14.7+ выровнять на `workspace.record_committed(action_kind='insight_bookmark')` |
| 5 | `ui.py` импортирует `from src.assistant import assistant_bp` — backward-compat | `ui.py:34` | Обновить на `from src.io.routes import assistant_bp` (cosmetic). Cleanup в любой touch ui.py wave |

### Design deviations (workspace-design.md vs implementation)

| # | Design said | Implemented as | Reason | Что делать |
|---|---|---|---|---|
| 1 | `add(source, kind, ...)` — `source` field | `add(actor, action_kind, ...)` — Action Memory taxonomy | Decision W14.1: единая taxonomy событий через `action_kind`, без отдельного `source` field. Согласует с Правилом 6 architecture-rules | **Update workspace-design.md** под реальный API |
| 2 | `add(actor="baddle")` — только baddle-side | `add(actor: "baddle"\|"user")` — оба | user_chat должен быть в workspace для cross-processing над user msgs | **Update workspace-design.md** — расширение signature documented |
| 3 | W14.4 briefings `accumulate=True, urgency=0.6, ttl=3600` | `accumulate=False, ttl=24h/7d` (immediate commit) | Briefings = explicit user POST request, не накапливающийся source | **Update workspace-design.md** §W14.4 — briefings = explicit publication, не накопление |
| 4 | W14.4 scout/dmn-bridge `accumulate=True, urgency=0.4, ttl=3600` (накопление) | immediate `record_committed` через `_record_baddle_action` | Bridges produced через explicit `pump.scout` call (не detector chain) — нет потока кандидатов для накопления | **Update workspace-design.md** §W14.4 — bridges = immediate, не accumulate |
| 5 | W14.5 cross-processing per-kind strategies (`pump.scout` / `_collapse_cluster_to_node` / `SmartDC`) | Generic `synthesize_similar` (text concatenation) для всех kinds | Simplification — text-aggregation работает для proof-of-concept | Закрыть в **W14.5+** (LLM-based synthesis through pump/collapse/SmartDC) |
| 6 | W14.1 `commit()` пишет в `data/chat_log.jsonl` для UI | scope mutation only — UI читает через `list_recent_alerts` graph query. `chat_history.jsonl` parallel слой существует для UI persistence (msg + card formatting) | Single source (graph) for events; UI presentation отдельно | **Update workspace-design.md** §W14.1 — `commit()` = scope mutation; UI delivery через graph queries; `chat_history.jsonl` = parallel UI persistence layer (документировано в [src/chat_history.py](../src/chat_history.py)) |

### Decisions (не tech debt, не deviations — explicit choices)

| Item | Decision | Документация |
|---|---|---|
| `chat_history.py` trim | **Не trim** — UI persistence layer complementary с workspace | Module docstring `src/chat_history.py` + детальный план если позже решим: [chat-history-trim-plan.md](chat-history-trim-plan.md) |
| W11 #7 mental operators extract | **Отменён** — операторы остаются в `process/` рядом с NAND | Done log выше + W18 description ниже |

### Recommended next session actions

При начале новой сессии — выбор между:
1. **Update workspace-design.md** под реальный implementation (~30-45 мин) — закрывает 4 design deviations документально
2. **W14.7 cognitive_loop split** (~2-3ч) — естественное продолжение W14.6
3. **W12 part Б jsonl_store** (~1-2ч) — независимая полезная wave
4. **W16.2 Analogy injection** (~3-3.5ч) — теперь разблокирован после W14.6

---

## Что НЕ закрывается

- **89 live Flask routes** — inherent IO; generic dispatcher = net negative.
- **`execute_deep`** — R4 (deepening / diversity guard / pairwise SmartDC).
- **`graph_logic.py`** — R3 каркас.
- **`cognitive_loop._run_*`** — алгоритмы (DMN/REM/scout).
- **`_assist` mega-route** — business orchestration.

---

## Recovery

1. `git log --oneline -20` — последний `WN done` для якоря.
2. `python -m pytest tests/ -q` + `python -m pyflakes src/` — green baseline.
3. Этот файл — точка продолжения. Каждый W — отдельный commit.
