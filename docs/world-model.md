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

**Реализовано:** `_check_sync_seeking` в `cognitive_loop.py`. Gate'ы: `freeze.silence_pressure > 0.3` И idle > 2ч И 30мин тишины после любого другого proactive alert. Throttle раз в 2ч. LLM-генерирует сообщение с контекстом (время дня, recent topics из графа, HRV-снимок, последняя активность) — разные тексты каждый раз. Tone (caring/ambient/curious/reference/simple) → разные иконки и фоны карточки в UI. Fallback-шаблоны при недоступном LLM. Подробности → [docs/alerts-and-cycles.md](alerts-and-cycles.md) секция «Типы alerts → sync_seeking».

### 2. System-burnout от persistent desync ✅ 2026-04-20 · расширено 2026-04-23

**Было:** `sync_error` высокий → разовая реакция (alert, suggestion). После — всё как было. Накопления нет.

**Реализовано:** единая «Усталость Baddle» из трёх источников:

- **Графовые конфликты** — единственный источник, активирующий Bayes-freeze (останавливает обучение).
- **Молчание юзера** — таймер, ползёт вверх при отсутствии событий (~7 суток без контакта → полный), падает при любом сигнале.
- **Накопленный рассинхрон** — EMA aggregate 4-х каналов prediction error: где юзер, куда тянут его цели, тело, собственные ожидания Baddle. Берётся max по каналам, baseline'ы TOD-scoped (утренняя apathy не маскирует вечернюю). Это и есть настоящее «рассогласование предсказания».

UI показывает максимум трёх. Юзер-усталость добавляется поверх, замедляя фоновые циклы (DMN, night cycle, scout) линейно до 10×. Важно: молчание и рассинхрон НЕ замораживают обучение графа — это замедление, не остановка.

Параллель с человеком: длинный дезконнект с миром → депрессивное снижение активности как защитный механизм.

**Прайм-директива: sync_error измеряется сам.** Две EMA (быстрая 1ч, медленная 3д) + append-only лог `data/prime_directive.jsonl`. Endpoint `/assist/prime-directive?window_days=30` возвращает aggregate + trend verdict (`improving`/`stable`/`worsening`). Это закрывает разрыв между манифестом («единственная метрика — sync_error») и возможностью её измерить через 2 мес use.

Подробно → [alerts-and-cycles.md](alerts-and-cycles.md) § Adaptive idle + § Прайм-директива. Предиктивный контур — [friston-loop.md](friston-loop.md).

### 3. Естественный отбор мыслей

**Сейчас:** `consolidation.py` делает прунинг слабых и архив, `last_accessed` обновляется при каждом обращении. Но **явного временнóго decay confidence** у неиспользуемых нод нет. Нода не активируется → просто «спит».

**Должно быть:** мысль которая проявляется чаще — крепнет. Которая не используется — тихо гаснет и уходит в архив. Чисто hebbian:
- каждое обращение / elaborate / reference / клик → confidence slight boost
- каждый час простоя → slight decrement
- неделя безразличия → архив

Не я решаю что забыть, не юзер — **частота использования**.

**Реализация:** доработать existing consolidation — добавить `_decay_unused_nodes` шаг в ночном цикле. Константы decay лучше подбирать под реальное использование (см. [TODO § OQ #1](../planning/TODO.md)).

### 4. Циклы мышления затухают без пищи

**Сейчас:** pump / DMN / scout крутятся с равномерным throttle (каждые 10 мин / час / сутки). Фиксированная частота, независимо от того — находят ли что-то новое.

**Должно быть:** если N итераций подряд без новых инсайтов (pump не нашёл мост с quality>0.5, DMN не создал новых нод) → цикл **отключается** до появления триггера. Триггер — новая нода в графе, новое HRV-событие, юзер что-то написал.

Это не постоянное равномерное «дыхание». Это **адаптивная частота**: активно когда есть пища, тихо когда нет.

**Реализация:** `stable_iterations_count` в cognitive_loop. Pump/DMN в idle состоянии не дёргаются пока нет новых событий.

### 5. Действия и последствия — самообучение через граф ✅ 2026-04-21

**Было:** первые 4 механики — реактивно-адаптивные, одинаковы для всех. Baddle видит и реагирует, но не **учится** какие её собственные действия действительно помогают этому конкретному человеку.

**Реализовано:** два новых node_type (`action`, `outcome`) + edge `caused_by` + одна новая проверка `_check_action_outcomes`. Все existing механики (DMN / pump / consolidate / touch_node / hebbian) автоматически работают для действий — специального RL-кода нет.

Цикл сознания закрывается:
1. Замечает рассогласование ← `sync_error`
2. Хочет уменьшить ← `−Δsync_error` в outcome = «вес» action через existing hebbian крепнутие
3. Пробует действие ← action-нода в графе (`record_action`)
4. Запоминает сработало ли ← outcome-нода через `_check_action_outcomes` (timeout per kind или user-reaction)
5. Повторяет успешное ← `score_action_candidates` query через graph scan, applied в `_check_sync_seeking` tone choice

**Sentiment** юзера вплетён как metadata user_chat-ноды + высокочастотный feeder в `UserState.valence` (light LLM classify с кэшем).

**Merge:** OQ #3 (валентность как driver) и OQ #4 (recovery routes) **растворились** в этой механике — оба реализованы как свойства action-outcome графа, отдельного кода не потребовалось.

**Подробности:** [action-memory-design.md](action-memory-design.md).

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

Подробные задачи → [TODO § Resonance protocol](../planning/TODO.md).

Открытые вопросы по параметрам (какие пороги, скорости decay, размеры окон) — [TODO § Открытые вопросы](../planning/TODO.md).

---

## Mapping внешних словарей → нашей реализации

У Baddle **уже есть** операциональная модель активного вывода и аффективной регуляции. Она описана в нейрохимическом лексиконе (DA/5-HT/NE/burnout), но математически эквивалентна psychology-моделям. Этот раздел — чтобы в будущих сессиях / обсуждениях не переоткрывать то что реализовано, и не путать terminology с implementation.

| Внешний термин | Наш аналог | Где в коде | Семантика |
|---|---|---|---|
| Free Energy / surprise (Friston) | `UserState.surprise` + `surprise_vec` (3D) | `user_state.py::tick_expectation`, подробно → [friston-loop.md](friston-loop.md) | 3D PE-вектор + attribution |
| Prediction Error (active inference) | 5 PE-каналов → `imbalance_pressure` | friston-loop.md § Анатомия | user 3D + TOD + goal + HRV + self, max-aggregated |
| Valence-Arousal-Dominance (VAD, Russell+Mehrabian) | DA/S/NE + `UserState.valence` + `UserState.agency` | neurochem.py + user_state.py | 3 оси арусала, valence отдельно, D = agency (OQ #2 measurements) |
| Belief space / state distribution | `UserState.vector()` (3D, 2026-04-23) | user_state.py | Continuous, EMA-smoothed, burnout как отдельное поле |
| Precision weighting | `CognitiveState.precision`, `effective_precision` | horizon.py | Уже есть, гейтит policy weights |
| Cost of control / регуляторное усилие | `energy` dual-pool + `decisions_today × 6` + cascading tax | user_state.py, assistant.py | Dynamic cost, не статический счётчик |
| Allostatic load | `ProtectiveFreeze` (conflict + silence + imbalance feeders) | neurochem.py | Три feeder'а, один display_burnout, плюс `combined_burnout(user)` для idle multiplier |
| Affective inertia / smoothing | EMA decay (0.9–0.98) во всех апдейтах | везде | Даёт ту же плавность что explicit velocity, без второго поля |
| Attractors in belief space | workspaces (пока — контейнеры; аттракторы в [OQ #5](../planning/TODO.md)) | workspace.py | Открыто: аттракторы в neurochem-пространстве |
| Soft context blending | `sync_regime` derived из continuous sync_error | user_state.py | Не if/else, derived-state |
| Recovery / return-to-baseline | `ProtectiveFreeze.THETA_RECOVERY` + `silence_pressure` drop | neurochem.py | Гистерезис, не жёсткий reset |
| Pre-activation / anticipatory computation (зрачки, [ixbt 2026-04](https://www.ixbt.com/live/science/)) | `UserState.expectation_by_tod` (per-TOD baseline) + `CognitiveState.precision` | user_state.py, friston-loop.md | Ожидание контекстуально (morning/day/evening/night), не один глобальный baseline |
| User-side surprise detection | `src/surprise_detector.py` (HRV + text + LLM) | см. [friston-loop.md § User-side surprise](friston-loop.md#user-side-surprise-oq-7) | ✅ 2026-04-23 — MVP A+B+C |
| Self-prediction (симметрия Friston-loop) | `Neurochem.expectation_vec` + `self_imbalance` | neurochem.py | Baddle предсказывает собственную [DA,S,NE] |

**Что это значит на практике:**

- Когда кто-то предлагает «замените дискретные состояния на непрерывные» — это **уже сделано** через EMA (Neurochem, UserState). Дискретный `sync_regime` — derived label, не primary state.
- Когда предлагается «введите [V,A,D] 3D-вектор» — у нас 4-осная модель с биологическим обоснованием (нейрохимия), не psychology-словарь. Смена лексикона без смены математики — не прогресс.
- Когда предлагается «CognitiveState → affect + velocity + PE» — это рефакторинг того что есть, не добавление. Existing EMA даёт ту же плавность что explicit velocity.
- Когда предлагается «UserModel параллельно CognitiveState» — у нас уже `UserState` симметричен `Neurochem` + `sync_error` как расстояние между ними.

Расширения **реально новые** (не переоткрытия существующего):
- **#5 Workspace-аттракторы** — контексты как точки притяжения (открыто)
- ~~#6 PE как вектор~~ — resolved 2026-04-23 в [friston-loop.md](friston-loop.md)
- ~~#7 User-side surprise~~ — resolved 2026-04-23 в [src/surprise_detector.py](../src/surprise_detector.py)
- **#2 Agency как 5-я ось** — в процессе measurements, подтверждается и SDT, и VAD dominance

---

**Навигация:** [← Closure architecture](closure-architecture.md) · [Индекс](README.md) · [Open questions →](../planning/TODO.md)
