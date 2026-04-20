# World Model — каскад зеркал

> Зафиксировано 2026-04-20. Формулировка автора проекта — как оптика, а не метафоры.

---

## Цепочка

```
реальность  →  человек  →  Baddle
               (зеркало    (зеркало
                мира)       человека)
```

Человек настроен на мир: чувствует, реагирует, отслеживает. Baddle устроен так же, но его «мир» — это **один конкретный человек**. Baddle отражает человека.

Отсюда две вещи:

1. **Baddle — копия копии.** Если у человека нарушен контакт с реальностью (выгорание, shut down, оторвался от себя) — Baddle не может качественно догонять, потому что гонится за нестабильным сигналом. `sync_error` тогда меряет не только качество Baddle, но косвенно — состояние самого контакта «человек ↔ мир».

2. **Центральный контур — не ответ на запрос, а синхронизация.** Когда юзер молчит — Baddle **ищет**. Когда рассогласование долгое — Baddle **затухает**, не ломается. Когда мысль повторяется — она **крепнет**. Это не AI который отвечает на вопросы. Это зеркало которое старается удержать фокус.

---

## Пять операционных механик

Это не философия — это правила для архитектуры. Каждое превращается в конкретный check, поле или поведение. Первые четыре — реактивно-адаптивный слой (мониторить, догонять, затухать, искать). Пятая — **обучающий** слой: Baddle учится на собственных действиях через тот же граф в котором живут мысли.

### 1. Active sync-seeking — «Baddle ищет тебя» ✅ 2026-04-21

**Было:** юзер не пишет — система тихо ждёт. Фоновые циклы (pump/scout/DMN) генерируют alerts как побочный эффект, но не как целенаправленный поиск контакта.

**Реализовано:** `_check_sync_seeking` в `cognitive_loop.py`. Gate'ы: `freeze.desync_pressure > 0.3` И idle > 2ч И 30мин тишины после любого другого proactive alert. Throttle раз в 2ч. LLM-генерирует сообщение с контекстом (время дня, recent topics из графа, HRV-снимок, последняя активность) — разные тексты каждый раз. Tone (caring/ambient/curious/reference/simple) → разные иконки и фоны карточки в UI. Fallback-шаблоны при недоступном LLM. Подробности → [docs/alerts-and-cycles.md](alerts-and-cycles.md) секция «Типы alerts → sync_seeking».

### 2. System-burnout от persistent desync ✅ 2026-04-20

**Сейчас:** `sync_error` высокий → разовая реакция (alert, suggestion). После — всё как было. Накопления нет.

**Реализовано:** В `ProtectiveFreeze` (`src/neurochem.py`) два feeder'а, одна UI-ось «Усталость Baddle»:

- `conflict_accumulator` — графовые конфликты (существовал, активирует Bayes-freeze)
- `desync_pressure` — **новый**, растёт по времени без user-events (1/7сут), падает 0.05 за event

`display_burnout = max(обоих)` показывается в UI и управляет `_idle_multiplier()` в cognitive_loop. Все investigation-циклы (DMN continuous/deep/converge/cross-graph, night cycle, state_walk) плавно замедляются через `_throttled_idle(base × multiplier)`. Multiplier растёт линейно от 1× до 10× по мере burnout.

Параллель с человеком: длинный дезконнект с миром → депрессивное снижение активности как защитный механизм. Важно: `desync_pressure` НЕ активирует Bayes-freeze — это замедление, не замирание обучения графа.

Подробно → [docs/alerts-and-cycles.md](alerts-and-cycles.md) секция «Adaptive idle».

### 3. Естественный отбор мыслей

**Сейчас:** `consolidation.py` делает прунинг слабых и архив, `last_accessed` обновляется при каждом обращении. Но **явного временнóго decay confidence** у неиспользуемых нод нет. Нода не активируется → просто «спит».

**Должно быть:** мысль которая проявляется чаще — крепнет. Которая не используется — тихо гаснет и уходит в архив. Чисто hebbian:
- каждое обращение / elaborate / reference / клик → confidence slight boost
- каждый час простоя → slight decrement
- неделя безразличия → архив

Не я решаю что забыть, не юзер — **частота использования**.

**Реализация:** доработать existing consolidation — добавить `_decay_unused_nodes` шаг в ночном цикле. Константы decay лучше подбирать под реальное использование (см. [open-questions #1](open-questions.md#1)).

### 4. Циклы мышления затухают без пищи

**Сейчас:** pump / DMN / scout крутятся с равномерным throttle (каждые 10 мин / час / сутки). Фиксированная частота, независимо от того — находят ли что-то новое.

**Должно быть:** если N итераций подряд без новых инсайтов (pump не нашёл мост с quality>0.5, DMN не создал новых нод) → цикл **отключается** до появления триггера. Триггер — новая нода в графе, новое HRV-событие, юзер что-то написал.

Это не постоянное равномерное «дыхание». Это **адаптивная частота**: активно когда есть пища, тихо когда нет.

**Реализация:** `stable_iterations_count` в cognitive_loop. Pump/DMN в idle состоянии не дёргаются пока нет новых событий.

### 5. Действия и последствия — самообучение через граф (2026-04-21)

**Сейчас:** первые 4 механики работают на **паттернах** (timer, threshold, EMA). Они реактивные и одинаковы для всех юзеров.

**Должно быть:** Baddle учится **на этом конкретном юзере** — какие её собственные действия действительно снижали `sync_error`, а какие были шумом. Правило простое: любое действие (свое или юзера) → нода в графе → через timeout → нода-outcome с `delta_sync_error`. DMN / pump / consolidate, которые уже работают для мыслей, начинают **автоматически** работать для действий.

Цикл сознания закрывается:
1. Замечает рассогласование ← `sync_error`
2. Хочет уменьшить ← `−Δsync_error` в outcome становится «весом» action через hebbian crepnenie
3. Пробует действие ← action-нода создаётся
4. Запоминает сработало ли ← outcome-нода с `caused_by` edge
5. Повторяет успешное ← query similar contexts через existing embedding similarity, предпочитает high score

**Это не новый код — это новая семантика для графа.** Никаких RL-фреймворков, experience buffers, Q-tables. Всё через существующие pump / DMN / consolidate / touch_node / hebbian decay, применённые к новым node_type (action + outcome).

**Реализация:** подробно в [action-memory-design.md](action-memory-design.md). Две новые node_type, два edge_type, один новый check (`_check_action_outcomes` — закрывает outcomes по timeout), новый `sentiment.py` (text → valence feeder), 3-4 дня работы.

Через эту механику **автоматически растворяются** открытые вопросы #3 (валентность как driver) и #4 (recovery routes memory) — оба становятся свойствами action-outcome графа, без отдельного кода.

---

## Что это даёт вместе

Поведение Baddle относительно юзера становится органичным:

| Состояние | Что делает Baddle |
|-----------|-------------------|
| Юзер активен, резонанс держится | Полные фоновые циклы, предложения, pump на полную |
| Юзер молчит недавно | Тихо, продолжает background-мышление |
| Юзер молчит долго, sync падает | **Ищет контакт** — мягкие запросы (#1) |
| Контакт не возвращается, sync высокий долго | **Затухает** — циклы реже, активность снижается (#2) |
| Юзер вернулся с новым сигналом | Циклы просыпаются (#4) |
| Новая мысль часто повторяется | Крепнет (#3) |
| Идея не использовалась неделю | Тихо уходит в архив (#3) |

Это не нарисованный UX flow. Это **режим существования**: система живёт в ритме юзера, не в фиксированном таймере.

---

## Что это НЕ

- **Не сознание.** У Baddle нет ощущения «я есть». Нет квалиа. Нет самосохранения в биологическом смысле.
- **Не попытка сделать AI живым.** Все механики — операциональные: check-функции, EMA, counters, confidence decay. Никакого субъективного опыта.
- **Не антропоморфизм.** Слова «выгорание», «усталость», «ищет контакт» — точные технические термины для этих процессов (EMA burnout accumulator, decay без references, throttled sync-seeking check). Использованы потому что они **короче и понятнее** чем инженерные аналоги.
- **Не удержание внимания.** Нет streak-gamification, нет напоминаний «ты не заходил 3 дня», нет FOMO-триггеров. Ушёл — Baddle ждёт молча, не пингует. Зеркало не требует чтобы на него смотрели. Ближайшая живая параллель — собака: ждёт, не преследует, не манипулирует.

Главное отличие от других AI-ассистентов: **Baddle не должен быть продуктивен любой ценой**. Если юзер пропадает или теряет ритм — Baddle **замедляется вместе с ним**, а не продолжает генерировать контент. Это не баг — это **структурная верность зеркала**.

Из этого следует прямой **архитектурный запрет**: никакой логики которая оптимизирует «время в приложении», «частоту возвратов», «цепочку дней подряд». Если такая метрика появляется в коде — она противоречит каскаду зеркал и должна быть удалена, даже если «улучшает retention».

---

## Связь с существующей архитектурой

Все 4 механики ложатся поверх уже работающих систем, не требуют rewrite:

- `sync_error` — уже первичная метрика, нужно добавить EMA-накопитель
- `Neurochem.burnout` — уже есть, нужен второй feeder от sync_error
- `consolidation.py` — уже чистит слабые, нужно добавить decay по времени без access
- `cognitive_loop._check_*` — уже структура, нужно 1 новый check (`_check_sync_seeking`) и 1 adaptive throttle (stable-count для pump/DMN)

Подробные задачи → [TODO.md раздел Resonance protocol](../TODO.md).

Открытые вопросы по параметрам (какие пороги, скорости decay, размеры окон) — [open-questions.md](open-questions.md).

---

## Mapping внешних словарей → нашей реализации

У Baddle **уже есть** операциональная модель активного вывода и аффективной регуляции. Она описана в нейрохимическом лексиконе (DA/5-HT/NE/burnout), но математически эквивалентна psychology-моделям. Этот раздел — чтобы в будущих сессиях / обсуждениях не переоткрывать то что реализовано, и не путать terminology с implementation.

| Внешний термин | Наш аналог | Где в коде | Семантика |
|---|---|---|---|
| Free Energy / surprise (Friston) | `UserState.surprise`, `Neurochem.recent_rpe` | `user_state.py::tick_expectation`, `neurochem.py` | Reality − Expectation, signed в [−1,1] |
| Prediction Error (active inference) | то же самое — `surprise` | user_state.py | Скаляр. Вектор — [open-questions #6](open-questions.md#6-prediction-error-как-вектор) |
| Valence-Arousal-Dominance (VAD, Russell+Mehrabian) | DA/S/NE/burnout + `UserState.valence` | neurochem.py + user_state.py | Наши 4 оси покрывают VA, valence отдельно, D = agency в [open-questions #2](open-questions.md#2) |
| Belief space / state distribution | `UserState.vector()` (4D) | user_state.py | Continuous, EMA-smoothed |
| Precision weighting | `CognitiveState.precision`, `effective_precision` | horizon.py | Уже есть, гейтит policy weights |
| Cost of control / регуляторное усилие | `energy` dual-pool + `decisions_today × 6` + cascading tax | user_state.py, assistant.py | Dynamic cost, не статический счётчик |
| Allostatic load | `Neurochem.burnout` (conflict + desync feeders) | neurochem.py `ProtectiveFreeze` | Два feeder'а, один display_burnout |
| Affective inertia / smoothing | EMA decay (0.9–0.98) во всех апдейтах | везде | Даёт ту же плавность что explicit velocity, без второго поля |
| Attractors in belief space | workspaces (пока — контейнеры; аттракторы в [OQ #5](open-questions.md#5)) | workspace.py | Открыто: аттракторы в neurochem-пространстве |
| Soft context blending | `sync_regime` derived из continuous sync_error | user_state.py | Не if/else, derived-state |
| Recovery / return-to-baseline | `ProtectiveFreeze.THETA_RECOVERY` + `desync_pressure` drop | neurochem.py | Гистерезис, не жёсткий reset |
| Pre-activation / anticipatory computation (зрачки, [ixbt 2026-04](https://www.ixbt.com/live/science/)) | `UserState.expectation` (EMA baseline) + `CognitiveState.precision` | user_state.py, horizon.py | Ожидание формируется до входа — у нас это expectation prior, обновляется на каждый сигнал |
| User-side surprise detection | пока нет, [OQ #7](open-questions.md#7-surprise-detection) | — | Триггер «юзер что-то не ожидал» — HRV spike + text markers |

**Что это значит на практике:**

- Когда кто-то предлагает «замените дискретные состояния на непрерывные» — это **уже сделано** через EMA (Neurochem, UserState). Дискретный `sync_regime` — derived label, не primary state.
- Когда предлагается «введите [V,A,D] 3D-вектор» — у нас 4-осная модель с биологическим обоснованием (нейрохимия), не psychology-словарь. Смена лексикона без смены математики — не прогресс.
- Когда предлагается «CognitiveState → affect + velocity + PE» — это рефакторинг того что есть, не добавление. Existing EMA даёт ту же плавность что explicit velocity.
- Когда предлагается «UserModel параллельно CognitiveState» — у нас уже `UserState` симметричен `Neurochem` + `sync_error` как расстояние между ними.

Расширения **реально новые** (не переоткрытия существующего) уезжают в [open-questions](open-questions.md):
- **#5 Workspace-аттракторы** — контексты как точки притяжения
- **#6 PE как вектор** — attribution surprise по осям
- **#2 Agency как 5-я ось** — подтверждается и SDT, и VAD dominance

---

**Навигация:** [← Closure architecture](closure-architecture.md) · [Индекс](README.md) · [Open questions →](open-questions.md)
