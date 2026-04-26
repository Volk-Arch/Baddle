# Cleanup plan

> Реалистичный compression bandwidth по аудиту 2026-04-25: **~420 LOC** через Track A (audit-driven small wins, не трогает adapter pattern) или **~1500-2500 LOC** через Track B (adapter unification, prerequisite Singleton РГК). Floor проекта **~22-23k**, не 5-7k. Архитектура **уже чистая** — 7 правил покрывают модель.
>
> Связь: [TODO.md](TODO.md) (текущие задачи); [docs/architecture-rules.md](../docs/architecture-rules.md) (7 правил + фильтр).

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

## Track A — closed 2026-04-26

A2 + A3 + A4 + A5 + A6 done. Track A не трогает adapter pattern; adapter overhead (~900 LOC) закрывается только через Track B + B5.

---

## Track B — Adapter unification (~1500-2500 LOC, medium-high risk)

Переписать всех callers UserState/Neurochem с property accessors на `r.project()`. Затем **удалить facades полностью**.

### B0. Singleton РГК ✅ done 2026-04-25

Каскад зеркал (UserState/Neurochem/ProtectiveFreeze) делят один объект `get_global_rgk()`.

### B4. Bespoke в projectors ✅ done 2026-04-25/26

- **W1+W2 (2026-04-25):** chem-only derivations + non-chem state move. UserState — thin facade с 15 @property proxies; non-chem projectors `hrv_surprise/frequency_regime/activity_zone`.
- **W3 (2026-04-26):** `project("named_state")` + `project("capacity")` + `_feedback_counts` дедуп.

LOC win не материализуется до B5 — proxy properties исчезнут когда facades удалены. B4 — groundwork.

### B1. `cognitive_loop.py` rewrite через project() — partial 2026-04-26

**Reality check vs audit:** ~21 reads в cognitive_loop читают `u.X`/`gs.neuro.X`/`fz.X` через @property accessors, которые **уже делегируют** к `_rgk.project(...)` после B4 W1+W2+W3. Замена literal `u.X` → `r.project()["X"]` даёт идентичный result, **net 0 LOC**. Audit estimate `−500..−800` оптимистичен — реальный win ~10-30 LOC.

**Что сделано (proof of pattern):**
- `_check_prime_directive_record` payload (39→30 LOC) — три bulk projector reads (`user_state`/`system`/`capacity`) вместо 16 individual property reads.
- `project("capacity")` расширен `zone` ключом (3-zone derived из 3 ok-индикаторов).

**Реальная ценность B1 — refactor для B5:** после удаления facades все callers упадут на `u.X`. Полная migration callers нужна только когда B5 на ходу (атомарно). Делать сейчас как dead refactor — лишняя работа.

**Решение:** B1 закрыт частично (record_tick). Остальные ~20 callers мигрируются inline в B5.

### B2. `assistant.py` rewrite через project()

**Та же ситуация что B1**: reads уже идут через @property accessors с делегацией к project. Refactor имеет смысл только атомарно с B5. Делается inline когда B5 на ходу.

### B3. `src/detectors.py` через project()

**Scope:** 13 детекторов уже читают derived поля (capacity_zone, frequency_regime, silence_pressure) через property accessors. После B5 это автоматически становится `r.project(...)["X"]` — sed-pass на ~30 мин. Без B5 даёт чисто косметический rename.

### B5. Удалить facades (UserState/Neurochem/ProtectiveFreeze)

**Scope:** Классы удаляются. Callers используют `r = get_global_rgk()` + `r.project("user_state")["dopamine"]` (или короткий wrapper `r.user.dopamine`). UI JS читает тот же `/assist/state` shape.

**Estimated:** −1000..−1500 | **Время:** 1-2 дня | **Risk:** **high**

**Prerequisite:** B1 + B2.

---

## Что Track A+B НЕ закроют

- **104 Flask routes** — inherent IO, 80% routes уже минимум 5-15 LOC. Generic dispatcher = net negative.
- **`execute_deep` deepening + diversity guard + pairwise SmartDC** — это R4 в действии, не bespoke.
- **`graph_logic.py`** — R3 каркас.
- **`cognitive_loop._run_*` heavy work** (DMN/REM/scout) — алгоритмы.
- **`_assist` mega-route** — business logic.

---

## Counter-wave (Правило 7) — done 2026-04-26

R7 активирован вчера + Tier 2 расширения сегодня (sync_seeking mode-aware tone + UI R/C индикатор + 4 property test'а на mode trajectory через `_advance_tick`).

---

## Стратегические замечания

**Track B sequencing (revised 2026-04-26):** B0 ✓ → B4 ✓ → **B5 (sole big burst)**. B1+B2+B3 теперь inline-фазы внутри B5 — нет смысла мигрировать callers заранее, когда facades ещё живы (получается dead refactor).

**B5 = единственный непрерывный burst** в Track B. ~1-2 дня, high risk. Включает: удаление UserState/Neurochem/ProtectiveFreeze + inline-миграцию ~80 callers (cognitive_loop ~21, assistant ~50, detectors ~13).
