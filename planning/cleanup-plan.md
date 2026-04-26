# Cleanup plan

> Track A + B0 + B4 done. Остался **B5 — delete facades**, пошагово 6 waves для устойчивости к смене сессии. Identity check после каждой (`python -m pytest tests/test_metric_identity.py` + full suite). Ветка `todo-16` continues.
>
> Связь: [TODO.md](TODO.md), [docs/architecture-rules.md](../docs/architecture-rules.md).

---

## Архитектурные решения

**Принцип π=3.14.** РГК — physical model. Любая константа / формула живёт в одном месте. Facade с extra-логикой = bug, скрытый абстракцией. Wave 0 устраняет 12 найденных расхождений до миграции callers.

**Naming.** `rgk.user.gain.value` (chem-critical inline) + `rgk.project("user_state")["dopamine"]` (UI / aggregate). Mapping `gain~DA / hyst~5HT / aperture~NE / plasticity~ACh / damping~GABA` живёт в [`src/rgk.py:37`](../src/rgk.py:37) Resonator docstring.

**Bespoke методы.** chem-related (`u_apply_checkin`, `u_focus_bump`, `u_ach_feed`, `u_gaba_feed`, `s_ach_feed`, `s_gaba_feed`, `bayes_step`) расширяют РГК API. Filesystem-touching (`update_cognitive_load`, `rollover_day`) → module-level functions в `src/user_dynamics.py` или после удаления facades — в самом `user_state.py` как mod-level.

**Identity contract.** `tests/test_metric_identity.py` (EXPECTED snapshot 2026-04-24) — bit-identical через все waves. Wave 0 добавляет 4 новых property test для покрытия путей которые EXPECTED не трогает (boost / unknown-kind / TOD / hrv-storage).

---

## Wave 0 — Physical model consolidation (4-6ч, low identity risk, high architectural value)

**12 расхождений** найдены чтением facades vs РГК. Каждое — отдельный fix-commit. Identity preserved (формулы идентичны, organisationally в РГК).

### #1 RPE history split — два независимых накопителя
- [`Neurochem._delta_history`](../src/neurochem.py:81) использует production через `cs.neuro.record_outcome` ([`graph_logic.py:112`](../src/graph_logic.py:112))
- [`РГК._rpe_hist`](../src/rgk.py:157) использует только identity test
- One physical concept — два списка. После B5 callers перейдут на `r.s_outcome` → начнут писать в `_rpe_hist`, а `_delta_history` останется в state.json (legacy сюрприз)
- **Fix:** удалить `Neurochem._delta_history` + `record_outcome` body, делегировать `_rgk.s_outcome`. Migration: legacy `_delta_history` → `_rpe_hist` в `from_dict`.

### #2 Surprise boost — есть в facade, нет в РГК
- [`UserState.tick_expectation`](../src/user_state.py:740) читает `_surprise_boost_remaining` + applies fast-decay override
- [`РГК.tick_u_pred`](../src/rgk.py:224) — НЕ читает counter, применяет default decay
- Поле `_surprise_boost_remaining` живёт в [`РГК.__init__:177`](../src/rgk.py:177)
- После Wave 5 callers через `r.tick_u_pred()` → boost потеряет эффект
- **Fix:** перенести boost handling в `_rgk.tick_u_pred`. UserState.tick_expectation → trivial delegate.

### #3 HRV raw values — facade сохраняет, РГК нет
- [`UserState.update_from_hrv`](../src/user_state.py:522) хранит `self.hrv_coherence/stress/rmssd` (через @property → `_rgk.hrv_coherence`)
- [`РГК.u_hrv`](../src/rgk.py:182) только feed'ит EMA, **НЕ записывает** на `self.hrv_coherence`
- После B5 → `r.hrv_coherence` останется `None` forever → `frequency_regime()` всегда `"flat"`, `activity_zone()` всегда `None`
- **Fix:** `_rgk.u_hrv` записывает `self.hrv_coherence/stress/rmssd` (поля уже в РГК).

### #4 Feedback kind filter — РГК accepts любой kind, facade filters
- [`UserState.update_from_feedback`](../src/user_state.py:629): `if kind not in fb: return`
- [`РГК.u_feedback`](../src/rgk.py:195): `self._fb[kind] = self._fb.get(kind, 0) + 1` — добавляет typo'ы, unknown kinds
- **Fix:** `_rgk.u_feedback` skip unknown kinds (set {accepted, rejected, ignored}).

### #5 РГК.u_X методы dead в production
- НИКТО не вызывает `_rgk.u_hrv/u_engage/u_feedback/u_chat/u_plan/u_energy/s_outcome` в production
- Существуют **исключительно** для `_run_identity_sequence` в identity-тесте
- Identity test зеркалит facade behaviour, но НЕ проверяет boost / hrv-storage / kind-filter / TOD
- **Fix:** добавить 4 новых property test в `tests/test_metric_identity.py` после исправлений #1-#4 + #10-#11. Закрепить РГК-path = production-path.

### #6 RPE constants leak
- [`Neurochem.RPE_GAIN = 0.15`](../src/neurochem.py:65), [`RPE_WINDOW = 20`](../src/neurochem.py:64)
- [`РГК.s_outcome`](../src/rgk.py:261) хардкодит `0.15` и `20` без констант
- Если кто-то крутит `RPE_GAIN` в Neurochem class — РГК silently на 0.15
- **Fix:** перенести `RPE_GAIN`/`RPE_WINDOW` в РГК (module-level или class-level), удалить Neurochem.RPE_*.

### #7 Freeze threshold constants — DEAD
- [`ProtectiveFreeze.TAU_STABLE = 0.6`](../src/neurochem.py:376), [`THETA_ACTIVE = 0.15`](../src/neurochem.py:377), [`THETA_RECOVERY = 0.08`](../src/neurochem.py:378)
- [`_rgk.p_conflict`](../src/rgk.py:270) хардкодит магические `0.6 / 0.15 / 0.08` — НЕ читает PF.TAU_STABLE/THETA_*
- Constants полностью dead. Если кто-то правит PF.TAU_STABLE → нет эффекта
- **Fix:** перенести 3 константы в РГК как class-level Resonator/РГК (или module-level), `_rgk.p_conflict` использует named.

### #8 Serialization — `_fb` counter теряется при restart
- `to_dict`/`from_dict` в трёх facades. `_rgk._fb` (rgk.py:159) — shared counter — **не сериализуется ни в одном facade**
- При restart streak counter обнуляется. Reject 3 подряд → restart → streak bias теряется
- **Fix:** добавить `to_dict()/load_state(d)` на РГК как single source. `_fb` входит. Facades.to_dict делегируют в `_rgk.to_dict()` (transition) → удаляются в Waves 3-5.

### #9 `_clamp` external pattern
- [`UserState._clamp()`](../src/user_state.py:709) clamps `activity_magnitude` в [0, 5]
- Вызывается **извне** ([`checkins.py:193`](../src/checkins.py:193): `user._clamp()`)
- Setter [`UserState.activity_magnitude`](../src/user_state.py:423) НЕ clamps, только conversion
- РГК НЕ имеет `_clamp`. После B5 — поведение силно зависит от того где caller помнит про clamp
- **Fix:** clamping в setter РГК (`activity_magnitude` сделать @property с clamp), удалить _clamp метод и вызов из checkins.

### #10 TOD нарезка расхождение
- [`UserState._current_tod`](../src/user_state.py:537): morning [5,11), day [11,17), evening [17,23), night
- [`РГК._current_tod`](../src/rgk.py:305): morning [5,12), day [12,18), evening [18,23), night
- **Один и тот же h=11 — UserState='day', РГК='morning'.** h=17 — UserState='evening', РГК='day'
- `expectation_by_tod[tod]` зависит от какой нарезки. Сейчас прод feeds через UserState нарезку, но `_rgk.hrv_surprise()` (rgk.py:322) и `_rgk.project()` reads через РГК-нарезку (через `_tod` field)
- **Fix:** одна нарезка. Решение — какая правильная (5-11-17-23 или 5-12-18-23)? Я предложу 5-12-18-23 (РГК version, более стандартная). Удалить UserState._current_tod, использовать РГК.

### #11 РГК._tod field никогда не обновляется в production
- [`РГК.__init__:160`](../src/rgk.py:160): `self._tod = "day"` (default)
- В identity test: `r._tod = "day"` (rgk.py:554) — explicit
- В production: НИКТО не обновляет `_tod` — всегда "day"
- `_rgk.u_hrv` (line 187), `_rgk.tick_u_pred` (line 227), `_rgk.project("user_state")` (line 378) — все используют `self._tod` (стале "day")
- Production работает потому что UserState.tick_expectation вычисляет TOD inline через `self._current_tod()` и feeds `self._rgk.u_exp_tod[tod]` напрямую — обходит `_tod`
- После Wave 5 → callers через `r.tick_u_pred()` → `_tod` всегда "day" → expectation_by_tod[morning/evening/night] никогда не обновляются → PE для не-day часов broken
- **Fix:** удалить `_tod` field, всегда вычислять текущий TOD inline через `_current_tod()` в u_hrv/tick_u_pred/project. Identity test переписать так чтобы передавать TOD явно или mock'ить time (`_current_tod` мok'ить на "day" для repeatability).

### #12 Capacity zone duplicate logic
- [`compute_capacity_zone(indicators)`](../src/user_state.py:211) дублирует логику из [`_rgk.project("capacity")["zone"]`](../src/rgk.py:502)
- [`UserState.capacity_zone`](../src/user_state.py:940): `compute_capacity_zone(compute_capacity_indicators(self))` — сначала shim к РГК.project, потом dup-logic поверх
- **Fix:** удалить `compute_capacity_zone` module function. `UserState.capacity_zone` → `_rgk.project("capacity")["zone"]`.

**Verify:** identity 470+ tests green после каждого fix. Один commit на расхождение для bisect-friendly.

---

## Wave 1 — Trivial delegate (1-2ч, near-zero risk, −150..−250 LOC)

После Wave 0 facade.update_from_X = one-line `return self._rgk.u_X(...)`. UserState properties остаются (для mass-callers стабильность), только методы съёживаются.

| Facade method | Тело после Wave 1 |
|---|---|
| [`UserState.update_from_hrv`](../src/user_state.py:500) | `return self._rgk.u_hrv(coherence, stress, rmssd)` (+ activity passthrough если оставить отдельным) |
| [`UserState.update_from_engagement`](../src/user_state.py:599) | `return self._rgk.u_engage(signal)` |
| [`UserState.update_from_feedback`](../src/user_state.py:617) | `return self._rgk.u_feedback(kind)` |
| [`UserState.update_from_chat_sentiment`](../src/user_state.py:655) | `return self._rgk.u_chat(sentiment)` |
| [`UserState.update_from_plan_completion`](../src/user_state.py:669) | `return self._rgk.u_plan(completed, planned)` |
| [`UserState.update_from_energy`](../src/user_state.py:695) | `return self._rgk.u_energy(decisions, max_budget)` |
| [`UserState.tick_expectation`](../src/user_state.py:727) | `return self._rgk.tick_u_pred()` (boost handled inside после #2) |
| [`Neurochem.update`](../src/neurochem.py:148) | already delegate |
| [`Neurochem.tick_expectation`](../src/neurochem.py:168) | already delegate |
| [`Neurochem.record_outcome`](../src/neurochem.py:254) | `return self._rgk.s_outcome(prior, posterior)` (после #1) |

**Verify:** identity + 470+ suite green. Commit "B5 W1 done — trivial delegate".

---

## Wave 2 — Перенести bespoke в РГК / новый модуль (2-4ч, medium, +50..+100 LOC в РГК / +150 user_dynamics.py)

Подготовка к удалению facades — все bespoke methods доступны без UserState/Neurochem/PF.

**В РГК (chem-related):**
- [`UserState.bump_focus_residue`](../src/user_state.py:562) → `_rgk.u_focus_bump(mode_id, now)`
- [`UserState.decay_focus_residue`](../src/user_state.py:588) → `_rgk.u_focus_decay(dt)`
- [`UserState.apply_subjective_surprise`](../src/user_state.py:761) → `_rgk.u_apply_surprise(signed, blend)`
- [`UserState.apply_checkin`](../src/user_state.py:788) → `_rgk.u_apply_checkin(stress, focus, reality)`
- [`UserState.apply_surprise_boost`](../src/user_state.py:808) → `_rgk.u_apply_boost(n_ticks)`
- [`UserState.feed_acetylcholine`](../src/user_state.py:826) → `_rgk.u_ach_feed(novelty, boost)`
- [`UserState.feed_gaba`](../src/user_state.py:847) → `_rgk.u_gaba_feed()`
- [`Neurochem.feed_acetylcholine`](../src/neurochem.py:191) → `_rgk.s_ach_feed(rate, bridge_quality)`
- [`Neurochem.feed_gaba`](../src/neurochem.py:212) → `_rgk.s_gaba_feed(freeze_active, scattering)`
- [`Neurochem.apply_to_bayes`](../src/neurochem.py:284) → `_rgk.bayes_step(prior, d)`
- [`ProtectiveFreeze.add_silence_pressure`](../src/neurochem.py:469) → `_rgk.add_silence(delta)`
- [`ProtectiveFreeze.combined_burnout`](../src/neurochem.py:459) → `_rgk.combined_burnout(user_burnout)`

**Новый `src/user_dynamics.py` (filesystem-touching):**
- [`UserState.update_cognitive_load`](../src/user_state.py:965) → `update_cognitive_load(rgk)`
- [`UserState.rollover_day`](../src/user_state.py:1036) → `rollover_day(rgk, hrv_recovery=None)`

**Verify:** facades всё ещё работают (proxy thin), identity green. Commit "B5 W2 done — bespoke moved".

---

## Wave 3 — ProtectiveFreeze callers + delete (2-3ч, medium, −150 LOC)

Самый маленький facade. PF.X на 6 файлах + 3 метода.

**Read sites (~22):**
- [`src/horizon.py`](../src/horizon.py) 15× — grep `pf\.|freeze\.` (largest)
- [`src/graph_logic.py`](../src/graph_logic.py) 5×
- [`src/detectors.py`](../src/detectors.py) 1×
- [`src/cognitive_loop.py`](../src/cognitive_loop.py) 1×

**Mapping:**
- `pf.conflict_accumulator` → `r.conflict.value`
- `pf.silence_pressure` → `r.silence_press`
- `pf.imbalance_pressure` → `r.imbalance_press.value`
- `pf.sync_error_ema_fast/slow` → `r.sync_fast.value` / `r.sync_slow.value`
- `pf.display_burnout` → `r.project("freeze")["display_burnout"]`
- `pf.active` → `r.freeze_active`
- `pf.update(d, serotonin)` → `r.p_conflict(d, serotonin)`
- `pf.feed_tick(dt, sync_err, imbalance)` → `r.p_tick(dt, sync_err, imbalance)`
- `pf.combined_burnout(ub)` → `r.combined_burnout(ub)` (Wave 2)
- `pf.add_silence_pressure(d)` → `r.add_silence(d)` (Wave 2)
- `pf.to_dict()/from_dict()` → `r.serialize_freeze()` / `r.load_freeze(d)` (на РГК после #8)

**Delete:** `class ProtectiveFreeze` + import + `.freeze` field в `CognitiveState`. Тест `tests/test_protective_freeze.py` переписать на `_rgk` API.

**Verify:** identity + suite green. Commit "B5 W3 done — PF gone".

---

## Wave 4 — Neurochem callers + delete (3-5ч, medium, −400 LOC)

15 chem-attribute usages + 6 file method calls.

**Read sites:**
- [`src/assistant.py`](../src/assistant.py) 6×
- [`src/cognitive_loop.py`](../src/cognitive_loop.py) 6×
- [`src/api_backend.py`](../src/api_backend.py) 1×
- [`src/detectors.py`](../src/detectors.py) 1×
- [`src/horizon.py`](../src/horizon.py) 1×
- [`src/meta_tick.py`](../src/meta_tick.py) 1×

**Mapping:**
- `chem.dopamine/serotonin/norepinephrine` → `r.system.gain.value` / `r.system.hyst.value` / `r.system.aperture.value`
- `chem.acetylcholine/gaba` → `r.system.plasticity.value` / `r.system.damping.value`
- `chem.gamma` → `r.gamma()`
- `chem.recent_rpe` → `r.recent_rpe`
- `chem.expectation_vec` → `r.s_exp_vec.value`
- `chem.self_imbalance` → `r.project("system")["self_imbalance"]`
- `chem.mode` → `r.system.mode`
- `chem.update(...)` → `r.s_graph(...)`
- `chem.tick_expectation()` → `r.tick_s_pred()`
- `chem.record_outcome(prior, post)` → `r.s_outcome(prior, post)` (Wave 1)
- `chem.feed_acetylcholine(...)` → `r.s_ach_feed(...)` (Wave 2)
- `chem.feed_gaba(...)` → `r.s_gaba_feed(...)` (Wave 2)
- `chem.apply_to_bayes(prior, d)` → `r.bayes_step(prior, d)` (Wave 2)
- `chem.update_mode(perturbation)` → `r.system.update_mode(perturbation)`
- `chem.to_dict()/from_dict(d)` → `r.serialize_system()` / `r.load_system(d)`

**Delete:** `class Neurochem` + import + `.chem` / `.neurochem` field в `CognitiveState`. `tests/test_neurochem.py` переписать.

**Verify:** identity + suite green. Commit "B5 W4 done — Neurochem gone".

---

## Wave 5 — UserState callers + delete (5-8ч, high, −600 LOC)

Самый большой scope: ~80 changes. Финальный шаг.

**Read sites:**
- [`src/cognitive_loop.py`](../src/cognitive_loop.py) ~31×
- [`src/assistant.py`](../src/assistant.py) ~23×
- [`src/detectors.py`](../src/detectors.py), [`src/checkins.py`](../src/checkins.py), [`src/horizon.py`](../src/horizon.py), [`src/graph_logic.py`](../src/graph_logic.py) и др.

**Mapping (chem props):** symmetric с Wave 4 на `r.user.X.value`. Aux: `r.valence.value`, `r.burnout.value`, `r.agency.value`, `r.u_exp.value`, `r.u_exp_vec.value`, `r.hrv_coherence`, etc. (всё уже live на РГК после B4 Wave 2).

**Mapping (derived):**
- `user.vector()` → `r.user.vector()`
- `user.state_level()` → inline `(r.user.gain.value + r.user.hyst.value)/2`
- `user.surprise/imbalance/attribution/agency_gap/hrv_surprise` → `r.project("user_state")["X"]`
- `user.frequency_regime` → `r.frequency_regime()`
- `user.activity_zone` → `r.activity_zone()`
- `user.named_state` → `r.project("named_state")`
- `user.capacity_zone/capacity_reason/capacity_indicators` → `r.project("capacity")["X"]`

**Mapping (methods):** все `u_X` aliases из Wave 1+2.

**Module-level fns:**
- `compute_sync_error(rgk)` — было `compute_sync_error(user, neuro, freeze)`
- `compute_sync_regime(rgk)` — было `(user, neuro, freeze)`
- `system_vector(rgk)` — был `(neuro, freeze)`
- `update_cognitive_load(rgk)` / `rollover_day(rgk)` — Wave 2 уже там
- `get_user_state()` → alias на `get_global_rgk()` (или удалить, callsites → `get_global_rgk()`)

**Delete:** `class UserState` + `.user` field + tests/test_user_state.py переписать.

**Verify:** identity + suite + smoke `python -m src.main` green. Commit "B5 W5 done — UserState gone. Track B closed."

---

## Что НЕ закрывается B5

- **104 Flask routes** — inherent IO; generic dispatcher = net negative.
- **`execute_deep` deepening / diversity guard / pairwise SmartDC** — R4 в действии.
- **`graph_logic.py`** — R3 каркас.
- **`cognitive_loop._run_*` heavy work** (DMN/REM/scout) — алгоритмы.
- **`_assist` mega-route** — business logic.

---

## Восстановление при обрыве сессии

1. `git log --oneline -15` на `todo-16` — найти последний `B5 WN done` или `B5 W0 #N` commit.
2. `python -m pytest tests/test_metric_identity.py -v` — identity baseline.
3. Этот файл — точка продолжения с следующей wave / расхождения.
4. После каждого расхождения Wave 0 — отдельный commit (12 commits suggested).
5. После каждой wave 1-5 — один commit с тегом для визуального якоря.
