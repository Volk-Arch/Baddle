# Cleanup plan — Phase E-I

> Опциональный line-count cleanup. Не делается по умолчанию. Делать только при 15-25ч непрерывного контекста и реальной боли от code volume (например, при онбординге, или при добавлении 14-й проекции которая упирается в boilerplate).
>
> Связь: [simplification-plan.md](simplification-plan.md) §5 (tradeoffs); [TODO.md](TODO.md) (текущие задачи); [decisions.md](decisions.md) D-1 (adapter pattern), D-5 (singleton РГК).

---

## Контекст: что осталось для line-count цели

`src/rgk.py` (493) + `src/user_state.py` (1342) + `src/neurochem.py` (481) + `cognitive_loop.py` (2549) + `assistant.py` (~3000) + `detectors.py` (~900) + `horizon.py` (~700) — это **~9500 строк state+dynamics+IO+cognitive_loop**, основная боль.

Spec [rgk-spec.md §7](rgk-spec.md) обещал `~6150 → ~2500` (4× reduction). Это был оптимизм — не учёл adapter overhead и bespoke business logic. Реалистичный target после Phase E-I: **~3500-4000 строк** (1.5-1.8× reduction в state+IO+cognitive_loop).

**Adapter overhead** (~500 строк) — UserState/Neurochem/ProtectiveFreeze оставлены как facades поверх `_rgk` потому что внешние callers (cognitive_loop, assistant, detectors, UI JS) ожидают их API: 33 properties + 14 methods + to_dict/from_dict. Без удаления callers facades остаются.

**Bespoke business logic** — `capacity_*` / `named_state` / `frequency_regime` / `focus_residue` это UX-логика, не chem state. Их можно переехать в projectors, но это **semantic move**, не удаление.

**Heavy work** (DMN/REM/pump/scout) — алгоритмы, не state, не сжимаются.

---

## Phase E — `cognitive_loop.py` rewrite

**Scope:** убрать bookkeeping checks которые сейчас читают `user.dopamine`/`neuro.gamma`/etc через property accessors. Заменить на `r.project("user_state")["dopamine"]` reads. Удалить дублирующие state copies в loop-локальных переменных.

**Estimated:** −500..−800 строк | **Время:** 4-6ч | **Risk:** medium

**Что меняется:**
- 21 bookkeeping `_check_*` методов читают через project()
- `_advance_tick` упрощается (state synced через _rgk напрямую)
- helpers (`_generate_sync_seeking_message`, `_build_morning_briefing_*`) тоже на project()

**Что НЕ меняется:** heavy work методы (`_run_dmn_continuous`, `_rem_emotional`, `_run_pump_bridge`, `_run_scout`) — алгоритмы остаются.

**Acceptance:** 83 detector+dispatcher+loop tests pass, 175 phase B tests pass.

---

## Phase F — `assistant.py` rewrite

**Scope:** все 50+ мест где `get_user_state()` + property read → `r.project()`. Удалить остатки legacy energy ссылок (`long_reserve`, `daily_spent` — Phase C удалила, но в комментариях/деталях могло остаться).

**Estimated:** −300..−500 строк | **Время:** 3-5ч | **Risk:** medium

**Acceptance:** все assistant endpoints возвращают то же что и раньше; tests/test_capacity.py + test_resonance_signals.py pass.

---

## Phase G — `src/detectors.py` audit

**Scope:** 13 детекторов читают user/neuro/freeze поля. Простые (1-3 поля) переписать через project(); сложные (multi-step computation, например `detect_observation_suggestions`) оставить.

**Estimated:** −100..−200 строк | **Время:** 1-2ч | **Risk:** low

**Acceptance:** test_detectors.py pass без изменений в самих тестах.

---

## Phase H — Bespoke в projectors

**Scope:** `compute_capacity_*` / `nearest_named_state` / `frequency_regime` derived / `focus_residue` логика — переехать из UserState в `rgk.project()` как 5 функций (semantic move). Сейчас живут как ~250 строк bespoke в user_state.py; в projectors это компактнее (~100 строк), single ownership.

**Estimated:** −150 строк (semantic move, не deletion) | **Время:** 3-4ч | **Risk:** medium

**Что выигрываем:** UserState становится чище — только chem state + HRV passthrough + day_summary. Логика capacity/named/regime — в одном месте (rgk projectors).

**Acceptance:** test_capacity.py + test_resonance_signals.py pass.

---

## Phase I — Удалить facades

**Scope:** UserState/Neurochem/ProtectiveFreeze как классы **удаляются**. Callers переписываются на `r = get_global_rgk()` + `r.project("user_state")["dopamine"]` (или короткий wrapper `r.user.dopamine` если оставить properties в Resonator). Все ~50 callers переписать. UI JS читает тот же `/assist/state` shape.

**Estimated:** −1000..−1500 строк | **Время:** 1-2 дня | **Risk:** **high**

**Prerequisite:** Singleton РГК (см. [decisions.md D-5](decisions.md)) — иначе callers будут обращаться к `UserState._rgk` / `Neurochem._rgk` (3 разных объекта).

**Что меняется publicly:** UserState/Neurochem/ProtectiveFreeze imports → `from src.rgk import get_global_rgk`. Все `update_from_*` методы → `r.feed_*` или explicit method names.

**Acceptance:** все 385 тестов pass; UI работает без визуальных изменений; serialization backward-compat.

---

## Зависимости

```
E ── (cognitive_loop тоньше) ─┐
F ── (assistant тоньше) ──────┤
G ── (detectors тоньше) ──────┼──> I (удаление facades возможно после E+F+G)
H ── (bespoke в projectors) ──┘
```

E/F/G/H можно делать **независимо** в любом порядке. **Phase I** — после хотя бы E+F (критично для самых больших callers).

---

## Что Phase E-I НЕ закроют

- DMN/REM/pump_bridge/scout heavy work — алгоритмы, остаются.
- LLM/HRV/HTTP IO — нельзя сжать.
- UI/CSS/JS — DOM manipulation forced.
- Test суиты — растут с features.

Финальный проект всё равно будет **~5000 строк** в src/. Это **OK** — один человек поддерживает за 2-3ч onboarding.

---

## Decision: делаем или нет?

Если ответ «да, line-count важен» — стартуем с Phase E (cheapest big-bang for buck) или Phase G (smallest, validate подход). Phase I последним.

Если ответ «нет, consolidation done на Phase D, переходим к Tier 2 фичам» — этот документ остаётся как reference. Никаких действий.

Текущее обязательство автора (2026-04-25): **не делается без явного решения**. Tier 2 фичи приоритетнее (см. [TODO.md](TODO.md) § 🌊 Tier 2).
