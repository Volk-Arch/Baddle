# Crystallization Plan — 2026-04-23

> Рабочий документ. Карта дупликаций и план консолидации.
> Начальный snapshot собран из двух параллельных агент-сканов (code + docs) + моего аудита.
> Stage 1 + Stage 2 уже выполнены — см. § Прогресс в конце. Этот документ
> отражает **что осталось** + переоценку после выполненного.

---

## 0. Что уже сделано

**Stage 1 (docs refresh):** timestamps + stub pointers в `user-model-design.md`,
`symbiosis-design.md`, `world-model.md`; новая секция в `neurochem-design.md`
с 3 feeders + self-prediction; `docs/README.md` index fixed.

**Stage 2 (code crystallization):** [src/ema.py](../src/ema.py) — `EMA` +
`VectorEMA` + `Decays` (17 named constants) + `TimeConsts` (4). Все
пред-migration EMA в `ProtectiveFreeze`, `Neurochem`, `UserState` (expectation
family + HRV baselines), `checkins.py` — мигрированы на EMA objects или
named constants. Багfix `checkins.py:193`.

Grep validation: все scattered `decay * x + (1-decay) * signal` в `src/`
кроме `src/ema.py` ушли. Единственная оставшаяся линейная формула
(`horizon.horizon_budget`) — не EMA, mapping.

Полный лог — § 7 ниже.

---

## 1. Что осталось (priority order)

### 1.1 P3 — `Predictor` base class (~1.5ч) · **переоценено после Stage 2**

**Статус:** частично достигнуто через EMA-миграцию. Текущее состояние:
`UserState` уже использует `EMA` + `VectorEMA` объекты для 4-х
предикторов (`_expectation`, `_expectation_by_tod[4]`, `_expectation_vec`,
`_hrv_baseline_by_tod[4]`). Что ещё не унифицировано — **TOD dispatch**
логика: `tick_expectation` и `update_from_hrv` вручную выбирают
`_*_by_tod[self._current_tod()]`.

**Что остаётся:**
- `TODPredictor` class — обёртка над `dict[tod, EMA]` с auto-dispatch
  по текущему TOD. ~15 строк в UserState уйдут.
- `VectorPredictor` (опционально) — `VectorEMA` + методы `attribution`,
  `surprise_vec`. Сейчас эти методы живут в `UserState` как properties.
  Перенос даст симметрию с `Neurochem` (где тоже есть `self_surprise_vec`).

**Trade-off:** ещё один слой абстракции vs ещё немного line reduction.
После Stage 2 EMA уже даёт больше половины обещанного gain. Predictor —
финальный полишинг, не must-have.

**Риск:** средний (горячая зона). Regression risk нивелируется тем что EMA
уже обкатан Stage 2.

---

### 1.15 Docs-consolidation · **расширено по запросу Игоря 2026-04-23**

Игорь отметил что «много разрозненных доков — надо собирать концептами».
Первый шаг (convergence-divergence → cone-design) сделан. Ниже — меню
остальных natural merge candidates по доменам. Выбрать можно по одному
за раз — каждый независим, не блокирует друг друга.

#### A. Операции мышления (~280 lines consolidated) · P2

4 отдельных doc'а, каждый 65-113 строк, описывают атомные operations
на графе:
- [smartdc-design.md](smartdc-design.md) (87) — dialectical 3-pole verification
- [pump-design.md](pump-design.md) (87) — bridge search между далёкими нодами
- [novelty-design.md](novelty-design.md) (65) — similarity filter + rephrase
- [embedding-first-design.md](embedding-first-design.md) (113) — lazy text render

**Предложение:** `thinking-operations.md` с 4 разделами. Каждая operation
получает свою секцию с тем же содержанием, но общие patterns (embedding
similarity, novelty threshold, lineage tracking) описываются один раз в
intro. Экономит ~20% boilerplate, даёт one-stop для «как граф работает».

**Risk:** теряется гранулярность linking (сейчас external refs вида
«см. smartdc-design.md» четкие). Можно сохранить через anchor links.

#### B. Episodic memory / background cycle (~470 lines) · P3

3 тесно связанных doc'а:
- [state-graph-design.md](state-graph-design.md) (204) — append-only JSONL, hash-chain
- [meta-tick-design.md](meta-tick-design.md) (95) — pattern detection на tail
- [consolidation-design.md](consolidation-design.md) (173) — prune + archive

Они образуют **один pipeline:** state_graph накапливает → meta-tick
смотрит на tail → consolidation чистит. Описаны отдельно, но никогда не
используются independently.

**Предложение:** `episodic-memory.md` с overview pipeline + три секции.
~470 строк слитно, но снижена cognitive load (один mental model вместо 3).

#### C. Storage map (~550 lines) · P4

- [ontology.md](../docs/ontology.md) (354) — schema всех 13 data-файлов
- [storage-layout.md](../docs/storage-layout.md) (195) — где что лежит на диске

Overlapping scope: один описывает **что** (schema полей), другой **где**
(файлы + data flow). Natural один doc `storage-map.md` с two levels
(WHERE + WHAT). ~550 строк — большой, но один reference.

**Risk:** ontology сейчас используется как quick reference при написании
кода. Merge может утяжелить.

#### D. DMN/Scout design doc (новый, не merge) · P4

`docs/README.md § Known gaps` уже помечает `dmn-scout-design.md` как
missing. Информация разбросана:
- [full-cycle.md](../docs/full-cycle.md) (335) — кратко упоминает DMN / Scout
- [state-graph-design.md](state-graph-design.md) — heartbeat substrate
- [alerts-and-cycles.md](../docs/alerts-and-cycles.md) — 21 check (DMN continuous / deep / converge / cross)
- [meta-tick-design.md](meta-tick-design.md) — tail detection

**Предложение:** собрать в отдельный `dmn-scout-design.md` — «фоновое
сознание 24/7» — overview + cross-references. ~250 строк. Не merge, а
new canonical doc + stub pointers в остальных.

#### E. «Концептуальная рамка» (4 doc'а) · P5 / не трогать

- nand-architecture.md (435) — фундаментальный примитив
- cone-design.md (~210 после merge) — метафора + ритм
- tick-design.md (187) — cycle mechanics
- full-cycle.md (335) — overview

Все важные, каждый уникален по углу. **Не сливать.** Просто убедиться
что navigation между ними чёткая (NAND → cone → tick → full-cycle —
natural reading order).

---

### 1.2 P4 — Classification Systems unification (~2ч) · **добавлено после пересмотра**

**Проблема (пропущено в первой итерации):** три параллельные системы
labeling одного continuous (user, system, sync_error) пространства:

| Система | Где | Labels | Потребители |
|---|---|---|---|
| `sync_regime` | `user_state.py::compute_sync_regime` | FLOW / REST / PROTECT / CONFESS (4) | UI dashboard, chat_commands, `/assist/alerts` |
| `named_state` | `user_state_map.py::nearest_named_state` (Voronoi) | 10 регионов (flow / stress / burnout / curiosity / ...) | UI dashboard, morning briefing, chat_commands |
| `CognitiveState.state` | `horizon.py` (7 phases) | EXPLORATION / INTEGRATION / PROTECTIVE_FREEZE / CONSOLIDATION / ... | tick machinery, policy_weights |

Каждая читает те же `(UserState, Neurochem, ProtectiveFreeze)` и превращает
в discrete label.

**Семантика действительно разная:**
- `sync_regime` — **bilateral** (рассогласование user↔system)
- `named_state` — **introspective** (user energy × tone)
- `CognitiveState.state` — **defensive/operational** (freeze logic, HRV gating, policy)

**Варианты:**
1. **Leave as-is** — 3 разных axes semantics justify 3 labels. Консервативно.
2. **`StateLabeler` registry** — один объект с методами `.sync_regime()`,
   `.named_state()`, `.cognitive_phase()` — share caching of inputs
   (`(u, neuro, freeze)`). Менее rewrite, больше structure.
3. **Merge pairs** — например `named_state` поглощает `sync_regime`
   (добавить 4 bilateral regions к 10 introspective = 14). Ломает UI.

**Вердикт:** **не рефакторить.** После code-агент reports и re-audit:
- `sync_regime` FLOW в 70%+ случаев (catch-all), активно используется
  только в 2 UI/alert call-sites. Почти-мертвый. **Кандидат на удаление
  если через 2 мес. use FLOW доминирует ≥ 80%** (добавить в `TODO §
  Ждём данных`).
- `named_state` — активно отображается UI, используется в morning briefing,
  chat_commands `/как я?`. Useful.
- `CognitiveState.state` — внутренняя state machine, необходима для freeze
  logic. Не трогать.

Нет трёхстороннего слияния. Но есть **один пункт для «Ждём данных»:**
сбор статистики частоты `sync_regime` transitions через 2 мес. Если
FLOW dominates 80%+ — упростить до `sync_healthy: bool` и удалить
REST/PROTECT/CONFESS.

---

### 1.3 P5 — `Accumulator` strategy pattern (~1ч, опц.) · **переоценено**

**Статус:** теперь что `ProtectiveFreeze` использует `EMA` для 4 из 5
накопителей (conflict / imbalance / 2 × sync_error EMA), остался один
outlier — `silence_pressure` (linear ramp, не EMA).

`feed_tick()` и `update()` уже простые (~30 строк). `Accumulator`
strategy class не даст существенного reduction — это была бы абстракция
ради абстракции.

**Вердикт:** **не делаем.** Статус в плане — RESOLVED via EMA migration.

---

## 2. Side discoveries (pending)

### 2.1 ~~`convergence-divergence.md`~~ — **resolved 2026-04-23**

Объединён в [cone-design.md § Универсальный ритм](../docs/cone-design.md#универсальный-ритм-divergence--convergence).
Удалён как отдельный файл. Natural fit — cone-design был spatial метафорой,
convergence-divergence был universal rhythm того же паттерна; теперь один doc.

### 2.2 Checkin decay'и — namedified, но подбор?

`Decays.CHECKIN_ENERGY=0.85 / STRESS=0.7 / FOCUS=0.7 / VALENCE=0.6` —
значительно агрессивнее чем остальные (0.9-0.99). Это **намеренно** —
явный user input должен корректировать модель сильнее чем автоматические
feeders. Но: эти значения были подобраны осознанно или унаследованы от
первой реализации? Через 2 мес use проверить не слишком ли жёстко.

### 2.3 `sync_regime` FLOW-dominance (см. § 1.2 выше)

Ждём данных 2 мес use. Если FLOW >80% — удалять остальные labels.

---

## 3. Отклонено / отложено (не делаем)

| Кандидат | Почему нет |
|---|---|
| Rename `display_burnout` → `baddle_fatigue` | Cosmetic, 8 call-sites, мало выгоды. Префикс `display_` хоть явно говорит «для UI». |
| `@periodic_check` decorator для 21 check'а | Сэкономит ~44 строки boilerplate, но каждый check уникален условиями и dependencies. Абстракция ради абстракции. |
| HRVSource adapter (миграция HRVManager → SensorStream) | Большой scope (4 файла, 9 call-sites), риск сломать HRV pipeline. Подождать sensor sprint когда реальный Polar подключится. |
| ~~Archive `article_ai_view.md` / `PITCH.md`~~ | Закрыто 2026-04-23: Игорь удалил PITCH, article_ai_view, epilogue, TESTS, DEMO вместе с перемещением TODO / ui-split / crystallization-plan в `planning/`. |
| Merge `sync_regime` + `named_state` + `CognitiveState.state` | Semantics различаются (bilateral vs introspective vs defensive). Потенциальная confusion > gain. Вместо — `sync_regime` кандидат на удаление (см. § 1.2). |
| Attention-weighted PE | Не crystallization, а extension. Живёт в `friston-loop.md § OQ` и `TODO § Edge cases`. |
| Graph storage layers unification | content graph + state_graph + chat_history + actions — разные retention / schema / purposes. Too big, out of scope. |

---

## 4. Порядок (если берём оставшееся)

### Stage 3 — `TODPredictor` + `VectorPredictor` (~1.5ч, опционально)
- [ ] `TODPredictor` class (dict of 4 EMA + auto-dispatch) — в `src/ema.py` или `src/predictor.py`
- [ ] `VectorPredictor` (VectorEMA + attribution/surprise_vec methods)
- [ ] Миграция `UserState._expectation_by_tod` + `_hrv_baseline_by_tod`
- [ ] Миграция `UserState._expectation_vec` (опц.) + `Neurochem._expectation_vec_ema`
- [ ] Smoke test: round-trip, attribution

### Stage 5 (новое, если делаем) — `sync_regime` мониторинг
- [ ] Добавить counter of regime transitions в `prime_directive.jsonl`
- [ ] Через 2 мес — анализ: если FLOW ≥ 80% → удалять остальные labels

---

## 5. Критерии «готово» (для оставшихся stages)

- Если делаем Stage 3: smoke tests + round-trip + attribution same
- Если делаем Stage 5 monitoring: `/assist/prime-directive` возвращает
  `regime_distribution` field

---

## 6. Ссылки

- [friston-loop.md](../docs/friston-loop.md) — single source of truth для PE-layer
- [world-model.md](../docs/world-model.md) — resonance protocol + оптика
- [alerts-and-cycles.md](../docs/alerts-and-cycles.md) — adaptive idle throttling + feeders
- [open-questions.md](open-questions.md) — resolved OQ #3/#4/#6/#7, открытые #1/#2/#5
- [TODO.md](TODO.md) — следующие workstreams (не crystallization)
- [src/ema.py](../src/ema.py) — Decays registry + EMA/VectorEMA classes

---

## 7. Прогресс

- **2026-04-23** — план создан после двух параллельных агент-сканов (code + docs).
- **2026-04-23** — **Stage 1 complete (docs refresh, ~60 мин).**
  - Stub pointers + timestamps в `user-model-design.md`, `symbiosis-design.md`, `world-model.md`.
  - `neurochem-design.md` — новая секция «(2026-04-23) Extended» с 3 feeders + self-prediction + sync_error EMAs.
  - `docs/README.md` — «27 documents» → «40», Known gaps обновлены, dependency graph + finale-note с friston-loop.md.
- **2026-04-23** — **Stage 2 complete (EMA class + migration, ~2.5ч).**
  - `src/ema.py` создан: `EMA` + `VectorEMA` classes (tick-constant + time-constant, seed-on-first, `decay_override` для fast-decay) + `Decays` (17 named constants) + `TimeConsts` (4).
  - `ProtectiveFreeze` — 4 EMA-поля + property accessors. Open-coded формулы убраны.
  - `Neurochem` — 3 scalar EMA + VectorEMA для self-prediction. Properties + setters для compat.
  - `UserState` — EMA objects для `_expectation` + `_expectation_by_tod[4]` + `_expectation_vec` + `_hrv_baseline_by_tod[4]`. Остальные inline формулы переведены на `Decays.*` константы.
  - `tick_expectation`: 40 → 15 строк.
  - Bug fix `checkins.py:193`: `user.surprise = ...` (property без setter'а) → корректный nudge через `_expectation.feed(..., decay_override=0.6)`.
  - Grep validation: все scattered `0.9X * self.` EMA ушли из `src/`.
  - 14 smoke tests pass: init / HRV TOD baseline / surprise boost 7.5× / attribution / feedback+engagement+sentiment / agency+burnout / round-trip / legacy from_dict / Neurochem / ProtectiveFreeze / checkins (no crash) / cognitive_loop._advance_tick integration.
- **2026-04-23** — **план очищен** от выполненных stages + добавлены пропущенные кандидаты (Classification Systems) + переоценка Stage 3/4 после Stage 2.
- **2026-04-23** — **docs merge #1: convergence-divergence → cone-design.** Слита секция «Универсальный ритм divergence/convergence» в [cone-design.md](../docs/cone-design.md). Удалены 152 строки из отдельного файла; 39 docs вместо 40. Обновлены перекрёстные ссылки в `docs/README.md`, `tick-design.md`, `TECH_README.md`. Natural fit — cone был spatial метафорой, convergence-divergence был universal rhythm того же паттерна.
- **2026-04-23** — **добавлен раздел «Docs-consolidation» (§ 1.15)** с 4 merge-кандидатами (A: thinking operations, B: episodic memory, C: storage map, D: new DMN-scout) для следующих раундов.
- **2026-04-23** — **docs merge A + D parallel:**
  - **A:** [thinking-operations.md](../docs/thinking-operations.md) (NEW, ~400 строк) — объединил `smartdc-design.md` + `pump-design.md` + `novelty-design.md` + `embedding-first-design.md` (352 строки → 400 включая общие паттерны). Новая секция «Общие паттерны» (embedding similarity, distinct, rephrase-before-reject, centroid) — deduplication boilerplate.
  - **D:** [dmn-scout-design.md](../docs/dmn-scout-design.md) (NEW, ~270 строк) — собрал разбросанную DMN/Scout-инфо: 4 DMN-check (continuous / deep / converge / cross-graph), Scout + night cycle (REM emotional / creative / Consolidation / patterns / rotation), heartbeat substrate, state-walk, gates, связь с action-memory. Закрыл «Known gap» в docs/README.md.
  - Удалены: smartdc-design, pump-design, novelty-design, embedding-first-design (4 files).
  - 39 → 37 docs (−4 + 2 NEW).
  - Обновлены cross-refs: `docs/README.md` (quick paths + chapter list + Known gaps), `docs/TECH_README.md` (3 таблицы), `ontology.md` / `cross-graph-design.md` / `full-cycle.md` (navigation), `src/cross_graph.py` (module docstring).
- **2026-04-23** — **docs merge B: state-graph + meta-tick + consolidation → episodic-memory.md.**
  - [episodic-memory.md](../docs/episodic-memory.md) (NEW, ~370 строк) — объединил три тесно связанных doc'а (state-graph 204 + meta-tick 95 + consolidation 173 = 472 строки) в единый pipeline doc. Порядок: накопление → pattern detection → cleanup. Никогда не используются независимо.
  - Удалены: state-graph-design, meta-tick-design, consolidation-design (3 files).
  - 37 → 35 docs.
  - Обновлены cross-refs: README.md (quick paths + chapter), TECH_README.md (2 таблицы), dmn-scout-design.md (5 refs), full-cycle.md, action-memory-design.md, nand-architecture.md, static-storage-design.md, hrv-design.md, tick-design.md, storage-layout.md, TODO.md.
- **2026-04-23** — **book-polish round:**
  - README.md полностью переписан (правильная нумерация глав 1-24, актуальный dependency graph, убраны hedge-фразы, вспомогательные doc'а отдельным блоком).
  - Навигационная цепочка сшита: PITCH/epilogue deleted → origin-story теперь «Начало книги», life-assistant → full-cycle напрямую.
  - Удалены 5 docs: `PITCH.md`, `article_ai_view.md`, `epilogue.md`, `TESTS.md`, `DEMO.md`.
  - В `planning/` вынесены: `TODO.md` (из root), `ui-split-plan.md`, `crystallization-plan.md` (этот doc).
  - 35 → 28 docs в `docs/` + 3 в `planning/`.
  - Обновлены cross-refs: root README, docs/README, TECH_README, world-model, open-questions, dmn-scout, full-cycle, origin-story, life-assistant, src/ema.py module docstring.
- **2026-04-23** — **logic-level round (Stage 6 — code consolidation beyond constants):**
  - **#3 Router intent dispatch** — `assistant.py` 4 блока по ~50 строк (`if router_intent.kind==X and subtype==Y and conf>=0.7: try{...}except{warn}`) → `_FASTPATH_ROUTES` registry из 4 tuples + 4 изолированных handler'а + `_fastpath_envelope()` shared response-shape + `_try_fastpath()` dispatcher. Тело `assist()` при этом 160 строк диспатча → 5 строк. `assistant.py`: 3146 → 3194 (+48 overhead, но изолированная зона).
  - **#1 JSON-endpoint wrapper** — новый `src/http_utils.py` (`APIError` + `@json_endpoint`), доступен всему проекту. Мигрированы 3 catch-all workspace endpoint'а (`workspace_create/switch/delete`) с `return jsonify({"error": str(e)})` → `raise APIError(...)` + dict return. `graph_routes.py`: 2048 → 2042 (-6).
  - **#2 cognitive_loop lazy-imports lifted** — 24 повторений `from .horizon import get_global_state` / `from .user_state import get_user_state` разбросанные по методам класса (внутри try/except в 20+ местах) → 2 top-level импорта. Circular import нет (проверено: `horizon` и `user_state` не импортируют `cognitive_loop`). Пары `gs, u = get_global_state(), get_user_state()` оставлены inline — pair-helper оказался false positive (всего 2 реальные пары). `cognitive_loop.py`: 3112 → 3090 (-22).
  - **Валидация:** 7/7 predicate smoke-тестов + full-module imports OK + `_find_distant_pair([])` sanity pass.
  - **Net LOC:** +20 строк в 4 файлах; структурный gain больше чем числовой.
  - **Хвосты — см. § 9 ниже.**

---

## 8. Открытые вопросы к Игорю

1. **Книжная полировка — что дальше?** Возможные шаги:
   - (a) тонкий проход по prose в старых design-doc'ах (horizon / tick / nand / full-cycle — удалить hedge-фразы, «multi-sentence intros», «Что не реализовано» секции со схожими формулировками)
   - (b) сжать `symbiosis-design.md` / `user-model-design.md` — после stub'ов в Stage 1 первые секции дублируют friston-loop
   - (c) merge C (`ontology` + `storage-layout` → `storage-map.md`)
   - (d) возврат к TODO workstreams (README_EN, desktop notif, food, META-questions)
2. **Stage 3 (Predictor base code)** — после Stage 2 ценность уменьшилась. Клонение к «закрыть, достаточно».

---

## 9. Хвосты logic-round (2026-04-23 late) — готовы для следующей волны

Реальные кандидаты обнаруженные во время Stage 6, не затронутые сейчас.
Каждый — независим, можно брать по одному.

### 9.1 `@json_endpoint` докрутка в `graph_routes.py`
Сейчас декоратор применён только к 3 catch-all endpoint'ам (workspace_*).
Осталось:
- [ ] 4 места вида `except Exception as e: return jsonify({"error": f"xxx failed: {e}"})` (строки ~1392, 1420, 1505, 1954 в `graph_routes.py`) — мигрировать на `@json_endpoint`. Gain ~8 строк.
- [ ] 42 validation-return'а (`return jsonify({"error": "empty topic"})` и аналоги) → `raise APIError("...")`. Механическая миграция, требует тщательного прогона — тесты после. Gain ~40 LOC + единая форма errors.
- [ ] Применить `@json_endpoint` ко всем 53 endpoint'ам (массовый catch-all wrapper — любое unhandled-исключение становится JSON 500). Защитный эффект.

**Vote:** делать в одну сессию (~2ч), не растягивать.

### 9.2 Lazy-imports в других файлах
Паттерн `from .cognitive_loop import get_cognitive_loop` inside function встречается в:
- [ ] `assistant.py` — 6 мест
- [ ] `assistant_exec.py` — 2 места
- [ ] `graph_routes.py` — 3 места (один внутри `_with_thinking` wrapper — там обосновано)
- [ ] `suggestions.py` — 2 места

Проверить что нет circular (`cognitive_loop` импортирует assistant/assistant_exec? — скорее всего нет, но подтвердить), поднять наверх. Gain ~10-12 LOC.

**Vote:** быстро (~20 мин), низкий риск.

### 9.3 Второй проход по `cognitive_loop.py` — lazy-imports прочих модулей
Во время #2 заметил: `from .prime_directive import record_tick` (строка 713), `from .cross_graph import seed_from_history`, много локальных `from .X import Y` внутри методов. Не все можно поднять (некоторые — для fault tolerance при optional deps), но подозреваю 5-8 поднимаемых. Gain ~5-8 LOC.

**Vote:** попутно, если будет вторая волна #2.

### 9.4 `_throttled` vs `_throttled_idle`
Два похожих helper'а в `cognitive_loop.py:733` и `:748`. Второй добавляет `silence_pressure`/idle-multiplier поверх первого. Возможно `_throttled` → параметризованный `_throttled(attr, interval_s, idle_aware=False)`. Gain скромный (~20 LOC), но лог один. Риск — средний (затрагивает 21 check).

**Vote:** отложить до sensor-sprint когда throttling и так пересмотрится.

### 9.5 «Базовые правила» архитектурное исследование
Игорь 2026-04-23 поднял идею: может ли приложение быть выведено из нескольких primitives как AND/OR/NAND? Уже есть основа — [nand-architecture.md](../docs/nand-architecture.md) + `distinct()` как единственный примитив. Но в коде этого **не видно**: `distinct()` есть, а вокруг разрастается процедурщина.

Это — не Stage 6-хвост, а **отдельный research workstream**. Возможные pathway:
- Выделить все операции графа к форме `primitive(a, b) → relation`.
- Посмотреть thinking-operations.md (SmartDC/Pump/Novelty/Embedding-first) сквозь призму «это все вариации одного базового оператора над (node, node)».
- Выявить нет ли неявного второго примитива (например `similarity()` или `attention()`) — и свести к одному.

Требует session с sustained thinking + code+docs параллельно. Отдельная сессия.

---
