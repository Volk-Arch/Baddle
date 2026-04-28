# Power — implementation план

> Концепция: [docs/power.md](../docs/power.md). Этот файл — sub-waves для cleanup-plan W15.

---

## Принцип реализации

Power — **derived layer** над existing скалярами, не replacement. Ничего не ломается до миграции, formula добавляется поверх. После калибровки (1-2 мес) — старые скаляры (`estimated_complexity` arbitrary, `dispatcher.budget = 5`) можно phase out.

**Зависимости:**
- W12 (tasks layer + jsonl_store primitive) — нужен для storage Power events.
- W14 (workspace) — для batch planning (cumulative power vs capacity).

W15 параллелен / следует за W12 + W14.1-2 минимум. **Не блокирует** B5 cleanup или текущие Tier 2 фичи.

---

## Sub-waves W15

### W15.1 — Static Power formula primitive (~3-4ч)

**Файл:** `src/power.py` (~150 LOC)

**API:**
```python
def estimate_power(
    U: float,                # 1..5 uncertainty
    V: float,                # 1..5 volume
    T_norm_min: float,       # comfortable duration
    T_actual_min: float | None,  # deadline; None = open-ended
    interest: float = 1.0,   # 0.5..1.5
    rgk = None,              # for chem_modulator
    gamma: float = 1.5,
    contour: str = "cogload"  # phys / affect / cogload
) -> float:
    """Returns power demand in dimensionless units.
    Open-ended (T_actual=None) → P=1 regardless of T_norm."""

def chem_modulator(rgk) -> float:
    """0.7..1.5 multiplier from РГК-state."""

def power_vector(task) -> tuple[float, float, float]:
    """Returns (P_phys, P_affect, P_cogload)."""
```

**Mapping категорий → contour distribution:**
```python
CATEGORY_CONTOUR_PROFILE = {
    "cooking":  {"phys": 0.6, "affect": 0.1, "cogload": 0.3},
    "coding":   {"phys": 0.05, "affect": 0.15, "cogload": 0.8},
    "meeting":  {"phys": 0.0, "affect": 0.4, "cogload": 0.6},
    "exercise": {"phys": 0.85, "affect": 0.1, "cogload": 0.05},
    "learning": {"phys": 0.0, "affect": 0.2, "cogload": 0.8},
    "social":   {"phys": 0.1, "affect": 0.7, "cogload": 0.2},
    "errand":   {"phys": 0.5, "affect": 0.2, "cogload": 0.3},
    "default":  {"phys": 0.2, "affect": 0.3, "cogload": 0.5},
}
```

**Tests:**
- Open-ended (T_actual=None) → P=1
- Sub-deadline (T_actual >= T_norm) → P=1 (no penalty)
- Tight deadline → P > 1 nonlinear
- Vector sums to scalar matching base
- chem_modulator clamped [0.7, 1.5]
- Identity preserved (no side-effects on rgk)

**Не подключаем нигде** — это primitive. Использование в W15.3+.

### W15.2 — Storage task lifecycle (~3-4ч)

Это **W12 task tracker** из cleanup-plan, расширенное под Power. Объединяем — реализуем сразу с Power-полями.

**Файл:** `src/tasks.py` (~250 LOC) + `data/tasks.jsonl` append-only.

**API:**
```python
def create_task(text, category="default", U=None, V=None,
                T_norm_min=None, deadline=None, interest=1.0,
                parent_goal_id=None) -> int:
    """Создать. Если U/V/T_norm не заданы — LLM-классификация.
    Записывает event create + estimated_power."""

def start_task(task_id) -> int:
    """Связь с activity_log.start_activity. Snapshots rgk-state."""

def update_progress(task_id, progress: float):
    """0..1 progress estimate. Computes drift, updates effective_P."""

def complete_task(task_id):
    """Final actual_power = recompute с actual_time.
    Calibration: bias_per_category += EMA(actual / estimated)."""

def defer_task(task_id, new_deadline=None):
def abandon_task(task_id, reason=None):

def list_backlog_for_day(date) -> list[task]:
    """Filter, prioritize for morning briefing."""

def get_bias(category) -> float:
    """EMA-tracked bias-coefficient per category."""
```

**Persistence shared primitive:** `src/jsonl_store.py` (новый ~80 LOC) — экстракт из plans/recurring/goals_store/activity_log дубликата `_append/_read_all/_replay`. Используется в tasks.py и опционально back-port в существующие 4 файла (отдельный refactor).

**Endpoints:**
- `POST /tasks/create`
- `POST /tasks/<id>/start`
- `POST /tasks/<id>/progress`
- `POST /tasks/<id>/done|defer|abandon`
- `GET /tasks?status=...&category=...`
- `GET /tasks/backlog-for-day?date=...`

**Tests:**
- Create with explicit U/V vs LLM-fallback
- Calibration: 5 tasks done → bias-coefficient stabilizes EMA
- Open-ended → P=1 throughout lifecycle
- jsonl_store roundtrip (event log → state replay)

### W15.3 — Live progress tracking (~2-3ч)

Через `activity_log` heartbeat (taskplayer running): каждые 5 мин если task active — call `tasks.update_progress`.

```python
# в cognitive_loop._advance_tick:
active = activity_log.get_active()
if active and active.get("_task_id"):
    elapsed = now - active["started_at"]
    expected_progress = elapsed / task.T_norm_min
    # progress estimate: либо explicit user input, либо LLM по recent chat,
    # либо linear if no signal
    progress = task.last_user_reported_progress or min(0.95, expected_progress)
    drift = progress / max(0.01, expected_progress)
    tasks.update_progress(task_id, progress)
```

**UI:** опциональный slider в taskplayer для self-report progress. Если пусто — auto-linear estimate.

**Effect:** workspace candidate metadata `current_power` обновляется в реалтайме. Dispatcher видит «эта задача растёт по cost» (drift < 0.8) — может escalate alert «может перенести?» или suggest defer.

### W15.4 — Calibration loop (~2-3ч) — **CI band = ширина полосы резонанса**

После углубления synchronization.md (2026-04-28): Beta-prior `confidence_ci` получает физический смысл — это **bandwidth резонанса** для конкретного axis или конкретной category. Узкий CI = точная настройка (мало evidence для подтверждения), широкий CI = грубая настройка (много evidence ещё нужно).

Это не новая метрика, переинтерпретация existing infrastructure ([rgk-spec testable claim 2](../docs/rgk-spec.md#testable-claims)) через wave optics. Применение:
- Bias-coefficient для category с узким CI → confident apply, formula adjusts immediately
- С широким CI → conservative apply (bias × 0.7 + 1.0 × 0.3), пока больше evidence не накопится
- UI hint: «CI узкий → калибровка сошлась»

После W15.2 + W15.3, ~2 недели данных накопится. Активация:

```python
# в consolidation.py night cycle (или отдельный cron):
def calibrate_bias():
    for cat in CATEGORIES:
        recent_done = tasks.list(status="done", category=cat, last_n=20)
        if len(recent_done) < 5:
            continue
        biases = [t.actual_power / t.estimated_power for t in recent_done
                  if t.estimated_power > 0]
        new_bias = ema(biases, decay=0.9)
        save_bias(cat, new_bias)
```

**Apply at create:**
```python
# в tasks.create_task:
formula_estimate = estimate_power(U, V, T_norm, T_actual, interest, rgk, contour)
bias = get_bias(category)  # default 1.0 если данных мало
final_estimate = formula_estimate * bias
record event(estimated_power=final_estimate, bias_applied=bias)
```

**Tests:**
- 5+ done tasks с известным bias → EMA reaches expected
- New category (no data) → bias=1.0 (no adjustment)
- Bias clamped [0.5, 2.0] чтобы не разнести при noisy data

### W15.5 — Interest parameter (~2ч)

**Explicit input:**
- При `tasks.create_task(text, ...)` UI имеет опциональный `interest` selector (4 варианта 🚫😐🙂🔥).
- Map в multipliers: 1.5 / 1.0 / 0.85 / 0.7.
- Если не задан — auto inference.

**Auto inference:**
```python
def infer_interest(category, rgk):
    # Recent valence для category (через action_memory)
    cat_valence = recent_chat_sentiment_for_category(category, days=14)
    # Recent abandonment rate
    abandon_rate = abandons_in_category(category, last_n=10) / 10
    # Default
    base = 1.0
    base -= 0.3 * cat_valence  # positive valence → lower cost
    base += 0.5 * abandon_rate  # high abandons → resistance
    return clamp(base, 0.7, 1.5)
```

**Risk:** если auto-inference systematically wrong, user perceived cost не совпадает с estimated. Mitigation — explicit override always wins; auto только default.

### W15.6 — Closure с dispatcher budget (~2-3ч)

Modify `Dispatcher.dispatch()`:

```python
class Dispatcher:
    def dispatch(self, candidates, now, user_mode='R'):
        capacity = available_capacity_now()  # из rgk.project("capacity")
        # capacity_zone → budget mapping
        budget = {"green": 5.0, "yellow": 2.0, "red": 0.5}[capacity["zone"]]

        sorted_candidates = sorted(candidates, key=lambda c: -c.urgency)
        emitted = []
        for c in sorted_candidates:
            cost = power_for_signal(c)  # cogload контур
            if user_mode == 'C' and c.type in COUNTER_WAVE_PUSH_TYPES:
                cost *= 1.3  # extra penalty
            if budget >= cost:
                emitted.append(c)
                budget -= cost
            else:
                drop_with_reason(c, "budget_exhausted")
        return emitted
```

**Tests:**
- Red zone → almost all dropped
- Green zone → top-5 by urgency emitted (как сейчас примерно)
- Counter-wave penalty applies on push-types only
- High-urgency signal (≥0.9) override budget by 1 (always emit critical)

### W15.7 — Vector capacity check (~2-3ч)

`capacity-design.md` 3 контура → Power vector закрывает.

```python
def can_take_task(task, rgk):
    p_vec = power_vector(task)  # (P_phys, P_affect, P_cogload)
    cap = rgk.project("capacity")
    # available per контур derived из cap booleans
    available = {
        "phys": 5.0 if cap["phys_ok"] else 1.0,
        "affect": 5.0 if cap["affect_ok"] else 1.0,
        "cogload": 5.0 if cap["cogload_ok"] else 1.0,
    }
    for contour, p in zip(["phys", "affect", "cogload"], p_vec):
        if p > available[contour]:
            return False, f"{contour}_overload"
    return True, None
```

**Use:** morning briefing auto-scheduling — фильтр кандидатов через `can_take_task`. Workspace W14.5 cross-обработка — батч-проверка `sum(P_vec) ≤ sum(available)`.

---

## Order и риски

**Порядок:**
1. **W15.1** Power primitive (no integrations).
2. **W15.2** Tasks storage + jsonl_store extraction.
3. **W15.3** Live progress (после W15.2).
4. **W15.5** Interest (parallel с W15.4).
5. **W15.4** Calibration loop (нужны 2 недели накопленных данных, могут быть delayed).
6. **W15.6** Dispatcher closure (independent, можно после W15.1 без полного tasks).
7. **W15.7** Vector capacity (последний — нужны все predecessors).

**Total:** ~16-22ч.

**Risk:**
- Calibration требует данных (1-2 мес), не immediate ценность.
- chem_modulator coefficients arbitrary — нужна калибровка через subjective_surprise data.
- Interest auto-inference может ошибаться — explicit override mandatory.
- Vectorization нагрузка на UI — нужно не показывать P_vec, а только resulting verdict («not enough cogload now»).

**Не блокирует:** B5 cleanup, W6-W11, W14 workspace. Может идти параллельно или последовательно.

---

## Open questions

1. **LLM-классификация U/V** — в каком endpoint? Inline при create или async (LLM call задерживает)? Default fallback на category profile если LLM недоступна.
2. **Interest auto vs explicit** — какой ratio? Возможно показывать explicit selector только если auto уверенно (high confidence per recent_chat_sentiment availability).
3. **Bias clamping** — `[0.5, 2.0]` arbitrary. Нужна 2-нед калибровка чтобы понять real spread.
4. **Open goals в Power** — `P=1` ОК, но что с U×V? Goal «изучить React» — V=∞? Или goals have отдельный Power формулу (long-term vector, без P)?
5. **Backwards compat:** legacy tasks (created до W15) не имеют U/V/contour. Default = scalar `estimated_complexity` mapped to U=3, V=3, contour=cogload.

---

## Estimate

Total ~16-22ч от primitive до полной интеграции. Не одна сессия — последовательность 7 sub-wave с зелёным baseline после каждой. Calibration loop (W15.4) ждёт 1-2 нед данных, остальное — implementable immediately.
