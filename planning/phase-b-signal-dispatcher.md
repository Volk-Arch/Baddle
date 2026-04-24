# Фаза B — Signal Dispatcher: спецификация перед кодингом

> Зафиксировано 2026-04-24 в продолжение [simplification-plan.md §5 Фаза B](simplification-plan.md). Документ — **дизайн**. Phase A завершён, attention teraz на правиле 1 из §4: «любое событие к юзеру это Signal(type, urgency, content, expires_at)».

---

## 1. Контекст

После Фазы A Правило 2 выполнено — EMA централизованы. Правило 1 остаётся bespoke: **21 check-функция** в `cognitive_loop.py`, каждая — своя cascade preconditions → throttle → quiet-after-other → emit. Каждая со своими `*_INTERVAL` и `_last_*` timestamps. Паттерн повторяется 13+ раз.

Цель Фазы B — извлечь общий каркас в `src/signals.py`:
- `Signal(type, urgency, content, expires_at, dedup_key)` — unified alert envelope
- `Dispatcher` — attention-budget + top-K sort + dedup + drop logger
- 13 **детекторов** (pure functions, return Optional[Signal]) вместо bespoke cascades

**Что НЕ делаем:**
- Не трогаем bookkeeping-checks (action_outcomes, hrv_push, heartbeat, graph_flush, agency_update, activity_cost, user_surprise, prime_directive_record, advance_tick) — они не alert-emitting
- Не трогаем внутренние циклы DMN (converge loop с max_steps/wall_time) — они остаются; только финальный emit идёт через Signal
- Не меняем UI-контракт — `/assist/alerts` endpoint получает те же alerts dict с теми же полями
- Не добавляем новые check'и

---

## 2. Инвентаризация: 13 alert-emitting checks (target для dispatcher)

Полная карта пришла из разведки перед написанием этой спеки. Свёрнута в таблицу для быстрого lookup при имплементации.

| # | Check | Alert type | Severity | Preconditions (throttle) | Context в content |
|--:|---|---|---|---|---|
| 1 | `_check_dmn_continuous` | `dmn_bridge` | info | `DMN_INTERVAL=600s × idle_mul`; не frozen; ne<0.55; idle≥30s; quality>0.5; text≥10 | bridge text, nodes linked |
| 2 | `_check_dmn_deep_research` | `dmn_deep_research` | info | `DMN_DEEP=1800s × idle_mul`; есть open goal; граф<30 нод | deep research text |
| 3 | `_check_dmn_converge` | `dmn_converge` | info | `DMN_CONVERGE=3600s × idle_mul`; граф 5..40 нод | summary, steps |
| 4 | `_check_state_walk` | `state_walk` | info | `STATE_WALK=1200s × idle_mul`; state_graph≥10; match старше 1ч | action, ts, reason |
| 5 | `_check_night_cycle` | `night_cycle` | info | `NIGHT=86400s × idle_mul` | phases summary |
| 6 | `_check_daily_briefing` | `morning_briefing` | info | `BRIEFING=72000s`; local_hour≥wake_hour | sections[] |
| 7 | `_check_low_energy_heavy` | `low_energy_heavy` | **warning** | `LOW_ENERGY=1800s`; daily<30; heavy-goal есть | goal id, actions |
| 8 | `_check_plan_reminders` | `plan_reminder` | info | `PLAN_REMIND_CHECK=60s`; 0<delta≤10min; dedup per (plan,date) | plan id, minutes |
| 9 | `_check_recurring_lag` | `recurring_lag` | info | `LAG=1800s`; lag≥1; per-goal 2×interval dedup | goal id, lag count |
| 10 | `_check_sync_seeking` | `sync_seeking` | info | `SYNC=7200s`; silence>0.3; idle>7200s; `QUIET_AFTER=1800s`; counterfactual 10% skip | tone, message |
| 11 | `_check_observation_suggestions` | `observation_suggestion` | info | `SUG=86400s`; user idle>600s; `MAX=2/day` | card dict |
| 12 | `_check_evening_retro` | `evening_retro` | info | `per-date`; local_hour≥wake+14 | unfinished[] |
| 13 | `_check_hrv_alerts` | `coherence_crit` | **warning** | coherence<0.25; dedupe=True | hrv values |

**Общий throttle-drop logger** (`_log_throttle_drop`) сейчас используют только 3 чека (sync_seeking, recurring_lag, observation_suggestions) — остальные дропают тихо. В dispatcher-е все drops становятся natural.

### Non-alert checks (9 функций, остаются как есть)

`_check_action_outcomes`, `_advance_tick`, `_check_hrv_push`, `_check_heartbeat`, `_check_agency_update`, `_check_activity_cost`, `_check_graph_flush`, `_check_user_surprise`, `_check_prime_directive_record`. Они делают bookkeeping / state writes / rolling baselines. В dispatcher scope не входят.

---

## 3. Signal dataclass

```python
# src/signals.py
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class Signal:
    """Unified alert envelope. Detector'ы возвращают это, dispatcher решает
    что эмитить юзеру."""

    type: str           # "sync_seeking", "dmn_bridge", "recurring_lag", ...
    urgency: float      # [0.0, 1.0]; >0.9 bypass budget (critical)
    content: dict       # alert payload — text, severity, metadata, action buttons
    expires_at: float   # unix ts; после этого сигнал дропается (stale)
    dedup_key: Optional[str] = None
    # `source` полезен для debug / throttle_drops.jsonl
    source: Optional[str] = None   # "detect_sync_seeking" etc.
```

**Поля:**

- **`type`** — строка kind (мапится 1:1 в `alert.type` который UI читает). Сохраняем 13 существующих имён.
- **`urgency`** — float [0, 1]. Считается детектором; выше = больше шанс пройти budget gate. Критические (coherence_crit, low_energy_heavy) → 0.95-1.0.
- **`content`** — dict формата существующего `_add_alert(...)`: `{type, severity, text, ...}`. Не меняем shape чтобы UI работал без изменений.
- **`expires_at`** — когда сигнал теряет актуальность. Для plan_reminder — 10 мин окно. Для dmn_bridge — 30 мин. Для morning_briefing — до конца дня. Stale = skip без emit.
- **`dedup_key`** — опциональный ключ; `None` означает «не дедуплицировать». Для recurring_lag — `"recurring_lag:{goal_id}"`. Для plan_reminder — `"plan_reminder:{plan_id}:{for_date}"`.
- **`source`** — имя детектора для telemetry. UI не видит.

**Что НЕ в Signal (специально):**
- severity — внутри `content`, не отдельное поле (в UI уже есть)
- timestamp создания — dispatcher добавляет в emit, не нужен в Signal
- priority ≠ urgency — одно понятие хватит

---

## 4. Detector protocol

Каждый из 13 alert-emitting checks становится **pure-function детектором**:

```python
def detect_sync_seeking(ctx: DetectorContext) -> Optional[Signal]:
    """Resonance protocol — reach out когда silence высокая."""
    silence = ctx.freeze.silence_pressure
    if silence <= 0.3:
        return None   # primary condition not met
    idle_h = (ctx.now - (ctx.user._last_input_ts or 0)) / 3600.0
    if idle_h < 2.0:
        return None

    # Compute urgency из state, не по времени
    hrv_delta = ctx.user.hrv_surprise
    urgency = min(1.0, 0.3 + 0.5*(silence - 0.3)/0.7 + 0.2*hrv_delta)

    message, tone = _generate_sync_seeking_message(
        ctx, silence, idle_h)   # bespoke — pure function

    return Signal(
        type="sync_seeking",
        urgency=urgency,
        content={
            "type": "sync_seeking",
            "severity": "info",
            "text": message,
            "tone": tone,
        },
        expires_at=ctx.now + 3600,   # 1 час до staleness
        dedup_key="sync_seeking",     # не чаще 1× per attention window
        source="detect_sync_seeking",
    )
```

**Ключевое:** детектор **не знает** про throttle и интервалы. Он только описывает «сейчас уместен сигнал такой-то urgency, живёт столько-то, duplicate-key такой».

### DetectorContext

```python
@dataclass
class DetectorContext:
    now: float
    user: UserState
    neuro: Neurochem
    freeze: ProtectiveFreeze
    graph: dict            # nodes/edges
    state_graph: ...       # episodic
    activity_log: ...
    goals_store: ...
    plans_store: ...
    # Read-only — детектор не мутирует state. Мутации (save graph,
    # record_baddle_action) идут в bespoke emit-обёртке после dispatcher.
```

Context собирается **один раз** в начале `_loop()` tick'а и передаётся в каждый detector. Вместо того чтобы каждый check делал `get_user_state()`, `get_global_state()`, `get_active_graph()` самостоятельно.

### Почему pure-function, не classes

13 функций — нет state между вызовами кроме того что в Context. Class-based детекторы добавят init/lifecycle overhead без выгоды. Зато закрывают возможность case-`match` по type для debug / testing.

### Side effects в DMN-checks

`_check_dmn_continuous/deep/converge` и `_check_night_cycle` — **длинные операции** с сайд-эффектами (pump между нодами, сохранение графа, добавление edges). Они НЕ становятся pure-function детекторами целиком. Разделение:

1. **Work function** (`run_dmn_continuous(ctx) -> Optional[BridgeResult]`) — делает heavy lifting, возвращает результат. Остаётся как есть.
2. **Detector** (`detect_dmn_bridge(ctx, result) -> Optional[Signal]`) — если work дал результат с quality>0.5, возвращает Signal.

Dispatcher flow для них:
```python
# В _loop():
if gate_passed_for_dmn(ctx):   # frozen/ne/idle — как сейчас
    result = run_dmn_continuous(ctx)    # heavy work
    if result:
        sig = detect_dmn_bridge(ctx, result)
        if sig:
            signal_queue.append(sig)
```

Work-функции сохраняют graph, пишут action-memory — **сайд-эффекты остаются внутри них**. Dispatcher отдельно решает, **показать ли** результат юзеру.

---

## 5. Dispatcher design

```python
class Dispatcher:
    """Attention-budget + top-K sort + dedup + drop logging."""

    def __init__(self,
                 budget_per_window: int = 5,
                 window_s: float = 3600.0,
                 critical_threshold: float = 0.9):
        self.budget_per_window = budget_per_window
        self.window_s = window_s
        self.critical_threshold = critical_threshold
        self._emitted_history: list[float] = []   # sliding window
        self._dedup_seen: dict[str, float] = {}    # key → ts
        self._lock = threading.Lock()

    def dispatch(self,
                 candidates: list[Signal],
                 now: float) -> list[Signal]:
        """Фильтрация expired → dedup → urgency sort → budget gate.

        Returns: signals одобренные к emit (UI-visible).
        Drops: пишутся в throttle_drops.jsonl через _log_drop.
        """
        with self._lock:
            self._prune_history(now)

            # 1. Filter expired
            alive = []
            for sig in candidates:
                if sig.expires_at <= now:
                    self._log_drop(sig, "expired", now)
                    continue
                alive.append(sig)

            # 2. Dedup
            fresh = []
            for sig in alive:
                if sig.dedup_key:
                    last_seen = self._dedup_seen.get(sig.dedup_key)
                    if last_seen is not None and now - last_seen < self.window_s:
                        self._log_drop(sig, "dedup", now)
                        continue
                fresh.append(sig)

            # 3. Sort by urgency desc
            fresh.sort(key=lambda s: -s.urgency)

            # 4. Budget gate (critical bypass)
            emitted = []
            budget_used = len(self._emitted_history)
            for sig in fresh:
                if sig.urgency >= self.critical_threshold:
                    emitted.append(sig)   # bypass budget
                elif budget_used < self.budget_per_window:
                    emitted.append(sig)
                    budget_used += 1
                else:
                    self._log_drop(sig, "budget", now)
                    continue
                # Mark
                self._emitted_history.append(now)
                if sig.dedup_key:
                    self._dedup_seen[sig.dedup_key] = now

            return emitted

    def _prune_history(self, now):
        cutoff = now - self.window_s
        self._emitted_history = [t for t in self._emitted_history if t > cutoff]
        self._dedup_seen = {k: t for k, t in self._dedup_seen.items()
                             if t > cutoff}

    def _log_drop(self, sig: Signal, reason: str, now: float):
        _append_jsonl("throttle_drops.jsonl", {
            "ts": now,
            "check": sig.type,
            "source": sig.source,
            "ctx": {
                "reason": reason,
                "urgency": round(sig.urgency, 3),
                "dedup_key": sig.dedup_key,
                "expires_in": round(sig.expires_at - now, 1),
            },
        })
```

**Где живёт Dispatcher:** singleton в `src/signals.py`, получает использованиеиспользование из `cognitive_loop._loop()`:

```python
signals = []
for detector in DETECTORS:
    try:
        sig = detector(ctx)
    except Exception as e:
        log.warning(f"[detector] {detector.__name__} failed: {e}")
        continue
    if sig:
        signals.append(sig)

# Dispatcher сам пишет drops в throttle_drops.jsonl
emitted = dispatcher.dispatch(signals, ctx.now)
for sig in emitted:
    self._add_alert(sig.content)
```

---

## 6. compute_urgency: эвристики per-detector

Без данных из throttle_drops.jsonl (пока нет файла) — эвристики. Калибровка после 2 недель реального use.

Формат: `base + weighted_context`. Все urgency в [0, 1].

| Detector | Formula | Диапазон | Rationale |
|---|---|---|---|
| `coherence_crit` | `1.0 − coherence`; если coherence<0.25 | 0.75..1.0 | Critical warning — bypass budget |
| `low_energy_heavy` | `0.5 + 0.4 * (1 − daily_remaining/30)` при daily<30 | 0.5..0.9 | Warning — high priority |
| `plan_reminder` | `0.7 + 0.3 * (1 − minutes_to_event/10)` | 0.7..1.0 | Time-critical, почти всегда критично |
| `morning_briefing` | `0.8` fixed | 0.8 | Ежедневный якорь, важен но не critical |
| `evening_retro` | `0.7` fixed | 0.7 | Ежедневный retrospective |
| `sync_seeking` | `0.3 + 0.5*(silence−0.3)/0.7 + 0.2*hrv_surprise` | 0.3..1.0 | Scales по intensity тишины+тела |
| `recurring_lag` | `0.3 + 0.15 * min(5, lag_count)` | 0.3..1.0 | Больше отставание = важнее |
| `observation_suggestion` | `0.2 + 0.6 * pattern_strength` | 0.2..0.8 | Зависит от силы паттерна |
| `dmn_bridge` | `0.2 + 0.7 * bridge_quality` | 0.2..0.9 | Качество моста = relevance |
| `dmn_deep_research` | `0.4 + 0.3 * novelty` | 0.4..0.7 | Зависит от научной ценности |
| `dmn_converge` | `0.5` fixed | 0.5 | Редкий (1ч interval), средняя важность |
| `state_walk` | `0.3 + 0.5 * similarity` | 0.3..0.8 | Ближе match = ценнее recall |
| `night_cycle` | `0.6` fixed | 0.6 | Ежедневный summary, medium |

**Критические пропускают budget** (urgency≥0.9): coherence_crit, plan_reminder (когда меньше 2 мин), low_energy_heavy при daily<5. Остальные через budget.

**Калибровка через 2 нед данных:** читаем `throttle_drops.jsonl`, ищем паттерны «high-urgency дропнут» vs «low-urgency прошёл». Корректируем коэффициенты.

---

## 7. Attention budget

**Начальные параметры:**
- `budget_per_window = 5` (max 5 non-critical alerts в час)
- `window_s = 3600` (1 час)
- `critical_threshold = 0.9`

**Adaptive variant (для Phase B.2, не первая версия):**

```python
budget = base_budget * (1 - 0.7 * freeze.combined_burnout(user.burnout))
```

Юзер выгоревший → меньше alerts (1-2 вместо 5). В resonance → полный budget. **Отложено до после данных** — сначала fixed budget + critical bypass, посмотрим что происходит.

### Почему именно эти цифры

- **5/час** — текущий pattern: morning_briefing, sync_seeking, observation (max 2), recurring_lag, plan_reminder могут все попасть в окно. 5 — upper bound.
- **1 час window** — достаточно чтобы SUGGESTIONS_MAX_PER_DAY=2 получилось естественно через dedup_key = "observation_suggestion" (dedup window = 1 час не блокирует 2 случая за день, но блокирует спам каждые 30 мин).
- **0.9 critical** — только coherence_crit+plan_reminder<2min+low_energy<5 достигают. Их пропускать обязательно.

### Отношения к существующим константам

После Phase B **удаляются:**
- `SYNC_SEEKING_INTERVAL`, `SYNC_SEEKING_QUIET_AFTER_OTHER`, `SYNC_SEEKING_IDLE_SECONDS` → urgency compute + dedup_key
- `RECURRING_LAG_CHECK_INTERVAL`, `RECURRING_LAG_MIN` → urgency compute + dedup_key per-goal
- `SUGGESTIONS_CHECK_INTERVAL`, `SUGGESTIONS_MAX_PER_DAY` → dedup_key per-kind с window_s
- `BRIEFING_INTERVAL` → dedup_key=`morning_briefing:{date}` + urgency scaling по времени дня
- `LOW_ENERGY_CHECK_INTERVAL` → urgency scaling
- `PLAN_REMINDER_CHECK_INTERVAL` → check остаётся каждую минуту, но dedup по plan_id делает emit правильным

Остаются (bookkeeping/work):
- `DMN_INTERVAL`, `DMN_DEEP_INTERVAL`, `DMN_CONVERGE_INTERVAL` → **для work-функций** (heavy compute), не для emit. Детекторы будут дёргаться на каждый loop-tick, но work-функции проверяют свой interval.
- `NIGHT_CYCLE_INTERVAL` → для work-функции.
- `HRV_PUSH_INTERVAL`, `HEARTBEAT_INTERVAL`, `GRAPH_FLUSH_INTERVAL` → bookkeeping, не alert.
- `TICK_INTERVAL`, `FOREGROUND_COOLDOWN` → loop pacing.

### Удаляются **`_last_*` timestamps**

Из 25+ — уходят ~12 (те что для alert-emit throttle). Остаются только для work-функций и bookkeeping:
- `_last_dmn`, `_last_dmn_deep`, `_last_dmn_converge` — для work loops
- `_last_night_cycle` — для ночной работы
- `_last_hrv_push`, `_last_heartbeat`, `_last_graph_flush` — bookkeeping
- `_last_loop_tick_ts`, `_last_foreground_tick` — loop pacing
- `_last_briefing` — persist (restart-safe briefing)
- `_last_evening_retro_date` — per-date dedup (можно через dispatcher dedup_key)
- `_last_proactive_alert_ts` → удаляется, заменяется history в dispatcher
- `_last_sync_seeking`, `_last_recurring_check`, `_last_low_energy_check`, `_last_suggestions_check`, `_last_plan_reminder_check` → **удаляются**, их роль берёт dispatcher

---

## 8. Migration plan по шагам

### Шаг 1 (≈2ч) — `src/signals.py` + unit-тесты

Написать `Signal` dataclass, `Dispatcher` class. Unit-тесты:
- dedup работает (одинаковый key не проходит дважды в окно)
- budget gate (top-K по urgency)
- critical bypass (urgency≥0.9 проходит помимо budget)
- expired skip (expires_at<now)
- drop logging пишет в JSONL

### Шаг 2 (≈1ч) — `DetectorContext` + base infrastructure

`src/signals.py`: `DetectorContext` dataclass, `DETECTORS: list[Callable]` registry. Helper функции в cognitive_loop для сборки context в каждый tick.

### Шаг 3 (≈4-5ч) — детекторы 1 по 1

Конвертировать 13 check-функций в детекторы:
- Вынести read-only логику в `detect_*(ctx) -> Optional[Signal]`
- Для DMN/night: разделить на `run_*(ctx) -> result` (heavy work) + `detect_*(ctx, result) -> Signal` (envelope)
- Удалять `*_INTERVAL`, `_last_*` timestamps, `_add_alert(...)` calls

Порядок миграции по сложности (easy first):
1. `_check_hrv_alerts` → `detect_coherence_crit` (простейшая, one check)
2. `_check_evening_retro` → `detect_evening_retro`
3. `_check_low_energy_heavy` → `detect_low_energy`
4. `_check_plan_reminders` → `detect_plan_reminder`
5. `_check_daily_briefing` → `detect_morning_briefing`
6. `_check_recurring_lag` → `detect_recurring_lag`
7. `_check_sync_seeking` → `detect_sync_seeking` (tone + A/B через action-memory сохраняется)
8. `_check_observation_suggestions` → `detect_observation_suggestion`
9. `_check_state_walk` → `detect_state_walk`
10. `_check_dmn_continuous` → `run_dmn_continuous` + `detect_dmn_bridge`
11. `_check_dmn_deep_research` → аналогично
12. `_check_dmn_converge` → аналогично
13. `_check_night_cycle` → `run_night_cycle` + `detect_night_cycle_summary`

### Шаг 4 (≈1-2ч) — переписать `_loop()`

- Собрать DetectorContext
- Запустить все work-функции где нужны (heavy DMN/night)
- Собрать signals из всех детекторов
- `dispatcher.dispatch(signals, now)` → emitted list
- Для каждого emitted: `_add_alert(sig.content)`

Всё bookkeeping (action_outcomes, agency_update, activity_cost, graph_flush, heartbeat, hrv_push, user_surprise, prime_directive_record, advance_tick) остаётся как раньше — свой гейт через `_last_*` timestamp.

### Шаг 5 (≈1ч) — удалить мертвый код

- `*_INTERVAL`, `*_QUIET_*`, `*_MAX_PER_DAY` константы (10-12 штук)
- `_last_sync_seeking`, `_last_recurring_check`, `_last_low_energy_check`, `_last_suggestions_check`, `_last_plan_reminder_check`, `_last_proactive_alert_ts` поля
- Старый `_log_throttle_drop` (dispatcher пишет сам)
- Все пути `_add_alert(...)` внутри check'ов (теперь только в `_loop` после dispatcher)
- Counterfactual sync_seeking skip — переносится в detector как 10% shortcut если проходят все conditions

### Шаг 6 (≈2-3ч) — integration test + smoke

- Integration test: setup user/state, run dispatcher, verify что expected signals emitted
- Smoke test на реальных данных (запускаем Baddle, убеждаемся alerts приходят)
- Прогон всего pytest

**Итого Phase B:** ≈11-14ч (в оценке 15-20ч из simplification-plan).

---

## 9. Тестирование

### Unit-tests (tests/test_signal_dispatcher.py)

- Signal dataclass construction
- Dispatcher dedup (same key twice in window → second dropped)
- Dispatcher budget gate (N+1 non-critical → N emitted, 1 dropped)
- Critical bypass (urgency=0.95 emitted даже при budget=0)
- Expired signals skipped
- Drop logger пишет в JSONL

### Detector-tests (tests/test_detectors.py)

По одному тесту per-detector:
- Stub DetectorContext с известным state
- Assert returned Signal (type, urgency в ожидаемом диапазоне, dedup_key shape)
- Assert None когда preconditions не выполнены

### Integration (tests/test_cognitive_loop_integration.py)

- Инициализировать cognitive loop + stubs
- Принудить state в известное состояние (silence=0.8, idle=3h)
- Run tick
- Assert что `sync_seeking` type прошёл в `_alerts_queue`

### Что **НЕ** identity-тестируется

Phase B не bit-identical как Phase A. Причина: семантика меняется — throttle был time-based, становится urgency-based. Alerts будут приходить в другое время и с другим приоритетом. Это feature, не bug. Identity-тесты PHASE A (EMA) продолжают проходить — formula EMA неизменны.

---

## 10. Открытые вопросы (решить перед кодингом)

1. **Budget: fixed=5/hr или adaptive-по-burnout?**
   Мой ответ: **fixed в первой версии** (5/hr). Adaptive после 2 нед данных. Проще дебажить.

2. **Critical threshold: 0.9 или 0.85?**
   Мой ответ: **0.9**. Только true-critical достигает. Сдвинуть ниже — bypass обесценивается.

3. **Window_s: 1 час или другое?**
   Мой ответ: **1 час**. Короче — plan_reminder+sync_seeking не влезут. Длиннее — backlog старых «ещё не показал» растёт.

4. **Detectors: pure functions или classes?**
   Мой ответ: **pure functions** — нет state между вызовами. Classes добавят init без выгоды.

5. **DetectorContext: dataclass или dict?**
   Мой ответ: **dataclass** с typed fields. Дебажится, IDE подсказывает, нет surprise key misses.

6. **DMN work-функции: рядом с detector или в существующих _check_*?**
   Мой ответ: **вынести в `run_*(ctx)` в отдельном модуле `src/dmn.py`**. Сейчас DMN-логика в cognitive_loop:1161-1500 — 350+ строк, портит navigation.

7. **morning_briefing `_briefing_loaded_from_disk` persist:** как через registry / где?
   Мой ответ: **оставить как есть** — persist в user_state.json. Это bookkeeping, не alert-throttle. Detector читает `user.last_briefing_ts` (read), dispatcher через dedup_key=`morning_briefing:{date}` гарантирует 1/день.

8. **Phase B.2 (adaptive burnout budget + data-driven urgency calibration): одна сессия после 2 нед или распределённая?**
   Мой ответ: **одна сессия через 2 нед**. Смотрим throttle_drops, корректируем формулы, тест.

---

## 11. Что остаётся bespoke

Правило: **dispatcher только для alert-envelope решений.** Heavy compute, sensor sync, bookkeeping — вне dispatcher.

| Остаётся | Где | Причина |
|---|---|---|
| `run_dmn_continuous/deep/converge` | `src/dmn.py` (новый) | Heavy graph ops + save, не envelope decision |
| `run_night_cycle` | `src/night_cycle.py` или остаётся | 5-phase pipeline |
| `_check_action_outcomes` | cognitive_loop | bookkeeping, closes actions |
| `_check_hrv_push` | cognitive_loop | sensor sync |
| `_check_heartbeat` | cognitive_loop | state_graph pulse |
| `_check_agency_update` | cognitive_loop | EMA update only |
| `_check_activity_cost` | cognitive_loop | energy debit |
| `_check_graph_flush` | cognitive_loop | disk persist |
| `_check_user_surprise` | cognitive_loop | records action, not alert |
| `_check_prime_directive_record` | cognitive_loop | JSONL snapshot |
| `_advance_tick` | cognitive_loop | feeders |
| `_idle_multiplier()` | cognitive_loop | для work-функций throttle |
| `NE_DECAY_PER_TICK` homeostasis | `_loop()` | adaptive sleep |
| Adaptive sleep в `_loop()` | cognitive_loop | по ne |

---

## 12. Связанные docs

- [simplification-plan.md](simplification-plan.md) §4 Правило 1 + §5 Фаза B
- [docs/alerts-and-cycles.md](../docs/alerts-and-cycles.md) — текущее описание 21 check'а (**будет сокращено после Phase B**)
- [src/cognitive_loop.py](../src/cognitive_loop.py) — источник миграции
- [src/metrics.py](../src/metrics.py) — Фаза A, образец для registry-паттерна
- `data/throttle_drops.jsonl` — пока нет файла (логгер есть, данных нет); после Phase B будет заполняться dispatcher'ом

---

## 13. Main takeaway

- 13 alert-emitting checks → 13 pure-function детекторов + 1 dispatcher
- Удаляются 10+ `*_INTERVAL` констант и ~12 `_last_*` timestamps
- cognitive_loop.py: **~3000 строк → ~1800-2000**
- Новая фича (detector) = 20-30 строк, не cascade из 50+
- `throttle_drops.jsonl` становится natural output dispatcher'а, не ad-hoc logger
- Bookkeeping checks (9 штук) остаются как были
- Identity Phase A (EMA) продолжает работать — Phase B касается только alert-envelope

**Оценка: 11-14ч по 6 шагам.**

**Не решаем в Phase B:**
- Adaptive burnout budget (Phase B.2 через 2 нед данных)
- Data-driven urgency calibration (там же)
- Capacity migration (после B, тонкая через registry)
- Graph-first (Phase C, опционально)
- DMN refactor в отдельный модуль — может быть sanity-clean в рамках Phase B, может стать отдельной мини-фазой
