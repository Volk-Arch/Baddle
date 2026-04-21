# Open Questions

Место для **не-решённых архитектурных размышлений**. Это не TODO — в TODO задачи с понятным scope'ом. Это места где **не ясно что правильно**, и нужно сначала подумать / измерить / попробовать мелкий эксперимент прежде чем решать.

Правила:
- Каждый раздел — одна открытая точка.
- Формат: *контекст → проблема → возможные направления → критерий выбора*.
- Когда направление выбрано → задача уходит в TODO, вопрос остаётся здесь с пометкой «resolved: …».

---

## 1. Личные лимиты энергии: prior, не constant

### Контекст

Сейчас в `src/user_state.py`:

```python
LONG_RESERVE_MAX = 2000
DAILY_ENERGY_MAX = 100
LONG_RESERVE_DEFAULT = 1500
LONG_RESERVE_TAP_THRESHOLD = 20
```

Это **hardcoded средний человек**. Не подстраивается под фактическую capacity конкретного юзера.

### Проблема

- Физик, работающий 14 часов/день, и бабушка с депрессией имеют разные истинные ceilings — но система их обрабатывает одинаково.
- Система либо **завышает**: юзер устал, получает советы как будто может ещё, продолжает, burnout. Либо **занижает**: юзер на пике, а система блокирует heavy goal'ы со своим «⚠ Энергия <20».
- Прайм-директива — `sync_error`. Если модель юзера статична, она по определению растёт вместе с новыми данными.

### Направления

**A. Bayesian online estimation.** Каждый день = one sample:

```
predicted_capacity = prior_mean
observed = yesterday_actual_cost (когда юзер остановился)
ceiling_t+1 = α · ceiling_t + (1-α) · observed,  α ≈ 0.95
```

Remember signals:
- «Юзер закрыл день энергично» → вероятно, ceiling выше оценки.
- «Ввалил все goals к обеду и не справился» → ceiling был ниже.
- HRV-derived recovery → корректировка `long_reserve_max` через недели (тренд RMSSD).

**B. Rhythm-aware** — не просто `daily_max`, а `daily_max[weekday][time_of_day]`. Через 3-4 недели даст индивидуальный ритм. Понедельник / вечер пятницы / воскресенье утром — разные ceilings.

**C. Surprise как guardian.** В UserState уже есть signed prediction error (`surprise`). Если каждый день surprise смещён в одну сторону — model биасится, корректируем ceiling. Это бесплатное качество оценки — сигнал уже собирается.

### Критерий выбора

Ставим эксперимент: 2 недели параллельно считаем `ceiling_static = 100` и `ceiling_estimated` (Bayesian). Если avg `sync_error` с estimated < static на >15% — принимаем. Если нет — остаёмся с constant как работающим baseline.

### План действий (когда решимся)

`src/capacity_estimator.py` — online update модуль:
- Входы: `daily_spent` + `stop_events` + `nightly_hrv_rmssd`
- Выход: `{daily_max, long_reserve_max, daily_max_by_hour, by_weekday}` в `user_profile.json`
- Медленный EMA (α ≈ 0.95) чтобы не прыгать
- Кормит `UserState._compute_energy` → daily_remaining считается из personal baseline

---

## 2. Четыре оси нейрохимии: imiplementation, а не user-facing

**Статус 2026-04-21: 5-я ось `agency` включена в сбор данных.**
`UserState.agency` (default 0.5) обновляется раз в час из `schedule_for_day()`
через EMA (decay 0.95). Показывается в UI пятой карточкой «Агентность».
Пока **НЕ** входит в 4D sync_error vector — через 2-3 недели измерений
решаем включать или отменить. Остальное (meaning / relatedness / flow-vs-DMN)
остаётся открытым пока эту одну не validating.

### Контекст

Сейчас `UserState` и `SystemState` оба питают 4 скаляра:

```
dopamine / serotonin / norepinephrine / burnout
```

Это **нейрохимическая метафора** для внутренней модели. Отлично работает как язык описания динамики системы.

### Проблема

Для **пользователя** эти оси слишком низкого уровня. Они — implementation, не то что человек чувствует.

Примеры:
- Высокий NE = «норадреналин» = «напряжение». Но не отличает «перегрет задачей» от «тревожусь про семью» — **причины разные, правильные действия разные**.
- Low dopamine = «мало интереса». Но не отличает «скучная работа» от «хроническая подавленность».
- «burnout» как единый scalar смешивает физическое истощение и эмоциональное выгорание — это разные процессы.

### Что теоретически лучше описывает опыт

| Ось | Показывает | Измерение (как) | Почему важна |
|---|---|---|---|
| **Agency** (чувство контроля) | «могу / не могу влиять» | `completed/planned`, частота отмен планов, surprise polarity | Low agency → learned helplessness, требует разукрупнения задач, не отдыха |
| **Meaning** (смысл) | Работа имеет значение? | Positive surprise на важных целях, темы в чате, частота «зачем я это делаю» | Low meaning + high energy = классический burnout profile |
| **Relatedness** (связь с людьми) | Социальное состояние | `activity[category=social] / week`, упоминания в записях | Ломается иначе чем stress |
| **Flow vs DMN** (режим сознания) | Goal-directed vs бродит | Variance длины сообщений, coherence между goal и activity | Фундаментальная оппозиция в neuroscience |
| **Urgency** (deadline pressure) | Дедлайн давит | Open goals near deadline | Отличается от общего stress — нужен фокус, не отдых |

### Направления

**A. Layer separation.** DA/S/NE/burnout остаются как **internal engine**. UI показывает пользователю 5-6 **user-facing axes** (valence + arousal + agency + relatedness + meaning). Trade-off: два слоя, двойная сложность, но зато internals не ломаются.

**B. Expand to 6-7.** `UserState` получает новые fields (agency, meaning, relatedness). Sync-error считается в 6-7 мерном пространстве. Честно, но дорого: нужно придумать и калибровать measurement для каждой новой оси.

**C. Status quo.** 4 оси — компромисс покрытия и простоты. Меньше (2 valence × arousal) теряем «почему», больше — шумно и измерения ненадёжны.

### Независимое подтверждение (2026-04-21)

Внешний диалог предложил VAD-модель (Valence / Arousal / **Dominance**). Dominance в psychological literature = «feeling of control / agency». Это косвенно подтверждает выбор **agency** как первой 5-й оси: две независимые traditions (self-determination theory из #2 и dimensional emotion из VAD) сходятся на одном term. Приоритет задачи — повышен.

### Критерий выбора

Middle-ground: **сначала добавить одну ось — `agency`** — как derived metric без изменения core. Она почти бесплатна:

```python
agency_today = completed_today / max(1, planned_today)
# поправка на разукрупнение: split-events → агентность растёт
```

Показываем в UI. Проверяем через 2-3 недели:
- Если `agency` регулярно вносит ≥ 20% в `sync_error` — ось работает, добавляем следующую (`meaning` или `relatedness`).
- Если шумит / не коррелирует — убираем, возвращаемся к 4.

Это Baddle-way: **прайм-директива мерит свои же расширения**.

### План действий (когда решимся)

1. `UserState.agency: float` добавить field, `update_from_plan_completion()` метод.
2. Dashboard — 5-я карточка «Agency» рядом с Интерес/Стабильность/Напряжение/Усталость.
3. Через 2 недели — проверить `sync_error` contribution, принять или отменить.

---

## 3. Валентность без антропоморфизма

> **Статус 2026-04-21: merged в [Action Memory](action-memory-design.md).**
> Предложенный в #3 путь (valence = −Δsync_error на event-level) —
> **именно** то что делает Action Memory для action-outcome пар.
> Мгновенный UserState.valence остаётся как sentiment-feeder из текста,
> policy-level valence встроена в action scoring через existing hebbian
> в графе. Отдельной реализации не требуется. Секция ниже оставлена как
> историческая запись размышлений.

---

### Контекст

Из переоптики 2026-04-20 (см. [world-model.md](world-model.md)) — чтобы Baddle стал камертоном, нужен слой **valence**: метрики получают «вес» — приятно/неприятно. Система начинает **предпочитать** одни состояния другим, а не просто вычислять. Без этого — вычисление без мотивации.

### Проблема

«Приятно/неприятно» звучит антропоморфно. Но операционально это:

```python
loss = base_loss - α * (coherence - baseline_coherence) \
                 + β * (burnout - baseline_burnout) \
                 + γ * sync_error ...
```

Просто коэффициенты в функции потерь. Никакого «чувствования».

Но вопрос в том **откуда брать эти коэффициенты**:
- Hardcoded → та же проблема что с DAILY_ENERGY_MAX (см. #1) — не подстраивается.
- Learned → нужно чему-то «обучать» валентность. На чём? Пользовательские апдейты (Да/Нет в intent_confirm) = очень редко. Success/failure микро-действий Actuator'а = лучше, но нужен cycle.
- Inferred from sync_error dynamics → самый чистый путь: валентность события = -(Δsync_error после него). Действие снизило рассогласование — «хорошо», повысило — «плохо». Никакой subjective оценки не нужно.

### Направления

**A. Валентность как observation of sync_error gradient.** Каждому event/action присваивается `valence = -Δsync_error`. Система учится на этом, не на subjective ratings.
Плюс: операциональный, без anthropomorphism'а, данные уже есть.
Минус: sync_error — сам по себе noisy signal на коротких окнах.

**B. Mixed: sync_error + explicit user feedback.** Добавить в intent_confirm и в ответы кнопку «это сработало / нет» → редкий но высококачественный сигнал. Используется как regularizer для A.

**C. Valence = multi-component.** Отдельные коэффициенты для coherence, burnout, energy, surprise. Не один скаляр, а 4. Сложнее, но точнее.

### Критерий выбора

Начать с **A** (простейшее, использует существующие данные). Если после внедрения Actuator'а и Experience Memory валентность систематически расходится с реальной успешностью — добавляем B (explicit feedback). C — только если A+B даёт слишком coarse сигнал.

### План действий (когда решимся)

1. В state_graph каждая entry получает `valence` поле (автодеривация: `- (sync_error_after - sync_error_before)` в окне 60-300с).
2. Actuator читает `experience_memory.best_action_for(context)` где «best» = max expected valence.
3. 2 недели A/B: с валентностью / без → смотрим trajectory `sync_error` по дням.

---

## 4. Recovery routes memory — путь возвращения в resonance

> **Статус 2026-04-21: merged в [Action Memory](action-memory-design.md).**
> Recovery routes = частный случай action-outcome памяти для
> `action_kind ∈ {sync_seeking, suggestion_*}`. Ровно то же поведение —
> «какое действие возвращало этого юзера в resonance» — получается
> бесплатно из общей инфраструктуры через query similar contexts.
> Открытые параметры (per-user baseline noise в OQ #4.C counterfactual
> honesty) остаются релевантны, но в контексте Action Memory.

---

### Контекст

Три из четырёх механик resonance protocol (см. [world-model.md](world-model.md)) — hebbian по природе: мысль крепнет от обращений, гаснет от простоя. Hebbian меряет **частоту** возвращения к ноде / мысли / теме.

Но resonance — это не просто «частое крепче». У каждого юзера есть свой **путь** возвращения в резонанс после рассогласования. Текущий `_check_sync_seeking` (механика #3 в TODO) выбирает шаблон мягкого запроса в чат полу-рандомно — но через месяц использования станет ясно, **что именно** работает для этого человека.

### Проблема

Разные люди возвращаются по-разному:

- **Юзер A:** sync_error растёт → Baddle спросил «как ты?» → юзер проигнорировал → через день вернулся сам. Для него **тишина** сработала лучше вопроса — вопрос воспринимался как давление.
- **Юзер B:** sync_error растёт → Baddle спросил «как ты?» → юзер сразу ответил → resonance восстановлен. Для него **вопрос** — точный инструмент, он ждал повода.
- **Юзер C:** sync_error растёт → Baddle предложил конкретную активность («выйди 10 минут?») → юзер вышел → вернулся в resonance. Для него помогает **внешний импульс**, не разговор.

Все три эпизода — одна и та же метрика «юзер вернулся». Hebbian на уровне нод этого **не различает**. Но это важно для механики sync-seeking: повторять то что работало, отказаться от того что не работало.

### Направления

**A. Recovery-typed events в state_graph.** Каждое событие где `sync_error` резко падает после длительного роста (`desync_duration > T` и `Δsync_error < -threshold`) — помечается как `recovery_event` с полями:

```python
{
  "preceding_baddle_action": "sync_seeking_question" | "silent_wait" |
                              "dmn_bridge_offer" | "activity_suggestion" | ...,
  "desync_duration_s": seconds,
  "recovery_delta": sync_error_before - sync_error_after,
  "user_context": {time_of_day, day_of_week, coherence, energy},
}
```

Через N событий — агрегат: «какое действие Baddle статистически чаще снижало sync_error после длительного desync, в каких контекстах». Sync-seeking читает этот агрегат, смещает распределение выбора шаблона.

**B. Частный случай valence (#3).** Если valence внедрена (`valence = -Δsync_error` на event level), recovery routes = valence, **стратифицированный по типу действия Baddle + контексту юзера**. Не отдельная система, а агрегирующий view поверх уже существующего signal'а.

**C. Counterfactual honesty.** Проблема и у A, и у B: мы не знаем что было бы **без действия** Baddle. Возможно юзер вернулся сам, а sync_seeking_question сработал вхолостую (или даже отсрочил). Честно — намеренно **не действовать** в части эпизодов и мерить baseline recovery time. Но это нарушает сам принцип механики #3 («Baddle ищет»). Компромисс: counterfactual эпизоды редкие, для калибровки, не постоянно.

### Критерий выбора

Зависит от #3 (valence). Если valence внедрена и не шумит — **B тривиален** (recovery routes = typed valence в новом view). Если #3 откатили или неустойчиво — нужно **A** (отдельный event-тип в state_graph).

**Счёт срочности — низкий.** Нужно **минимум месяц** реальных данных после внедрения active sync-seeking (механика #3 из resonance protocol), чтобы было на чём статистику считать. До этого — спекуляции без base rate.

### План действий (когда решимся)

1. Дождаться что `_check_sync_seeking` работает ≥ месяц, набрать выборку recovery-эпизодов.
2. Лог-анализ: после каких действий Baddle sync_error реально падал, после каких нет или рос.
3. Если есть статистически значимые различия между типами действий → внедрять A или B (выбор определяется состоянием #3 на тот момент).
4. Встроить в будущий Actuator (если он появится): `best_recovery_action_for(user_context)` вместо полу-случайного выбора.

### Почему это здесь, а не в TODO

- Нет данных — нельзя имплементировать честно, получится домысел.
- Зависит от #3 (структурно может раствориться в ней).
- Contra-паттерн «антропоморфизм» есть риск: «собака помнит как ты возвращаешься» звучит красиво, но без статистики на реальной выборке это будет hardcoded if-else под нескольких юзеров.

---

## 5. Workspace-аттракторы — контексты как точки притяжения в neurochem

### Контекст

Сейчас workspaces (`work`, `personal`, `research`, ...) — контейнеры для графа и целей. Переключение между ними — дискретная операция через workspace-select. Juger либо в `work`, либо в `personal`, третьего не дано.

Но **физиологически** переключение контекста имеет **цену** (decision fatigue, context switching tax). Юзер, 15 раз за день переключившийся между work и personal, устаёт сильнее чем тот кто провёл day в одном контексте.

### Проблема

Мы **не видим** cost переключения. Energy у нас расходуется на решения (`decisions_today × 6`), но скачок между контекстами не отличается от скачка внутри одного.

### Направления

**A. Workspace как target в neurochem-пространстве.** Каждый workspace имеет profile-вектор ожидаемого состояния:

```python
WORKSPACE_ATTRACTORS = {
    "work":     {"dopamine": 0.6, "norepinephrine": 0.5, "serotonin": 0.4, "burnout": 0.3},
    "personal": {"dopamine": 0.4, "norepinephrine": 0.3, "serotonin": 0.6, "burnout": 0.2},
    "rest":     {"dopamine": 0.3, "norepinephrine": 0.2, "serotonin": 0.7, "burnout": 0.1},
}
```

Дистанция в 4D между текущим `UserState` и attractor текущего workspace → **дополнительный context_cost**, добавляется к burnout на каждый tick или switch.

**B. Soft blending вместо hard switch.** Юзер работает в `work`, но в фоне периодически открывает `personal`. Вместо жёсткого current=one — вероятностный mix `{work: 0.7, personal: 0.3}` на основе последних N actions. CognitiveState «находится» между аттракторами, и cost переключения = distance × frequency.

**C. Per-user attractor learning.** Не hardcode profile-векторов, а learn из истории: когда юзер **реально** в `work` — какое среднее DA/S/NE/burnout наблюдалось? Через 2-3 недели use у каждого свой набор аттракторов, соответствующий его реальному опыту контекста.

### Критерий выбора

Начать с **A** (hardcoded attractors, 3-4 основных workspace-тип). Добавить в morning briefing секцию «вчера ты переключался между work/rest 12 раз, cost = X». Если метрика коррелирует с вечерним burnout — validated, переходим к C (learned profiles). Если шумит — возвращаемся к status quo (workspaces без attractor-metric).

### План действий (когда решимся)

1. Добавить `WORKSPACE_ATTRACTORS` константу в `src/workspace.py` или profile-based.
2. В `CognitiveLoop._check_activity_cost` добавить `switch_cost` на event переключения workspace.
3. Morning briefing — секция «context switching» если switch_count > 5.
4. 2-3 недели наблюдения → решение о learned profile (C).

### Источники идеи

Внешний AI-диалог 2026-04-21 (градиентное поле [V,A,D] + context_attractors как Вороной → soft weights). Ложится поверх существующего без рефакторинга: аттракторы в нашей 4-осной нейрохимии, не в VAD.

---

## 6. Prediction error как вектор — attribution по осям

> **Статус 2026-04-23: resolved (3D + attribution).**
> - 3D `surprise_vec` в `UserState` и `Neurochem` — см. [friston-loop.md](friston-loop.md).
> - `UserState.attribution` возвращает доминирующую ось ('dopamine' | 'serotonin'
>   | 'norepinephrine'), `attribution_magnitude` и `attribution_signed` —
>   для UI debug «что именно недооценили».
> - Per-channel decomposition в `data/prime_directive.jsonl` (user_imbalance,
>   self_imbalance, agency_gap, hrv_surprise) — видно какой канал реально
>   двигал burnout через 2 мес.
>
> **Что остаётся:** расширить `vector()` до 4D (включить agency) если через
> месяц use окажется что она соизмерима с нейрохимическими осями. Пока
> agency держим как отдельный канал в `imbalance_pressure` через `agency_gap`.
> Секция ниже оставлена как историческая запись.

---


### Контекст

Сейчас `UserState.surprise = reality − expectation` и `Neurochem.recent_rpe` — **скаляры**. «Насколько реальность отклонилась от ожидания», один number.

### Проблема

Скаляр теряет информацию о **том, в чём именно ошиблись**. Одинаковое `surprise = 0.5` может быть:
- неожиданно высокий DA (юзер в потоке, мы не ожидали)
- неожиданно низкий serotonin (юзер нестабилен, мы думали спокоен)
- неожиданно высокий burnout (юзер устал быстрее)

Все три требуют разной реакции системы. Текущий скаляр сливает их в один шум.

### Направления

**A. Per-axis surprise.** `surprise` → `surprise_vec = [Δdopamine, Δserotonin, Δnorepinephrine, Δburnout]`. `|surprise_vec|` остаётся как старый скаляр (обратная совместимость), но появляется атрибуция: «в какой оси модель ошибалась больше всего».

```python
surprise_vec = user_observed_vec - system_expectation_vec
attribution = argmax(abs(surprise_vec))  # ось с максимальной ошибкой
```

**B. Drive-force interpretation.** Использовать `surprise_vec` как «силу сдвига» expectation EMA: `expectation += α · surprise_vec`. Это уже делается скалярно через `tick_expectation()`, но per-axis даст более точную калибровку — baseline подстраивается там где правда ошибались, не шумит остальное.

**C. UI-feedback.** В карточке dashboard показать «в чём система ошибалась сегодня»: «недооценил твой interest на 30%» vs «не понял твой стресс». Debug-канал для понимания почему sync_error высокий.

### Критерий выбора

**A + B** идут вместе: per-axis surprise почти бесплатно (4 числа вместо 1 в EMA), attribution в UI — отдельно по готовности.

Критерий: через 2 недели посмотреть — есть ли системный bias? Если юзер постоянно больше surprise в одной оси → model калибруется лучше → `sync_error` общий падает. Если bias случайный → вернуться к скаляру.

### План действий (когда решимся)

1. `UserState.surprise_vec: np.ndarray` добавить поле (4D).
2. `tick_expectation` — разбить на per-axis EMA.
3. Dashboard тестово — цифра «max attribution» для debug.
4. 2 недели → accept/reject based on sync_error trajectory.

### Источники идеи

Тот же AI-диалог 2026-04-21 (PE как векторная сила `∇U`, а не скаляр-множитель). Ложится на существующий `surprise` механизм — расширение, не переписывание.

---

## 7. Surprise detection у **юзера** — триггер «он только что что-то не ожидал»

> **Статус 2026-04-23: resolved (MVP A+B).**
> - `src/surprise_detector.py` — HRV-based (rolling RMSSD σ-threshold +
>   activity gate) + text markers (ru/en regex + капс + `??`/`!!!` bursts).
> - Combined check `detect_user_surprise(text, activity)` — OR обоих.
> - Integration: `cognitive_loop._check_user_surprise` раз в 5 мин;
>   throttle + processed_msg_ts защита от повторной обработки.
> - Side-effects: `UserState.apply_surprise_boost(3)` → EMA decay
>   ускоряется 0.98→0.85 на 3 tick'а (baseline быстро адаптируется к
>   новой реальности юзера); `record_action(actor="user",
>   action_kind="user_surprise", extras={source, confidence, ...})`
>   в граф для action-memory и DMN-bridge discovery.
>
> **Не реализовано** (см. [friston-loop.md § User-side surprise](friston-loop.md#user-side-surprise-oq-7)):
> - LLM fallback для borderline text scores
> - Dialog pivot detection через embedding distance
> - Per-user adaptive HRV threshold (связано с OQ #1)
> - Pupil tracking (D) и GSR/EDA (E) — зависят от hardware
>
> Секция ниже оставлена как историческая запись дизайна.

---


### Контекст

Сейчас `UserState.surprise = reality − expectation` — это **системный** surprise: насколько наша модель юзера ошиблась. Это **наш** сюрприз относительно него.

Но у юзера есть **свой** сюрприз относительно мира: он прочитал что-то неожиданное, увидел неожиданный результат, услышал факт который меняет картину. Это **другое** событие, и оно было бы очень полезно знать:

- Если юзер только что что-то узнал → `expectation` должно обновиться быстрее (его модель мира изменилась, мы не должны продолжать опираться на старый baseline)
- Если юзер удивлён конкретной информацией → Baddle может спросить «что это значит?» и зафиксировать момент инсайта
- Паттерн «часто удивляется темой X» → добавить в профиль как область активного обучения
- HRV spike на surprise → корректная реакция системы, не paniс

Исследование из [ixbt 2026-04](https://www.ixbt.com/live/science/...) (зрачки показывают начало вычислений ДО условий) — мозг делает pre-activation по ожидаемому паттерну. Surprise = момент когда pre-activation не совпала с input, это чёткий физиологический сигнал.

### Проблема

Откуда брать surprise-trigger? Прямые методы дорогие / privacy-sensitive / требуют hardware. Косвенные — шумят.

### Направления (по реализуемости)

**A. HRV-based (самое чистое, инфра готова).** RMSSD drop на 15-30% в окне 10-20с или LF/HF jump от baseline — физиологическая реакция на surprise. Симулятор уже есть, реальные адаптеры (Polar / Apple / Oura) подключатся. `sensor_stream.py` уже накапливает данные, нужен detector поверх.

Критерий: `|ΔRMSSD| > threshold × baseline_std` в окне N секунд → `surprise_event` в state_graph.

**B. Text markers.** В самом сообщении юзера: маркеры «воу», «не ожидал», «??», «wait», «really?», «huh», многоточия, капс. Плюс behavioral: пауза между быстрым набором и отправкой (набирал быстро, остановился, подумал, отправил). Regex + LLM-classify на сомнительных.

**C. Dialog pivot detection.** Резкое изменение темы после ответа Baddle — возможно юзер увидел что-то неожиданное и переключился. Измеряется через embedding-distance между последовательными user-сообщениями.

**D. Pupil tracking через webcam.** Самый точный научно, но: требует CV pipeline (MediaPipe face landmarks + iris tracking), privacy-sensitive, работает только когда юзер смотрит в камеру. Почти наверняка не для MVP.

**E. GSR / EDA через wearable.** Будущее (Oura / Apple Watch раздают limited EDA). Очень чистый surprise signal, ждём когда sensors будут доступны.

### Критерий выбора

MVP — **A + B** (HRV + text markers). Оба требуют существующей инфраструктуры без новой.

1. Detector в `cognitive_loop._check_user_surprise`: читает последние N секунд HRV из sensor stream + последнее user-сообщение из chat_history. Если HRV spike ИЛИ text marker match → event.
2. `user_state.py::tick_expectation` — при surprise_event временно ускорить EMA decay (0.98 → 0.85) на 2-3 ticks: наш baseline быстро адаптируется к тому что юзер узнал новое.
3. Опционально: Baddle спрашивает «что ты сейчас увидел?» (max раз в час, мягко).

### Что за этим остаётся неясным

- **Baseline noise у разных людей разный.** Чей RMSSD дрожит на ±15% в покое, чей стабильный. Нужен per-user adaptive threshold (рядом с OQ #1 personal capacity).
- **Text markers русско-английская смесь.** Регексы вырастают. Можно делегировать LLM classify «surprised / not».
- **False positives от стресса / кофеина / физнагрузки.** HRV spike не всегда surprise. Нужна disambiguation через context: если `activity_magnitude > threshold` — игнорировать.

### План действий (когда решимся)

1. `HRVSurpriseDetector` в sensor_stream — rolling std × threshold.
2. `text_surprise_score(msg)` — regex + LLM fallback.
3. `_check_user_surprise` в cognitive_loop — раз в 30с, читает оба сигнала.
4. Event в state_graph: `{kind: "user_surprise", source: "hrv|text|combined", confidence}`.
5. 2 недели наблюдения → validate false-positive rate на реальных записях.

### Источники идеи

ixbt-статья 2026-04-21 про зрачки как pre-activation signal + просьба юзера «как бы этот триггер достать, он полезен». Связан с OQ #1 (personal baseline adaptive) и с текущими feedback-каналами UserState (accept/reject — уже существующие user-surprise proxies, но низкочастотные).

---

## Добавлять сюда

Когда встречаем архитектурный вопрос без очевидного ответа — раздел сюда. Не в TODO.

Признаки что место этому здесь, а не в TODO:
- Не ясно **что правильно**, а не «как сделать».
- Требует эксперимента / измерения перед implementation.
- Может потребовать откатить решение — лучше не embedding сразу.
- Зависит от данных которых пока нет (неделя использования / долгий HRV).

---

**Навигация:** [Индекс docs](README.md) · [TODO](../planning/TODO.md)
