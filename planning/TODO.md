# TODO

> 🗺 **Где что:**
> - **Эта страница** — что **остаётся сделать**.
> - [docs/](../docs/) — как код работает (по подсистемам).
> - [docs/architecture-rules.md](../docs/architecture-rules.md) — 7 правил архитектуры + фильтр для новых фич.
> - [docs/resonance-model.md](../docs/resonance-model.md) — резонансная оптика, 5 аксиом.
> - [cleanup-plan.md](cleanup-plan.md) — Track A + B оставшегося cleanup.
> - [breathing-mode.md](breathing-mode.md), [resonance-code-changes.md](resonance-code-changes.md), [resonance-prompt-preset.md](resonance-prompt-preset.md) — Tier 2 design specs.

---

## 🎯 Прайм-директива

**`sync_error = d(model_prediction, user_action)` — единственная метрика** ценности любого пункта. Если пункт не снижает рассинхрон — низкий приоритет, даже если архитектурно красиво.

**Измерение:** `sync_error_ema_slow` пишется раз в час в [`data/prime_directive.jsonl`](../src/prime_directive.py). Endpoint `GET /assist/prime-directive?window_days=30&daily=1` даёт aggregate + trend verdict.

### Принцип: параметр с теоретической основой не выходит на переговоры

Если механика выводится из физической / биологической / математической модели (Friston, VAD, ВНС, SDT, нейрохимия, РГК) — она **остаётся** и только калибруется на данных. Gate-решения («оставлять или убирать?») — исключительно для ad-hoc механик. Константы вроде `BALANCE_CORRIDOR=[0.3, 1.5]` крутятся молча по мере данных, в TODO не попадают.

---

## 🚦 Calibration (ждём 2 нед данных)

| Что | Источник данных | Что смотреть | Действие когда соберём |
|---|---|---|---|
| Phase B urgency формулы | `data/throttle_drops.jsonl` | доля `reason="budget"` дропов с `urgency≥0.7`; распределение urgency по детекторам | крутка `compute_urgency` в `src/detectors.py` (~1ч) |
| Phase C cognitive_load | `day_summary` в `user_state.json` | соответствие `cognitive_load_today` реальному ощущению; распределение по cap'ам | крутка коэффициентов `0.20/0.30/0.30/0.25/0.25`; `CAPACITY_*` thresholds (~1ч) |
| Phase D balance corridor | `prime_directive.jsonl` aggregate | distribution `balance()` user/system; 95%+ ли в `[0.3, 1.5]` | если 95% в [0.4, 0.7] → формула плоская, нужны жёстче feeders (~1-2ч) |
| Phase D ACh/GABA caps | то же | реальный effect cap'ов (10 нод/час, freeze.active boolean) | крутка cap'ов; решение про подключение отложенных feeders |

Корректировка: ~1-2ч на крутку. Не задача для отдельной сессии до накопления данных.

---

## ⚡ Cheap (1-3ч каждый, можно брать в любом порядке)

- [ ] **`aperture` UI slider** — backend done (`get_aperture()` в `api_backend.py` + 3 derived функции, backward-compat infer из legacy `deep_response_format`, 5 unit tests). Осталось добавить slider в settings UI (🎯 Фокус | 📘 Эссе | 📖 Статья | 🌐 Панорама) + одна строка POST `deep_aperture` в `update_settings`. Spec — [resonance-code-changes.md](resonance-code-changes.md). ~1ч UI.

---

## 🌊 Tier 2 — фичи (большие, по очереди когда appetite)

### Резонансные

- [ ] **Дыхательный режим** — Baddle предлагает 5→5 / 4-4-4-4 / 4-7-8 при рассогласовании frequency_regime. Action Memory cycle закрывает обучение по outcome. Bonus: User GABA boost feeder через breathing detection. Spec — [breathing-mode.md](breathing-mode.md). **~5ч**.
- [ ] **Резонансный промпт-preset** — Chat UI dropdown 🔵/🔴/⚪ + шаблон `[Контекст волны] [Состояние] [Запрос] [Параметры]`. Опционально auto-detect через `frequency_regime`. Spec — [resonance-prompt-preset.md](resonance-prompt-preset.md). **~1-2ч**.
- [ ] **Snapshot-якорь узора при перерыве** — когда юзер прерывает работу (close session, switch context, idle >10мин), фиксировать текущую геометрию конуса + frequency_regime + active session_indices в `data/anchor_snapshots.jsonl`. При возврате — restore-предложение «продолжить узор» с этими параметрами. Идея: вход в поток после перерыва стоит 15-40 мин восстановления — anchor может сократить. A/B измеримо. **~3ч** (backend + UI prompt). Из chat-export 2026-04-22..24.
- [ ] **Cone-viz controls (рычаги конуса как инструмент)** — текущий `baddle-cone-svg` показывает форму как индикатор. Сделать его **управляемым**: ширина (aperture slider), длина (horizon slider), направление (mode dropdown). Юзер видит и управляет геометрией внимания напрямую. Зависит от aperture фичи (cheap). **~2ч** UI после aperture. Из chat-export.
- [ ] **Counter-wave Tier 2 — explicit mode-aware tactics** — Counter-wave (Правило 7) активирован 2026-04-25: `Resonator.update_mode()` вызывается в `_advance_tick`, Dispatcher понижает urgency push-style сигналов при `mode='C'`. Нужно расширить:
  - **Sync_seeking explicit mode-aware tone** — `_generate_sync_seeking_message` при `user.mode='C'` выбирает curious/reference (без давления) вместо caring/simple. **~1ч**.
  - **UI индикатор R/C** в balance widget — JS читает `state.user_state.mode`/`state.neurochem.mode` и рисует 🌊R / 🌊C значок рядом с balance числом. **~30 мин**.
  - **Property test** на mode trajectory через реальный `_advance_tick` (sync_err > 0.15 → mode='C' через N тиков, потом restore при низком). **~30 мин**.

### Memory / RAG

- [ ] **RAG в `execute_deep` и `_fastpath_chat`** — similar past nodes/outcomes через `distinct()` к query, инжект в prompt. Pump/DMN частично делают это, нужно довести до всех путей. **~3-5ч**.
- [ ] **META-вопросы — ночная генерация «что ты не заметил»** — два scout-моста обнаруживают общий абстрактный паттерн → вопрос. Зависит от того что scout реально находит мосты. **~2-3ч**.

### Sensors

- [ ] **`PolarH10Adapter`** — `bleak` + `bleakheart` async BLE loop. Push `rr_ms` + accelerometer. Каждые 15с агрегат. **~2-3ч**.
- [ ] **`UserState → sensor stream` migration** — мигрировать `update_from_hrv` на `stream.latest_hrv_aggregate()` + `stream.recent_activity()`. ~15 call-sites. Блокирует реальные адаптеры.
- [ ] **`AppleWatchAdapter`** — через HealthKit export или Shortcuts API. Sparse HR. Confidence 0.8.
- [ ] **HRV-аналитика** — VO2max тест (Cooper / Åstrand / step), HRR (Heart Rate Recovery), «Аварийный режим коробки» детектор (хронический симпатический перегруз).
- [ ] **Event-based HRV compression** — `hrv_events.jsonl` вместо raw RR. События: `rest_baseline_update`, `activity_start/peak/recovery`, `coherence_shift`, `anomaly_spike`, `autonomic_strain`.

### UX / еда / план

- [ ] **Constraint expansion через LLM** — юзер добавил `"лактоза"` → LLM генерит синонимы → сохранить в `profile.categories[cat].constraints_expanded`. **~1-2ч**.
- [ ] **Auto-parse constraints из message** — «не ем / аллергия / без X» в чате → LLM-parse → draft-card. **~2ч**.
- [ ] **`plan.create_from_text`** — «встречу в среду 11:00» → plan-object через LLM. **~2ч**.
- [ ] **Предложение еды без tool-use** — реактивное и проактивное (pattern-detector видит «пропускаешь завтрак по четвергам → energy crash»). **~3ч**.
- [ ] **Specialized card-рендеры для `fan` / `rhythm`** — сейчас оба падают в `deep_research` card. **~3ч**.
- [ ] **Один чат** — убрать legacy entry, оставить единый chat UI. Дублирующая навигация копит cognitive overhead. **~2ч**.

### Эмоциональная модель

- [ ] **Модель Рассела (Valence × Arousal, 2D)** — UI-карта эмоционального состояния поверх существующих `valence` (LLM sentiment EMA) + `norepinephrine` (arousal proxy). Morning briefing раздел с координатой + траекторией за день. Надстройка без новых данных. **~3-4ч**.

### Action Memory расширения

- [ ] **Расширение `score_action_candidates`** на детекторы помимо `detect_sync_seeking` — кандидаты: suggestion-tone, morning-briefing section prioritization, recurring-lag reminder timing.
- [ ] **Dialog pivot detection** в surprise detector — embedding distance между последовательными user-сообщениями. Третий канал OR рядом с HRV+text markers. **~2ч**.

### Capacity полишинг (после Phase C)

- [ ] **`detect_evening_retro` расширить** по 6 observable + progress дня.
- [ ] **`detect_morning_briefing`** добавить causal explanation зоны (какой контур провисает + почему).
- [ ] **`/assist/simulate-day`** переписать на прогноз capacity-зон.

### Desktop / OS

- [ ] **Desktop notifications** — alerts работают только пока вкладка открыта. MVP: `pystray` + `plyer`. **~2-3ч**.

---

## 🛠 Tool-use (отдельная сессия)

- [ ] Слой действий (calendar / weather / web.search / file / rag / permission model) — отдельная сессия когда появится необходимость.

---

## 🧹 Line-count cleanup

Опциональный план Phase E-I (5 фаз, −2000..−3200 строк, 15-25ч) — отдельный документ [cleanup-plan.md](cleanup-plan.md). Не делается по умолчанию.

---

## 🤔 Открытые архитектурные вопросы

### #1 Personal capacity — prior, не constant
**Проблема:** capacity thresholds хардкод. Один юзер — физик 14ч/день, другой — бабушка; система обрабатывает одинаково.
**Направление:** Bayesian online EMA на `daily_spent + stop_events + nightly_hrv_rmssd` → per-user `{daily_max, daily_max_by_hour[weekday]}`.
**Критерий:** A/B 2 недели static vs estimated. Принимаем если avg `sync_error` падает >15%.
**Где:** новый `src/capacity_estimator.py`.
**Блок:** минимум 1 мес реальных данных.

### #2 6-я ось — VAD-Dominance уже есть, что после
**Контекст:** 5-axis (DA/5HT/NE/ACh/GABA) реализована Phase D + B0/B4. `UserState.agency` (VAD-Dominance) собирается как aux observable.
**Дилемма:** если данные через 2-3 мес покажут что нужна 6-я ось — тянуть `meaning` / `relatedness` из SDT (Self-Determination Theory) или продолжать на 5-axis. Trade-off: SDT добавляет психометрику без чистого нейрохимического mapping → может разрушить чистоту резонатора.
**Блок:** статистика `mean_pe_agency` за 2-3 мес use.

### #3 Память как ключ настройки vs text + embedding
**Проблема:** Нода хранит `{text, embedding, ...}`. По резонансной оптике (диалог 2026-04-24, источник РГК v1.0) память должна быть **протоколом воспроизведения волны**, не копией: мозг хранит параметры `[частота, фаза, угол, энергия]`, не сам узор. Каждое воспроизведение = новая сборка из текущего шума и контекста, поэтому память **меняется** при каждом recall (фазовый сдвиг от текущего frequency_regime). Embedding близок к «ключу настройки», text — к копии объекта. Возможно дублирование избыточно.
**Направление:** хранить только embedding + контекст для restoration; text восстанавливается LLM при retrieval. Фазовый сдвиг при reconstruction — отдельное явление: измерять и предсказывать когда recall будет искажён (текущий frequency_regime ≠ исходному).
**Блок:** 1-2 мес use, статистика по чтению text + работоспособность LLM-reconstruction.

### #4 Lab-scratch — изолированный граф для экспериментов
**Три варианта:** A. Active pointer; B. Cognitive pause; C. Dual runtime (`_graph` для main, `_lab_graph` для scratch).
**Блок:** понять после 1-2 мес daily-use есть ли реальная потребность.

---

## 🏗 Edge cases

- [ ] **Attention-weighted PE** — 4 PE-канала normalized + max. Можно ввести precision-weights: шумные каналы получают меньший вес. Классический Фристон. Не блокер; если `mean_pe_hrv` через 2 мес окажется заметно шумнее `mean_pe_user` — precision-gating решит.

---

## 💡 Бэклог идей (думать, не делать)

> Не задачи — направления. Оценка P×R = полезность × реалистичность (из 5).
>
> **Метанаблюдение.** Половина идей — вариации «переписать всё через одну абстракцию» (граф = мозг). Это то же искушение что NAND-эксперимент 2026-04-24 (null-result). Unified abstractions приятны для архитектора, но часто проигрывают гибриду.
>
> **Блокер для большей части бэклога:** 1-2 мес реальных данных через прайм-директиву.

### Пакет «Всё через граф»

- **#3 Зачем циклы если всё в графе?** (P5/R3) DMN блуждает по нодам, находит паттерны. *Мнение:* **не делать** — замена предсказуемой системы на stochastic.
- **#5 Recurring = циклический goal-узел** (P4/R3). Только после доказательства #3.
- **#6 Цели дня = 3 узла графа** (P4/R2). *Мнение:* **не делать полную замену** — goals имеют transactional семантику.
- **#7 Узел выполнения** (P3/R4). *Мнение:* **да, быстрый win.** Action Memory делает 80%, остаётся форма + endpoint.
- **#13 Constraints/preferences как узлы графа** (P4/R2). *Мнение:* **не делать** — `profile.json` editable руками, git-backup, viewable.
- **#15 История чата влияет на reasoning** (P4/R3). *Мнение:* **делать** — это Tier 2 RAG в execute_deep.

### Пакет «Память и pruning»

- **#11 Оперативная vs долговременная память** (P4/R3). *Мнение:* **не проактивно** — текущий consolidation решает 80%.
- **#12 Pruning: коллапсировать похожие** (P3/R4). Откладывать.

### Пакет «Эмоциональная модель»

- **#14 Emergent emotions из 2D** (P3/R2). *Мнение:* **не делать.** Дискретизация навязчива, риск psychological harm.

### Пакет «Задачный слой»

- [ ] Расширение taskplayer'а — отдельное хранилище `data/tasks.jsonl`, оценка сложности при создании, auto-scheduling через capacity-зону. Spec — [docs/task-tracker-design.md](../docs/task-tracker-design.md).

### Пакет «UX/работа графа»

- **#4 Пересмотр 14 режимов** (P3/R4). *Мнение:* **да, но через месяц use-counts.** Удалить неиспользуемые = −300 LOC в `modes.py`.
- **#8 Визуализация кластеров по типу в Lab** (P2/R4). *Мнение:* **по возможности** — d3/force-layout уже есть.

### Долгосрочно

- **#1 Определение СДВГ и прочих паттернов** (P?/R1). Без корпуса + ethical layer — false positives могут навредить. 6+ мес реальных данных.
