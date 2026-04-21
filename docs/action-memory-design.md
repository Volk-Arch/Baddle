# Action Memory — самообучение через граф

> Действия (свои и юзера) живут в том же графе что и мысли. DMN, pump,
> consolidate, touch_node, hebbian decay — всё что уже работает для
> мыслей — **автоматически** начинает работать для действий. Baddle
> учится что работает без отдельного RL-loop'а.
>
> Пятая механика резонансного протокола. Структурно другого уровня —
> не новый check, а расширение семантики графа.

---

## Зачем

4 первые механики дали Baddle сенсорику (HRV, feedback, energy, agency),
симметричный слой (sync_error, silence/imbalance pressure) и реактивный
слой (sync-seeking, suggestions, reminders, DMN bridges). Но actions
выполнялись — никто не запоминал **что именно** работает с этим конкретным
человеком.

Как собака которая видит что ты грустный, подходит, виляет хвостом. Если
прогнать — в следующий раз подойдёт ровно так же. Не учится.

Пятишаговый цикл сознания замыкается так:
1. Замечает рассогласование — `sync_error` ✓
2. Хочет уменьшить — через `valence = -Δsync_error` per action type
3. Пробует действие — 6+ proactive checks ✓
4. Запоминает сработало ли — **action/outcome ноды в графе**
5. Повторяет успешное — **score_action_candidates через similarity**

Шаги 4 и 5 — то что раньше было дырой.

---

## Почему именно через граф

Альтернатива — отдельный RL-layer (experience replay buffer, Q-table
по (state, action) → reward). **Именно то что не хочется:** плодит
структуры, дублирует embedding/similarity/decay (в графе уже есть), не
интегрируется с DMN (ищет мосты между нодами, не между записями таблицы),
не inspectable в Graph Lab.

Через граф всё достаётся **бесплатно:**

| Механика | На мыслях | На actions (без нового кода) |
|---|---|---|
| `touch_node(idx)` +0.02 | мысль крепнет от обращений | успешное действие крепнет когда выбирается |
| decay −0.005/сутки | неиспользуемая тает | неудачное тает |
| `pump(a, b)` | мост между ideas | мост между action и outcome |
| `smartdc` | верифицирует hypothesis | верифицирует что действие сработало |
| embedding similarity | похожие идеи | похожие контексты где применялось |
| DMN continuous | связи между мыслями | что работало когда |

Когда DMN находит мост между `action:sync_seeking(evening, high_burnout)`
и `outcome:user_returned_quick` — связь крепнет через hebbian. В следующий
раз в похожем контексте та же связь найдётся быстрее и влияет на выбор.

Без RL-loop'а. DMN уже это делает, просто ему дали больше типов нод.

---

## Новые node-типы

### `action` — что-то было сделано

Поля помимо обычных node-fields: `actor` ∈ {baddle, user},
`action_kind` (строка, см. enum), `context` со snapshot'ом всех
скаляров системы и юзера на момент действия + `time_of_day`,
`hrv_regime`, `sync_regime`. Плюс `closed: bool` и `outcome_idx`.

**Baddle-side:** sync_seeking, dmn_bridge, scout_bridge, suggestion_habit /
constraint, reminder_plan, alert_low_energy, morning_briefing, pump_run,
baddle_reply, chat_event_{mode}, evening_retro.

**User-side:** user_chat (со sentiment в context), user_accept / reject,
user_goal_create / done, user_activity_start / stop, user_checkin.

`action_kind` не hardcoded — любая строка, UI фильтруется по наблюдаемым.

### `outcome` — что произошло после

Поля: `linked_action_idx` (обратная ссылка), `delta_sync_error` =
`after − before` (negative = good), `user_reaction` ∈ {chat, accept,
reject, ignore, silence}, `latency_s`, `confidence` (измеряется
надёжность — sync_seeking через 2 мин уверенный, через 4 часа — шумный).

### Edges

- **`caused_by`** (outcome → action) — жёсткий causal claim. Pump/DMN
  **не** traverse'ят по умолчанию — слишком прямо, не insight.
- **`followed_by`** (action-N → action-N+1) — temporal, без causal
  claim. Даёт цепочку для policy-planning.

---

## Sentiment как metadata

Каждое user-сообщение создаёт `action:user_chat` ноду. Sentiment — поле
в её context'е. LLM classify однократно при создании (лёгкий вызов с
SHA1-кэшем, повторные сообщения не бьют LLM).

Плюс UserState.valence получает **высокочастотный feeder**: было только
от accept/reject (редко), теперь каждое сообщение — EMA с baseline 0.92.

Не меняет структуру. Ещё один вход в существующий слой + поле в
action-node.

---

## Closing outcomes

Раз в 5 минут `_check_action_outcomes` проходит по open actions
(`closed=False`). Для каждого измеряет post-state, создаёт outcome-ноду
+ edge `caused_by`, закрывает action.

**Timeout per kind:**

| action_kind | timeout | user_reaction signal |
|---|---|---|
| sync_seeking | 30 мин | chat within = «chat», else «silence» |
| reminder_plan | 30 мин | started task = «acted», else «skipped» |
| alert_low_energy | 1 ч | не взял heavy = «heeded», else «ignored» |
| morning_briefing | 4 ч | chat within = «engaged» |
| dmn_bridge / scout_bridge | 24 ч | next briefing include = «seen», else «missed» |
| suggestion_{kind} | 7 дней | accept / reject card / ignore |

User_chat сам по себе не требует closing. Он **закрывает open
baddle-actions** в своём timeout window'е — прямая обратная связь.
Если открытых нет — просто stored node со sentiment, доступен для
similarity-поиска.

---

## Retrieval для policy

Когда проактивный check готов emit'нуть action — можно сначала спросить:
«какой вариант в похожих прошлых контекстах давал meaningful
`-delta_sync_error`?». Это `score_action_candidates(kind, candidates,
variant_field, time_of_day, min_history)` — positive score = past
success.

MVP: применено в `_check_sync_seeking` для выбора tone (caring / ambient
/ curious / reference / simple). Если winner ≥ 0.05 over 2nd place
после ≥ 3 prior closed actions — override эвристики. Cold start (< 3
данных) возвращает 0, fallback на heuristic. Постепенно расширяется на
другие checks.

---

## Ловушки

**Actions растут быстро.** 150+/день баддл + 20-50 user-сообщений за
месяц = ~6000 нод. Решение: агрессивный consolidation — |delta| < 0.05
через 30 дней → archive; значимые (|delta| ≥ 0.1) остаются как
долгосрочная action-memory.

**Pump путает actions с мыслями.** В `_find_distant_pair` по умолчанию
исключаем action+outcome. Отдельный метод для намеренного поиска
action-outcome связей, DMN использует раз в N тиков.

**False causality.** Sync-seeking не гарантированно вернул юзера —
мог прийти в себя. Принимаем noise — статистика за 1-3 месяца
усредняет. Плюс `outcome.confidence` отражает измерительную
неопределённость. Counterfactual honesty (намеренно не действовать
в части случаев) — в OQ, пока не делается.

**Cold start.** Первые недели нет данных → score=0 → policy
эквивалентна hardcoded. Это **правильно**: Baddle начинает с нулевого
знания о конкретном человеке. Никаких fallback hardcoded preferences —
иначе теряем главное качество (personalization).

---

## Как это работает через месяц

Вечер четверга, юзер устал, sync_error растёт.

**Без Action Memory:** sync-seeking шлёт случайный template. Юзер
игнорирует. Через 2ч снова случайный.

**С Action Memory, месяц данных:**
- DMN pump за прошлый месяц нашёл мост между
  `action:sync_seeking(evening, burnout>0.5, tone=caring)` и
  `outcome:user_ignored`.
- Параллельно — мост между
  `action:sync_seeking(evening, burnout>0.5, tone=reference, topic=recent_goal)`
  и `outcome:user_chatted_quick`.
- В четверг вечером `score_action_candidates` возвращает
  `{caring: +0.02, reference: −0.14}`. Reference выбран.
- LLM prompt включает recent_goal_topic. Юзер отвечает. `outcome(delta=−0.18, reaction=chat)`
  — мост крепнет ещё.

Это произошло без написания RL-кода. Pump/DMN уже делают ровно это.

---

## Что это НЕ

- **Не замена графа мыслей.** Actions и thoughts живут параллельно, разные
  типы узлов. Pump/DMN на thoughts работают как раньше; отдельный
  action_pump на action-outcome пары.
- **Не RL-framework.** Нет reward function извне. Reward = `-Δsync_error`,
  вытекает из прайм-директивы. Policy = softmax over scored candidates.
  Никаких Q-tables, replay buffers, TD-learning.
- **Не формальное сознание.** Операциональное: counters, EMA, hebbian
  decay, embedding similarity. «Замечает / хочет / пробует / запоминает /
  повторяет» — механические свойства, не переживания.
- **Не blackbox.** Каждый action и outcome — node в графе, inspectable
  в Lab, можно удалить ложный outcome и scoring изменится.

---

## Валидация

Через 2-3 месяца `avg sync_error` за неделю должен быть ниже чем в
первом месяце при прочих равных — прайм-директива валидирует
расширение сама себя (детали — [friston-loop.md § Связь с прайм-директивой](friston-loop.md#связь-с-прайм-директивой)).

---

## Где в коде

Ключевые точки: `graph_logic.record_action` / `score_action_candidates`,
`sentiment.classify_message_sentiment`, `cognitive_loop._check_action_outcomes`,
`consolidation` per-type archive. Связь: [world-model.md](world-model.md)
(5-я механика), [alerts-and-cycles.md](alerts-and-cycles.md) (в 21-check
таблице), [episodic-memory.md § Consolidation](episodic-memory.md#consolidation).

---

**Навигация:** [← Resonance protocol](world-model.md) · [Индекс](README.md) · [Планирование](../planning/TODO.md)
