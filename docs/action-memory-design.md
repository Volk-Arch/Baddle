# Action Memory — самообучение через граф

> Действия (свои и юзера) живут в том же графе что и мысли. DMN / pump /
> consolidate / touch_node / hebbian decay — всё что уже работает для
> мыслей — **автоматически** начинает работать для действий. Baddle
> учится что работает без написания отдельного RL-loop'а.

Это пятая механика резонансного протокола (после: hebbian decay, adaptive
idle, system burnout, active sync-seeking). Но она **структурно** другого
уровня — не новый check, а **расширение семантики графа**.

---

## Зачем

После 4 закрытых механик у нас было:

- **Сенсорный слой** — видим юзера (HRV, feedback, energy, agency)
- **Симметричный слой** — считаем sync_error, desync_pressure
- **Реактивный слой** — sync-seeking, suggestions, reminders, DMN bridges
- **Но:** actions есть, их никто не запоминает и ничья обратная связь не измеряется.

Baddle как собака которая видит что ты грустный, подходит, виляет хвостом.
Но если ты её прогонишь — в следующий раз подойдёт ровно так же.
Не учится **что с этим человеком работает**.

5-шаговый цикл сознания (из диалога 2026-04-21):
1. Замечает рассогласование ← `sync_error` ✅
2. Хочет уменьшить (валентность) ← пока наблюдаемая метрика, не driver
3. Пробует действие ← 6+ proactive checks ✅
4. Запоминает сработало ли ← **дыра**
5. Повторяет успешное ← **дыра**

Action Memory закрывает 4+5 и превращает 2 из наблюдаемой метрики в
действующую — `valence = -Δsync_error` per action type, что автоматически
становится tilt в сторону успешных действий через hebbian механики.

---

## Почему именно через граф

Альтернатива — отдельный RL-layer (experience replay buffer, Q-table
по (state, action) → reward). **Ровно то что не хочется:**

- Плодит concepts (ещё одна структура рядом с графом)
- Дублирует инфраструктуру (embedding, similarity, decay — в графе уже)
- Не интегрируется с DMN (DMN ищет мосты между нодами графа, не между
  записями отдельной таблицы)
- Не поддаётся visible inspection в Graph Lab

Через граф получаем **бесплатно**:

| Механика | На мыслях | На actions (без нового кода) |
|---|---|---|
| `touch_node(idx)` hebbian +0.02 | мысль крепнет от обращений | **успешное действие** крепнет когда его выбирают |
| `consolidate` decay −0.005/сутки | неиспользуемая тает | **неудачное** тает |
| `pump(a, b)` bridges | мост между ideas | мост между action и outcome |
| `smartdc` dialectical | проверяет hypothesis | **верифицирует** что действие реально сработало |
| embedding similarity | похожие идеи | **похожие контексты** где применялось |
| `_check_dmn_continuous` | находит новые связи между мыслями | находит **что работало когда** без нового кода |

**Самое красивое:** когда DMN pump находит сильный мост (quality > 0.5)
между нодами `action:sync_seeking(evening, high_burnout)` и `outcome:user_returned_quick`,
эта связь **крепнет через hebbian** от самого факта обнаружения. В
следующий раз в похожем контексте та же связь найдётся быстрее, сильнее,
и попадёт в query-по-similarity.

Без RL-loop'а. DMN уже это делает, просто мы даём ему больше типов нод.

---

## Новые node-типы

Расширяем существующий `node.type` enum. Сейчас: `thought`, `hypothesis`,
`fact`, `evidence`, `goal`, `action` (использовалось редко), `question`,
`synthesis`. Уточняем семантику `action` и добавляем `outcome`.

### `action` — Что-то было сделано

```python
{
  "type": "action",
  "actor": "baddle" | "user",     # кто совершил
  "action_kind": str,              # см. enum ниже
  "text": str,                     # human-readable для UI и LLM-контекста
  "embedding": [...],              # для similarity / pump
  "context": {
    "sync_error_before": 0.42,
    "user_state_before": {         # snapshot (4 скаляра + agency + valence)
      "dopamine": 0.6, "serotonin": 0.5, "norepinephrine": 0.4,
      "burnout": 0.3, "agency": 0.55, "valence": 0.1
    },
    "system_state_before": {       # симметрично, для Baddle-side
      "dopamine": 0.5, "serotonin": 0.5, "norepinephrine": 0.45,
      "conflict_accumulator": 0.1, "desync_pressure": 0.35
    },
    "time_of_day": "evening",      # morning | day | evening | night
    "hrv_regime": "healthy_load",  # recovery | stress_rest | healthy_load | overload
    "sync_regime": "flow",         # flow | rest | protect | confess
    "recent_topic_nodes": [12, 47, 89],  # refs в _graph["nodes"]
    "sentiment": 0.3,              # для user_chat actions (см. ниже)
  },
  "confidence": 0.5,               # обычный node-field; для actions — "наша уверенность что сработает"
  "created_at": "...",
  "last_accessed": "...",
  "closed": False,                 # True после записи outcome
  "outcome_idx": None,             # idx ноды-outcome когда закроется
}
```

### `action_kind` enum (initial)

Baddle-side actions:
- `sync_seeking` — мягкое сообщение «как ты?»
- `dmn_bridge` — найденный мост запушен как инсайт
- `pump_run` — запустил pump (успешный / неуспешный)
- `scout_bridge` — ночной scout bridge-save
- `suggestion_habit` — предложил recurring
- `suggestion_constraint` — предложил ограничение
- `reminder_plan` — пуш напоминание о плане
- `alert_low_energy` — предупредил о low energy heavy
- `morning_briefing` — утренний brief

User-side actions:
- `user_chat` — написал сообщение (плюс sentiment в контексте)
- `user_accept` — принял suggestion / bridge
- `user_reject` — отклонил
- `user_ignore` — 7 дней без реакции = auto-ignore
- `user_goal_create` — создал новую цель
- `user_goal_done` — завершил цель
- `user_activity_start` — начал активность
- `user_activity_stop` — остановил активность

Открыто для расширения. `action_kind` не hardcoded — любой string, в UI
фильтруется по наблюдаемым типам.

### `outcome` — Что произошло после

```python
{
  "type": "outcome",
  "text": str,                     # «sync_error упал 0.42 → 0.28, юзер вернулся через 12 мин»
  "embedding": [...],
  "linked_action_idx": int,        # обратная ссылка на action
  "delta_sync_error": -0.14,       # pre - post (negative = good)
  "user_reaction": "chat" | "accept" | "reject" | "ignore" | "silence",
  "latency_s": 720,                # от action.ts до outcome.ts
  "closed_at": "...",
  "confidence": 0.8,               # quality measurement — для sync_seeking через 2 мин confidence низкая, через час высокая
}
```

---

## Новые edge-типы

Добавляем в `_graph["edges"]`:

- **`caused_by`** — directed от **outcome → action**. Жёсткий causal claim. *«Этот outcome — результат того action»*. Pump/DMN **не** traverse'ят обычные `caused_by`-мосты по умолчанию — это слишком прямо, не insight.

- **`followed_by`** — directed от action-N → action-N+1. Temporal, без causal claim. Даёт «цепочку действий» для policy-planning.

- `in_context` уже покрывается существующими `evidence_target` / `manual_links` от action-ноды к нодам из `recent_topic_nodes`.

---

## Sentiment юзера как metadata, не отдельная система

**Вопрос из диалога:** «можно из текста смотреть радуется или нет — отдельная задача?»

**Ответ:** нет. Каждое user-сообщение создаёт action-ноду `user_chat`.
Sentiment — **поле в её `context`**. LLM classify однократно при создании.

```python
# В assistant.py при приёме user-сообщения:
from .sentiment import classify_message_sentiment
sentiment = classify_message_sentiment(message)  # ∈ [-1, 1]

record_action(
    actor="user",
    action_kind="user_chat",
    text=message[:200],
    context={
        "sentiment": sentiment,
        "sync_error_before": current_sync_error,
        ...
    }
)
```

`classify_message_sentiment` — лёгкий LLM-вызов (max_tokens=5, temp=0.0):

```
system: Return a single number -1.0..1.0 — sentiment of this message.
        -1 = frustration/anger/sadness, 0 = neutral, +1 = joy/excitement.
        No explanation, just the number.
user: {message}
```

Плюс: `UserState.valence` получает **высокочастотный** feeder. Сейчас valence
живёт только от accept/reject (редко). С sentiment — каждое сообщение:

```python
# В user_state.py новый метод:
def update_from_chat_sentiment(self, sentiment: float):
    """EMA: 0.92 baseline, 0.08 сигнала — за ~12 сообщений baseline полностью пересчитается."""
    self.valence = 0.92 * self.valence + 0.08 * sentiment
    self._clamp()
```

Это **не** меняет существующую структуру. Просто ещё один вход в
`UserState.valence` + новое поле в action-node.

---

## Integration points

### 1. Helper-функции в graph_logic

```python
def record_action(actor: str, action_kind: str, text: str,
                   context: dict, ts: Optional[float] = None) -> int:
    """Создать action-ноду, вернуть её idx.
    Embedding генерится при следующем _ensure_embeddings.
    """
    ...

def close_action(action_idx: int, delta_sync_error: float,
                  user_reaction: str, latency_s: float) -> int:
    """Создать outcome-ноду, залинковать с action через caused_by edge.
    Ставит action.closed = True, action.outcome_idx = outcome_idx.
    """
    ...
```

### 2. Запись actions в каждом _check_*

Каждый proactive check, который уже emit'ит alert, добавляет `record_action`:

```python
# Пример для _check_sync_seeking (после успешного emit):
action_idx = record_action(
    actor="baddle",
    action_kind="sync_seeking",
    text=f"Spoke to user: «{text[:80]}»",
    context=self._current_snapshot(),
)
# Запомнить idx чтобы потом закрыть outcome
self._open_actions[action_idx] = time.time()
```

### 3. `_check_action_outcomes` — закрытие outcomes

Новый check, раз в 5 мин, проходит по `action` нодам где `closed=False`,
для тех что старше timeout своего kind — измеряет post-state и закрывает
через `close_action`.

**Timeout per kind** (начальные значения, калибруются на реальных данных):

| action_kind | timeout | user_reaction signal |
|---|---|---|
| `sync_seeking` | 30 мин | chat within 30m = "chat", else "silence" |
| `dmn_bridge` | 24ч | next morning briefing include = "seen", else "missed" |
| `suggestion_habit` | 7 дней | accept/reject card / ignore |
| `suggestion_constraint` | 7 дней | same |
| `pump_run` | immediate | quality > 0.5 = success, else fail |
| `reminder_plan` | 30 мин после planned_ts | started task = "acted", else "skipped" |
| `alert_low_energy` | 1ч | не взял heavy task = "heeded", else "ignored" |
| `morning_briefing` | 4ч | chat within = "engaged" |
| `user_chat` | 0 | сам по себе immediate, outcome не нужен |
| `user_accept/reject` | 0 | same |

Если juser_chat происходит во время открытой sync_seeking (в её timeout
window) — это прямая обратная связь, sync_seeking закрывается немедленно
с reaction="chat".

### 4. Query при выборе next action

Когда проактивный check готов emit'нуть — перед этим вызывает:

```python
def score_action_candidates(candidates: list[str], context: dict) -> dict[str, float]:
    """Для каждого action_kind вернуть ожидаемый -delta_sync_error в похожем контексте.
    Через embedding similarity с прошлыми closed action-outcome парами.
    Если данных нет (<3 past instances) → возвращаем 0.0 (нейтрально).
    """
    ...
```

MVP: hardcode применение только в `_check_sync_seeking` — выбор между
tone templates (caring/ambient/curious) по истории. Постепенно расширяем.

### 5. Consolidation: per-type архивация

Расширяем `consolidation.py`:

```python
def consolidate_actions(age_days: float = 30,
                         signal_threshold: float = 0.05) -> dict:
    """Actions старше age_days с |delta_sync_error| < threshold → archive.
    Значимые (|delta| >= 0.1) остаются как долгосрочная память."""
    ...
```

Запускается в ночном цикле вместе с существующей `consolidate_all`.

---

## Ловушки и их решения

### 1. Actions растут быстро

150+/день баддл-стороны + 20-50 user-сообщений = ~200/день. За месяц
6000 нод только из actions. Граф раздуется.

**Решение:**
- Агрессивная consolidation: |delta| < 0.05 через 30 дней → archive
- Embedding similarity ловит duplicates до archive'а (похожие actions
  сливаются через существующий collapse-механизм)
- Prototypical actions с stable сигналом остаются — это долгосрочная
  action-memory, polezna

### 2. Pump начинает путать actions с мыслями

Сейчас pump ищет мосты между любыми нодами. Actions заполнят pool,
pump будет находить operational-связи вместо insight-связей.

**Решение:** в `pump_logic._find_distant_pair()` по умолчанию исключаем
`action`+`outcome` типы. Отдельный метод `pump_action_bridges()` для
намеренного поиска action-outcome связей (его DMN использует раз в N тиков).

### 3. False causality (sync_error мог упасть сам)

Sync_seeking не гарантированно вернул юзера — мог просто сам прийти в себя.
Без counterfactual это шум.

**Решение:**
- Принимаем noise, статистика за 1-3 месяца усредняет
- Иногда **намеренно не действуем** в триггерных контекстах — baseline
  recovery time для сравнения. Это OQ #7 (Surprise detection) направление C,
  **мы его и так планировали**.
- `outcome.confidence` отражает уверенность: outcome измеренный через 2 мин
  после sync_seeking уверенный, через 4 часа — шумный.

### 4. Outcome timing heterogeneous

Sync-seeking 30 мин, suggestion 7 дней. Разные latency.

**Решение:** per-kind timeouts в config (см. таблицу выше). Multi-tier
outcomes для длинных (suggestion: immediate reaction + week-later persistence).

### 5. Cold start — первые недели нет данных

`score_action_candidates` возвращает 0.0 для нового юзера. Policy
эквивалентна сегодняшней (hardcoded).

**Решение:** это **правильно**. Baddle начинает с нулевого знания о
конкретном человеке. Учится постепенно. Через 2-3 недели данных score
начинает смещать выбор. Никаких fallback hardcoded preferences per
action_kind — иначе теряем главное качество (**personalization**).

### 6. User_chat сам по себе не имеет outcome

Юзер написал что-то → что делать с этим как с action? Он сам — реакция
на что-то.

**Решение:** `user_chat` не требует closing. Он **сам** закрывает открытые
baddle-actions в timeout window'е. Если нет открытых — просто stored node
со sentiment, доступен для future similarity поиска.

---

## Как это работает через месяц (mental model)

**Сценарий:** вечер четверга, юзер устал, sync_error растёт.

*Без Action Memory:*
Sync-seeking шлёт случайный template. Юзер игнорирует. Через 2ч снова
случайный. Юзер возвращается когда сам захочет.

*С Action Memory, месяц данных:*
1. DMN pump за прошлый месяц нашёл мост `action:sync_seeking(evening, burnout>0.5, tone=caring)` ↔ `outcome:user_ignored` → confidence этой связи ~0.7.
2. Параллельно нашёл `action:sync_seeking(evening, burnout>0.5, tone=reference, topic=recent_goal)` ↔ `outcome:user_chatted_quick` → confidence ~0.6.
3. Когда в четверг вечером триггерится sync_seeking — `score_action_candidates` возвращает `{caring: +0.02, reference: -0.14}`.
4. Reference tone выбран. LLM промпт включает recent_goal_topic.
5. Юзер отвечает. `outcome(delta=-0.18, reaction=chat)` записывается, mosts крепнут ещё.

**Это произошло без написания RL-кода.** Пump/DMN уже делают ровно это,
мы им просто дали новые типы нод.

---

## Порядок реализации

**Этап 1 — инфраструктура** (~1 день):
- `graph_logic.record_action()`, `close_action()`, `_current_snapshot()` helpers
- `consolidation.consolidate_actions()` — archive stale actions
- Фильтры в `pump_logic` + `_find_distant_pair` — исключать action/outcome
- UI Graph Lab: фильтр по node_type, отдельные цвета для action/outcome

**Этап 2 — запись** (~½ дня):
- Patches в: `_check_sync_seeking`, `_check_dmn_continuous`, `_check_dmn_deep_research`, `_check_observation_suggestions`, `_check_low_energy_heavy`, `_check_plan_reminders`, `_check_night_cycle` scout-save, morning-briefing emit
- В `assistant.py` на user-chat: `record_action("user", "user_chat", ..., sentiment=...)`
- В feedback endpoints: `record_action("user", "user_accept|user_reject", ...)`
- В `/goals`, `/activity/*`: `user_goal_create`, `user_activity_start/stop`

**Этап 3 — closing** (~½ дня):
- `_check_action_outcomes` — раз в 5 мин, по open actions
- Per-kind timeout config
- User_chat auto-closes open baddle-actions in window

**Этап 4 — sentiment** (~½ дня):
- `sentiment.py` — `classify_message_sentiment(text) → float`
- Cache через hash(text) — повторные сообщения не тратят LLM
- `UserState.update_from_chat_sentiment()` — EMA feeder
- Интеграция в `assistant.py` параллельно `register_input()`

**Этап 5 — retrieval & policy** (~1 день):
- `score_action_candidates(candidates, context)` через embedding similarity
- Применение в `_check_sync_seeking` — выбор tone по истории
- Постепенное расширение на другие checks (по мере накопления данных)

**Этап 6 — визуализация** (опционально, ~½ дня):
- Lab: node_type filter, action/outcome раскраска, timeline view
- Dashboard: «actions за неделю» с summary по delta
- Metrics endpoint: `/metrics/actions` — aggregate statistics

**Суммарно:** 3-4 дня работы за несколько сессий.

---

## Статус реализации 2026-04-21

**Все 6 этапов закрыты:**

### ✅ Этап 1 — инфраструктура
- `graph_logic.record_action/close_action/_current_snapshot/list_open_actions/score_action_candidates/link_chat_continuation`
- `consolidation.consolidate_actions` — в ночном цикле
- Два новых edge types: `caused_by` (outcome→action), `followed_by` (temporal chain chat-сообщений)
- `_remap_edges` правильно ремапит при remove, ссылки `outcome_idx` / `linked_action_idx` обновляются

### ✅ Этап 2 — запись actions
Baddle-side через `_record_baddle_action`:
- `sync_seeking` · `dmn_bridge` · `suggestion_{kind}` · `morning_briefing` · `alert_low_energy` · `reminder_plan` · `evening_retro` · `scout_bridge` · `baddle_reply` · `chat_event_{mode_name}`

User-side в endpoints:
- `user_chat` (с sentiment в context) · `user_accept` / `user_reject` · `user_goal_create_{kind}` · `user_activity_start` / `user_activity_stop` · `user_checkin`

### ✅ Этап 3 — closing
- `_check_action_outcomes` — раз в 5 мин
- Per-kind timeouts: 30мин sync/reminder, 1ч low_energy, 4ч briefing, 24ч bridge, 7д suggestion
- User-reaction в окне: `user_chat` → закрывает sync_seeking/reminder, `user_accept/reject` → закрывают suggestions
- Восстановление `_open_actions` после рестарта через `created_at`

### ✅ Этап 4 — sentiment
- `sentiment.classify_message_sentiment` — light LLM (max_tokens=8, temp=0), SHA1 cache max 500
- `UserState.update_from_chat_sentiment` — EMA feeder (0.92 baseline, 0.08 сигнала)
- В `/assist/chat/append` при `role=user`: classify → EMA → user_chat action со sentiment в context

### ✅ Этап 5 — retrieval и применение
- `score_action_candidates(kind, candidates, variant_field, time_of_day, min_history)` — positive score = past success в похожих contexts
- Применено в `_generate_sync_seeking_message` — override heuristic tone если winner ≥ 0.05 over 2nd place

### ✅ Этап 6 — UI визуализация
- Graph Lab: action ноды оранжевым stroke (user-actions dashed), outcome — зелёный/красный/серый по знаку delta_sync_error
- `/graph/actions-timeline` endpoint — chronological view для Lab UI (фильтры по kind/actor, include_outcomes)

---

## Что будет добавлено по мере данных

- **UI Chat-timeline view** в Lab (читает `/graph/actions-timeline`) — listbox переключающийся между режимами «conversation only / все actions / actions+outcomes»
- **Применение `score_action_candidates`** в других checks (suggestion kind choice, DMN bridge timing)
- **Counterfactual honesty** (OQ #4.C) — иногда не действовать для baseline recovery-time
- **Валидация через прайм-директиву** — через 2 месяца сравнить avg weekly sync_error: если падает — механика работает

---

## Проверка

После этапа 2+3 (запись + closing):
- `GET /graph/list?type=action` — растущий список
- `GET /graph/list?type=outcome` — закрывается с delta_sync_error
- Graph Lab: видны action→outcome цепочки

После этапа 5 (retrieval):
- Юзер отклоняет 5 sync-seeking с tone=caring → 6-й sync-seeking выбирает другой tone
- Лог: `[sync_seeking] score: {caring: -0.08, ambient: +0.02, reference: +0.11} → reference`

Через 2-3 месяца:
- `avg sync_error` за неделю **ниже** чем в первом месяце (при прочих равных условиях)
- Это финальная валидация: прайм-директива мерит своё же расширение

---

## Слияние с OQ #3 и OQ #4

### OQ #3 Valence без антропоморфизма — **реализуется автоматически**

Задача OQ #3 была: «как сделать valence действующим, не просто
наблюдаемым». Предложенный путь: `valence = −Δsync_error` через event-level.

Action Memory делает **это самое**: каждая action-outcome пара имеет
`delta_sync_error`, это и есть event-level valence per action. `UserState.valence`
остаётся как мгновенный sentiment-feeder, но **policy-level** valence
теперь встроена в action scoring.

### OQ #4 Recovery routes memory — **частный случай Action Memory**

Задача OQ #4 была: «какое действие Baddle возвращает юзера в resonance».
Action Memory делает это для **всех** actions, не только sync-seeking.
Recovery routes = query на action_kind ∈ {sync_seeking, suggestion}.

**Оба merged в эту механику.** В open-questions.md помечены статусом
«merged into action-memory», но сами вопросы (timing, counterfactual,
per-user threshold adaptation) остаются открытыми — просто в контексте
Action Memory.

---

## Что это НЕ

- **Не замена графа мыслей.** Actions и thoughts живут параллельно, разные
  типы узлов. Pump/DMN работают на thoughts по-прежнему, отдельный
  action_pump на action-outcome.
- **Не RL-framework.** Нет reward function явно заданного извне. Reward =
  `-Δsync_error`, вытекает из прайм-директивы. Policy = softmax over
  scored candidates. Никаких Q-tables, replay buffers, TD-learning.
- **Не формальное сознание.** Остаётся операциональным: counters, EMA,
  hebbian decay, embedding similarity. «Замечает / хочет уменьшить / пробует
  / запоминает / повторяет» — это **механические свойства**, не переживания.
- **Не blackbox.** Каждый action и outcome — nodes в графе, inspectable в
  Lab, поддаются ручному просмотру и вмешательству. Юзер может удалить
  ложный outcome, что сразу изменит scoring.

---

**Связь с существующим:**

- [world-model.md](world-model.md) — 5-я механика резонансного протокола.
- [open-questions.md #3 и #4](open-questions.md) — merged.
- [consolidation-design.md](consolidation-design.md) — расширяется `consolidate_actions`.
- [alerts-and-cycles.md](alerts-and-cycles.md) — новый check `_check_action_outcomes`.

**Реализация:**

- `src/graph_logic.py` — `record_action`, `close_action`, `_current_snapshot`
- `src/sentiment.py` — **новый**, `classify_message_sentiment`
- `src/user_state.py` — `update_from_chat_sentiment`
- `src/cognitive_loop.py` — `_check_action_outcomes`, `score_action_candidates`
- `src/consolidation.py` — `consolidate_actions`
- `src/pump_logic.py` — exclude action/outcome по умолчанию
- `src/assistant.py` — патчи в user-chat, feedback, goals, activity endpoints

---

**Навигация:** [← Resonance protocol](world-model.md) · [Индекс](README.md) · [Open questions →](open-questions.md)
