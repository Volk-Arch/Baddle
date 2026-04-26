# Cleanup plan

> **Track B closed 2026-04-26.** B5 завершён за 24 commits (W0 + W1-W5).
> 2479 → 1583 LOC (−896, −36% substrate code). 473 passed, pyflakes 0.
>
> Связь: [TODO.md](TODO.md), [docs/architecture-rules.md](../docs/architecture-rules.md).

---

## Что сделано

### Wave 0 — Physical model consolidation (12 commits)
Устранены 12 расхождений между facade и РГК (формулы дублировались, константы
жили в нескольких местах, TOD field не обновлялся). Identity bit-preserved.

### Wave 1 — Trivial delegates
Все `update_from_X` методов UserState/Neurochem стали 1-line `return self._rgk.u_X(...)`.

### Wave 2 — Bespoke методы → РГК (4 пакета)
- UserState: `bump_focus_residue`, `apply_*`, `feed_*` → `_rgk.u_focus_bump/apply_/ach_/gaba_*`
- Neurochem: `feed_acetylcholine`, `feed_gaba`, `apply_to_bayes` → `_rgk.s_ach_feed/s_gaba_feed/bayes_step`
- ProtectiveFreeze: `combined_burnout`, `add_silence_pressure` → `_rgk.combined_burnout/add_silence`
- Filesystem-touching `update_cognitive_load`, `rollover_day` → новый `src/user_dynamics.py`

### Wave 3 — ProtectiveFreeze class удалён
22 attribute usages в 6 файлах + 5 method calls мигрированы на `_rgk.X`.
`CognitiveState.freeze` field удалён, callers через `cs.rgk.freeze_active /
conflict.value / silence_press / etc.`. Serialization через
`_rgk.serialize_freeze() / load_freeze(d)`.

### Wave 4 — Neurochem class удалён
15 chem-attribute usages + 6 method calls в `assistant.py`, `cognitive_loop.py`,
`api_backend.py`, `detectors.py`, `horizon.py`, `meta_tick.py` мигрированы.
`CognitiveState.neuro` удалён, `DetectorContext.neuro` тоже.
neurochem.py — 34-LOC stub с migration mapping.

### Wave 5 — UserState callers + class compaction
Production code мигрирован на `_rgk.X` напрямую:
- `assistant.py` 9 callsites, `cognitive_loop.py` 8, `detectors.py` 6,
  `checkins.py` 2, `graph_routes.py`, `assistant_exec.py`, `activity_log.py`,
  `graph_logic.py`, `horizon.py` — всё на rgk.

UserState class сжат с 1295 до 393 LOC: thin facade с @property aliases +
1-line delegate methods. Оставлен как backward-compat shim для 132 test refs.

`compute_sync_error / compute_sync_regime / system_vector / system_state_level`
упрощены — принимают только rgk (раньше 3 args user/neuro/freeze).

`get_user_state()` возвращает singleton привязанный к `get_global_rgk()`.

---

## LOC delta

| Файл | До | После | Δ |
|---|---|---|---|
| `src/user_state.py` | 1295 | 393 | −902 |
| `src/neurochem.py` | 494 | 34 | −460 |
| `src/rgk.py` | 690 | 1038 | +348 |
| `src/user_dynamics.py` | — | 118 | +118 |
| **Total** | 2479 | 1583 | **−896** |

---

## Что НЕ закрылось

- **104 Flask routes** — inherent IO; generic dispatcher = net negative.
- **`execute_deep`** — R4 (deepening / diversity guard / pairwise SmartDC).
- **`graph_logic.py`** — R3 каркас.
- **`cognitive_loop._run_*`** — алгоритмы (DMN/REM/scout).
- **`_assist` mega-route** — business.

---

## Открытое

**UserState shim — 393 LOC.** Можно полностью удалить если переписать 132 test
refs на rgk-only API. Не приоритет — production не использует, identity сохранён.

**Property tests** в `tests/test_rgk_consolidation.py` (12 шт) закрепляют что
РГК = production-path после устранения 12 расхождений.

---

## Recovery (для будущих больших cleanup'ов)

1. `git log --oneline -25` на ветке — найти последний `B5 WN` commit для якоря.
2. `python -m pytest tests/test_metric_identity.py -v` — identity baseline.
3. Если остаётся работа в W5 (UserState shim sweep) — удалить class + переписать
   132 test refs (`tests/test_capacity.py`, `test_metric_identity.py`,
   `test_rgk_properties.py`, etc.) на прямые РГК вызовы.
