# TODO

## 🎯 Прайм-директива

**`sync_error = d(model_prediction, user_action)` — единственная метрика**, которая оценивает ценность любого пункта ниже. Если пункт не снижает рассинхрон с конкретным пользователем — низкий приоритет, даже если архитектурно красиво.

Подробнее о трёх столбах и принципах → [README](README.md).

---

## 🌀 Resonance protocol — все 5 механик закрыты

1. Hebbian decay → [consolidation-design.md](docs/consolidation-design.md)
2. Adaptive idle (плавное затухание по desync) → [alerts-and-cycles.md](docs/alerts-and-cycles.md)
3. Active sync-seeking → [alerts-and-cycles.md](docs/alerts-and-cycles.md) (тип `sync_seeking`)
4. System-burnout от desync (слился с #2) → `ProtectiveFreeze.desync_pressure`
5. Action Memory — самообучение через граф → [action-memory-design.md](docs/action-memory-design.md)

**Измерение эффективности:** через 2 месяца use сравнить avg weekly `sync_error`. Если падает — механики работают, прайм-директива валидирует сама себя.

**Опционально по мере данных** (не приоритет):
- [ ] Chat-timeline view в Lab UI — рендер `/graph/actions-timeline` как листающийся список actions с clik → focus в графе.
- [ ] Расширение `score_action_candidates` на другие checks — когда через месяц станет видно где реальный разброс outcomes.
- [ ] Counterfactual honesty — иногда намеренно не действовать (5-10% randomized skip) для baseline recovery-time.

---

## 📌 Ближайшее

- [ ] **README_EN.md** — английская версия корневого README для международной GitHub-аудитории. Без дословного перевода. ~1ч.
- [ ] **Desktop / system-tray notifications.** Alerts работают только пока вкладка браузера открыта. Закрыл — morning briefing, DMN-мосты, night_cycle summary уходят в пустоту. Варианты: (а) `pystray` + `plyer` — иконка в трее + OS toast; (б) PyWebView single window с фоном; (в) Service Worker + WebSocket push + Notification API. **Минимум для daily — (а)**. Альтернативно: Telegram Mini App (см. Экосистема) закрывает ту же проблему бесплатно.
- [ ] **Alerts coverage** — test harness `/debug/alerts/trigger-all` показал что 10 check'ов из 17 silent_ok на demo-данных. Пройтись по каждому, покрыть пустые условия или пометить как "not applicable on empty state". Некоторые могут нуждаться в fallback-сообщении когда данных нет.
- [ ] **Patterns × intent_router auto-abandon.** Если детектор нашёл паттерн но юзер молчит 2+ недели — убирать предложение чтобы не накапливались старые alerts.
- [ ] **«Попробовать 1 неделю» кнопка в suggestion** — временная рекурсивная цель → через неделю auto-abandon если не помогает. Мягче чем «Да, создать».
- [ ] **`plan.create_from_text`** — «встречу в среду 11:00» → plan-object через LLM. Естественный ввод vs форма.

- [ ] **Предложение еды без tool-use** (из [mockup.html](docs/mockup.html)). Два места:
  - **Реактивное.** Юзер пишет «что поесть?» → Baddle предлагает 3 варианта из `profile.food.preferences + constraints` через LLM, формат: `{название, kcal, время приготовления, почему}`. Bottom-line: «decision cost: 2 energy · или я выберу за тебя». Не требует inventory / холодильника — работает на self-report.
  - **Проактивное в morning briefing.** Если pattern-detector видит «пропускаешь завтрак по четвергам → energy crash к 14:00» — в briefing секция «Завтрак» с конкретным предложением + обоснованием паттерна. Интент-confirm card с кнопкой «съесть это» = отметить в activity.
  - **Реализация:** mode в `suggestions.py` (reactive) + pattern в `patterns.py` (proactive). ~3ч.

- [ ] **META-вопросы — ночная генерация «что ты не заметил»** (из [mockup.html](docs/mockup.html) строка 172). Scout ночью уже ищет мосты между далёкими нодами. Новый слой: когда два моста обнаруживают **общий абстрактный паттерн** («single point of failure» в auth-модуле И «single point of failure» в energy-понедельниках) — сгенерить **вопрос** уровня абстракции: «какие ещё single points of failure есть в твоей жизни которые ты не заметил?». Это не связь между нодами, а **вопрос** к юзеру. В briefing утром отдельной секцией «META question · waiting for morning». **Реализация:** новый шаг в `_check_night_cycle` после Scout — поиск общих predicates между bridges через LLM, если найден — генерация вопроса. ~2-3ч. Зависит от того что scout реально находит мосты (т.е. граф должен быть нетривиальный).

- [ ] **Специализированные card-рендеры для `fan` / `rhythm`**. Сейчас оба режима падают в `deep_research` card (trace + synthesis). По смыслу им бы свои: `fan` (Мозговой штурм) = deep-generate list с ranging по новизне; `rhythm` (Привычка) = habit-tracker view с streak-счётчиком и next-occurrence. Не блокер, но UX для этих двух режимов странный — рендерится pipeline исследования вместо натурального для них формата. ~3ч (2 новых card types в assistant.js + адаптация execute_deep под эти renderer_style'ы).

---

## 🧬 Сенсоры и устройства

MVP sensor stream работает ([docs/hrv-design.md](docs/hrv-design.md#sensor-stream-multi-source-polymorphism)): `SensorReading{ts, source, kind, metrics, confidence}`, `latest_hrv_aggregate(window_s)` с weighted avg, симулятор пушит в stream. Осталось подключить реальные источники и довести миграцию:

### Миграция UserState на stream

- [ ] **UserState → sensor stream** (ядро). Сейчас `UserState.update_from_hrv` кормится через `hrv_manager.get_baddle_state()` (из `CognitiveLoop._check_hrv_push` раз в 15с). Мигрировать на `stream.latest_hrv_aggregate()` + `stream.recent_activity()` — тогда **любой** источник (Polar / Apple / Oura / Garmin / manual) влияет на UserState напрямую без мостика через HRVManager. ~15 call-sites в cognitive_loop/assistant. Блокирует чистое подключение реальных адаптеров. Средняя сложность.

### Адаптеры (каждый — свой SensorAdapter)

- [ ] **`PolarH10Adapter`** — `bleak` + `bleakheart`, async BLE loop. Push `rr_ms` каждый beat + accelerometer magnitude. Каждые 15с агрегат через `calculate_hrv_metrics` → `push_hrv_snapshot`. Требует физ. устройство для теста. ~2-3ч.
- [ ] **Apple Watch.** HealthKit XML export parser (one-shot импорт истории). Для continuous — iOS shortcut → локальный HTTP endpoint. Confidence 0.8.
- [ ] **Oura Ring.** REST v2 API, personal token, polling утреннего sleep+HRV snapshot. Confidence 0.9 за свежий снимок.
- [ ] **Garmin.** `garminconnect` pip, HR-stream + stress + body battery. Требует login.

### Калибровка и конфликты

- [ ] **`data/sensor_baselines.json`** — per-source calibration (chest-strap vs optical — значения разные, нужна нормализация относительно baseline каждого устройства).
- [ ] **Conflict resolution** — при расхождении одновременных источников > threshold log'ировать (Polar отвалился? Oura устарел?).

### Визуализация

- [ ] **Polar H10 cone viz с θ/φ** — добавить polyvagal двухпараметрическую визуализацию когда появится реальный сенсор (зависит от hardware integration).

---

## 🛠 Tool-use — слой действий

Baddle сейчас умеет только думать и трекать. Слой execution — когда ассистент может **делать** вещи в мире: читать календарь, запрашивать погоду, искать в интернете, сохранять файл — пока отсутствует.

**Scope guardrail:** не делать generic agent framework. Каждый tool отвечает на вопрос «как это снижает sync_error?» — т.е. помогает юзеру в контексте его plans / goals / activities / check-in'ов. Если ответа нет — не добавляем.

### Инфраструктура

- [ ] **`/tool/run` endpoint + registry** — whitelist tools с явными schema (`{name, description, input_schema, output_schema, permission_level}`). LLM возвращает `tool_call` → бэкенд проверяет permission, выполняет, инжектит результат в следующий turn. Паттерн как OpenAI function-calling или Anthropic tool-use.
- [ ] **Permission model** — 3 уровня: `read` (auto), `write_self` (свой workspace — auto), `external` (сеть / вне workspace — confirm в UI на каждый вызов).
- [ ] **UI: tool-call visualization** — в card «🛠 `weather.now(Moscow)` …» → результат inline → продолжение текста. Прозрачно что происходит.
- [ ] **Pattern × tool-invocation loop** — pattern detector видит anomaly → соответствующий tool предлагает действие (`calendar.block_time("завтрак", time)`).

### Built-in tools

- [ ] **`rag.search`** — vector search по state_graph + content graph + solved archive. «Помнишь я решал X?». Закрывает «память о прошлых решениях» без интернета.
- [ ] **`calendar.fetch_today`** — iCal/Google → plans. Морфит календарь в plan-object. Утренний брифинг получает реальный день.
- [ ] **`weather.now`** — feeds morning briefing + outdoor-активности + одежда.
- [ ] **`file.read` + `file.write` scoped** — в `workspaces/{ws}/` или явно выбранной папке.
- [ ] **`hrv.calibrate`** — 60с baseline session.
- [ ] **Интернет поиск** — для фактчекинга в Research/Debate режимах. Реализуется как tool `web.search`.
- [ ] **LLM + поиск гибрид** — LLM генерит гипотезу → tool проверяет факты → синтез. Паттерн, не отдельный модуль.
- [ ] **Per-этап выбор модели** — local 8B для generate, cloud для doubt/essay. Инфраструктура роутинга, не tool-use.
- [ ] **Reasoning-backend для heavy modes** (вдохновлено [OpenMythos](https://github.com/kyegomez/OpenMythos) + o1/qwen3-thinking/R1). Режимы `dispute` / `tournament` / `smartdc` сейчас делают 5-15 раздельных LLM calls (thesis → antithesis → synthesis → verification → ...). Reasoning-модели делают thinking внутри **одного** вызова с hidden CoT. **Гибрид:** если в settings доступна reasoning-модель — тяжёлые режимы роутятся туда как single-shot, trace = thinking stream (когда модель отдаёт). Лёгкие режимы (morning briefing, quick answers) остаются на local 8B. Плюс: быстрее, дешевле, увереннее. Минус: в графе меньше промежуточных нод (thinking hidden). Конфигурируется per-mode в settings. ~1 день.
- [ ] **Продукты / рецепты inventory** — опционально. Сейчас еда решается через profile.food constraints + LLM (без холодильника). Нужно только для expiry-tracking.
- [ ] **Гардероб** — что есть + погода + календарь → outfit через связку tool'ов.
- [ ] **Браузер-расширение** — impulse guard (покупки), emotion guard (письма). Это **input** канал (читает контекст страницы), не tool-use как таковой.

---

## 📈 Экосистема / scale

- [ ] **Layout** — d3/dagre/ELK вместо плоской линии для графа.
- [ ] **Экспорт** — PNG / SVG / markdown / Obsidian.
- [ ] **EXE-установщик** — PyInstaller.
- [ ] **Graph Store** — маркетплейс графов, review, рейтинги.
- [ ] **Извлечение графа из текста** — статья → граф.
- [ ] **SSE/WebSocket** — push вместо polling для HRV/alerts (instant feel).
- [ ] **Telegram Mini App**

---

## 🏗 Архитектурно открытые (edge cases)

- [ ] **UserState global per-person.** Один UserState на все workspaces. Если захочется разных `profile.food` для work vs personal — потребуется UserState per-workspace + context-switcher.
---

## 🤔 Нерешённые размышления

Архитектурные вопросы без очевидного ответа — в отдельном документе: **[docs/open-questions.md](docs/open-questions.md)**. Когда направление выбрано — задача приезжает сюда в TODO.

Сейчас открыты:
- **#1 Personal capacity** — отложено ≥ 1 мес use (без реальных данных подбор гадание). [Подробности](docs/open-questions.md#1-личные-лимиты-энергии-prior-не-constant).
- **#2 Agency как 5-я ось** — 🔬 **в процессе измерений 2-3 недели** (2026-04-21): `UserState.agency` собирается, показывается в UI, пока НЕ в sync_error. [Подробности](docs/open-questions.md#2).
- ~~**#3 Валентность через −Δsync_error**~~ — **merged в [Action Memory](docs/action-memory-design.md)** 2026-04-21.
- ~~**#4 Recovery routes**~~ — **merged в [Action Memory](docs/action-memory-design.md)** 2026-04-21.
- **#5 Workspace attractors** — ✨ следующее после Action Memory. [Подробности](docs/open-questions.md#5).
- **#6 PE как вектор** + **#7 Surprise detection у юзера** — делать вместе, зеркальные механизмы. Требует реального HRV (Polar), не симулятора. [#6](docs/open-questions.md#6) · [#7](docs/open-questions.md#7).

---