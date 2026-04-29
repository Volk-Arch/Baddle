# Cleanup plan v2

> После B5 (Track B closed) — следующий раунд. Не feature-burst, а полировка. Без `python -m pytest` зелёного — каждый Wave не стартует.
>
> Связь: [TODO.md](TODO.md), [../docs/architecture-rules.md](../docs/architecture-rules.md), [../docs/rgk-spec.md](../docs/rgk-spec.md), [../examples/ontology-v3.json](../examples/ontology-v3.json).

---

## 🎯 Следующий шаг: Фаза 1 W18 — substrate + process migration

**Что:** перенести готовые подгруппы в директории на основе ветви H графа онтологии v3.

**Почему именно это:** ontology v3 (создана 2026-04-29) предлагает file structure через ветвь H графа, и две подгруппы — substrate и process — уже **концептуально готовы** (файлы есть, тесты зелёные). Переезд = `git mv` + обновление импортов, без изменения логики. После этого новые waves (W14.1 workspace, W15 Power, W16.2 analogies) **сразу впадают** в правильные директории, не накапливая долг.

**Порядок (~1.5-2ч):**

1. **substrate/** (~30-40 мин): `mkdir src/substrate` + `git mv src/{rgk,horizon,user_state}.py src/substrate/` + `__init__.py` с re-exports + grep imports + tests + commit.
2. **process/** (~40-50 мин): `mkdir src/process` + `git mv src/{nand,detectors,signals,cognitive_loop,pump,consolidation}.py src/process/` + `__init__.py` + grep imports + tests + commit.

**Принцип:** каждая подгруппа — отдельный commit. Bisect-friendly. Tests зелёные после каждого шага.

**После Фазы 1:** структура **проявится наполовину**. Дальнейшие waves (W14.1 → memory/, W12 part Б → storage/, W15 → capacity/, W16.2 → transfer/, W14.6 → io/routes/) создают файлы сразу в правильных местах. Это и есть **разворачивающийся план**, не «один большой refactor».

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

### W14 — Workspace primitive + декомпозиция (16-22ч suммарно)

**Главный архитектурный шаг после B5.** Концепция — [docs/workspace.md](../docs/workspace.md). Implementation план — [workspace-design.md](workspace-design.md).

Идея: **«не ограничиваем систему в действиях, но выбираем из того что она сделала»**. Активное пред-сознательное пространство между divergent generation (детекторы / scout / brief / dmn-bridge / assist reply) и committed graph. Параллель — Global Workspace Theory (Bernard Baars, 1988).

**Реализация:** workspace = **scope над графом**, не отдельный store. Поля `scope: "workspace" | "graph"` + `expires_at` на нодах.

**Asymmetric cost:** дневной режим — cheap (workspace in-memory + bayesian/chem); ночной режим — thoughtful (3 фазы integration). Ночь: NREM replay → REM remote associations → Synaptic homeostasis.

**Sub-waves (11 шагов, ~22-30ч):**

День — cheap workspace operations:
- **W14.1** `src/workspace.py` primitive + scope/expires_at fields (3-4ч)
- **W14.2** `/assist` + user message через workspace (1-2ч)
- **W14.3** alerts → workspace (2-3ч)
- **W14.4** briefings + scout → workspace (2-3ч)
- **W14.5** Cross-кандидатная обработка (scout/SmartDC между similar candidates) (2-3ч)
- **W14.9** Lazy LTM recall queue (1-2ч)

Декомпозиция файлов (после migration):
- **W14.6** assistant.py split → `src/routes/{chat,goals,activity,plans,checkins,profile,briefings,misc}.py` (3-5ч)
- **W14.7** cognitive_loop.py split → `bookkeeping.py + briefings.py + advance_tick` (2-3ч)

Ночь — 3-фазный sleep cycle:
- **W14.8** Phase 1 — Sequential integration (NREM-like) (3-4ч)
- **W14.10** Phase 2 — Cross-batch REM scout (2-3ч)
- **W14.11** Phase 3 — Synaptic homeostasis (1-2ч)

**Ожидаемая дельта:** assistant.py 3105 → ~150, cognitive_loop.py 2628 → ~1200, +workspace.py 150 + 8 routes/*.py.

**Risk:** behaviour drift (alert delay ~5s); hot path performance; ночной cycle time budget per phase; over-aggressive decay в W14.11.

**Закрывает / разблокирует:** Backlog #11 (STM/LTM), Backlog #12 (pruning), W6 investigate-tier, W16.2 (analogy injection нужен chat unification).

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
- 🔶 `substrate/` — частично готова (rgk.py, horizon.py, user_state.py shim рядом)
- 🔶 `process/` — частично готова (nand.py + detectors.py + signals.py + cognitive_loop.py)
- ⏳ `memory/` — ждёт W14.1 (workspace) + W12 part Б (jsonl_store)
- ⏳ `transfer/` — ждёт W16.2
- ⏳ `capacity/` — ждёт W15 (Power) + extract из user_state.py
- ⏳ `io/` — ждёт W14.6 (assistant.py split)
- ⏳ `storage/` — ждёт W12 part Б
- ⏳ `ui_render/` — ждёт W14.6 + W11 #5 (chat package)

**Как это делать (по фазам):**

1. **Фаза 1 — стабильные подгруппы.** Перенести то, что уже готово: `substrate/` (rgk + horizon + user_state shim), `process/` (nand + detectors + signals + cognitive_loop). 1-2 сессии.
2. **Фаза 2 — после W14.1.** Workspace primitive → создать `memory/` с graph_logic + workspace + state_graph + consolidation + action_memory.
3. **Фаза 3 — после W12 part Б.** jsonl_store → `storage/` с goals + plans + recurring + activity_log.
4. **Фаза 4 — после W14.6.** assistant.py split → `io/routes/*.py`.
5. **Фаза 5 — после W15 + W16.2.** capacity + transfer → final подгруппы.

Это **разворачивающийся план**, не «один большой refactor». Каждая фаза — самостоятельная.

**Артефакт когда придёт время:** `planning/file-structure-physics.md` — narrative design doc с rationale per группа + migration plan + соответствие узлам графа.

**Главное преимущество ontology-derived плана:** структура файлов **совпадает** с структурой графа, и каждая директория имеет **онтологическое обоснование** (через `realizes` связи в ветви H). Когда новый разработчик или LLM открывает проект — структура **сама объясняет**, что есть что. Это и есть «mental model для bootstrapping» в чистом виде.

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
