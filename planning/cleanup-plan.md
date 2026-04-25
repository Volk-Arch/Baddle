# Cleanup plan

> Реалистичный compression bandwidth по аудиту 2026-04-25: **~420 LOC** через Track A (audit-driven small wins, не трогает adapter pattern) или **~1500-2500 LOC** через Track B (adapter unification, prerequisite Singleton РГК). Tracks **не пересекаются** по scope. Floor проекта **~22-23k**, не 5-7k. Архитектура **уже чистая** — 7 правил покрывают модель.
>
> Связь: [simplification-plan.md](simplification-plan.md) §5 (tradeoffs); [TODO.md](TODO.md) (текущие задачи); [decisions.md](decisions.md).

---

## Контекст: что показал аудит

`src/` ~25k LOC — обманчивая цифра. Реальный breakdown:

| Категория | LOC | % |
|---|---|---|
| **IO/HTTP routes** (104 Flask endpoints + persistence) | ~9700 | 39% |
| **R6 Resonator state** (rgk + adapter facades) | ~3200 | 13% |
| **R3 Graph node** (graph_logic + store + state_graph) | ~2800 | 11% |
| **Algorithm/Heavy work** (DMN/REM/scout/pump) | ~1900 | 8% |
| **R1 Signal driver** (signals + detectors + suggestions) | ~1800 | 7% |
| **R4 distinct primitive** (tick_nand + thinking + dialectic + meta) | ~1400 | 6% |
| **Misc/glue** (defaults, prompts, modes, demo, init) | ~1200 | 5% |
| **Adapter overhead** (UserState/Neurochem facades поверх _rgk) | ~900 | 4% |
| **Bespoke pockets** (execute_deep, suggestions weekly, chat_commands NL) | ~1500 | 6% |
| **R2 EMA** (ema.py) | ~330 | 1% |
| **R5 PE driver** (распределён) | ~300 | 1% |

**60% src/ — каркас 7 правил. 39% — inherent IO. 10% — реально сжимаемо.**

---

## Track A — Audit-driven small wins (~420 LOC, low risk)

5 шагов из аудита. **Не трогают adapter pattern** (UserState/Neurochem facades живут). Можно делать в любом порядке. Не требуют архитектурных prerequisites.

> Adapter overhead (~900 LOC) **не закрывается Track A** — для него нужен Track B (через `r.project()` + delete facades).

### A2. NS_HINT chem-routing → prompts.py ✅ done

`_NS_HINT` inline dict в `assistant_exec.py:740-756` (8 entries × 2 langs = 17 LOC) → `_p(lang, f"ns_hint_{ns_key}")` в prompts.py.

**Done 2026-04-25:** −14 LOC inline, +16 LOC prompts.py, single source of truth.

### A3. Sync_seeking + retro text templates → prompts.py

**Scope:** `src/cognitive_loop._generate_sync_seeking_message` + retro text generation — шаблонная генерация с `if lang == "ru"` блоками. Перенести в prompts.py как именованные templates.

**Estimated:** −80 LOC | **Время:** 30 мин | **Risk:** low

### A4. Suggestions 5 sources → unified `SOURCES` table

**Scope:** `src/suggestions.py` — 5 detector wrappers `suggest_from_*` каждый со своим LLM hint + `try/except return None` boilerplate. Унифицировать в `SOURCES = [(name, collector_fn, draft_hint_fn), ...]`.

**Estimated:** −150 LOC | **Время:** 1ч | **Risk:** medium

### A5. Morning briefing 13 sections → registry

**Scope:** `src/cognitive_loop.py:1673-2065` — 13 sections (capacity/named_state/recurring/checkins/sleep/...) каждая ~30 LOC inline. Переписать как `SECTIONS = [(collector_fn, render_template, condition), ...]`.

**Estimated:** −150 LOC | **Время:** 1ч | **Risk:** medium

### A6. Per-mode prompts (51 inline `if lang == "ru"` blocks) → prompts.py

**Scope:** `src/assistant_exec.py` содержит 51 occurrence `if lang == "ru"` для prompt templates. Каждый — bespoke template inline. Перенести в `prompts.py` с осмысленными ключами.

**Estimated:** −40 LOC | **Время:** 1-1.5ч | **Risk:** medium (user-facing prompts, неточный ключ ломает UX).

---

## Track B — Adapter unification (~1500-2500 LOC, medium-high risk)

Альтернативный путь: вместо точечных wins — переписать всех callers UserState/Neurochem с property accessors на `r.project()`. Затем **удалить facades полностью**.

### B0. Singleton РГК ✅ done 2026-04-25

`src/rgk.py` — `get_global_rgk()` + `reset_global_rgk()`. UserState/Neurochem/ProtectiveFreeze принимают keyword-only `rgk=` (default `None` → создаётся новый, backward-compat для тестов). Production bootstrap: `get_user_state()` + `CognitiveState.__init__` передают `rgk=get_global_rgk()` — каскад зеркал на одном объекте. 5 smoke tests добавлены, 398 passed.

### B1. `cognitive_loop.py` rewrite через project()

**Scope:** ~21 bookkeeping checks читают `user.dopamine`/`neuro.gamma` через property accessors. Заменить на `r.project("user_state")["dopamine"]`. Удалить дублирующие state copies.

**Estimated:** −500..−800 | **Время:** 4-6ч | **Risk:** medium

**Что НЕ меняется:** heavy work (`_run_dmn_*`, `_rem_emotional`, `_run_pump_bridge`, `_run_scout`) — алгоритмы остаются.

### B2. `assistant.py` rewrite через project()

**Scope:** все ~50 мест где `get_user_state()` + property read → `r.project()`.

**Estimated:** −300..−500 | **Время:** 3-5ч | **Risk:** medium

**Аудит caveat:** «fat routes» (`_assist` 405 LOC, `_morning`, `_weekly`, `/decompose`) — business logic, не сжимается. Win — в маленьких routes которые делают `cs.get_metrics()` boilerplate.

### B3. `src/detectors.py` через project()

**Scope:** 13 детекторов читают user/neuro/freeze поля. Простые (1-3 поля) переписать через project(); сложные (multi-step) оставить.

**Estimated:** −100..−200 | **Время:** 1-2ч | **Risk:** low

### B4. Bespoke в projectors (semantic move) ✅ done 2026-04-25

**Wave 1:** `RGK.project("user_state")` расширен chem-only derivations: ACh/GABA/balance/mode (Phase D + B0) + attribution/attribution_magnitude/attribution_signed + agency_gap + surprise_vec. UserState attribution/agency_gap properties + Neurochem self_imbalance/gamma переведены на delegation к project(). 6 smoke tests.

**Wave 2:** state move + non-chem projectors. HRV state (3 fields) + activity_magnitude + day_summary + cognitive_load_today + last_sleep_duration_h + focus_residue + 4 timestamps (`_last_focus_input_ts`/`_last_focus_mode_id`/`_last_input_ts`/`_last_user_surprise_ts`) + `_surprise_boost_remaining` перемещены из UserState в РГК. UserState — thin facade с 15 @property proxies. Non-chem projectors добавлены: `hrv_surprise()`, `frequency_regime()`, `activity_zone()` + `_current_tod()` helper. UserState frequency_regime/hrv_surprise/activity_zone делегируют. 10 smoke tests. **414 passed, 0 regressions.**

**Net result:** ~150 LOC грубо, как audit estimated. user_state.py: +13 LOC (15 proxy properties − removed inline derivations и __init__ assignments). rgk.py: +147 LOC. Net repo growth ~150 LOC. **LOC win не материализуется до B5** — proxy properties исчезнут когда facades удалены. B4 — groundwork для B5.

**Что ещё в Wave 2 НЕ сделано (можно отдельной сессией):**
- `capacity_zone` / `capacity_reason` / `capacity_indicators` — `compute_capacity_*` функции остаются в user_state.py module-level. Они читают `self.hrv_coherence`/`self.serotonin`/etc. через UserState, но эта логика не унесена в РГК. Зависит от cognitive_load_today + day_summary + activity_log.
- `named_state` — UserState.named_state property делегирует к `user_state_map.nearest_named_state` с chem args. Может быть projector в РГК.
- `_feedback_counts` — duplicate с `_rgk._fb`. Cleanup.

### B5. Удалить facades (UserState/Neurochem/ProtectiveFreeze)

**Scope:** Классы удаляются. Callers используют `r = get_global_rgk()` + `r.project("user_state")["dopamine"]` (или короткий wrapper `r.user.dopamine`). UI JS читает тот же `/assist/state` shape.

**Estimated:** −1000..−1500 | **Время:** 1-2 дня | **Risk:** **high**

**Prerequisite:** B0 + B1 + B2 + B3 (хотя бы B1+B2).

---

## Что Track A+B НЕ закроют

- **104 Flask routes** — inherent IO, 80% routes уже минимум 5-15 LOC. Generic dispatcher = net negative (см. аудит H3 verdict).
- **`execute_deep` deepening + diversity guard + pairwise SmartDC** — это R4 в действии, не bespoke.
- **`graph_logic.py`** — R3 каркас.
- **`cognitive_loop._run_*` heavy work** (DMN/REM/scout) — алгоритмы.
- **`_assist` mega-route** — business logic.

---

## Counter-wave (Правило 7) — активирован 2026-04-25

`Resonator.update_mode(perturbation)` теперь вызывается в `cognitive_loop._advance_tick`. Dispatcher понижает urgency push-style сигналов при `user.mode='C'` (см. [signals.py](../src/signals.py) `COUNTER_WAVE_PUSH_TYPES`).

**Tier 2 расширения** (см. [TODO.md § Tier 2 Резонансные](TODO.md)):
- Sync_seeking explicit mode-aware tone (~1ч)
- UI R/C индикатор в balance widget (~30 мин)
- Property test на mode trajectory через `_advance_tick` (~30 мин)

---

## Стратегические замечания

**Track A vs Track B trade-off:**
- **Track A** — низкий риск, точечные wins (templates/registries/sources). Не трогает adapter pattern. Подходит для bursts свободного времени. Adapter overhead (~900 LOC) остаётся.
- **Track B** — большой ROI на сжатие через РГК-only архитектуру. Требует ~2-3 дней непрерывного контекста + B0 (Singleton) + риск breaks UI/tests. Соответствует РГК-spec'у «один резонатор, всё остальное проекции» — финализирует Phase D.

**Sequencing если делается Track B (skorrektirovano по факту B0):**
B0 (Singleton) → **B4 (расширить project() bespoke fields)** → B3 (detectors pilot) → B1 (cognitive_loop) → B2 (assistant) → B5 (delete facades).

**Почему B4 перед B3:** текущий `RGK.project("user_state")` покрывает chem axes + expectation + imbalance, но НЕ покрывает derived bespoke (capacity_zone, named_state, frequency_regime, focus_residue, hrv_surprise). Детекторы читают именно derived. Без B4 у B3 нет ground.

**Текущее обязательство (2026-04-25):** Track A продвигается incrementally (A2 done). Track B — **не делается без явного решения о ~2-3 днях работы**. Tier 2 фичи приоритетнее по value-per-hour.
