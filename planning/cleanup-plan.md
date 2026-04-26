# Cleanup plan

> Track A + B0 + B4 done (Counter-wave R7 тоже). Остался **B5 — единственный остающийся big burst** Track B. B1/B2/B3 — inline-фазы внутри B5, не самостоятельные шаги.
>
> Связь: [TODO.md](TODO.md), [docs/architecture-rules.md](../docs/architecture-rules.md).

---

## B5. Удалить facades (UserState / Neurochem / ProtectiveFreeze)

**Scope:** классы удаляются. Callers используют `r = get_global_rgk()` + `r.project("user_state")["dopamine"]` (или короткий wrapper `r.user.dopamine`). UI JS читает тот же `/assist/state` shape. Включает inline-миграцию ~80 callers: cognitive_loop ~21, assistant ~50, detectors ~13.

**Estimated:** −1000..−1500 LOC | **Время:** 1-2 дня | **Risk:** high

**Prerequisite:** ничего внешнего (B0 + B4 готовы — projectors покрывают всё что читают callers).

---

## Что НЕ закроется

- **104 Flask routes** — inherent IO; generic dispatcher = net negative.
- **`execute_deep` deepening + diversity guard + pairwise SmartDC** — R4 в действии, не bespoke.
- **`graph_logic.py`** — R3 каркас.
- **`cognitive_loop._run_*` heavy work** (DMN/REM/scout) — алгоритмы.
- **`_assist` mega-route** — business logic.
