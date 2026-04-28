# Cleanup plan v2

> После B5 (Track B closed) — следующий раунд. Не feature-burst, а полировка. Без `python -m pytest` зелёного — каждый Wave не стартует.
>
> Связь: [TODO.md](TODO.md), [../docs/architecture-rules.md](../docs/architecture-rules.md), [../docs/rgk-spec.md](../docs/rgk-spec.md).

---

## W6 — Routes manual review (1-2ч, low risk, possible feature wire-up)

**16 routes без вызова в `templates/*.html` / `static/*.js` / `partials/*`.** НЕ автоматически удалять — Игорь: "может просто не добавили использование". Каждый — отдельное решение: **keep + wire frontend** / **delete** / **leave dead для future**.

| Route | File:line | Гипотеза |
|---|---|---|
| `/assist/health` | assistant.py:1100 | Медицинские параметры — может для отдельного "Health" виджета |
| `/assist/chemistry` | assistant.py:1107 | yaml/json snapshot 5-axis. **Useful for Lab/debug** — wire в /lab |
| `/assist/history` | assistant.py:1186 | История interactions — может для timeline view |
| `/assist/named-states` | assistant.py:1305 | 8 регионов карты — для UI legend / docs |
| `/patterns`, `/patterns/run` | assistant.py:1154,1171 | Legacy pattern-detector? Или pre-Signal? |
| `/suggestions/pending` | assistant.py:1578 | Очередь — может для "What I'm thinking" UI |
| `/goals/update` | assistant.py:1670 | Generic — vs `/goals/complete /abandon /postpone` (specific) |
| `/goals/solved/<ref>` | assistant.py:1718 | По ref — vs `/goals/solved` без |
| `/plan` | assistant.py:1962 | Весь план vs `/plan/today` |
| `/sensor/readings`, `/sensor/aggregate` | assistant.py:2446,2475 | Сенсорный stream — wire когда Polar/AppleWatch адаптеры |
| `/graph/compare` | graph_routes.py:1011 | Сравнение нод — может для diff UI |
| `/graph/horizon-feedback` | graph_routes.py:1182 | Feedback в Horizon — для оценки confidence? |
| `/graph/self/similar` | graph_routes.py:1238 | Найти похожие — для "related" блока |
| `/graph/brainstorm-seed` | graph_routes.py:1262 | Seed для DMN bridge — internal? |
| `/graph/render-node` | graph_routes.py:1316 | Один ноду — partial render? |
| `/graph/consolidate`, `/graph/synthesize` | graph_routes.py:1382,1408 | Manual triggers — может для Lab |
| `/graph/actions-timeline` | graph_routes.py:1659 | Action Memory timeline — wire в /lab |
| `/hrv/calibrate` | graph_routes.py:1891 | HRV baseline reset — для setup-flow |

**Process:** для каждого — открыть handler, прочитать docstring + body, решить:
- Если route даёт UI value которого нет → **wire** (TODO frontend)
- Если route для dev/debug → **keep + comment** "// dev-only, no UI"
- Если route legacy/superseded → **delete**

---

## W7 — Docs sync с post-B5 (1-2ч, doc-only)

После W3+W4 удалены `Neurochem` + `ProtectiveFreeze`. Несколько docs упоминают их как живые.

**Files to fix:**
- [`docs/neurochem-design.md`](../docs/neurochem-design.md) — line 15 `(ProtectiveFreeze)`, lines 72-87 целая секция "Защитный режим (ProtectiveFreeze)", lines 91-98 `Neurochem.expectation_vec`, lines 195-197 `TAU_STABLE/THETA_ACTIVE/THETA_RECOVERY` → `FREEZE_*`, lines 203-209 "Где в коде" `class Neurochem + ProtectiveFreeze`, `self.neuro`, `self.freeze`. → переписать как РГК-only narrative с map "старое имя → новое".
- [`docs/architecture-rules.md`](../docs/architecture-rules.md) — Правило 6 line 81 `UserState/Neurochem facades остаются как thin proxies` (Neurochem нет, UserState shim). Правило 7 line 93 `THETA_ACT=0.15 / THETA_REC=0.08` — нужно явно `Resonator.THETA_*` vs `FREEZE_*` чтобы coincidence не путала.
- [`docs/rgk-spec.md`](../docs/rgk-spec.md) — §6 lines 128 `ProtectiveFreeze.THETA_*` ссылки → `FREEZE_*`. §9 lines 240-244 "Реализация" — упоминает 3 facades, реально 1 shim. Lines 195-201 LOC оценки сильно устарели (user_state 1300→393, neurochem 400→34, totals 6150→1583).

**Process:** прочитать ключевые секции, заменить упоминания удалённых классов на РГК-эквиваленты, обновить LOC цифры в rgk-spec §7 § «Размер».

---

## W8 — rgk-spec strategic gaps (3-5ч, medium risk per item)

[`docs/rgk-spec.md`](../docs/rgk-spec.md) §6 «Расхождения» отмечает 3 не-сделанных пункта. Пост-B5 substrate готов их закрыть.

### #1. sync_regime 4 режима → R/C projection (1-2ч)

`compute_sync_regime` returns FLOW/REST/PROTECT/CONFESS — но это **проекция** R/C × (user_state, system_state) на 2D. РГК-spec предлагает: `sync_regime = project_regime(R_or_C, user_level, system_level)`.

**Action:** переписать `compute_sync_regime` через `r.user.mode` + `system.mode` + state_levels. 4 strings остаются как UI labels. Логика становится таблицей не if-каскадом.

### #2. Counter-wave channel selector (2-3ч)

13 детекторов независимо решают «давить или нет». РГК §6 предлагает: `if user_mode == 'C': emit_counter(channel_selector(state))`. Сейчас в W0 я только понизил urgency push-style сигналов на 0.3 при mode='C' — это базовая инверсия.

**Action:** проверить — реально ли 13 детекторов имеют bespoke counter-wave logic, или dispatcher's urgency_penalty достаточно. Возможно single function `select_counter_channel(state) → "text" | "tone" | "silence" | "regime"` уже излишня — Dispatcher делает то же.

**Risk:** может оказаться что existing dispatcher penalty закрывает. Тогда W8.#2 = no-op + закрыть как «уже сделано».

### #3. balance() как control signal (1-2ч)

balance ∈ [0.3, 1.5] — diagnostic scalar, **не используется** для решений. `Calib` tab в Outcome — observable.

**Action:** проверить TODO консьюмеров (Beta-prior consumers — гайдлайн). Возможно candidates: `if r.user.balance() > 1.5` → mute push-сигналы (гиперрезонанс — мания); `if < 0.5` → нежнее тон (апатия). Это extension существующего R/C logic, без новой подсистемы.

**Опционально:** glutamate axis (rgk-spec §4) — **6-я ось**, не реализована. Spec: «Base_amplitude + Propagation, сырая энергия колебаний». Нет clear feeder. Отложить пока balance() не покажет нужду.

---

## W9 — Fallback polish (1ч, low risk)

Из аудита Explore-агента:

- **`assistant_exec.py:790-792`** — `div_min = float(get_depth_defaults().get("deep_diversity_min", 0.30))` затем `if div_min is None: div_min = 0.30`. Если config вернёт `{"deep_diversity_min": None}` — `float(None)` крашится **до** if-проверки. **Fix:** один fallback в `.get("…", 0.30) or 0.30`, удалить `if`.
- **`assistant.py:750`** — `except Exception: pass` без logging. **Fix:** `log.debug(f"[recent_briefing] parse failed: {e}")`.
- **`compute_sync_regime` (user_state.py)** — двойной `return FLOW` (один в if-branch, один как default). **Fix:** упростить — middle case = explicit comment "amb → FLOW".
- **`inject_ne(0.4)` (assist:677) vs `inject_ne(0.3)` (graph_assist:2193)** — **Investigate:** intentional разница (assist более user-engaged, graph более внутренний)? Если да — comment. Если copy-paste error — unify.

---

## W14 — Workspace + декомпозиция (16-22ч suммарно, design wave + impl)

**Главный архитектурный шаг после B5.** Концепция — [docs/workspace.md](../docs/workspace.md). Implementation план — [workspace-design.md](workspace-design.md).

Идея автора (2026-04-27): **«не ограничиваем систему в действиях, но выбираем из того что она сделала»**. Это активное пред-сознательное пространство между divergent generation (детекторы / scout / brief / dmn-bridge / assist reply) и committed graph. Не store: накопленные кандидаты могут быть **обработаны между собой** через scout/SmartDC/consolidation до broadcast'а.

Параллель в когнитивной науке — Global Workspace Theory (Bernard Baars, 1988). Закрывает [TODO Backlog #11 «Оперативная vs долговременная память»](TODO.md): workspace = STM, граф = LTM, перенос через consolidation.

**Реализация:** workspace = **scope над графом**, не отдельный store. Поля `scope: "workspace" | "graph"` + `expires_at` на нодах. Все существующие graph operations работают.

**Asymmetric cost insight (2026-04-27):** дневной режим — cheap (workspace in-memory + bayesian/chem); ночной режим — thoughtful (3 фазы integration). LTM recall днём = expensive overhead, поэтому **opt-in lazy queue** вместо broad activation. Ночь — три фазы соответствуют биологическому sleep: NREM replay → REM remote associations → Synaptic homeostasis.

**Sub-waves (11 шагов, ~22-30ч):**

День — cheap workspace operations:
- **W14.1** `src/workspace.py` primitive + scope/expires_at fields (3-4ч)
- **W14.2** `/assist` + user message через workspace (1-2ч)
- **W14.3** alerts → workspace (2-3ч)
- **W14.4** briefings + scout → workspace (2-3ч)
- **W14.5** Cross-кандидатная обработка (scout/SmartDC между similar workspace-кандидатами) (2-3ч)
- **W14.9** Lazy LTM recall queue (note "need context: X" → answer ночью) (1-2ч)

Декомпозиция файлов (после migration):
- **W14.6** assistant.py split → `src/routes/{chat,goals,activity,plans,checkins,profile,briefings,misc}.py` (3-5ч)
- **W14.7** cognitive_loop.py split → `bookkeeping.py + briefings.py + advance_tick` (2-3ч)

Ночь — 3-фазный sleep cycle (закрывает Backlog #11+#12+Tier 2 «META»):
- **W14.8** Phase 1 — Sequential integration (NREM-like): per-node merge/mid-distance/promote (3-4ч)
- **W14.10** Phase 2 — Cross-batch REM scout: pairs внутри сегодняшнего batch + remote associations с давним LTM (2-3ч)
- **W14.11** Phase 3 — Synaptic homeostasis: global confidence decay × restoration touched-today (1-2ч)

**Ожидаемая дельта:** assistant.py 3105 → ~150, cognitive_loop.py 2628 → ~1200, +workspace.py 150 + 8 routes/*.py.

**Risk:** behaviour drift (alert delay ~5s); hot path performance; ночной cycle time budget per phase; over-aggressive decay в W14.11 (mitigation: confidence_at_promote сохранять).

---

## W13 — Пересмотр assistant.py + cognitive_loop.py (4-8ч, high value)

**Заменён W14** — оригинальный W13 был «искать magic numbers в больших файлах». W14 это включает + предлагает **архитектурное решение** для split. Если W14 невозможен/не подходит — fallback к audit-only W13:

Эти два файла = **5733 LOC = 23% всего проекта**. Самые большие, активные, самые «бизнес-логические». Hypothesis: там же скрыты следующие 12 расхождений с РГК (по аналогии с W0 для facade). Business orchestration — место где правила architecture-rules ещё **не доползли** до полного применения.

### Что искать (audit пройти аналогично W0):

1. **Magic numbers** — hardcoded значения которые должны быть РГК const'ами или EMA decay'ями. Уже найдены в W9: `inject_ne(0.4)` (assist:677) vs `inject_ne(0.3)` (graph_assist:2193). Скан на остальные 0.X / 0.0X в логике (не UI).

2. **Inline формулы** — расчёты которые дублируют `_rgk.X` методы. Например — где-то может быть inline `(da + 5ht) / 2` вместо `_rgk.user_state_level()` (если такой helper есть/нужен).

3. **Прямые state mutations** — `_rgk.user.gain.value = X` вместо `_rgk.u_X(...)`. Через мутацию обходится clamping/feeding/tick — semantic divergence.

4. **Bespoke flows которые могут быть Signal-producers** — Правило 1 говорит «любое событие к юзеру это Signal через Dispatcher». В assistant.py много мест где alerts генерируются inline в response (zone_overload и подобное около строки 3050). Должны быть детекторы → dispatcher.

5. **104 routes vs 1 Signal/Dispatcher несоответствие** — каждая фича = custom HTTP handler, не declaration. Это не cleanup-tier, это **архитектурное напряжение**: «inherent IO» vs Правило 1. Возможно часть routes можно превратить в `signals.dispatch(SignalKind, params)` через generic endpoint.

6. **Переплетение IO с business** — в одной функции state load + LLM call + EMA update + persist. Pattern `_load_state` → mutate → `_save_state` встречается часто; может быть `with state_session(): ...` контекст-менеджер.

7. **Legacy code paths** — комментарии «Phase A», «before W3», «pre-Signal» — patterns из ранних фаз которые сейчас могут быть проще.

### Почему сейчас:

Cleanup продолжается пока явное не исчерпано — а assistant.py + cognitive_loop.py явно **не подвергались** consolidation (B5 трогал их только как callers facade). Внутренняя консистентность не проверялась с моделью РГК. Возможны те же расхождения «physics drift» что в W0.

### Подход:

1. Прогон Explore-агента на оба файла с конкретными вопросами выше
2. По находкам — оценка: trivial fix / wave / architectural redesign
3. Тривиальные → один commit. Wave-tier → отдельные подпункты W13.X.

### Risk:

Эти два файла — **горячий путь** (assistant.py — каждый /assist call; cognitive_loop.py — background loop каждые 5s). Identity test недостаточен — нужны smoke tests или manual /assist runs после изменений. Браузерное preview verification обязательно для UI-affecting изменений.

---

## W16 — Resonance transfer onboarding (6-10ч, после W14+W15)

**Главный архитектурный вопрос проекта получает реализацию.** Концепт — [../docs/synchronization.md](../docs/synchronization.md). Мотивация — [../docs/foundation.md § Origin question](../docs/foundation.md#origin-question--главный-вопрос-проекта).

Превращает onboarding нового юзера из 1.5 мес passive accumulation в 1-2 недели active probing через analogies. **Каждая задача = стоячая волна = сумма component waves**, активация тех же components у нового юзера → узор воспроизводится.

5-axis chem РГК уже = 5 component frequencies — substrate готов. Нужен только transfer protocol поверх.

**Sub-waves (после углубления synchronization.md 2026-04-28 — 3 sub-task'а покрывают 3 узких места resonance transfer):**

- **W16.1** `sync_error_wave` per axis + **phase-aware comparison** (вместо scalar L2). Не просто `|user[axis] − system[axis]|`, а phase + amplitude per component. Диагностика «на каких частотах расхождение и в какой фазе». Закрывает sub-task **«измерение рассогласования»**. (2-3ч)
- **W16.2** Onboarding analogies endpoint: `GET /onboarding/analogies` + `POST /onboarding/answer`. 5-7 questions с target named_state'ами из 8-region map. Embedding similarity → activation. Закрывает sub-task **«генерация резонансных аналогий»** — аналогия = оператор преобразования базиса между пользовательским и target spectrum. (2-3ч)
- **W16.3** Few-shot bias calibration: при первом task в category — UI с 3-5 examples, linear fit → initial bias_coefficient (1-2ч). **Calib CI band получает физический смысл**: ширина полосы резонанса. Узкий CI = узкая полоса (точная настройка). Это переинтерпретация existing Beta-prior infrastructure через wave optics — не новая метрика.
- **W16.4** Adiabatic adjustment: при больших sync_error_wave[axis] система генерирует analogies для axis в morning briefing. Закрывает sub-task **«стабильность волны при передаче»** — система проверяет не дрифтует ли её spectrum под user input. Active learning loop. (2-3ч)

**Главный архитектурный shift после W16:** качество понимания меряется не через perplexity, а через **коэффициент резонанса** (фазовое рассогласование + амплитудная корреляция per axis). Не LLM-фреймворк, **когнитивный резонатор**. См. [docs/synchronization.md § Углубление](../docs/synchronization.md).

**Зависит:** W14 (workspace для onboarding flow) + W15.4 (calibration loop infrastructure). Без них transfer protocol работает на substrate готовом не до конца.

**Validation (Testable claim 8 в rgk-spec):** A/B test 2+ users — Group A passive, Group B onboarding analogies. Если Group B sync convergence за ≤2 недели и Group A за 1-2 мес — differential ≥3x подтверждает. Если differential <1.5x — analogies маргинальны, learning происходит через events.

**Risk:** **первое реальное трение проекта**. До W16 все insights накладывались без трения. Resonance transfer — точка где модель проверяется на extensibility за пределы single-user. Если works — модель действительно universal. Если нет — переосмысление (что universal vs personal в РГК?).

---

## W15 — Power: единая метрика сложности/нагрузки (16-22ч, design + impl)

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

## W11 — File consolidation (2-4ч suммарно, рискованно по 1 шагу)

51 .py файл / 24.5k LOC сейчас. Несколько арбитрарных разделений по historical reasons. **Каждый шаг — отдельный commit** (одна группа за раз, не bulk).

### #1 surprise_detector.py → detectors.py (low effort)
[`src/surprise_detector.py`](../src/surprise_detector.py) (401 LOC) — фактически 14-й детектор по семантике. До Phase B (Signal/Dispatcher) был отдельным модулем. Сейчас должен жить рядом с 13 другими в `detectors.py`. Ожидаемый delta: 0 LOC (move без сжатия), но архитектурная честность.

### #2 NAND tick triplet → src/nand.py (medium)
[`src/tick_nand.py`](../src/tick_nand.py) 499 + [`src/thinking.py`](../src/thinking.py) 186 + [`src/meta_tick.py`](../src/meta_tick.py) 172 = **857 LOC**. Все три — один tick: distinct → Bayes → policy nudge. `thinking.py` — generic name, на самом деле NAND helpers (classify_nodes, _filter_lineage, _pick_target). После consolidation: ~750 LOC после dedup общих imports/utils. Имя точнее отражает.

### #3 DMN heavy work → src/dmn.py (medium)
[`src/pump_logic.py`](../src/pump_logic.py) 374 + [`src/consolidation.py`](../src/consolidation.py) 442 = **816 LOC**. REM прорастание + bridge pump = одна семантика «фоновая обработка графа». Сейчас split arbitrary. Ожидаемый ~750 LOC.

### #4 Sensors → src/sensors/ package (medium)
[`hrv_manager`](../src/hrv_manager.py) 205 + [`hrv_metrics`](../src/hrv_metrics.py) 272 + [`sensor_stream`](../src/sensor_stream.py) 290 + [`sensor_adapters`](../src/sensor_adapters.py) 95 = **862 LOC**. Логически один subsystem. Package с 4 sub-files (`manager.py`, `metrics.py`, `stream.py`, `adapters.py`) или один `sensors.py`. Когда добавятся реальные адаптеры (Polar, Apple Watch) — package масштабируется.

### #5 Chat → src/chat/ package (low)
[`chat.py`](../src/chat.py) 65 + [`chat_history.py`](../src/chat_history.py) 189 + [`chat_commands.py`](../src/chat_commands.py) 425 = **679 LOC**. Один UI-слой, split по historical reasons. `src/chat/{routes.py, history.py, commands.py}`. Польза не огромна — но naming чище.

### #6 Seed → src/seed.py (low effort, low value)
[`demo.py`](../src/demo.py) 309 + [`defaults.py`](../src/defaults.py) 60 = **369 LOC**. Оба про initial bootstrap (demo seeder + roles/templates JSON). Объединить в `seed.py`. Maybe `dev_only` flag чтобы не тащить в prod.

**Ставка приоритета:**
1. #1 surprise_detector → detectors.py (15 минут, явный win архитектурно)
2. #2 NAND triplet (~1ч, средний win — лучший discoverability)
3. #4 Sensors package (~1ч, future-proof для real adapters)
4. #3 DMN, #5 Chat, #6 Seed — opportunistic, когда касаешься этих файлов.

---

## W12 — Tasks redesign + shared storage primitive (4-6ч, high value, design wave)

### Часть А: добавить `tasks` слой (по spec)

[`docs/task-tracker-design.md`](../docs/task-tracker-design.md) описывает **новое промежуточное звено** между goals (долгосрочные направления) и activity_log (что прямо сейчас). Текущие 6 слоёв (goals / recurring / constraints / plans / activity_log / patterns) — semantically разные, не объединяемые. `tasks` — седьмое звено: «конкретная работа с оценкой сложности».

**Action:**
1. `src/tasks.py` (~250 LOC) по spec §«Реализация»: add_task / schedule_task / start_task (через activity_log link) / complete_task / list_backlog_for_day.
2. `data/tasks.jsonl` append-only.
3. Endpoints `/tasks/add /schedule /start /done /defer /abandon`.
4. `_check_daily_briefing` расширяется: candidate'ы из backlog по capacity-зоне (zone red → 0 task; yellow → ≤2 light; green → ≤4 любых).
5. UI taskplayer panel — backlog + briefing-карточка с кнопками-задачами.

### Часть Б: shared storage primitive (1-2ч, low risk)

В trio [`plans.py`](../src/plans.py) + [`recurring.py`](../src/recurring.py) + [`goals_store.py`](../src/goals_store.py) (1112 LOC) — каждый имеет **дубликат** API:
- `_append(entry)` — atomic append в jsonl
- `_read_all() -> list[dict]` — read raw events
- `_replay() -> dict[id, state]` — fold events to current state
- file lock + atomic write + rotation

Один `src/jsonl_store.py` (~80 LOC):
```python
class JsonlStore:
    def __init__(self, path, lock):
        ...
    def append(self, event): ...
    def read_all(self) -> list[dict]: ...
    def replay(self, fold_fn) -> dict: ...
```

Использует: `plans / recurring / goals_store / activity_log / tasks`. Каждый передаёт свой `fold_fn` (event → state mutation). Дубликат `_append/_read_all/_replay` исчезает в 5 файлах. Ожидаемый delta: −150..−250 LOC + единая дисциплина (если в `JsonlStore` появится rotation/compaction — все слои получают бесплатно).

**Risk:** атомарность file ops critical (concurrent /assist requests мутируют). Тесты на race conditions обязательны.

### Часть В: ontology drift (опц)

`docs/ontology.md` § tasks — пока не актуализирован под spec. Sync после реализации.

---

## W10 — Optional: UserState shim deletion (2-4ч, high risk)

UserState shim 393 LOC — backward-compat для **132 test refs**. Production не использует. Если sweep:
- Все `tests/test_capacity.py`, `test_metric_identity.py`, `test_rgk_properties.py`, `test_rgk_consolidation.py` переписать на `РГК()` + helper-фабрику для `set_capacity(rgk, zone, reasons)` etc.
- Удалить class UserState + module-level `get_user_state/set_user_state`
- `compute_sync_error/regime` остаются module-level (принимают rgk)
- Final user_state.py: ~150 LOC (только helpers + sync constants)

**Не приоритет:** identity preserved через test (как mocking surface). Делать только если shim начнёт мешать.

---

## Что НЕ закрывается

- **89 live Flask routes** — inherent IO; generic dispatcher = net negative.
- **`execute_deep`** — R4 (deepening / diversity guard / pairwise SmartDC).
- **`graph_logic.py`** — R3 каркас.
- **`cognitive_loop._run_*`** — алгоритмы (DMN/REM/scout).
- **`_assist` mega-route** — business orchestration.

---

## Recovery

1. `git log --oneline -10` — последний `WN done` для якоря.
2. `python -m pytest tests/ -q` + `python -m pyflakes src/` — green baseline.
3. Этот файл — точка продолжения. Каждый W — отдельный commit.
