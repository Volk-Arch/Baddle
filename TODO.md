# TODO

> Концепция, архитектура и научные основы → [README](README.md)
>
> Сверху — что ещё не сделано. Снизу — что сделано (не тестировано / в main).

---

# ⬆ НЕ СДЕЛАНО

## v1.7: доводка

- [ ] **Pump: визуализация облаков** на SVG (два hull расширяющихся навстречу)

---

## v2: доработки

- [ ] **Snapshot для repeatable** — виджет streak/today/trend
- [ ] **Персистентность для Ритм** — state beyond session (дни/недели), history log
- [ ] **Pause-on-question в autorun** — во время Run система приостанавливается и
  через уже существующий `/graph/assist` задаёт вопрос пользователю когда
  неопределённость высокая. Требует state-машины в JS autorun и signal'а из tick
  (например новый action `"ask"` когда зоны EXPLORE доминируют)

---

## v3: источники данных

- [ ] **Доступ в интернет** — search / RAG, для фактчекинга в исследовательских режимах
- [ ] **Гибрид LLM + поиск** — LLM генерит гипотезу → поиск проверяет факты
- [ ] **Per-этап выбор модели** — local 8B для generate, API для doubt/essay
- [ ] **UI** — настройки источника per-режим и per-этап

---

## v4: мульти-граф и мета-граф

- [ ] **Множественные графы** — вкладки, отдельный save/load, теги/слои
- [ ] **Мета-граф** — отдельный граф связей между графами
- [ ] **Cross-graph edges** — `serendipity_engine`, ассоциации между задачами
- [ ] **JSONL storage** — `nodes.jsonl` + `edges.jsonl` + `meta.json`, lazy load для больших графов

---

## v5: автономность + резонансный режим

### 5a: Автономное блуждание

- [ ] **Постоянный tick + переключение Horizon↔DMN по NE** — система не стоит в
  ожидании Run, а тикает непрерывно в фоне. Разделение бюджета между Horizon
  (активная задача) и DMN (фоновое блуждание) идёт по уровню норадреналина (см. v5d):
  - Юзер пишет/делает ввод → NE spike → `budget_H = f(NE)` растёт → внимание
    переключается на текущую задачу, DMN ставится на паузу
  - Юзер молчит → NE падает → `DMN_activate()`, фон начинает сканировать,
    искать мосты, консолидировать
  - Граница: `NE > 0.7` — жёсткий фокус; `NE < 0.3` — жёсткий DMN; между —
    пропорциональное деление
  - Реализация: отдельный поток (как watchdog), но с общим state. `/assist`-ввод
    инъектирует `NE += Δ`. UI индикатор показывает «что система сейчас делает»
- [ ] **Консолидация** — прунинг слабых веток, "забывание"

### 5b: HRV-интеграция (резонансный режим)

Тело как источник сигналов для конуса. Детали → [docs/hrv-design.md](docs/hrv-design.md)

- [ ] **MVP: Polar H10 → RR** — bleak BLE клиент, реальный сенсор (сейчас только simulator)
- [ ] **UI: cone визуализация с θ/φ** — цветовая кодировка состояний

### 5c: Бесцелевое сознание (Default Mode Network для графа)

Запуск без цели пользователя — система развивается автономно, как сознание младенца.

**Концепция:**
Нейробиология: default mode network (DMN) активен когда человек НЕ думает целенаправленно.
Блуждание ума — не баг, а способ нахождения скрытых связей. Инсайты приходят когда
не думаешь о проблеме.

**Реализация:**
- Scout mode (0 целей) + infinite + автосохранение
- Seed: случайное слово, или последний граф, или внешний стимул
- Цикл: brainstorm → elaborate → doubt → merge → brainstorm от результатов merge
- Без goal → без стоп-условия по confidence. Стоп только по novelty exhaustion.
- Pump между случайными далёкими нодами = поиск скрытых мостов (DMN-like)
- Cron job: запуск каждые N часов. Граф растёт между сессиями.

**От младенца к взрослому:**
- Horizon precision drift: начинает с 0.2 (сфера, всё возможно),
  постепенно растёт с количеством verified нод (конус сужается, модель мира уплотняется)
- Cross-graph: выводы одной сессии → seed следующей
- Консолидация: prune слабых веток (забывание), усиление сильных (память)
- Результат: граф который "знает" тему не потому что ему сказали, а потому что
  сам исследовал

**Открытый вопрос:** может ли **смысл** появиться из бесцелевого блуждания?
Нейробиология говорит да — DMN порождает самореференцию ("я", "мои мысли"),
планирование будущего, переосмысление прошлого. Если граф достаточно плотный,
Pump между далёкими нодами может породить мета-ноды — обобщения которых никто не запрашивал.

---

### 5d: Нейрохимические модули — авторегуляция конуса

**Идея:** ввести 2 динамических скаляра + 1 защитный режим, которые входят
прямо в формулу Байесовского обновления и управляют переключением Horizon↔DMN.
Не 6 параллельных ручек — а минимальный набор сигналов, каждый с чёткой ролью.

#### Серотонин (S) — «цена обновления» / пластичность

`S ∈ [0.2, 1.0]`, обновляется через `EMA(rejection_rate, λ_S)` или внешним сигналом.

Роли:
- `α_eff = α₀ · S` — learning rate для мета-калибровки (γ, τ, T)
- `τ_in_eff  = τ_in₀  / (0.5 + 0.5·S)` — низкий S → выше порог, труднее принять новое
- `τ_out_eff = τ_out₀ · (1.5 − 0.5·S)` — низкий S → узлы дольше удерживаются
- **Входит в Байес:** `logW_new = logW_old − γ·d·S + log(prior)·(1−S)`
  - S→0: веса почти не меняются (ригидность, опора на prior)
  - S→1: полностью подчинены свидетельству

Поведение: S↓ → система «дорого» платит за изменения, избегает ветвлений.
S↑ → быстрое обучение, активный рефакторинг графа.

#### Норадреналин (NE) — фокус / переключение Horizon ↔ DMN

`NE ∈ [0,1] = EMA(max(0, d − d_baseline), λ_NE)` — уровень недавнего «удивления».

Роли:
- `T_eff = T₀ · (1 − κ·NE) + T_floor` — высокий NE → T↓ → резкий выбор (эксплуатация);
  низкий NE → T↑ → размытие, исследование
- `budget_H = clamp(0.8·NE + 0.2, 0.2, 0.95)` — доля CPU/времени для Horizon, остаток → DMN
- `if NE > 0.7`: `DMN_pause()`, `Horizon_priority_max()` — принудительный фокус
- `if NE < 0.3`: `DMN_activate(budget = 1 − budget_H)` — фоновое сканирование, консолидация,
  поиск аналогий

Поведение: NE↑ → сужение горизонта, реакция. NE↓ → диффузный режим, скрытые связи для
следующего цикла.

#### Выгорание — защитный режим при хроническом конфликте

Не медиатор, а триггер режима `PROTECTIVE_FREEZE`.

```
conflict_signal = max(0, d − τ_stable)
burnout_idx = EMA(conflict_signal, λ_b) · (1 − S)
```

- `if burnout_idx > θ_burnout`: `mode = PROTECTIVE_FREEZE`
  - `ΔlogW = 0` — блокировка обновления весов
  - `γ = γ_min, T = T_max` — минимальная чувствительность, максимальное сглаживание
  - `archive_threshold = τ_out · 0.8` — агрессивная архивация конфликтных узлов
- Recovery: `if burnout_idx < θ_recovery AND S > S_min` → `mode = NORMAL`,
  плавное восстановление γ, T, S

Поведение: система не ломается, а **замораживает обновления**, логирует диффы,
запускает лёгкий DMN без обязательств. Восстановление — только при снижении
хронического конфликта и возврате пластичности.

#### Порядок вызовов в ядре

```
1. Sample: d = distinct(A,B)
2. Update: NE, S, burnout_idx через EMA
3. Mode check: if burnout_idx > θ_burnout → PROTECTIVE_FREEZE
4. Apply: γ_eff = γ·S, T_eff = T₀·(1−κ·NE)+T_floor, τ_in/τ_out_eff = f(S)
5. Bayes: logW_new = logW_old − γ_eff·d·S + log(prior)·(1−S)
6. Normalize: W_new = softmax(logW_new / T_eff)
7. Horizon/DMN: budget_H = f(NE), переключение потоков
8. Commit(state_hash, {d, W_new, S, NE, burnout_idx, mode})
```

#### Задачи реализации

- [ ] **`src/neuromod.py`** — `NeurochemState(S, NE, burnout_idx, mode)` с save/load
  в graph state; EMA-обновления, пороги (θ_burnout, θ_recovery, τ_stable, d_baseline)
  в `settings.json`
- [ ] **Интеграция в tick-ядро** — шаги 1-6 из порядка вызовов. `γ·d·S` в
  `_bayesian_update_distinct` (расширить подпись), `τ_in_eff / τ_out_eff` передаются
  в `distinct_decision`
- [ ] **Horizon.apply_neurochem(state)** — `γ_eff`, `T_eff`, пороги; persist в метрики
- [ ] **`PROTECTIVE_FREEZE` как 6-е состояние** Horizon — рядом с EXPLORATION / EXECUTION /
  RECOVERY / INTEGRATION / STABILIZE / CONFLICT
- [ ] **UI-панель** — 3 индикатора (S, NE, burnout) в header; подсветка mode=FREEZE
- [ ] **Git-аудит** — `S`, `NE`, `burnout_idx`, `mode` в коммит-трассе каждого шага
  (дополнение к уже заложенному Git-коммиту из [docs/nand-architecture.md](docs/nand-architecture.md))
- [ ] **Связь с HRV** — NE получает дополнительный input от HRV stress, S — от
  coherence (спокойствие → выше пластичность)

#### Резюме

**S** управляет ценой обновления, сужая/расширяя пороги и скорость обучения.
**NE** переключает режимы: высокий NE — фокус и эксплуатация, низкий NE — DMN-сканер.
**Burnout** при хроническом конфликте + низкой пластичности замораживает систему.
Три механизма работают как динамические коэффициенты в формулах ядра, не нарушая
байесовскую строгость и Git-аудит. Когда будет реализовано — перенести весь дизайн
в `docs/neurochem-design.md`.

---

## v6: мета-режим А→Б

- [ ] **Автоопределение режима** — по промпту/намерению пользователя, без ручного селектора
- [ ] **Декомпозиция цели** — разбивка сложной задачи на подграфы разных режимов

---

## v7: экосистема и полировка

- [ ] **Layout** — d3/dagre/ELK вместо плоской линии
- [ ] **Экспорт** — PNG / SVG / markdown / Obsidian
- [ ] **EXE-установщик** — PyInstaller
- [ ] **Graph Store** — маркетплейс графов, review, рейтинги
- [ ] **Извлечение графа из текста** — статья → граф
- [ ] **Создать прототип из лендинга** — mockup.html → рабочий landing page

---

## v8: NAND полировка (8b, 8c)

### 8b: мышление эмбеддингами

- [ ] **Эмбеддинги как основной формат** — ноды хранят вектор первично, текст — для отображения. distinct() без LLM-вызова (1мс vs 2с)
- [ ] **Генерация через эмбеддинги** — LLM генерирует текст → эмбеддинг → нода. Brainstorm в пространстве эмбеддингов
- [ ] **Текст по запросу** — текстовое описание ноды генерируется только когда пользователь кликает

### 8c: расширенные концепции

- [ ] **Камера сенсорной депривации** — режим без LLM
- [ ] **state_origin** — 1_rest vs 1_held
- [ ] **Pipeline 3 слоя** — эмбеддинги (маршрутизация) → символическое ядро → отложенный текст

### 8d полировка (после завершения миграции)

- [ ] **Golden tests для 14 режимов** — зафиксировать поведение зон
- [ ] **Профилирование** — distinct matrix для 7+ идей делает N embedding-вызовов, может быть долго
- [ ] **UI-подсказка «ожидаемая vs фактическая зона»** — если пользователь выбрал Debate, а зоны показывают CONFIRM, честно показать

---

## v9.5: WOW-эффект — усиления

- [ ] **Demo mode** — кнопка "демо-неделя" с ускоренной симуляцией
- [ ] **HRV эффект instant** — push через SSE/WebSocket вместо polling
- [ ] **Weekly review с графиками** — chart.js для HRV trend, streaks, mode distribution
- [ ] **Красивый advanced graph view** — упростить технический рендер

---

## v10: Интеграции с внешним миром

- [ ] **Продукты / рецепты** — что в холодильнике → XOR выбор блюда с учётом калорий и энергии
- [ ] **Гардероб** — что есть → outfit под погоду + календарь
- [ ] **Календарь** — события дня → приоритизация задач, напоминания
- [ ] **Погода API** — утренний брифинг, одежда, outdoor активности
- [ ] **Браузер-расширение** — мониторинг покупок (impulse guard), писем (emotion guard)

---

## v11: Открытые вопросы без известного решения

Это не TODO в обычном смысле — это список **концептуальных дыр**, для которых сейчас
нет архитектурного ответа. Записаны явно чтобы не забыть и не врать себе что «эти
вещи покрыты». При работе над любым из этих вопросов сначала нужен design-этап,
не реализация.

- [ ] **Эпизодическая память** — навигация «что я думал во вторник», связная лента
  событий, а не плоский граф. Сейчас есть только переписка пользователя + `history`
  в user_state.json + timestamps на нодах. Это не эпизоды в смысле нейробиологии
  (hippocampal replay, context binding). Нужен отдельный слой над графом или
  отдельная структура «лента».
- [ ] **Эмоции как вход (не только arousal)** — HRV ловит возбуждение и когерентность,
  но не валентность (приятно/неприятно) и не дискретные аффекты (страх, интерес,
  радость). Без валентности нет мотивации в полном смысле. Вопрос: можно ли
  вытащить валентность из HRV + поведения (время отклика, длина сообщений), или
  нужен отдельный датчик (мимика через камеру?).
- [ ] **Self-narrative — может ли смысл возникнуть** — v5c поставил этот вопрос,
  но архитектурного ответа пока нет. DMN порождает «я» у людей через
  интеграцию эпизодической памяти + body schema + social mirror. В Baddle
  эпизодической памяти нет (см. выше), body schema зачаточный (HRV), social
  нет. Возможно self появляется только когда сойдутся все три. Или вообще не появится.
- [ ] **Sleep-консолидация полного цикла** — Scout 3h ≈ медленные волны сна,
  но нет быстрого сна (REM) = эмоциональной переработки и творческого merge.
  У людей REM делает то что Pump + консолидация делают по отдельности. Можно ли
  это собрать в один связный ночной цикл, не понятно.
- [ ] **Валидация нейронаучных аналогий** — научные основы в `docs/*` написаны
  как LLM-context и пост-рационализация. Прошли ли они peer review кого-то
  кроме меня? Нет. Это не критично для работы инструмента, но критично для
  публикации и продажи. Нужен профильный нейросаентист который либо подтвердит
  что аналогии работают, либо укажет где они сломаны.

---
---

# ⬇ СДЕЛАНО

## 🆕 В текущей сессии — не тестировано с реальным LM

### v8d — полный переход AND/OR/XOR → NAND

NAND — единственный tick engine. `primitive/strategy/goal_type` остались только
как UI-metadata в `modes.py`, ни одна функция в runtime на них не switch-ит.
Логика возникает из зон distinct(): CONFIRM/EXPLORE/CONFLICT.

**Что сделано:**
- `thinking.tick()` (classic) удалён, helpers остались (classify_nodes, _pick_target, etc)
- `_bayesian_update(prior, p_e_h, p_e_nh)` удалён → только `_bayesian_update_distinct(prior, d, γ)`
  со знаковой формулой: `logit(post) = logit(prior) + γ·(1−2d)`
- `check_stop()` с goal_type switch заменён на универсальный `should_stop(cl, graph, horizon, goal_node)`:
  - Case 1: subgoals → avg_d между ними решает AND (avg_d≤τ_out: все нужны) vs OR (avg_d>τ_out: первый)
  - Case 2: d(goal, best_verified) < τ_in → цель достигнута
  - Case 3: strong convergence (3+ verified, avg>0.85)
  - Case 4: novelty exhaustion (precision>0.85 + ничего pending)
- `tick_nand.tick_emergent` получил subgoal-фильтр, stop-check и emergent compare-action
  (CONFLICT-зона среди verified → external judge)
- `graph_routes.graph_tick` больше не читает feature flag, всегда NAND
- `assistant_exec.execute()` диспатч по mode_id (не primitive/strategy), плюс
  `execute_via_zones()` для общих режимов — генерит N идей, строит distinct matrix,
  рендерит по доминирующей зоне (CONFIRM→synthesis, CONFLICT→dialectic, EXPLORE→list)
- Feature flag `nand_emergent` удалён из settings.json и api_backend.py
- Goal node больше не хранит `primitive/strategy/goal_type` — только `mode`

**Прогнать:** весь поток с LM Studio — 14 режимов, убедиться что `execute_via_zones`
не тормозит из-за N embedding-вызовов, и что классические паттерны (AND/OR/XOR) всё
ещё дают ожидаемое UX.

### Третий контур — диалог (замыкает README «три контура»)

- `/graph/assist` endpoint — LLM задаёт уточняющий вопрос → ответ становится
  нодой нужного типа (evidence для bayes, subgoal для multi-goal, seed иначе)
- Кнопка "?" в UI — рядом с Send, рендерит вопрос с ярлыком типа ответа
- Кнопка "↯" — `/assist/decompose` с редактируемой карточкой подзадач → `/graph/add`
- Persist чата через localStorage, restore при загрузке (эфемерные warning-алерты
  исключены из persist, чтоб не спамили после reload)

**Прогнать:** проверить что вопросы осмысленные и ответ корректно становится
evidence/subgoal/seed. Проверить Bayes-поток: answer → `_bayesian_update_distinct` →
confidence двигается в правильную сторону.

---

## Сделано ранее, в main (тестировано руками; прогнать под нагрузкой раз)

### Horizon
- Полный гистерезис для 4 состояний + debounce
- 5 состояний (STABILIZE/CONFLICT через HRV)
- `update_gamma()` — EMA от d(A,A), clip [0.1, 10]
- `update_sync_error()` — рассинхрон с пользователем
- `update_from_hrv()` — coherence → precision, γ = γ₀ + η·(stress − coherence), τ_in/τ_out/α
- `update_temperature()` — T adaptation via KL(W_t‖W_{t-1}), diagnostic metric

### v2: алгебра режимов
Goal structured object, stop conditions, multi-goal ввод (subgoals), UI selector
с примерами и динамической формой. Детали → [docs/tick-design.md](docs/tick-design.md)

### v5a: Watchdog
- `src/watchdog.py` — фоновый поток, alerts queue, dedup
- Scout ночной (3h): реальный Pump, сохраняет мост-ноду
- DMN continuous (10min): Pump без сохранения, alerts если quality>0.5
- `_find_distant_pair()` pivot+furthest O(n)
- HRV-alerts: coherence<0.25 → dedup

### v5b: HRV integration
- `src/hrv_metrics.py` — RMSSD/SDNN/pNN50/LF-HF/coherence через FFT
- `src/hrv_manager.py` — singleton, скользящий буфер RR 240 beats
- HRVSimulator — RR с RSA-модуляцией
- Routes: `/hrv/start|stop|status|metrics|calibrate|simulate`
- UI widget: coherence+energy в header, polling

### v8a: NAND core
- `distinct()` в main.py
- `distinct_decision()` — CONFIRM/CONFLICT/EXPLORE
- `_bayesian_update_distinct()` — знаковая формула `logit+γ·(1−2d)` (обновлена в v8d)
- `_d_from_relation()` — relation+strength → d
- `_beta_prior_update()` + `_beta_mean_ci()` — Beta(α,β)
- `tick_emergent()` в `src/tick_nand.py` — единственный tick engine (после v8d)

### v9: Life Assistant — обёртка поверх ядра
Chat-first, graph-hidden. Детали → [docs/life-assistant-design.md](docs/life-assistant-design.md)

- `/assist` — execute_mode по mode_id
- `execute_via_zones()` — общий исполнитель через distinct matrix
- `detect_mode()` — keyword-эвристика, 14 режимов
- `/assist/morning`, `/assist/weekly`, `/assist/alerts`, `/assist/decompose`, `/assist/detect-mode`
- Energy counter + persist в user_state.json

### v9.5: WOW UI ядро
- 5 типов карточек: dialectic / comparison / bayesian / ideas_list / habit
- Visible thinking, pending dots
- Scout/DMN bridges в чате
