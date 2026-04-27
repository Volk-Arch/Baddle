# Chat-store — convergence buffer между divergent generation и user view

> Design doc, не реализация. Корни — `docs/universe-as-git.md` Глава 8 (дивергенция/конвергенция как универсальный паттерн), `docs/architecture-rules.md` Правило 1 (Signal/Dispatcher), `docs/world-model.md` Механика 4 (циклы затухают без пищи).
>
> Идея автора (2026-04-27): «не ограничиваем систему в действиях, но выбираем из того что она сделала». Дивергенция = генерируй; convergence = chat-store + selection.

---

## Проблема

`assistant.py` 3105 LOC + `cognitive_loop.py` 2628 LOC = 23% проекта в двух файлах. Большая часть — **routing и manual aggregation** для отображения кандидатов в чат-ленте. Каждый источник имеет свой path:

| Источник | Сейчас |
|---|---|
| `/assist` reply | inline в response, append в граф через `record_action(actor='baddle')` |
| Alerts (13 детекторов) | через `signals.Dispatcher` → `_add_alert` → отображение в UI overlay |
| Morning/Weekly briefings | `cognitive_loop._build_morning_briefing_*` → push через alerts |
| Scout / dmn-bridge | накоплены в `loop._recent_bridges`, **не показываются** напрямую — UI должен poll |
| Observation suggestions | через Dispatcher, immediate |
| Sync-seeking (silence > 2h) | через детектор, immediate |

**Симптомы:**
1. Логика «что показать пользователю сейчас» размазана по 5+ файлам.
2. Нет временного буфера: low-urgency кандидаты либо emit'ятся сразу (давление), либо теряются (если detect не выстрелил в нужный момент).
3. Дублирование dedup logic: Dispatcher dedup'ит по `dedup_key`, но `_recent_bridges` имеет свой timeline, briefings — свою throttle.
4. Нет **selection** между source'ами: если scout нашёл интересный мост и одновременно morning briefing — показываются оба independently.

---

## Решение: ChatStore = единая convergence-точка

Все источники кладут **кандидатов** в один store. ChatStore решает что и когда показать. Селекция = **convergence**: из множества возможных вариантов один поток для UI.

### Контракт

```python
@dataclass
class ChatCandidate:
    source: str         # "assist_reply" | "scout" | "brief_morning" | "alert" | "observation" | ...
    kind: str           # "message" | "card" | "alert"
    text: str
    urgency: float      # 0..1
    created_at: float
    expires_at: float
    accumulate: bool    # True → ждать селекции; False → immediate
    dedup_key: str
    metadata: dict      # tone, mode_id, cards, refs

class ChatStore:
    """Convergence buffer для всего что показывается user в чат-ленте."""
    _buffer: list[ChatCandidate]

    def add(self, candidate: ChatCandidate) -> None:
        """Кандидат поступает в buffer. Auto-immediate если accumulate=False."""

    def select(self, now: float, max_emit: int = 1) -> list[ChatCandidate]:
        """Convergence rule: какие кандидаты показать сейчас.
        Drop expired; dedup by key; immediate-flag preempts; иначе urgency-budget."""

    def commit(self, selected: list[ChatCandidate]) -> list[int]:
        """Selected → ноды графа (baddle_reply / suggestion_card / alert).
        Returns: list of node_idx."""
```

### Selection rule (convergence)

1. **Drop expired** — `now > expires_at`.
2. **Immediate preemption** — `accumulate=False` (assist reply на user-сообщение, urgency=critical alert) проходят сразу, в обход буфера.
3. **Counter-wave penalty** — если `r.user.mode == 'C'`, push-style sources (scout, brief, observation, sync_seeking) получают −0.3 urgency. Уже есть в Dispatcher, переиспользовать.
4. **Budget per window** — N кандидатов в час, mirror'ить `Dispatcher.budget_per_window` (5/hour). Если budget исчерпан — `select()` returns `[]`.
5. **Dedup** — по `dedup_key` (source+content-hash). Старший (по urgency) выигрывает.
6. **Selection order** — sort by urgency, breakti by FIFO. Top-K = max_emit.

### Источники → ChatStore

| Источник | accumulate | urgency | Когда select |
|---|---|---|---|
| `/assist` reply | False | 1.0 | immediate (на user-message) |
| critical alert (zone_overload, plan_reminder в ближайшие 5 мин) | False | 0.85+ | immediate |
| morning briefing | True | 0.6 | next select cycle (раз в 5 мин tick) |
| scout / dmn-bridge | True | 0.4 | budget per hour |
| observation_suggestion | True | 0.5 | budget |
| sync_seeking | True | 0.3-0.7 | budget, скейлится по silence |
| insight bookmark response | False | 1.0 | immediate (user clicked ⭐) |

### Связь с существующим Signal/Dispatcher

ChatStore — это **расширение Dispatcher на временной оси**. Сейчас Dispatcher делает per-window dispatch для immediate alerts. ChatStore добавляет:
- candidates могут **ждать** в буфере N минут
- selection происходит **периодически** (на каждом cognitive_loop tick'e), не только при добавлении
- single source of truth для **chat-visible output**

Возможен compromise: Dispatcher продолжает работать для UI-overlay alerts (баннеры в шапке), ChatStore только для chat-history. Но более чисто: alerts — это вид кандидатов, всё через ChatStore.

### Persistence

`data/chat_store_buffer.jsonl` — только pending candidates. На каждом `select()` старые/expired/committed уходят. Восстанавливается из жилых entries при start.

`commit()` пишет в основной граф через `record_action(actor="baddle", action_kind=...)`. ChatStore сам граф **не дублирует** — он **очередь до**, граф — после.

### UI

- Chat-история: лента нод графа (как сейчас) — **не меняется**.
- Frontend poll'ит endpoint `GET /chat/recent` (~existing): возвращает selected+committed candidates.
- Alerts (UI overlay) — отдельный endpoint, читает только `kind="alert"` из committed candidates.

---

## Что это даёт

1. **assistant.py compaction** — handlers больше не агрегируют alerts/briefings/scout. Один `/chat/recent` endpoint вместо `_alerts()` логики ~500 LOC.
2. **cognitive_loop.py compaction** — `_check_*` детекторы перестают думать про timing/throttle. Просто: «нашёл candidate → store.add(...)». Throttle живёт в `select()`.
3. **Принцип «не ограничиваем но выбираем»** — explicit. Система свободно генерирует. ChatStore конвергирует.
4. **Низкий barrier на новый источник** — добавил callsite `chat_store.add(ChatCandidate(...))`. Не нужно встраиваться в Dispatcher или alerts collection.
5. **Темпоральный буфер** — scout/observation не теряются если момент не подходит. Ждут до 1ч в буфере.
6. **Single source of truth** для chat output — debug проще («почему Baddle написал X сейчас?» — посмотреть `chat_store_buffer.jsonl`).

---

## Что это НЕ

- **Не замена Signal/Dispatcher.** Dispatcher остаётся для immediate alerts (UI banner). ChatStore — chat-history convergence.
- **Не замена action memory.** Граф хранит committed candidates как nodes; ChatStore — буфер до commit.
- **Не messaging queue.** Не distributed; in-process буфер с jsonl persistence для restart-safety.

---

## Миграция (план Wave)

### W14.1 — Chat-store primitive (~3-4ч)

`src/chat_store.py` (~200 LOC):
- `ChatCandidate` dataclass
- `ChatStore` class с `add/select/commit`
- jsonl persistence
- Tests на selection rules (urgency order, dedup, expiry, counter-wave penalty, budget)

### W14.2 — Migrate assist reply (~1-2ч)

`/assist` route: вместо inline `record_action(actor="baddle")` → `chat_store.add(ChatCandidate(source="assist_reply", accumulate=False))` → `commit(select(...))`.

Identity test: один user message → один baddle reply в graph (как было).

### W14.3 — Migrate alerts (~2-3ч)

`Dispatcher.dispatch()` returns Signal'ы → конвертируются в ChatCandidate → store.add. UI alerts читают через ChatStore committed.

### W14.4 — Migrate briefings + scout (~2-3ч)

`_build_morning_briefing_*` → результат → `chat_store.add(accumulate=True)`.
Scout/dmn-bridge: в `_advance_tick` после нахождения моста → `chat_store.add(accumulate=True)`.

### W14.5 — Decompose assistant.py (~3-5ч)

После W14.2-4 размер `assist()` route падает. Time для split:
- `src/routes/chat.py` — /assist, /assist/feedback, /assist/state, /chat/recent (~700 LOC)
- `src/routes/goals.py` — все goals/recurring/constraints endpoints (~280)
- `src/routes/activity.py` — activity_log endpoints (~230)
- `src/routes/plans.py` — plans endpoints (~130)
- `src/routes/checkins.py` (~70)
- `src/routes/profile.py` — profile + named_states + bookmark (~150)
- `src/routes/briefings.py` — morning/weekly (~330)
- `src/routes/misc.py` — sensors/patterns/debug/decompose (~250)

**Ожидаемая дельта:** assistant.py 3105 → ~150 (только Flask blueprint setup + import routes).

### W14.6 — Decompose cognitive_loop.py (~2-3ч)

После chat-store migration `_check_*` упростятся. Split:
- `cognitive_loop.py` — main loop + advance_tick (~1200)
- `bookkeeping.py` — _check_heartbeat / _check_graph_flush / _check_activity_cost (~400)
- `briefings.py` — _build_morning_briefing_* / _build_current_state_signature (~500)

DMN/REM heavy work уже консолидируется в W11 #3 (`pump_logic` + `consolidation` → `dmn.py`).

---

## Open questions

**1. ChatStore vs Dispatcher — слить или раздельно?**
Возможно один primitive с двумя modes (immediate + buffered). Решить после W14.1 prototype.

**2. Кто решает `accumulate=True/False`?**
Сейчас табличка выше — hardcoded. Может быть driven by user state: в `r.user.mode == 'C'` всё переключается на accumulate; в FLOW часть сразу.

**3. Что с user-input?**
User message — это тоже candidate? Сейчас просто пишется в граф через `record_action(actor="user")`. Возможно ChatStore только для baddle output, user input — separate path.

**4. Persistence overhead.**
jsonl на каждый `add/select/commit` = 3 file writes. Может buffer in-memory + flush on tick? Решить когда увидим throughput.

---

## Risk

- **Behaviour drift.** Сейчас alert immediate, после migration может задержаться на tick (~5s). UX-наблюдение нужно.
- **State.json compatibility.** Если буфер persist'ится — миграция legacy state.json без `chat_store_buffer.jsonl` тривиальна (пустой буфер на старте).
- **Hot path.** ChatStore вызывается на каждом /assist + каждом cognitive_loop tick. Нужен performance check.

---

## Связано

- [`docs/architecture-rules.md`](../docs/architecture-rules.md) Правило 1 (Signal/Dispatcher)
- [`docs/universe-as-git.md`](../docs/universe-as-git.md) Глава 8 (divergence/convergence)
- [`docs/world-model.md`](../docs/world-model.md) Механика 4 (циклы затухают без пищи)
- [`src/signals.py`](../src/signals.py) — текущий Dispatcher
- [`cleanup-plan.md`](cleanup-plan.md) W13 — пересмотр assistant.py + cognitive_loop.py
