# DMN & Scout — фоновое сознание 24/7

> Default Mode Network — в нейронауке это сеть которая активна когда мозг
> «бездельничает»: бродит по ассоциациям, находит далёкие связи,
> переваривает опыт. У Baddle тот же принцип — когда юзер idle, система
> не простаивает, а **гуляет по графу** в поисках мостов и инсайтов.
>
> Scout — ночной аналог: более тяжёлый поиск, REM-фаза (переработка
> эпизодов), консолидация (забывание слабого). Работает раз в сутки.
>
> Этот doc — единственное место где описан весь фоновый контур. До
> 2026-04-23 инфо была разбросана по [full-cycle](full-cycle.md) /
> [episodic-memory](episodic-memory.md) / [alerts-and-cycles](alerts-and-cycles.md).

---

## Карта фоновых циклов

`cognitive_loop._loop` — единственный background thread (~60s poll cycle).
Четыре **DMN-check'а** + один **night cycle** + heartbeat:

| Check | Интервал | Что делает | Файл |
|---|---|---|---|
| `_check_dmn_continuous` | 10 мин | Pump между парой далёких нод текущего графа | `src/cognitive_loop.py:DMN_INTERVAL` |
| `_check_dmn_deep_research` | 30 мин | Polный pipeline (brainstorm → elaborate → smartdc) на одной open-цели | `DMN_DEEP_INTERVAL` |
| `_check_dmn_converge` | 60 мин | Server-side autorun до STABLE (max 100 steps, 15 min wall) | `DMN_CONVERGE_INTERVAL` |
| `_check_state_walk` | 20 мин | Эпизодическая память через state_graph similarity | `STATE_WALK_INTERVAL` |
| `_check_night_cycle` | 24 ч | Scout + REM emotional + REM creative + Consolidation | `NIGHT_CYCLE_INTERVAL` |
| `_check_heartbeat` | 5 мин | Пишет single state_node со стримами (substrate для Scout) | `HEARTBEAT_INTERVAL` |

Все интервалы масштабируются `_throttled_idle()` через `combined_burnout`
(см. [alerts-and-cycles.md § Adaptive idle](alerts-and-cycles.md)) —
циклы **замедляются** когда Baddle устала или юзер пропал. Спектр от 1×
(свежий resonance) до 10× (долгое молчание / графовый конфликт).

---

## DMN continuous — pump bridges

Самая частая фоновая операция. Раз в 10 мин:

1. Взять текущий content graph
2. Найти **distant pair** — две ноды с большим `distinct()` (см.
   [`_find_distant_pair`](../src/cognitive_loop.py) — softmax по
   `novelty × relevance` с температурой от dopamine)
3. Запустить [Pump](thinking-operations.md#pump--поиск-скрытых-мостов) —
   поиск 3 осей между облаками
4. Лучший мост (quality > 0.5) → alert `dmn_bridge` для утреннего
   briefing + action-нода (action kind = `dmn_bridge` для Action Memory)

**Intrinsic pull вместо рандома.** Раньше пара выбиралась случайно,
теперь `softmax(novelty · recency · uncertainty, T=1.1 − dopamine)`.
Высокий DA → острый выбор (любопытство ведёт в самую новую связь).
Низкий DA → мягкое распределение (ангедония, выбор ближе к рандому).

**Gate:** NE < 0.55 (юзер не активен) + FOREGROUND_COOLDOWN (после
user-tick ждём 30с).

---

## DMN deep research — полный pipeline на open-goal

Раз в 30 мин, если есть хотя бы одна open-цель:

1. Выбрать open-goal
2. Пропустить через pipeline:
   - **brainstorm-seed** в embedding space (см.
     [thinking-operations.md § embedding-first](thinking-operations.md#embedding-first-brainstorm--мышление-без-слов))
   - **elaborate** seed-нод (текст + evidence)
   - **smartdc** на свежих гипотезах
3. Результат → alert `dmn_deep_research` card с trace + synthesis

Это **не просто bridge между двумя нодами**, а реальная работа мозга в
фоне на конкретной цели. Генерирует 5–10 новых нод за проход.

---

## DMN converge — autorun до STABLE

Раз в час, самый тяжёлый DMN. Запускает серверный autorun:
- До **100 шагов** tick'а или **15 мин wall-time** (что раньше)
- Stall detection: если 12 шагов подряд граф не растёт → стоп
- Цель — довести active workspace до STABLE (нет bare нод, все
  unverified прошли doubt, verified ≥ stable_threshold)

Emit'ит `dmn_converge` alert с summary: сколько фаз прошло, сколько нод
добавлено, почему остановились.

**Идейно** — это **сон на часок**: система берёт паузу от реактивного
режима и доводит граф до состояния покоя. Следующий user-interaction
начинается из чистого baseline.

---

## State walk — эпизодическая память

Раз в 20 мин система выбирает random state-node из истории и ищет
похожие (через `state_graph.query_similar`). Пример output:

> «🕰 Похожий момент (дата: 3 дня назад): тогда я запускал deep-research
> на цели X — завершилось synthesis + acceptance».

Используется:
- Для context'а в morning briefing
- Для learning через Action Memory (см.
  [action-memory-design.md](action-memory-design.md) — похожий прошлый
  context влияет на выбор действия сегодня)
- Эвристический пример re-use: «когда система решала похожее — что
  помогло?»

Реализация — `query_similar()` в [episodic-memory.md](episodic-memory.md#эпизодическая-память-через-distinct).
Один и тот же `distinct(a, b)`
что и на content-graph — NAND primitive применён к self-history.

---

## Night cycle — Scout + REM + Consolidation

Раз в сутки единый проход. Последовательность «slow-wave → REM →
cleanup»:

### 1. Scout pump+save

Одна pump-сессия, но с `save=True` — лучший мост **сохраняется как нода**
в content graph (не только alert). Scout накапливает persistent связи.

Action-нода kind=`scout_bridge` для Action Memory — утром в briefing
показываем «ночью нашла это», отслеживаем seen/missed через 24ч.

### 2. REM emotional

Эпизоды с высоким `|recent_rpe|` (reward prediction error) прогоняются
через Pump между парами `content_touched` нод. Работает как
«переработка эмоционально насыщенного опыта» — consolidation того, что
удивило систему сегодня.

Max 3 пампа за ночь, чтобы не засорять граф.

### 3. REM creative

Content-пары которые **близкие в embedding** + **далёкие в path-графе**
получают `manual_link` — парадоксальные связи. Они не были замечены в
реактивном режиме (graph distance большой, поэтому BFS/Walk их не
находит), но embedding-близость намекает на скрытое родство.

Max 3 creative merges за ночь.

### 4. Consolidation

См. [episodic-memory.md § Consolidation](episodic-memory.md#consolidation--забывание-как-фича).
Ночной цикл:
- `_decay_unused_nodes` — hebbian decay (−0.005/сутки без access)
- Прунинг слабых (`confidence < threshold` + долго без обращений)
- Архив state_graph (старые эпизоды → compressed)

### 5. Patterns detect

`patterns.detect_all(days_back=21)` — weekday × activity → outcome
корреляции. Найденные → candidate suggestions (см.
[observation_suggestion](alerts-and-cycles.md#типы-alerts-в-ui)).

### 6. Goals rotation

`rotate_if_needed` — gzip старых завершённых goal'ов.

---

## Heartbeat substrate

Раз в 5 мин пишет **single state_node** с streams (HRV snapshot,
neurochem, sync_error, active workspace). Не добавляется как action
(это не решение), но создаёт substrate для DMN walks + state_graph
similarity queries.

Без heartbeat state_graph редел бы между user-initiated tick'ами.
Heartbeat даёт equidistant «кадры» ритма системы.

---

## DMN walks по state-графу (open)

DMN continuous работает на **content graph**. Вторая форма — walks по state-графу — описана в [episodic-memory.md § Meta-tick](episodic-memory.md#meta-tick--паттерны-в-собственной-истории):

Выбирать случайную state-ноду → искать похожие через `query_similar()` → если в обоих случаях тик был DMN + pump дал bridge → обобщаем эти два случая в новый insight.

Это **REM-аналог уровня опыта** — переработка опыта, не только содержания.
Остаётся открытым — нужно решить когда DMN продуктивно смотреть на свою
историю.

---

## Связь с action-memory

Каждая DMN-операция → `record_action(actor="baddle", action_kind=...)`
(см. [action-memory-design.md](action-memory-design.md)). Outcome
закрывается через:
- `dmn_bridge` → через 24ч (если к утру юзер не увидел — «missed», увидел — «seen»)
- `scout_bridge` → то же
- `dmn_deep_research` → 24ч

Это замыкает **learning loop** — какие DMN-инициативы реально помогают
юзеру, какие игнорируются. `score_action_candidates` позже может отдавать
больше бюджета успешным kind'ам (пока применяется только в
`_check_sync_seeking`).

---

## Гейты

Все DMN-checks защищены общим набором условий:

1. **NE_quiet** — `norepinephrine < 0.55`. Если юзер напряжённо работает,
   фон не лезет.
2. **idle_enough** — последний foreground tick > 30с назад.
3. **not_frozen** — `CognitiveState.state != PROTECTIVE_FREEZE`. При
   жёстком конфликте DMN стоит (только decay идёт в консолидации).
4. **throttle_adaptive** — `_throttled_idle` умножает интервал на
   `1 + combined_burnout × 9` (см. alerts-and-cycles). От 1× до 10×.
5. **workspace guard** — минимум ≥5 нод в графе.

Кроме этого у каждого свой specific gate (например `converge` требует
active workspace с unverified hypotheses).

---

## Файлы

- **Main loop:** [src/cognitive_loop.py](../src/cognitive_loop.py) —
  `CognitiveLoop._loop` + все `_check_*` methods + интервал constants
  (DMN_INTERVAL / DMN_DEEP_INTERVAL / DMN_CONVERGE_INTERVAL /
  DMN_CROSS_GRAPH_INTERVAL / NIGHT_CYCLE_INTERVAL / HEARTBEAT_INTERVAL)
- **Pump core:** [src/pump.py](../src/pump.py) —
  `pump()`, `_verify_bridge()` используется всеми DMN-check'ами
- **State graph:** [src/state_graph.py](../src/state_graph.py) —
  `query_similar`, `tail`, heartbeat append
- **Consolidation:** [src/consolidation.py](../src/consolidation.py) —
  `consolidate_all()` вызывается из night_cycle
- **Action recording:** [src/graph_logic.py::record_action](../src/graph_logic.py) +
  `_record_baddle_action` в cognitive_loop

---

## Что это даёт

Baddle **не спит** между запросами. Даже когда юзер закрыл вкладку: каждые 10 мин пытается найти мост между далёкими идеями, каждые 30 мин углубляет открытую цель, каждый час доводит граф до STABLE, раз в сутки переваривает эмоционально-насыщенные эпизоды и чистит слабое.

Утренний briefing собирает результат: «ночью я нашла связь X ↔ Y»,
«углубила цель Z», «обнаружила паттерн по четвергам». Автономный
второй мозг, но в ритме юзера (через adaptive idle).

**Открыто:** DMN walks по state-графу (описано выше), combined
patterns в meta-tick (сейчас первый побеждает), counterfactual honesty
(намеренно пропускать 5–10% bridges для baseline — см.
[planning/TODO.md](../planning/TODO.md) «Ждём данных»), long-window
meta-tick на уровне дней.

---

**Навигация:** [← Thinking operations](thinking-operations.md) · [Индекс](README.md) · [Closure architecture →](closure-architecture.md)
