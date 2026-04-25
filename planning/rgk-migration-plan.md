# Phase D — РГК-коллапс: пошаговый план миграции

> Зафиксировано 2026-04-25 после прототипа [src/rgk.py](../src/rgk.py) и решения автора «идём в полную 5-axis физмодель, не упрощаем». Документ — **рабочий план**, читать по шагам, отмечать прогресс.
>
> Phase D = четвёртая фаза consolidation после A (metric registry), B (signal dispatcher), C (3-zone capacity). См. [simplification-plan.md](simplification-plan.md) §11.

---

## 1. Контекст и предусловия

**Что готово (✓):**
- Phase A+B+C завершены (2026-04-24/25)
- РГК-spec — [rgk-spec.md](rgk-spec.md) — единая физическая модель (5-axis + balance + R/C bit)
- Прототип [src/rgk.py](../src/rgk.py) — identity 42/42 на event sequence Phase A
- Диагностика что НЕ покрывается ядром — §6.5 spec'а

**Что блокирует Шаг 4 (рефакторинг user_state):**
- Дизайн feeders для ACh (plasticity) и GABA (damping) — без них 5-axis не замыкается. См. §6 ниже.
- Property-based test contract — fixed-point identity недостаточен (real-world events разойдутся), нужны инварианты. См. §5.

**Что параллельно (не блокируется):**
- Calibration window urgency (Phase B) — пишет throttle_drops.jsonl, ortogonal к state
- Detector-content (`detect_evening_retro` extension, `morning_briefing` causal explanation) — читают `project("user_state")`, не зависят от внутренней структуры

**Что замораживается до merge Phase D:**
- TODO «Pure-function formulas в один файл» — Phase D делает глубже
- TODO «5-axis ACh+GABA как отдельный пункт» (§12 simplification-plan) — встроено
- `aperture` скаляр в depth engine ([resonance-code-changes.md](resonance-code-changes.md)) — после Phase D станет derived из `r.user.aperture × r.balance()`
- Calibration `cognitive_load` коэффициентов — после коллапса cognitive_load — projection

---

## 2. 8 шагов

| # | Что | Время | Acceptance |
|---|---|---|---|
| **1** | Update spec + миграционный план | 1-2ч | rgk-spec §6.5/§7.5 + этот документ |
| **2** | Property-based tests | 2-3ч | tests/test_rgk_properties.py: 6 инвариантов pass на random event traces |
| **3** | Branch + collapse user_state.py | 6-8ч | Identity 42/42 сохраняется через `UserState` adapter поверх `РГК` |
| **4** | Collapse neurochem.py + ProtectiveFreeze | 3-4ч | Identity 42/42 сохраняется (та же checkpoint) |
| **5** | cognitive_loop._advance_tick adapter | 4-6ч | 175 существующих тестов pass; idle_multiplier читает r.balance() |
| **6** | Detector API compat | 3-4ч | 13 detectors pass через `ctx.user.project()` без переименования fields |
| **7** | UI / endpoints / serialization | 2-3ч | `/assist/state` shape stable, balance() добавлен в response, 3-zone UI работает |
| **8** | Cleanup + docs sync | 3-4ч | dead code удалён, simplification-plan.md обновлён (Phase D done), world-model.md и resonance-model.md sync |
| | **Итого** | **25-35ч** | |

Шаги 1+2 — без кода, фундамент. Шаги 3+4 — главный коллапс (один sitting). Шаги 5-8 — интеграция и cleanup, дробится на отдельные сессии.

---

## 3. Acceptance criteria детально

### Шаг 1 (этот документ + spec)

- [x] rgk-spec.md §6.5 «Диагностика после прототипа»
- [x] rgk-spec.md §7.5 «Уточнённая оценка после прототипа»
- [x] rgk-spec.md §9 помечен как ✓ выполнено + pointer на migration-plan
- [x] planning/rgk-migration-plan.md (этот файл)

### Шаг 2 — Property-based tests

Задача: заменить bit-identity на инварианты которые держатся **на любых** event sequences, не только на fixed Phase A. Identity-тест Phase A остаётся как **fixed-point reference** (один тест), не как контракт.

6 инвариантов для нового `tests/test_rgk_properties.py`:

| # | Инвариант | Property |
|---|---|---|
| 1 | **Mode dynamics**: при стабильной perturbation > THETA_ACT, mode→C в течение N тиков; perturbation < THETA_REC, mode→R | hysteresis монотонна |
| 2 | **Balance bounds**: при штатном flow событий из identity sequence balance() ∈ [0.2, 2.5] | модель не уходит в гипер/гипо |
| 3 | **Coupling reduction**: после mode_user→C, если system gas'ит (генерирует контрволну), sync_error падает в течение 5+ тиков | контрволна работает |
| 4 | **Chem bounds**: для **любого** random event sequence все 5 chem ∈ [0,1], valence ∈ [-1,1] | clamp работает |
| 5 | **PE consistency**: surprise = state_level − expectation_by_tod[cur], `|surprise| ≤ 1` всегда | derived consistency |
| 6 | **Phase A identity** (sentinel): на fixed event sequence project("user_state"/"system"/"freeze") дает EXPECTED ± 1e-5 | Phase A snapshot воспроизводится |

Используем `hypothesis` для генерации random event traces (если ещё не в зависимостях — добавить в pyproject), либо ручные edge-case sequences если hypothesis не хочется тащить.

### Шаг 3 — Collapse user_state.py

Стратегия: **adapter pattern**, не replace. `UserState` остаётся publicly как класс, его методы `update_from_*` и properties `dopamine`/`serotonin`/`...` становятся **wrappers** поверх `РГК.user` resonator + AuxAxes + PredictiveLayer.

```python
# src/user_state.py после коллапса (~150 строк)

from .rgk import РГК  # одна импортируется

class UserState:
    """Adapter поверх РГК.user. Сохраняет publicly известные fields/methods."""
    def __init__(self, ...):
        self._rgk = РГК()  # owns one РГК; alternative — shared global
        # ... seed initial values

    @property
    def dopamine(self): return float(self._rgk.user.gain.value)
    @property
    def serotonin(self): return float(self._rgk.user.hyst.value)
    @property
    def norepinephrine(self): return float(self._rgk.user.aperture.value)
    @property
    def valence(self): return float(self._rgk.valence.value)
    # ... etc

    def update_from_hrv(self, coherence=None, stress=None, ...):
        self._rgk.u_hrv(coherence=coherence, stress=stress, ...)

    def update_from_engagement(self, signal=0.65):
        self._rgk.u_engage(signal)

    def vector(self): return self._rgk.user.vector()
    def state_level(self): return ...
    # ... etc

    def to_dict(self): return self._rgk.project("user_state") | {...}
    @classmethod
    def from_dict(cls, d): ...  # populate _rgk via direct setters
```

Acceptance: identity-тест `tests/test_metric_identity.py` 42/42 pass без изменений в тестах. То есть adapter полностью прозрачен.

### Шаг 4 — Collapse neurochem.py + ProtectiveFreeze

Аналогично Шагу 3: `Neurochem` — adapter поверх `РГК.system` resonator + bespoke RPE history. `ProtectiveFreeze` — adapter поверх `РГК.pressure` PressureLayer.

Тут происходит **реальный физический момент**: ACh + GABA получают feeders (см. §6 ниже). До этого момента 5-axis chem в коде есть, но plasticity/damping = 0.5. После этого момента — наблюдаемые.

Acceptance: identity 42/42 + новые поля `Neurochem.acetylcholine`, `Neurochem.gaba`, `UserState.acetylcholine`, `UserState.gaba` доступны через project(). balance() становится осмысленной (не private 0.5×0.5).

### Шаг 5 — cognitive_loop._advance_tick

Что меняется:
- `_advance_tick` теперь делает `r.tick_u_pred()` + `r.tick_s_pred()` + `r.feed_pressure_tick(dt, ...)` — три вызова вместо разбросанных по coupling-логике
- `_idle_multiplier` теперь использует `max(r.balance() < 0.5, r.pressure.display_burnout())` — добавляется balance-based замедление как индикатор гипостабильности
- `_check_action_outcomes`/`_check_hrv_push`/`_check_sync_seeking` остаются bookkeeping, читают через `r.project(...)`

Acceptance: 175 текущих тестов pass без изменений.

### Шаг 6 — Detectors API compat

13 детекторов в `src/detectors.py` читают user_state fields. Сейчас они читают через прямой attribute access (`user.dopamine`, `user.frequency_regime`, `user.capacity_zone`). После Шага 3 эти properties работают через adapter — детекторы НЕ нужно править. Но проверить что:

- `frequency_regime` derived остаётся доступен как property
- `capacity_zone`, `capacity_reason` derived через `project("capacity")` (новый domain)
- `focus_residue` остаётся отдельным полем UserState (это user-state, не chem)
- `named_state` (Voronoi) — derived property работает через user.serotonin/.dopamine/.norepinephrine

Acceptance: все 13 детекторов сохраняют поведение. Тесты detector'ов pass.

### Шаг 7 — UI / endpoints / serialization

Что меняется в `/assist/state` response shape:
- **Добавляется**: `balance: {user, system}` (новый scalar для UI)
- **Добавляется**: `mode: {user_mode, system_mode}` (R/C bit)
- **Опционально**: `acetylcholine`/`gaba` в `user_state` и `system` projection (если хотим показать в Lab)
- **Не меняется**: `dopamine`/`serotonin`/`norepinephrine`/`valence`/`burnout`/`agency`/`capacity_zone` — те же имена, тот же диапазон

Serialization: `to_dict`/`from_dict` остаются `UserState`/`Neurochem`/`ProtectiveFreeze` adapter-методами — `data/state.json` rolling forward без миграционного скрипта. Новые поля (acetylcholine/gaba) appendly: при load если их нет → seed defaults 0.5.

Acceptance: UI работает без изменений в JS (старые fields читаются), новые fields опционально показываются в Lab.

### Шаг 8 — Cleanup + docs

- Удалить из `src/user_state.py`/`neurochem.py` дублирующиеся EMAs которые теперь живут в `_rgk` (если adapter полностью прозрачен — старые поля просто становятся property-only)
- `src/ema.py::Decays` — оставить, decays конфигурируются в одном месте (РГК Resonator factories их используют)
- `simplification-plan.md` — добавить «Phase D ✓ Завершена YYYY-MM-DD: РГК-коллапс. ~6150→~500 строк state+dynamics. 5-axis полная (ACh+GABA с feeders X/Y).»
- `docs/world-model.md` — apдeйт §«Связь с существующей архитектурой»: Neurochem.burnout → r.pressure.display_burnout, etc.
- `docs/resonance-model.md` — секция «Нейрохимия как параметры резонатора» теперь не «5 классических ручек» а **реализованная** модель в коде. γ-формула остаётся как backward-compat derived (если используется); основная diagnostic = balance().
- `planning/rgk-spec.md` — пометить §11 «когда делать» как ✓ done.

Acceptance: документы синхронизированы с кодом. Нет inconsistency.

---

## 4. Matrix замораживания TODO

| TODO-пункт | Статус относительно Phase D |
|---|---|
| Calibration urgency формул (compute_urgency) | **Параллельно** — данные пишутся независимо |
| Calibration cognitive_load коэффициентов | **Заморожено до merge** — после коллапса коэффициенты применятся к финальному коду |
| detect_evening_retro extension | **Параллельно** — content-work над detector |
| detect_morning_briefing causal | **Параллельно** — content-work |
| /assist/simulate-day reimplementation | **Параллельно** — capacity-based, не state |
| Pure-function formulas в один файл | **Удаляется из TODO** — Phase D делает глубже |
| 5-axis ACh+GABA расширение (§12 simplification-plan) | **Встраивается в Phase D Шаг 4** |
| `aperture` скаляр в depth engine | **Заморожено** — после Phase D станет derived из РГК |
| breathing-mode | **Параллельно** — читает frequency_regime через project() |
| resonance-prompt-preset | **Параллельно** — UI-only, без backend |
| PolarH10Adapter, sensor stream | **Параллельно** — точка входа `u_hrv()` стабильна |
| AppleWatchAdapter | **Заморожено** — слишком близко к response shape changes Шага 7 |
| Constraint expansion / auto-parse / plan.create_from_text | **Параллельно** — profile/LLM content |
| META-questions, food suggestions | **Параллельно** — DMN/scout content |
| patterns auto-abandon, fan/rhythm card-renders | **Параллельно** — UI/UX |
| Action Memory расширение `score_action_candidates` | **Параллельно** — graph-content |
| Dialog pivot detection | **Параллельно** — surprise_detector content |

---

## 5. Property-based test contract (для Шага 2)

### Зачем

Identity-тест Phase A — это fixed-point: **один** event sequence даёт **одни** числа. После Phase D порядок операций внутри коллапсированного state может измениться в неочевидных местах (например, `tick_expectation` сейчас вызывается в конце `update_from_hrv`, после Phase D это будет один call внутри `r.u_hrv` — но порядок операций внутри fire_event vs внутри Resonator может разойтись на ε). Bit-identity на любых данных — недостижимо. Semantic identity на Phase A sequence — достижимо (прототип показал) и сохраняется как **sentinel**.

Property-based tests — про общие инварианты, которые держатся **на любых** event sequences. Без них регрессия может проскочить когда мы добавим новый детектор который кормит chem нестандартно.

### 6 инвариантов

**Inv 1 — Mode hysteresis monotone**
```
Given r = РГК(); fix sequence of perturbations p_1..p_N where all p_i > THETA_ACT.
After feeding p_1..p_K (small K), eventually mode→C.
After feeding p_K+1..p_N where all p_i < THETA_REC, eventually mode→R.
Mode never flips back without crossing the opposite threshold.
```

**Inv 2 — Balance corridor on identity flow**
```
Run identity event sequence (or random Hypothesis-generated valid trace).
balance() must stay in [0.2, 2.5] all the way.
Outside this corridor = модель неполна или входы за пределы реалистичных.
```

**Inv 3 — Counter-wave reduces sync_error**
```
Given r with user.mode = "C" and system.mode = "C" (both generating counter-wave).
sync_error[t] should be monotone non-increasing for at least 5 consecutive ticks
(в которых нет новых perturbations).
```

**Inv 4 — Chem bounds on random traces**
```
Hypothesis: for any sequence of (event_type, payload) pairs from a defined alphabet:
- 5 chem axes ∈ [0, 1]
- valence ∈ [-1, 1]
- agency ∈ [0, 1]
- burnout ∈ [0, 1]
Никогда не выходят за границы (clamp работает).
```

**Inv 5 — PE derived consistency**
```
After arbitrary update sequence:
- surprise == state_level − expectation_by_tod[current_tod] (или fallback)
- |surprise| ≤ 1
- imbalance == ‖vector − expectation_vec‖, в [0, √3]
- attribution ∈ AXIS_NAMES ∪ {"none"}
```

**Inv 6 — Phase A snapshot sentinel**
```
On EXPECTED event sequence, project("user_state") and project("system") and
project("freeze") match EXPECTED dicts within TOL=1e-5.
```

Implementation note: hypothesis может усложнить debug при failure. Альтернатива — table-driven random seeds (10 fixed seeds, each generates 100-step trace, check inv 1-5).

---

## 6. Дизайн ACh + GABA feeders

> Без этого Шаг 4 невозможен — 5-axis chem остаётся 3-axis с двумя hardcoded 0.5.
>
> Опора: [docs/resonance-model.md § Нейрохимия как параметры резонатора](../docs/resonance-model.md). 5 модуляторов с биологической функцией.

### ACh — Plasticity (текучесть ткани)

Биологическая роль: «снижает энергетическую стоимость перестройки, открывает режим обучения, новизна как драйвер пластичности» (resonance-model). DA уже ловит «хочу нового» (RPE), ACh должен ловить «**готов перестраиваться**» — отдельный сигнал.

#### System-side (Neurochem.acetylcholine)

| Кандидат | Источник | Формула | Pro | Contra |
|---|---|---|---|---|
| **A. Node-creation rate** | record_card / record_action / DMN-bridges | `EMA(nodes_added_per_hour / cap, decay=0.95)` | Прямой proxy «текучесть графа» | Зависит от graph-event hookup, нужна интеграция в graph_logic |
| **B. Embedding novelty** | distinct(new_node, recent_nodes) | `EMA(distinct_avg_for_recent_inserts, decay=0.92)` | Близко к семантике «насколько новое то что заехало» | Считается per-insert, performance |
| **C. DMN-bridge frequency** | _check_dmn_bridges + _check_dmn_deep_research success rate | `EMA(bridge_quality_when_found, decay=0.9)` | Прямо отражает «находит мосты = ткань пластична» | Только когда DMN активен; в idle ACh «замораживается» |
| **D. Confidence churn** | std(Δconfidence) активных нод за час | `EMA(churn_normalized, decay=0.95)` | Универсально, не зависит от типа события | Шумит когда мало активных нод |

**Рекомендация: A + C combo.**
```python
# Источник 1: новые ноды (graph_logic emits "node_added" event)
acetylcholine.feed(min(1.0, nodes_added_last_hour / 10.0))

# Источник 2: bridge quality (DMN при success)
acetylcholine.feed(bridge_quality, decay_override=0.9)
```

Decay 0.95 — медленный baseline, час+ memory. Без обоих feeders → стандартный 0.5 (нейтральная пластичность). Когда DMN активно находит мосты + ноды добавляются — поднимается к 0.7-0.8.

#### User-side (UserState.acetylcholine)

User-side ACh — про «юзер в режиме обучения / открыт новому». Прямые сигналы:

| Кандидат | Источник | Формула |
|---|---|---|
| **U-A. Message novelty** | distinct(msg_curr, msg_prev_5) | `EMA(distinct_avg, decay=0.92)` — высокая если темы прыгают/новые |
| **U-B. Feedback diversity** | distinct(accepted_card_curr, recent_accepted) | EMA — если юзер принимает разное, не одно и то же |
| **U-C. Surprise count** | apply_surprise_boost frequency | `EMA(1.0 if recent surprise else 0.0, decay=0.95)` |
| **U-D. Meditation signal** | low NE + high HRV coh + slow input rate | EMA — детектит «юзер делает практику обучения» (см. РГК v1.0 §«Практическая калибровка»: ACh↑ от медитации) |

**Рекомендация: U-A основной + U-C boost + U-D opt-in.**
```python
# Источник 1: новизна сообщений (per-input)
distinct_to_recent = compute_distinct(msg, recent_msgs[-5:])
user.acetylcholine.feed(min(1.0, distinct_to_recent))

# Источник 2: surprise boost (детект user-side surprise)
user.acetylcholine.feed(1.0, decay_override=0.85)

# Источник 3 (opt-in): meditation detection
if user.norepinephrine < 0.3 and user.hrv_coherence > 0.7 and input_rate_per_min < 1:
    user.acetylcholine.feed(0.85, decay_override=0.9)  # тихий boost
```

U-D — добавляется в Step 5 (cognitive_loop), не в Step 4. Сначала U-A+U-C, потом разморозим U-D после калибровки.

### GABA — Damping (стенки стоячей волны, гасит боковые лепестки)

Биологическая роль: «жёсткие границы, торможение, чёткие стенки стоячей волны отдельно от серотонинового гистерезиса» (resonance-model). 5HT даёт hysteresis (медленная стабилизация); GABA — острая граница «дальше не идём».

#### System-side (Neurochem.gaba)

| Кандидат | Источник | Формула |
|---|---|---|
| **A. ProtectiveFreeze active duration** | freeze.active state | `EMA(1.0 if freeze.active else 0.0, decay=0.95)` — high = chronically inhibiting |
| **B. Inverse scattering** | std of active_node embeddings | `EMA(1 − scattering_norm, decay=0.95)` — узкая стоячая волна = high damping |
| **C. Conflict resolution rate** | conflict_accumulator decay rate | производная `−dC/dt` нормированная |

**Рекомендация: A + B combo.**
```python
# Источник 1: freeze duration
gaba.feed(1.0 if freeze.active else 0.0)

# Источник 2: текущая компактность активной фокусировки
embeddings = [n.embedding for n in active_nodes]
if embeddings:
    spread = float(np.std(np.linalg.norm(embeddings, axis=1)))
    gaba.feed(max(0.0, 1.0 - min(1.0, spread)))
```

Без feeders → 0.5. Активный freeze → подтягивает к 1.0 (жёсткие стенки, ничего нового не входит). Узкий focus в графе → подтягивает к 0.7.

#### User-side (UserState.gaba)

User-side GABA — про «юзер сфокусирован, не разбегается + активно тормозит шум». Два независимых пути:

| Кандидат | Источник | Формула |
|---|---|---|
| **U-A. Focus residue inverse** | existing focus_residue field | `EMA(1 − focus_residue, decay=0.95)` — стабильность работы над одной темой |
| **U-B. Breathing signal** | low NE + high HRV coh + low input rate | EMA — explicit «медленное дыхание» из калибровки РГК v1.0 («GABA ↑: медленное дыхание, прогрессивная релаксация») |

**Рекомендация: U-A основной + U-B при детекции дыхательной практики.**
```python
# Источник 1: derived из focus_residue (existing)
self.gaba.feed(1.0 - self.focus_residue)

# Источник 2 (opt-in после Step 5): breathing detection
if user.hrv_coherence > 0.75 and user.norepinephrine < 0.25 and input_rate < 0.3:
    self.gaba.feed(0.9, decay_override=0.85)
```

U-A не новый детектор — `focus_residue` уже decay'ит сам по себе, GABA получает плавный сигнал.
U-B перекликается с user.acetylcholine.U-D (та же триада low NE + high HRV + slow input). Разница: ACh boost'ится при novelty + meditation, GABA boost'ится при чистом breathing focus без novelty. Различение через дополнительный сигнал — distinct(msg, recent) low → GABA, distinct(msg, recent) high → ACh.

### Сводная таблица feeders (для Шага 4)

| Axis | User feeder | System feeder | Default |
|---|---|---|---|
| gain (DA) | engagement signal + feedback | distinct(d) + RPE | 0.5 |
| hyst (5HT) | HRV coherence + checkin focus | weight stability | 0.5 |
| aperture (NE) | HRV stress + checkin stress | weights entropy | 0.5 |
| **plasticity (ACh)** | **distinct(msg, recent_5) + surprise_boost** | **node_creation_rate + bridge_quality** | 0.5 |
| **damping (GABA)** | **1 − focus_residue** | **freeze.active + 1 − embedding_scattering** | 0.5 |

### balance() после feeders заехали

```
balance = (gain × aperture × plasticity) / (hysteresis × damping)
```

Сценарии (для UI/diagnostic):

| Состояние | DA | 5HT | NE | ACh | GABA | balance |
|---|---|---|---|---|---|---|
| Спокойный поток | 0.6 | 0.6 | 0.4 | 0.5 | 0.5 | (0.6·0.4·0.5)/(0.6·0.5) = 0.40 |
| Творческий пик | 0.7 | 0.5 | 0.5 | 0.8 | 0.4 | (0.7·0.5·0.8)/(0.5·0.4) = 1.40 |
| Защитный фриз | 0.3 | 0.7 | 0.6 | 0.3 | 0.9 | (0.3·0.6·0.3)/(0.7·0.9) = 0.086 — гипостабильность |
| Гиперрезонанс/мания | 0.9 | 0.3 | 0.7 | 0.7 | 0.3 | (0.9·0.7·0.7)/(0.3·0.3) = 4.9 — срыв |

Корридор «здорового резонанса» — **balance ∈ [0.3, 1.5]**. Меньше → закрытие/апатия. Больше → срыв/мания. Это **новая diagnostic** для долгосрочного health monitoring (через 2 мес use → trend).

---

## 7. Risk register

| Риск | Вероятность | Mitigation |
|---|---|---|
| Identity не сходится bit-identical после adapter | High | Phase A test остаётся на TOL 1e-5 (semantic не bit). Прототип показал что semantic identity достижима. |
| Adapter `UserState` поверх РГК создаёт двойную indirection (perf) | Low | EMA-feeds копеечны; profile только если `_advance_tick` начнёт занимать >10ms |
| ACh+GABA feeders шумят на старте (нет данных) | Medium | Defaults 0.5 + slow decays (0.95) → плавный rollout. Через 2 нед видно distribution. |
| Поломка serialization (legacy state.json не загружается) | Medium | from_dict добавляет new fields с defaults — backward-compat. Тест: загрузить production state.json, проверить что dump = identity на ключевых fields. |
| Detector regression (читают field которого больше нет) | Medium | Шаг 6 — explicit pass через 13 detectors, имена не меняем. Тесты detector'ов pass = acceptance. |
| Cognitive_loop._idle_multiplier теперь учитывает balance() и тормозит сильнее чем раньше | Medium | Калибровать threshold (`balance < 0.5` vs `balance < 0.3`) на 2-нед окне после merge. До этого — **disable** balance-based slowdown, оставить только legacy display_burnout-based. |
| Phase D перетянет calibration window и придётся повторно собирать данные | Low | Calibration про urgency dispatcher (Phase B), Phase D про state. Ortogonal. Существующие throttle_drops продолжают писаться. |

---

## 8. Когда identity не сходится — что делать

Если на Шаге 3 или 4 identity 42/42 ломается:

1. **Не править EXPECTED**. EXPECTED — это контракт legacy semantics. Менять можно только если **сознательно** меняем формулу (например, добавляем новый axis в `vector()`).

2. **Diff per-field**. Запустить `python -m src.rgk` (текущий прототип) → сравнить с adapter-version. Точно знать **какое поле** разошлось.

3. **Track event ordering**. Чаще всего расхождение — порядок операций. В legacy `update_from_feedback("rejected")` делает: 1) fire_event(feedback), 2) burnout += 0.05, 3) streak check, 4) tick_expectation. Adapter должен сохранять **ровно этот порядок**. Если нет — расходится.

4. **Float32 quirks**. `np.std` / `np.linalg.norm` в float32 могут дать Δ ~1e-7. Если все остальные fields совпадают bit-identical и только vector-derived поля разъехались на 1e-7 — увеличить TOL до 1e-6, обновить test docstring.

5. **Last resort**: `git diff HEAD~1 src/rgk.py src/user_state.py` — посмотреть что я сам изменил в РГК после прототипа. Возможно случайный typo в decay или extractor.

---

## 9. После merge Phase D

Что появляется в TODO как новые задачи:

- [ ] **Калибровка corridor balance()** — через 1-2 мес use смотреть distribution `r.balance()` в `prime_directive.jsonl` aggregate. Если 99% случаев в [0.3, 1.5] — corridor верный. Если нет — пересмотреть feeder'ы.
- [ ] **Калибровка `_idle_multiplier` threshold** для balance-based slowdown (если включаем). Default `balance < 0.3` или `balance > 2.0` → +50% throttle.
- [ ] **UI: balance() как один scalar в Header** — рядом с capacity_zone. «🌊 Резонанс: 0.85» — новый визуальный indicator поверх 3-zone.
- [ ] **Aperture скаляр в depth engine** ([resonance-code-changes.md](resonance-code-changes.md)) — теперь дешевле, можно делать. RGK.user.aperture × RGK.balance() даёт derived value, settings UI получает один slider.
- [ ] **Phase D calibration** через 2 нед — distribution ACh/GABA, тонкая настройка cap'ов в feeders.

Что **удаляется** из TODO:
- Pure-function formulas в один файл (закрыто)
- 5-axis расширение (закрыто)
- §12 simplification-plan «возможные будущие расширения» нейрохимии (закрыто)

---

## 10. Что НЕ входит в Phase D core (post-merge или opt-in)

Из исходной спецификации РГК v1.0 (диалог 2026-04-24, источник rgk-spec.md) есть слои которые НЕ берутся в Phase D, но остаются как Tier 2 после merge:

### A. Counter-wave actual generation (`step(obs, dt)` с buffer)

В исходнике §6.1 «Цифровая симуляция» — pure RGK simulation с буфером задержки:
```python
def step(self, signal_in):
    self.buffer.append(signal_in)
    if mode == "C":
        return -self.buffer[0]  # инверсия с задержкой
```

Phase D **не** реализует actual wave-generation — R/C bit это только signal к промпт-роутеру (см. ниже). Если в будущем понадобится buffer + −k·signal output (например для real-time audio/sensor processing) — добавляется в Resonator как опциональный метод. Не блокер.

### B. Prompt-routing по `r.balance() + r.user.mode`

Исходник v1.0 §«Влияние на промпт-роутинг» даёт прямой contract:
- `5HT↑ DA↑` → «Поддерживай поток. Развивай идеи.»
- `NE↑ 5HT↓` → «Действуй как якорь. Структурируй. Гаси шум.»
- `ACh↑ NE↓` → «Исследуй аналогии. Будь гибким.»
- `GABA↑ DA↓` → «Упрощай. Микро-шаги.»

После Phase D `detect_sync_seeking` и `execute_deep` могут читать `r.balance()` + `r.user.mode` + 5-axis chem и выбирать tone. Это **TODO Tier 2** — добавится в TODO после merge (§9 этого плана).

### C. UI: Карта состояний 8 регионов + экстренные протоколы

Исходник v1.0 даёт «🗺 Карту состояний РГК» с 8 регионами (🔵Поток / 🟢Устойчивость / 🟠Фокус / 🟡Исследование / 🔴Перегруз / ⚫Застой / ⚪Выгорание / ✨Инсайт) и экстренными протоколами (60-180 сек breathing). Это **content для UI**, не core architecture. Добавляется как Tier 2 после Phase D — `named_state` уже есть в коде (10-region Voronoi), переименование в 8-region РГК-карта = ~50 строк изменений в `user_state_map.py`.

### D. Параметрический yaml-профиль сессии

Исходник v1.0 даёт `session.chemistry: dict[str, float]` как канонический snapshot. После Phase D `r.project("balance")` уже даёт всё что нужно для этого snapshot — добавляется только endpoint `/assist/chemistry` который выводит yaml-форму. ~30 строк, опционально.

---

## 11. Связанные docs

- [rgk-spec.md](rgk-spec.md) — адаптированная физическая модель (мать в repo)
- **РГК v1.0 source** — исходная спека от 2026-04-24 (источник rgk-spec.md, у автора локально)
- [simplification-plan.md](simplification-plan.md) — Phase A/B/C исторический контекст; Phase D добавится в §«Статус»
- [docs/resonance-model.md](../docs/resonance-model.md) — таблица 5 модуляторов (источник дизайна feeders)
- [docs/world-model.md](../docs/world-model.md) — каскад зеркал (двойной резонатор обоснован)
- [docs/friston-loop.md](../docs/friston-loop.md) — PE-предиктивный слой (PredictiveLayer)
- [src/rgk.py](../src/rgk.py) — прототип Phase D Step 0
- [tests/test_metric_identity.py](../tests/test_metric_identity.py) — Phase A identity, остаётся как sentinel
