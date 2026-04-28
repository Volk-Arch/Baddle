# Cleanup plan v2

> После B5 (Track B closed) — следующий раунд. Не feature-burst, а полировка. Без `python -m pytest` зелёного — каждый Wave не стартует.
>
> Связь: [TODO.md](TODO.md), [../docs/architecture-rules.md](../docs/architecture-rules.md), [../docs/rgk-spec.md](../docs/rgk-spec.md).

---

## W6 — Routes manual review

**Полный аудит 2026-04-28** (Explore-агент, 104 routes — 58 в `assistant.py` + 46 в `graph_routes.py`):

| Категория | Кол-во |
|---|---|
| USED-UI | 83 |
| DEAD (нет callsite, явный кандидат) | 21 |
| DUPLICATE (overlap c другим route) | 2 пары |
| DEV-ONLY (curl debug) | 1 |

### ✅ Quick win 2026-04-28 — удалены 7 routes

| Route | Причина |
|---|---|
| ~~`/assist/chemistry`~~ | 0 callsites; данные дублировали `/assist/state`. Унификация. |
| ~~`/assist/health`~~ | Subset `/assist/state` (api_health field уже там). |
| ~~`/assist/named-states`~~ | Voronoi regions уже в `/assist/state.user_state.named_state`. |
| ~~`/goals/update`~~ | Generic CRUD — UI использует specific actions (`/complete`/`/abandon`/`/postpone`). |
| ~~`/plan`~~ | Generic listing — UI использует только `/plan/today`. |
| ~~`/plan/update`~~ | Как `/goals/update`, generic dead. |
| ~~`/suggestions/pending`~~ | Orphan — нет UI integration; observation_suggestion идёт через alert-flow (`detectors.py:620` использует `collect_suggestions` напрямую). |

**Pattern-урок:** **generic CRUD endpoints мертвы**, UI предпочитает **specific actions**. Не плодить новые `/X/update` — добавлять `/X/{action}`.

### Investigate-tier — ждут workspace primitive (W14)

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

**Re-audit когда W14.1 (workspace primitive) готов** — часть routes исчезнет естественно.

### Dev-only (keep, no UI)
- `/debug/alerts/trigger-all` — force-run всех alert checkers, curl для отладки.

---

## W7 — Docs sync с post-B5 ✅ done 2026-04-28

Активные ссылки на удалённые `Neurochem` + `ProtectiveFreeze` в docs обновлены на `_rgk` substrate. Затронуты:

- `docs/neurochem-design.md` — Pressure layer секция переписана через `_rgk.{conflict,silence_press,imbalance_press,freeze_active}`, FREEZE_* thresholds (вместо TAU_STABLE/THETA_*), Self-prediction → `_rgk.s_exp_vec`, balance() → `_rgk.user/system.balance()`, `/assist/chemistry` ссылка убрана (endpoint deleted), «Где в коде» обновлено.
- `docs/architecture-rules.md` — Правило 6 (B5 facades удалены note), Правило 5 каркас (`_rgk.p_tick`), Правило 7 (Resonator MODE_* thresholds).
- `docs/rgk-spec.md` — Маппинг таблица обновлена (Neurochem.dopamine → _rgk.system.gain), § Размер расширен фактическими post-B5 LOC (substrate 2479→2136 = −65% vs prognozed 6150), § Реализация — facades удалены note.
- `docs/horizon-design.md`, `docs/friston-loop.md`, `docs/full-cycle.md`, `docs/hrv-design.md`, `docs/resonance-model.md`, `docs/symbiosis-design.md`, `docs/world-model.md`, `docs/README.md` — точечные refs.

Sync_error везде обновлён 3D → 5D после W16.1 clean break.

Historical narratives (`foundation.md` про эволюцию, `README.md` glossary с *(deprecated)* tags, `TECH_README.md` neurochem.py 34-line stub) оставлены — это правильное описание истории миграции.

(Архив plan'а:)

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

## W9 — Fallback polish ✅ done 2026-04-28

Все 4 точечных fix закрыты:

- **`assistant_exec.py:790`** — `float(get_depth_defaults().get("deep_diversity_min") or 0.30)` ловит и отсутствие key, и явный None в config (раньше при None — TypeError перед except).
- **`assistant.py:750`** — silent `except Exception: pass` → `except Exception as e: log.debug(...)`.
- **`compute_sync_regime` (user_state.py)** — упрощён через if-rearrangement: убран дубль `return FLOW` в sync_high branch (теперь только REST или FLOW), внешний default остался для ambiguous-low-sync. Читаемее.
- **`inject_ne(0.4)` vs `(0.3)` mismatch** — verified intentional. Comment в assistant.py:677 фиксирует: `/assist` user-initiated ярче `/graph/assist` background loop.

487 passed, pyflakes 0.

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

- **W16.1a** ✅ done 2026-04-28 — `compute_sync_error_wave(rgk)` в `user_state.py` + `РГК.sync_error_wave()` method + `CognitiveState.sync_error_wave` property. Возвращает 5-axis amplitude breakdown + max_axis + scalar_5d. Expose через `/assist/chemistry coupling.sync_error_wave` + `get_metrics()`. 5 property tests в test_rgk_properties.py. Диагностика «по которой частоте расхождение». 487 passed pyflakes 0. Spec — [docs/synchronization.md § Компоненты](../docs/synchronization.md#1-sync_error_wave-per-axis--mvp-сделан-2026-04-28).
- **W16.1b** ✅ done 2026-04-28 — `РГК.phase_per_axis()` + поле `phases` в `compute_sync_error_wave`. Lazy `_phase_snapshot` (ts/user/system) с min-age 30s — refresh window. Per-axis `user_velocity`, `system_velocity`, `mismatch` flag (signs opposite AND обе > noise 0.005/s). `_mismatch_count` total. 5 property tests (`test_phase_*`). Smoke verified: при user↑/system↓ на DA — `mismatch=True`, при ACh velocity ниже noise — не считается mismatch. Self-contained — без hooks в loop, ленивое обновление. 492 passed pyflakes 0.
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

### #1 surprise_detector.py → detectors.py ✅ done 2026-04-28

`src/surprise_detector.py` (401 LOC) удалён, содержимое перенесено в `src/detectors.py` после `DETECTORS` registry. Move без сжатия (`detectors.py` 890 → 1296 LOC). Импортёр (`cognitive_loop.py:629`) переключён на `from .detectors import detect_user_surprise`. 4 docs ссылки обновлены (TECH_README, world-model, friston-loop). Module docstring расширен — отмечено что файл содержит два контракта (Signal-style + dict-based user_surprise). 473 passed pyflakes 0.

### #2 NAND tick triplet → src/nand.py ✅ done 2026-04-28

`tick_nand.py` 499 + `thinking.py` 186 + `meta_tick.py` 172 = 857 LOC удалены, объединены в `src/nand.py` 867 LOC (4 секции: classification helpers / force collapse / meta-tick / main tick). Без сжатия — move ради discoverability и удаления generic-имени `thinking.py`. 5 импортов в 3 файлах переключены на `from .nand import ...`. 9 docs обновлены (TECH_README, full-cycle, episodic-memory, nand-architecture, neurochem-design, storage, horizon-design, architecture-rules, tick-design, README, плюс 4 inline-комментария в src/). 473 passed pyflakes 0.

### #3 DMN heavy work → src/dmn.py ❌ rejected 2026-04-28

Изначальная гипотеза — `pump_logic.py` 374 + `consolidation.py` 442 = одна «фоновая обработка графа», склеить в `dmn.py`. Игорь увидел дизайн-проблему до commit'а:

- **`pump`** — mental operator наравне с elaborate / collapse / smartdc. Берёт две идеи, ищет hidden axis (LLM-абстракция). 4 callsite: `/graph/pump` UI ([graph_routes.py:980](../src/graph_routes.py)), DMN bridge ([cognitive_loop.py:1061](../src/cognitive_loop.py)), scout ([cognitive_loop.py:1599](../src/cognitive_loop.py)), `execute_deep` EXPLORE zone ([assistant_exec.py:802](../src/assistant_exec.py)). День + ночь.
- **`consolidation`** — night cycle housekeeping (Hebbian decay, content-graph pruning, state-graph archive). 2 callsite: `_run_night_cycle` (24ч авто), `/graph/consolidate` (manual). Только ночь.

Семантики разные. Склейка в `dmn.py` создаст ложное равенство (pump = housekeeping) — буквально anti-pattern из урока B5 «facade прячет inconsistencies».

**Что сделано вместо:** `pump_logic.py` → `pump.py` (drop `_logic` suffix, симметрично nand.py / detectors.py / signals.py / consolidation.py). 4 импорта в src/ обновлены, 8 docs ссылок. `consolidation.py` остаётся как есть.

**Direction для будущего:** см. #7 ниже — собрать рассеянные mental operators в `src/operators/`.

### #4 Sensors → src/sensors/ package ✅ done 2026-04-28

### #4 Sensors → src/sensors/ package ✅ done 2026-04-28

`hrv_manager` 205 + `hrv_metrics` 272 + `sensor_stream` 290 + `sensor_adapters` 95 = 862 LOC переехали в `src/sensors/{manager,metrics,stream,adapters}.py` через `git mv` (rename history preserved). `__init__.py` минимальный — production code использует прямые sub-module импорты (`from .sensors.manager import get_manager`), test patches симметричны (`src.sensors.manager.get_manager`). 11 импортов в src/ (assistant/cognitive_loop/detectors/graph_routes/checkins) + ui.py + 8 test patches переключены. 4 docs обновлены (TECH_README, storage, hrv-design, foundation). Package готов к расширению — реальный Polar BLE / Apple Watch / EEG добавляются как отдельные модули. 473 passed pyflakes 0.

### #5 Chat → src/chat/ package (low)
[`chat.py`](../src/chat.py) 65 + [`chat_history.py`](../src/chat_history.py) 189 + [`chat_commands.py`](../src/chat_commands.py) 425 = **679 LOC**. Один UI-слой, split по historical reasons. `src/chat/{routes.py, history.py, commands.py}`. Польза не огромна — но naming чище.

### #6 Seed → src/seed.py (low effort, low value)
[`demo.py`](../src/demo.py) 309 + [`defaults.py`](../src/defaults.py) 60 = **369 LOC**. Оба про initial bootstrap (demo seeder + roles/templates JSON). Объединить в `seed.py`. Maybe `dev_only` flag чтобы не тащить в prod.

### #7 Mental operators → src/operators/ package — preconditions нужны

Audit 2026-04-28 (Explore-агент): **прямой extract сейчас = false modularity**, тот же anti-pattern что W11 #3 (склейка прячущая inconsistencies). Конкретные verdict'ы по операторам:

| Operator | Location | Verdict | Что мешает |
|---|---|---|---|
| `distinct` + `distinct_decision` | [main.py:37-65](../src/main.py) | **A pure** | — |
| `classify_nodes` | [nand.py:39-73](../src/nand.py) | **A pure** | — |
| `_filter_lineage` | [nand.py:81-113](../src/nand.py) | **A pure** | — |
| `_pick_distant_pair` | [nand.py:118-141](../src/nand.py) | **A pure** | — |
| `_tick_force_collapse` | [nand.py:186-211](../src/nand.py) | **A pure** | — |
| `_pick_target` | [nand.py:146-179](../src/nand.py) | **B isolatable** | Stateful `_count` attribute (3-round diversity counter) — нужен decouple в session param |
| `pump` | [pump.py:25-148](../src/pump.py) | **C entangled** | 6 local helpers + `_graph` global + 10+ LLM calls + `touch_node` mutations |
| `elaborate` | [assistant_exec.py:552-637](../src/assistant_exec.py) | **C entangled** | LLM + 3 graph mutations + 4 imports от graph_logic (`_graph_generate`, `_add_node`, `parse_lines_clean`, `parse_smartdc_triple`) |
| `smartdc` | [assistant_exec.py:596-637](../src/assistant_exec.py) | **C inlined** | Не отдельная функция, embedded в `_deepen_round` |
| `collapse` | — | **action-only** | Не функция. Decision из `_tick_force_collapse` (`{"action": "collapse"}`) + execution handler в [cognitive_loop.py:1400](../src/cognitive_loop.py) (bumps confidence) |

**Главный insight:** pump/elaborate/smartdc/collapse — это **не operators, а graph transactions** (think → mutate). Их extract в `src/operators/` сделает thin wrappers с обратным импортом из graph_logic — false modularity.

Реально вынести сейчас можно только tier A (5 nand helpers + distinct). Но это будет «pure graph helpers», не «mental operators» — главная цель (собрать `pump/elaborate/smartdc/collapse` рядом) не достигается, имя package вводит в заблуждение.

**Preconditions для honest W11 #7:**

1. **Decouple `_pick_target`** — переписать `_count` через session-state, передаваемый в аргументах (~30 мин).
2. **Extract `smartdc`** из `_deepen_round` в отдельную функцию (assistant_exec.py:596-637 → standalone) (~1ч).
3. **Decouple `elaborate`** от mutations: возвращать **draft** (что добавить / какие edges создать) вместо прямого вызова `_add_node`. Caller применяет. (~2-3ч, нужны tests)
4. **Создать `collapse_nodes(graph, indices, ...)`** функцию которая делает работу что сейчас в `cognitive_loop.py:1400` execution handler. (~1ч)
5. **Decouple `pump`** от `_graph` global — принимать nodes/embeddings явно. 6 helpers оставить рядом (внутри pump.py). (~2ч)

**После всех 5 preconditions** — extract в `src/operators/` становится механическим move (~1-2ч). Но тогда package реально содержит то что обещает.

**Tier-A only сейчас** возможно (~30 мин), но без tier B/C толку мало. Решение: **не делать.** Вернёмся к W11 #7 когда будет appetite на 5-сессионный refactor самих операторов.

**Связь с W16:** при resonance transfer аналогии = «оператор преобразования базиса». Это ещё один operator — добавится после реализации W16.1+. К тому моменту, может, накопится критическая масса для package.

**Польза для будущего (если сделаем):**
- Discoverability — «где определена операция X» тривиально.
- thinking-operations.md получает 1-к-1 mirror в коде.
- Тестирование операторов в изоляции (сейчас pump-test = full graph fixture).

**Ставка приоритета:**
1. ~~#1 surprise_detector → detectors.py~~ ✅ 2026-04-28
2. ~~#2 NAND triplet → nand.py~~ ✅ 2026-04-28
3. ~~#4 Sensors → sensors/ package~~ ✅ 2026-04-28
4. ~~#3 DMN склейка~~ ❌ rejected 2026-04-28 (вместо: pump_logic→pump rename)
5. #5 Chat, #6 Seed — opportunistic, когда касаешься этих файлов.
6. #7 Mental operators package — заблокирован 5 preconditions (decouple smartdc/elaborate/pump/collapse/_pick_target). Не делать прямой extract — будет false modularity.

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

## W17 — Naming consolidation: bio_physics compound names в API ✅ done 2026-04-28

Full rename: `dopamine` → `dopamine_gain`, `serotonin` → `serotonin_hysteresis`,
`norepinephrine` → `norepinephrine_aperture`, `acetylcholine` →
`acetylcholine_plasticity`, `gaba` → `gaba_damping`. Сделан incremental по
файлам (rgk → horizon → user_state → consumers → JS → tests → docs).

**Затронуты:** `rgk.py` (serialize_user/system, load_user/system, project()
returns, _AXIS_NAMES, EXPECTED_USER/SYS), `horizon.get_metrics`,
`user_state` (_WAVE_AXES, _AXIS_FIELDS, compute_sync_error_wave),
`cognitive_loop.py` (chem snapshot writes), `assistant.py`/`assistant_exec.py`/
`graph_logic.py` (output dicts), `static/js/assistant.js` (6 dot-accesses +
_RADAR_AXES keys), `test_metric_identity.py` + `test_rgk_properties.py`
(EXPECTED + assertions), `docs/{synchronization,ontology}.md`.

**Не трогали:** `_low` capacity reasons (semantic codes, не chem-axes),
named_state map (отдельный namespace `da/ne/ach/gaba`), UserState shim
attributes (`us.dopamine` — Python property, под W10 deletion), neurochem.py
deprecated stub historical mapping.

**Верифицировано:** 487 passed, pyflakes 0, smoke в preview — radar с new
keys рендерит pentagon корректно (mock NE 0.7 → vertex вытянут upper-right),
console errors 0, `/assist/state` отдаёт compound keys, legacy `dopamine` =
undefined.

API теперь self-documenting: `dopamine_gain` сразу показывает bio↔physics
mapping (DA → амплитуда захвата). `/assist/chemistry` deletion-decision
2026-04-28 закрыта правильно — наследие compound naming живёт в основном
API.

(Архив изначального plan'а:)

**Мотивация.** В API output (`/assist/state`, `serialize_user`,
`serialize_system`, `project()`) chem-axes отдаются по биологическим
именам: `dopamine`, `serotonin`, `norepinephrine`, `acetylcholine`, `gaba`.
Внутри РГК физические имена: `gain`, `hyst`, `aperture`, `plasticity`,
`damping`. Mapping bio↔physics есть только в [rgk.py:55-60 docstring](../src/rgk.py)
и [docs/rgk-spec.md](../docs/rgk-spec.md). API self-documenting не даёт.

Compound naming (`dopamine_gain`, `serotonin_hysteresis`,
`norepinephrine_aperture`, `acetylcholine_plasticity`, `gaba_damping`)
показывает связь биологии и физики **прямо в JSON**. Ранее жил в удалённом
`/assist/chemistry` endpoint (deleted 2026-04-28), но только там.

**Scope (audit 2026-04-28):**
- **67 occurrences** в `src/*.py` — keys в dict literals, `.get()` access
  (`graph_logic.py` snapshots, `cognitive_loop.py` chem fields, `assistant.py`,
  `assistant_exec.py`, `horizon.get_metrics`, `rgk.serialize_*`/`load_*`/`project`).
- **6 occurrences** в `static/*.js` — UI читает `metrics.user_state.dopamine`,
  `metrics.neurochem.dopamine` etc.
- **21 occurrences** в `tests/*.py` — `EXPECTED_USER_STATE["dopamine"]` etc.
- **state.json + prime_directive.jsonl** — persistence keys; full rename
  ломает старые saves. По правилу [no backward-compat](../memory) — это OK.

**Подход (incremental по 1 файлу — bisect-friendly):**
1. `serialize_user` / `serialize_system` / `load_user` / `load_system` —
   output + input keys в РГК (rgk.py).
2. `project()` returns в РГК — `user_state` / `system` / `freeze` projections.
3. `get_metrics()` (horizon.py) — `neurochem` секция keys.
4. `compute_sync_error_wave` axes keys + `_AXIS_NAMES` + `_WAVE_AXES`.
5. Consumers в src/ — `cognitive_loop.py`, `assistant.py`,
   `assistant_exec.py`, `graph_logic.py`. Каждый — отдельный commit.
6. UI JS (`assistant.js` + другие) — обновить access paths.
7. Tests — `EXPECTED_USER_STATE`, `EXPECTED_NEUROCHEM`, identity sentinels
   в rgk.py.
8. Docs sync — `neurochem-design.md`, `rgk-spec.md`, `synchronization.md`,
   `architecture-rules.md` upda mapping references.

**Effort:** ~1.5-2ч аккуратно. Зелёные tests после каждого шага. Risk
medium — много touchpoints, missed grep даст пустые поля в UI.

**Альтернатива (если больших изменений не хочется):** API-only rename —
менять только output в `serialize_*` + `get_metrics`, internal код
оставить с short keys (~20-30 мин). Минус — двойная карта (compound в API,
short в РГК.user/system attribute access — последнее уже физика).

**Не блокирует:** W14, W15, W16. Делать когда appetite на чистый refactor.

**Не делать:** не делать одновременно с другим feature burst — много
occurrences, грязный merge с любым параллельным изменением chem polling.

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
