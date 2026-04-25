# TODO

> **⚠ Читать первым: [simplification-plan.md](simplification-plan.md).** Consolidation Фазы A+B завершены (2026-04-24/25): MetricRegistry + Signal Dispatcher + 13 детекторов. Cognitive_loop сжат на ~600 строк, правила 1-2 из §4 реализованы. **Сейчас:** 2-недельное окно реального use → калибровка `compute_urgency` по `throttle_drops.jsonl`. **Следующее:** Capacity migration (тонкая через registry). Tier 2 фичи (резонансные, RAG, sensors) разморожены частично — берутся в очерёдности. Нарушение дисциплины = возврат к 20k строк.

## 🎯 Прайм-директива

**`sync_error = d(model_prediction, user_action)` — единственная метрика**, которая оценивает ценность любого пункта. Если пункт не снижает рассинхрон — низкий приоритет, даже если архитектурно красиво.

**Измерение:** `sync_error_ema_slow` пишется раз в час в [`data/prime_directive.jsonl`](../src/prime_directive.py). Endpoint `GET /assist/prime-directive?window_days=30&daily=1` даёт aggregate + trend verdict. Через 2 мес use сравнить mean(first third) vs mean(last third) — если `trend_slow_delta < 0`, механики работают.

### Принцип: параметр с теоретической основой не выходит на переговоры

Если механика/ось/канал выводится из физической, биологической или математической модели (Friston, VAD, ВНС, SDT, нейрохимия) — она **остаётся** и только калибруется на данных. Gate-решения («оставлять или убирать?») — исключительно для ad-hoc механик без теоретической анкеровки. Константы вроде `CHECKIN_ENERGY_DECAY=0.85` крутятся молча по мере данных, в TODO не попадают.

---

## 📊 Наблюдение за метриками

Единая точка входа для «как система ведёт себя прямо сейчас»:

- **`GET /assist/prime-directive?window_days=30&daily=1`** — главный дашборд. Сейчас возвращает: `mean_ema_slow`, `trend_verdict` (improving/stable/worsening), `mean_pe_user / _self / _agency / _hrv`, daily breakdown.
- **`data/throttle_drops.jsonl`** — append-only лог дропов dispatcher'а. Формат `{"ts", "check", "ctx": {reason, urgency, dedup_key, expires_in_s, source}}`. Все 13 детекторов пишут natively через dispatcher (Phase B). Через 2 нед калибруем `compute_urgency` формулы по реальным паттернам drops (high-urgency теряем? low-urgency спамим?).

Что планируется добавить туда же по мере появления механик:

- Counter регимов (FLOW / REST / PROTECT / CONFESS distribution за окно)
- Capacity-зоны (доля времени в green / yellow / red когда 3-контурная модель заедет)
- `frequency_regime` distribution когда появится (long_wave / short_wave / mixed)
- Action-memory outcomes summary (accepted / declined / effective по action_kind)
- Per-mode use counts (какие из 14 режимов реально вызываются)

Через 2 мес read раз в неделю → видно что двигает систему, что шумит, где доминирует один канал, что вообще не активируется. Оттуда берутся реальные задачи на калибровку.

---

## 🚦 Calibration window (активное)

Phase B завершена — 13 детекторов + Dispatcher на месте. Сейчас собираем 2 нед реального use в `data/throttle_drops.jsonl` чтобы откалибровать `compute_urgency` формулы (заданы как эвристики в спеке Phase B, см. [simplification-plan.md §5](simplification-plan.md)).

**Что смотреть через 2 нед:**
- Доля `reason="budget"` дропов с urgency≥0.7 — теряем ли ценное?
- Распределение urgency по типам — есть ли systematically низко-эмитящиеся (формула слишком жадная) или высоко-эмитящиеся (формула слишком щедрая)?
- Soft outcomes из Action Memory: какие emitted alerts получили user-reaction vs ignored?

**Корректировка (когда данные будут):** ~1-2ч на крутку коэффициентов. Не задача для отдельной сессии до накопления данных.

---

## 🧠 Capacity миграция — dual-pool → три контура

Docs описывают 3-контурную модель (физио / эмо / когнитивный) с capacity-zone как decision gate как текущую реализацию. Код — всё ещё dual-pool (`daily_spent` 0..100 + `long_reserve` 0..2000). Задача: привести код к описанному состоянию.

Полная спецификация формул, полей и call-sites — [docs/capacity-design.md](../docs/capacity-design.md). Ниже — фазы миграции.

### Новые поля и методы

- [ ] `UserState.day_summary: dict` — ежедневный агрегат (tasks_started, tasks_completed, context_switches, complexity_sum, progress_delta, engagement_mean, cognitive_load).
- [ ] `UserState.cognitive_load_today: float` — живой агрегат текущего дня.
- [ ] `UserState.capacity_zone: str` — derived property (`green` / `yellow` / `red`).
- [ ] `UserState.capacity_reason: list[str]` — причины при жёлтой/красной.
- [ ] `UserState.rollover_day()` — полуночный reset, вызывается из полуночного check.
- [ ] `activity_log.surprise_at_start: float` — снимок модуля surprise-вектора при `start_activity`.
- [ ] `activity_log.surprise_delta: float` — изменение surprise от старта до конца (на `stop_activity`).
- [ ] `activity_log.context_switches` — category diff между последовательными запусками.

### Формулы (новые)

- [ ] `cognitive_load_day` — агрегат 6 observable (детали в capacity-design).
- [ ] `phys_ok / affect_ok / cogload_ok` booleans по порогам EMA.
- [ ] `capacity_zone` через число выполненных условий (green/yellow/red).
- [ ] `allowed_modes` через зону (all / medium_or_light / light_only).
- [ ] Explanation генератор при отказе (phys_ok/affect_ok/cogload_ok fail).

### Фоновые check'и и endpoints

- [ ] `_check_cognitive_load_update` в cognitive_loop (bookkeeping, не в DETECTORS) — раз в 5 мин пересчитывает cognitive_load_today.
- [ ] `detect_evening_retro` — расширить content по 6 observable + progress дня.
- [ ] `detect_morning_briefing` (через `_build_morning_briefing_sections`) — включить текущее состояние capacity + causal explanation.
- [ ] `/assist/simulate-day` — переписать через прогноз capacity-зон (не через вычитание энергии).

### Удалить (dual-pool legacy)

- [ ] `daily_spent`, `long_reserve` поля в UserState.
- [ ] `debit_energy`, `_compute_energy` методы.
- [ ] `_decision_cost`, `_MODE_COST` таблица в `assistant.py`.
- [ ] `DAILY_ENERGY_MAX = 100`, `LONG_RESERVE_MAX = 2000` константы.
- [ ] Старый UI-бар «Энергия» в header.

### UI (новый)

- [ ] Три мини-бара 🟢/🟡/🔴: Физо / Эмо / Когн (вместо единой шкалы «Энергия»).
- [ ] Explanation в decision-gate отказе (какой контур провисает).
- [ ] Evening retro с 6 observable + progress_delta как главной метрикой.

### Калибровка

- [ ] Коэффициенты в формуле `cognitive_load_day` — крутить на 2-недельном окне данных.
- [ ] Пороги `phys_ok / affect_ok / cogload_ok` — калибровать по корреляции с sync_error.

OQ #1 (personal capacity prior) из архитектурных вопросов подключается после миграции — сначала базовая 3-контурная модель работает на хардкод-порогах, потом per-user prior.

---

## 🌊 Резонансная модель

Единый словарь для существующих механик Baddle через оптику «сознание как резонатор в едином поле». Теоретическая рамка + 4 практических документа. Большая часть уже есть в коде — это reframing, не rewrite.

**Основа:** [docs/resonance-model.md](../docs/resonance-model.md) — overview с mapping-таблицей «резонансный словарь ↔ Baddle-концепт ↔ код».

**Патчи в существующие docs** (сделано): [cone-design](../docs/cone-design.md) — частота и чистота конуса; [hrv-design](../docs/hrv-design.md) — ВНС как переключатель несущей; [friston-loop](../docs/friston-loop.md) — отсутствующий объект и PE = 0; [neurochem-design](../docs/neurochem-design.md) — нейромодуляторы как регуляторы чистоты и полосы.

**Открытые пункты:**

- [ ] **`aperture` скаляр в depth engine** — заменяет 3 несвязанных параметра (`deep_response_format`, `deep_batched_synthesis`, `deep_mode_steps`) одним [0,1] slider'ом. Settings UI rework: Tier 2, ~2ч + UI. См. [resonance-code-changes.md](resonance-code-changes.md).
- [ ] **Дыхательный режим (breathing_suggestion).** Baddle предлагает 5→5 / 4-4-4-4 / 4-7-8 при rassogласовании frequency_regime и ближайшей задачи. Self-guided, не лечит. Action Memory cycle закрывает обучение по outcome. Спецификация — [breathing-mode.md](breathing-mode.md). Tier 2: ~5ч (backend 2ч + animated UI overlay 2ч + action-memory 1ч).
- [ ] **Резонансный промпт-преset.** Chat UI: dropdown 🔵/🔴/⚪ + шаблон `[Контекст волны] [Состояние] [Запрос] [Параметры]`. Делает active inference юзера явным, zero backend change. Опционально auto-detect через `UserState.frequency_regime` (Variant C). Спецификация — [resonance-prompt-preset.md](resonance-prompt-preset.md). ~1-2ч.

---

## 📌 Задачи
- [ ] **Embeddings** Убрать хранение embeddings
- [ ] **Унифицировать связи графов и их хранения** Очень много файлов непонятно зачем
- [ ] **Pure-function formulas в один файл.** EMA-часть закрыта Фазой A (все EMA в `src/metrics.py` registry). Остались разбросаны: γ в `neurochem.py`, precision/T в `horizon.py`, RMSSD/LF-HF в `hrv_metrics.py`, distinct в `main.py`, Bayesian update в `graph_logic.py`, cognitive_load (в capacity migration). Сделать `src/formulas.py` — pure-functions helpers (`compute_gamma(NE, S)`, `compute_effective_precision(raw, maturity)`, `bayes_update_logit(prior, d, gamma)`, `compute_cognitive_load(events)`). Объекты state вызывают их, сами держат только данные. ~2ч (меньше после Фазы A), средний риск. После Фазы B.
- [ ] **Desktop notifications.** Alerts работают только пока вкладка открыта. Закрыл → morning briefing / DMN-мосты / night cycle уходят в пустоту. MVP: `pystray` + `plyer` (иконка в трее + OS toast). ~2-3ч.
- [ ] **Patterns × intent_router auto-abandon.** Если детектор нашёл паттерн, но юзер молчит 2+ недели — убирать предложение чтобы не накапливались старые alerts. ~1ч.
- [ ] **Constraint expansion через LLM.** Юзер добавил `"лактоза"` → LLM раз генерит синонимы `["молоко", "кефир", "сметана", ...]` → сохранить в `profile.categories[cat].constraints_expanded`. `profile_summary_for_prompt` инжектит расширенный вид. Закрывает кейс «8B Q4 предложила кефир как замену молока» (2026-04-24). ~1-2ч.
- [ ] **Auto-parse constraints из message.** «не ем / аллергия / не перевариваю / без X» в чате → LLM-parse → draft-card через существующий `make_draft_card` flow → юзер подтверждает. Закрывает случай когда ограничение упомянуто в чате но не закреплено в профиле. ~2ч.
- [ ] **`plan.create_from_text`** — «встречу в среду 11:00» → plan-object через LLM. Естественный ввод вместо формы. ~2ч.
- [ ] **Предложение еды без tool-use** ([mockup.html](../docs/mockup.html)). Реактивное: «что поесть?» → 3 варианта из `profile.food.preferences + constraints` через LLM. Проактивное: pattern-detector видит «пропускаешь завтрак по четвергам → energy crash к 14:00» → секция «Завтрак» в morning briefing с обоснованием паттерна. Реализация: mode в `suggestions.py` + pattern в `patterns.py`. ~3ч.
- [ ] **META-вопросы — ночная генерация «что ты не заметил»** ([mockup.html](../docs/mockup.html) строка 172). Когда два scout-моста обнаруживают общий абстрактный паттерн («single point of failure» в auth-модуле И в energy-понедельниках) — генерить вопрос: «какие ещё SPoF у тебя есть?». Отдельная секция в briefing. Зависит от того что scout реально находит мосты — граф должен быть нетривиальный. ~2-3ч.
- [ ] **Специализированные card-рендеры для `fan` / `rhythm`.** Сейчас оба падают в `deep_research` card. `fan` (Мозговой штурм) = generate-list с ranging по новизне; `rhythm` (Привычка) = habit-tracker view с streak + next-occurrence. ~3ч.
- [ ] **Расширение `score_action_candidates`** на другие детекторы помимо `detect_sync_seeking` — когда через месяц станет видно где реальный разброс outcomes по action_kind. Сейчас только tone-selection в sync_seeking. Кандидаты: suggestion-tone в observation→suggestion, morning-briefing section prioritization, recurring-lag reminder timing.
- [ ] **Dialog pivot detection** в surprise detector. Резкое изменение темы через embedding distance между последовательными user-сообщениями: если `distinct(msg_prev, msg_curr) > τ_out` при коротком временном окне → candidate pivot-event. Третий канал OR рядом с HRV+text markers. Стоит только если false-positive rate низкий на реальных chat-логах. ~2ч.

---

## 🧬 Сенсоры

MVP stream + симулятор работают ([storage-layout § Sensor stream](../docs/storage-layout.md)): `SensorReading{ts, source, kind, metrics, confidence}` + `latest_hrv_aggregate(window_s)` + weighted multi-source aggregate. Docs описывают адаптеры Polar / Apple как часть системы — в коде это пока скелет-классы в `sensor_adapters.py`.

- [ ] **UserState → sensor stream.** Сейчас `UserState.update_from_hrv` через `hrv_manager.get_baddle_state()`. Мигрировать на `stream.latest_hrv_aggregate()` + `stream.recent_activity()` — любой источник влияет на UserState напрямую. ~15 call-sites. Блокирует реальные адаптеры.
- [ ] **`PolarH10Adapter`** — `bleak` + `bleakheart`, async BLE loop. Push `rr_ms` + accelerometer. Каждые 15с агрегат через `calculate_hrv_metrics` → `push_hrv_snapshot`. ~2-3ч.
- [ ] **`AppleWatchAdapter`** — через HealthKit export или Shortcuts API. Sparse HR snapshots. Confidence 0.8. Низкий приоритет — Polar даёт лучшее разрешение.
- [ ] **Polar H10 cone viz с θ/φ** — polyvagal двухпараметрическая визуализация когда реальный сенсор подключён.

### Расширенная HRV-аналитика

- [ ] **Тест VO2max (оценка аэробной выносливости).** Один из протоколов: Cooper 12-минутный бег, Åstrand submaximal cycle, или оценка через HR-response на стандартизированную нагрузку (step test). Замер раз в 2-4 недели — тренд показывает реальные изменения тренированности. Persist в `data/fitness_tests.jsonl`.
- [ ] **Метрики восстановления после движения.** Heart Rate Recovery (HRR1 — падение пульса за 1 мин после прекращения нагрузки, HRR2 — за 2 мин, slope — крутизна). Низкий HRR = плохая парасимпатическая регуляция. Сравнивать с baseline, тренд за недели. Automatic detection через accelerometer (активность кончилась) + HR-stream.
- [ ] **«Аварийный режим коробки» детектор.** Паттерн хронического симпатического перегруза: (а) resting HR поднят на 5-10 bpm выше baseline несколько дней, (б) при старте активности HR **скачком** достигает 80%+ от HRmax (нет плавной рампы), (в) упирается в потолок, не даёт резерва, (г) медленное восстановление. Композитный score по 4 признакам → event `autonomic_strain` в `hrv_events.jsonl`. Алерт только при устойчивом паттерне (3+ дня), не по одному замеру — чтобы не паниковать на плохом сне.

### Event-based сжатие HRV-потока

- [ ] **Вместо сырых RR — интерпретируемые события.** Сейчас `data/sensor_readings.jsonl` хранит downsampled RR (каждый 10-й). Перейти на `data/hrv_events.jsonl` с событиями: `rest_baseline_update` (тренд resting HR / RMSSD), `activity_start / peak / recovery`, `coherence_shift`, `anomaly_spike`, `autonomic_strain`. Сырые RR живут в памяти 1-2 часа как ring buffer, на диск уходит только **интерпретация**. Файл в 50-100× меньше, данные читаемые глазом, Baddle потребляет сигналы как поток событий, не timeseries. Детекторы из пунктов выше — производители этих событий.

---

## 🛠 Tool-use

- [ ] Слой действий (calendar / weather / web.search / file / rag / permission model) — отдельная сессия когда появится необходимость. Пока не делаем.

---

## 🤔 Открытые архитектурные вопросы

### #1 Personal capacity — prior, не constant
**Проблема:** `LONG_RESERVE_MAX=2000, DAILY_ENERGY_MAX=100` хардкод. Один юзер — физик 14ч/день, другой — бабушка; система обрабатывает одинаково, sync_error растёт.
**Направление:** Bayesian online EMA (α≈0.95) на `daily_spent + stop_events + nightly_hrv_rmssd` → per-user `{daily_max, long_reserve_max, daily_max_by_hour[weekday]}` в `user_profile.json`.
**Критерий:** A/B 2 недели `ceiling_static=100` vs `ceiling_estimated`. Принимаем если avg `sync_error` падает >15%.
**Где:** новый `src/capacity_estimator.py`, кормит `UserState._compute_energy`.
**Блок:** минимум 1 мес реальных данных — иначе гадание.

### #3 Память как ключ настройки vs text + embedding
**Проблема:** Нода графа хранит `{text, embedding, confidence, ts, ...}`. Embedding — это уже «ключ настройки» (вектор в семантическом пространстве), а `text` — копия объекта, которую мозг-как-резонатор физически не хранит (см. [resonance-model.md § Research challenge](../docs/resonance-model.md#research-challenge-память-как-ключ-настройки)). Возможно дублирование избыточно.
**Направление:** В пределе — хранить только embedding + контекст + energy; text восстанавливается LLM'ом из embedding при retrieval. Ближе к биологической реальности, меньше storage. Против: LLM-generation шумит, теряется audit/debug/human-readable trail, git-backup удобнее когда text живой.
**Критерий:** Через 1-2 мес измерить storage breakdown (сколько на text vs embedding на типичном графе 500+ нод) + log частоту ручного чтения text пользователем. Если text читается редко а embedding retrieval стабильный → можно уходить к ключ-only. Если часто → оставляем дубль.
**Где:** структура node'а в [graph_logic.py](../src/graph_logic.py), storage в [storage-layout.md](../docs/storage-layout.md).
**Блок:** минимум 1-2 мес реального use, статистика по чтению text + работоспособность LLM-reconstruction.

### #2 Agency — 6-я ось уже после (не gate, калибровка)
**Статус:** `UserState.agency` — 5-я ось, собирается (EMA decay 0.95), входит в sync-вектор с весом 1.0 (по умолчанию наравне с DA/S/NE). VAD-dominance = agency в psychological lit; теоретическая основа закрыта, механика остаётся.
**Направление:** через 2-3 мес данных смотреть `mean_pe_agency` в `prime_directive.jsonl` и калибровать вес в `vector()`. Если agency дрейфует независимо от DA/S/NE → держать вес 1.0, если коррелирует → понижать до 0.5.
**Дальше:** `meaning` / `relatedness` из SDT как 6-я ось когда появятся источники сигнала. Не «решим включать или нет» — теоретически обоснованы, значит включаем с начальным весом.

### #4 Lab-scratch — изолированный граф для экспериментов
**Проблема:** Lab сейчас работает с тем же `_graph` что и chat/cognitive_loop. Хочется «поиграть» в Lab (собрать тестовый граф, поэкспериментировать с режимами) **не трогая** живой Baddle-контекст. Но любое изменение в Lab сейчас подменяет runtime для всех, включая автономный цикл мышления.
**Три варианта** (обсуждены 2026-04-23, решение отложено):
- **A. Active pointer** — одна runtime-переменная `_graph`, имя активного хранится в settings, переключение в Lab swap'ает всё. Chat и cognitive_loop едут на scratch вместе с Lab. Простая (~1.5ч), но scratch = мини-Baddle (DMN пишет в него, непрерывность main-мышления рвётся при переключении).
- **B. Cognitive pause** — active pointer плюс cognitive_loop пропускает цикл когда active ≠ main. Chat всё равно на scratch при его активации — внутренне противоречиво (Lab и chat в разных графах одновременно).
- **C. Dual runtime** — `_graph` для main, отдельный `_lab_graph` для scratch. Chat и cognitive_loop всегда на main, Lab изолирован. Дороже (~3-4ч), но честная семантика.
**Критерий выбора:** насколько часто реально нужен Lab-эксперимент без прерывания Baddle-мышления. Если редко — достаточно git-бэкапа `graphs/main/` и `reset_and_seed()` перед игрой. Если регулярно — C.
**Где:** новый `_scratch` в `graph_logic.py`, отдельные `/graph/scratch/*` endpoints, UI-переключатель в Lab.
**Блок:** понять после 1-2 мес daily-use есть ли реальная потребность. Возможно надобность исчезнет сама.

---

## 🏗 Edge cases

- [ ] **Attention-weighted PE.** Сейчас 4 PE-канала normalized + max. Можно ввести precision-weights: шумные каналы получают меньший вес при агрегации. Классический Фристон. Не блокер, но если `mean_pe_hrv` через 2 мес окажется заметно шумнее `mean_pe_user` — precision-gating решит.

---

## 💡 Бэклог идей (думать, не делать)

Не задачи — направления. Оценка P×R = полезность × реалистичность (из 5).

**Метанаблюдение.** Половина идей — вариации одного паттерна «переписать всё через одну абстракцию» (граф = мозг, всё остальное лишнее). Это то же искушение что NAND-эксперимент 2026-04-24 (null-result: красивая единая теория не работает на реальных задачах). Unified abstractions приятны для архитектора, но часто проигрывают гибриду где каждая структура оптимизирована под свой use-case. Рекомендация: **добавлять функции** (RAG, 2D affect, outcome UI — реально новое) важнее чем **перестраивать инфраструктуру** (constraints-узлы, циклы→DMN — эстетика вместо работы).

### Зависимости и порядок

**Блокер для большей части бэклога:** 1-2 мес реальных данных через прайм-директиву. Без этого re-foundation через граф = гадание.

Граф зависимостей внутри бэклога:
- **#10** ≡ summary эффекта от `#3 + #5 + #6 + #13`. Не отдельная задача.
- **#3** (убрать циклы) требует доказательства что `#15` (история в reasoning) работает — иначе DMN будет крутиться впустую.
- **#5** (recurring = граф) → открывает `#6` (цели дня как узлы) → открывает `#13` (constraints как узлы).
- **#12** (pruning похожих) ⇐ **#11** (working/long-term tiers). Нельзя pruning'ить без выделенных tier'ов.
- **#14** (emergent emotions) ⇐ **#2** (Рассел 2D). Квадранты без осей не построить.
- **#7** (outcome узел) — частично закрыт в Action Memory, нужен только UI поверх.

**Быстрые wins (можно брать изолированно):**
- **#9** (один чат в графовом режиме, P3/R5) — чистка UX на пару часов, не блокируется ничем.
- **#7** (manual outcome UI поверх Action Memory, P3/R4) — форма + endpoint, не требует re-foundation.
- **#8** (кластеры в Lab по color, P2/R4) — косметика, d3-layout уже есть.

**Не делать без статистики (≥ 1 мес use):**
- **#4** (пересмотр 14 режимов) — нужны use-counts какие реально вызываются.
- **#3** (циклы→DMN) — нужно доказать что DMN находит нетривиальное на живом графе.

### Пакет «Всё через граф» (архитектурная трансформация)

Фундаментальная идея: убрать параллельные хранилища (goals_store, user_profile, recurring) и процедурные циклы (21 check в cognitive_loop). Оставить один граф, где DMN блуждает и всё находит эмерджентно. Это **многонедельная работа**, возможна только после накопления месяца реальных данных для валидации.

- **#3 Зачем циклы если всё в графе?** (P5/R3) DMN блуждает по нодам, находит паттерны в activity/habits → предлагает. Сейчас 21 check — процедурщина, «пустышки» на empty state. Блок: нужно сначала доказать что DMN **реально** находит нетривиальные связи. Связано с #15.
  *Мнение:* **не делать.** Замена предсказуемой системы на stochastic. Процедурные check'и — safety net с контрактом (раз в 5 мин проверил silence, среагировал). DMN — без гарантии timing'а; алерты могут не сработать когда нужно. Плюс на 398 текущих нодах DMN не обнаружил ни одного значимого моста — заменять tests на то что **пока не работает** = плохая ставка.

- **#5 Recurring = циклический goal-узел.** (P4/R3) «покушать 3 раза» — goal с `repeat: daily × 3` + 3 instance-ноды. Закрывает goals_store + recurring.py в граф. Упрощает.
  *Мнение:* только **после** доказательства #3. Иначе «выпил воды» не посчитается в recurring без процедурного tracker'а.

- **#6 Цели дня = 3 узла графа.** (P4/R2) Каждый приём пищи / задача = отдельная нода графа с типом `goal_instance`. Двигается/закрывается. Связь с #5.
  *Мнение:* **не делать полную замену.** Goals имеют transactional семантику (atomic `record_instance`) — граф append-only, при crash half-states. Поиск «вчерашние 3 instance» = O(log N) traversal вместо O(1) dict lookup. Компромисс: `goal_instance` как узел **с ref** в goals_store, не замена store'а.

- **#7 Узел выполнения (manual или auto-linked).** (P3/R4) Почти есть в Action Memory (outcome). Добавить manual creation через UI + auto-link на упоминание в чате через entity matching.
  *Мнение:* **да, быстрый win.** Action Memory делает 80%, остаётся форма + endpoint. Доводит систему до полного контура обучения.

- **#9 Один чат (убрать «обычный чат с ИИ» в графовом режиме).** (P3/R5) Сейчас два входа. Оставить deep-mode/fastpath/scout как single entry. Простая чистка UX.
  *Мнение:* **да, быстрый win.** Legacy UX на пару часов работы. Минимальный риск.

- **#10 Один большой круглый граф как мозг.** (P∞/R—) — это summary после #3/#5/#6/#13. Не отдельная задача, а видение.
  *Мнение:* видение, не план. Оценивать эффект только **когда** появится реальная emergent польза из #3/#15. Сейчас красивая метафора без рабочего выигрыша.

- **#13 Constraints/preferences как узлы графа.** (P4/R2) Вместо `user_profile.json` — ноды `constraint`/`preference` с hebbian decay, которые участвуют в reasoning естественно. Риск: `profile.json` удобно читать руками и делать backup. Может быть гибрид — node references внешний JSON.
  *Мнение:* **не делать.** Constraint — декларативный факт, не воспоминание. «Не ем лактозу» не должно затухать из-за того что две недели не ел молочного, а hebbian decay именно так и работает. Plus `profile.json` editable руками, git-backup, viewable в 3 строки — перенос в граф = data lock-in. Relevance gate (сегодняшний fix) уже работает на JSON.

- **#15 История чата влияет на reasoning.** (P4/R3) Сейчас старые ноды оживают только через DMN. Нужен retrieval step в `execute_deep` и `_fastpath_chat`: перед LLM-генерацией — similar past nodes/outcomes через `distinct()` к query, инжект в prompt. Это уже **частично** есть (pump, RAG), нужно довести до всех путей.
  *Мнение:* **делать, самая правильная из списка.** Сейчас 90% старого контекста не участвует в новых рассуждениях. Классический RAG, добавляет context, не меняет поведение. Низкий риск, большой эффект. Первым в пакете.

### Пакет «Память и pruning»

- **#11 Оперативная vs долговременная память.** (P4/R3) Working memory = recent nodes + hot cluster; long-term = archived + consolidated. Развитие существующего consolidation (`episodic-memory.md`). Нужен explicit tier в node-схеме + ночной transfer oper→long.
  *Мнение:* **не проактивно.** Красивая нейробиологическая модель, но текущий consolidation уже решает 80% простым age-based archive. Начинать tiering когда появится реальная боль (reasoning тормозит, граф раздулся), не заранее.

- **#12 Pruning: коллапсировать похожие, переносить oper→long.** (P3/R4) Связано с #11. Технически: nightly job на `distinct(a,b) < 0.1` в oper-layer → merge + transfer. У нас есть `_rem_creative` для paradox, нужен similar для consolidation.
  *Мнение:* то же что #11. Откладывать. Но `_rem_creative` как шаблон — действительно удобно, когда время придёт.

### Пакет «Эмоциональная модель»

- **#2 Модель Рассела (Valence × Arousal, 2D).** (P4/R4) UI показывает юзеру где он сейчас на 2D-карте аффекта — grasp'able способ «как я?». Валентность уже есть (`UserState.valence` через sentiment + accept/reject). Arousal = функция от `norepinephrine` / `HRV stress`. Отдельный view + morning briefing.
  *Мнение:* **да, лёгкий win.** Надстройка над существующими данными, не refactor. 2D точка графически понятнее чем 4 нейрохимических скаляра. Рабочая психологическая модель с литературной основой. Делать после ближайшего ядра задач.

- **#14 Emergent emotions (отчаяние, радость) из 2D.** (P3/R2) Расселл разбивает 2D-плоскость на квадранты: high-neg-arousal+negative = anger/despair, low-arousal+negative = apathy. Можно назвать зоны и показывать как label. Зависит от #2. Риск: эмоции не должны быть UI-primary — мы не терапевт.
  *Мнение:* **не делать.** Один и тот же point в (V, A) может быть «отчаянием» или «grief» в зависимости от контекста — дискретизация навязчива. Риск false positive «ты в отчаянии» → psychologically concerning. Baddle не терапевт. Координаты пусть будут координаты, без ярлыков.

### Пакет «Задачный слой — backlog + auto-scheduling»

- [ ] Расширение taskplayer'а на полный задачный слой: отдельное хранилище `data/tasks.jsonl` (append-only), оценка сложности при создании, auto-scheduling в план дня через capacity-зону, возврат незавершённого с флагом `touched_today`, калибровка оценки через `surprise_at_start`. Структура, схема, алгоритм matching'а, API, миграция — в [docs/task-tracker-design.md](../docs/task-tracker-design.md). Связан с [capacity-design.md](../docs/capacity-design.md) и идеей #6 из пакета «Всё через граф». Архитектурная граница: Baddle **предлагает**, не настаивает — никакой streak-gamification («никакой логики оптимизирующей время в приложении» из [world-model](../docs/world-model.md)).

### Пакет «UX/работа графа»

- **#4 Пересмотр 14 режимов.** (P3/R4) Каких реально юзаешь? Dispute/tournament/fan/rhythm/horizon/... — отсев не-используемых, merge похожих. Требует статистики за N недель чтобы знать какие activate часто.
  *Мнение:* **да, но через месяц use-counts.** Уверен что реально работают 5-7 режимов, остальное — на всякий случай. Удалить неиспользуемые = −300 LOC в `modes.py`. Сейчас инструментация: добавить counter в `prime_directive.jsonl` per-mode, смотреть через месяц.

- **#8 Визуализация кластеров по типу в Lab.** (P2/R4) Цветовая группировка habits/actions/thoughts/constraints в Graph Lab. Косметика, но нетрудно — d3/force-layout уже есть.
  *Мнение:* **по возможности.** Действительно полезно видеть habits отдельно от actions. Низкий приоритет — не критично, но быстро.

### Долгосрочно

- **#1 Определение СДВГ и прочих паттернов.** (P?/R1) Классификатор по накопленному поведению. Нужен корпус, ethical review, сильно позже. Метка чтобы не забыть.
  *Мнение:* **правильно что в конце.** Без корпуса + без ethical layer — false positives могут навредить. Возвращаться через 6+ мес реальных данных и только в формате «ты можешь посмотреть такой-то паттерн», без диагноза.
