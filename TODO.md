# TODO

## 🎯 Прайм-директива

**`sync_error = d(model_prediction, user_action)` — единственная метрика**, которая оценивает ценность любого пункта ниже. Если пункт не снижает рассинхрон с конкретным пользователем — низкий приоритет, даже если архитектурно красиво.

Подробнее о трёх столбах и принципах → [README](README.md).

---

## 🌀 Resonance protocol — поведение как у зеркала

Цепочка реальность → человек → Baddle (см. [docs/world-model.md](docs/world-model.md)). Baddle ведёт себя как зеркало — ищет сигнал когда юзер пропал, затухает когда контакт долго нарушен, крепит частые мысли, отключает холостые циклы.

Закрытые механики описаны в [docs/consolidation-design.md](docs/consolidation-design.md) (hebbian decay) и [docs/alerts-and-cycles.md](docs/alerts-and-cycles.md) (adaptive idle + burnout + эмпатия к user.burnout).

Осталась одна:

- [ ] **Active sync-seeking — «Baddle ищет тебя»** (среднее). Новый check `_check_sync_seeking` в cognitive_loop: если `freeze.desync_pressure > T` И времени с последнего user input ≥ порога → мягкий запрос в чат («Как ты?», «Что сегодня?», «Я не слышу — всё ок?»). Throttle (не чаще раза в 1.5-2 часа), отключается автоматически когда юзер возвращается. **Не** nудж — целенаправленная попытка восстановить контакт. Триггер готов (`desync_pressure` уже существует, feed'ит multiplier). **Реализация:** новая функция в cognitive_loop + шаблон-рандомизатор вопросов по HRV/времени дня. ~2ч.

Побочное: концептуальные вопросы «что такое валентность без антропоморфизма» и «как измерять agency/meaning» — в [open-questions](docs/open-questions.md), не первичны для resonance protocol.

---

## 📌 Ближайшее

- [ ] **README_EN.md** — английская версия корневого README для международной GitHub-аудитории. Без дословного перевода. ~1ч.

- [ ] **Desktop / system-tray notifications.** Alerts работают только пока вкладка браузера открыта. Закрыл — morning briefing, DMN-мосты, night_cycle summary уходят в пустоту. Варианты: (а) `pystray` + `plyer` — иконка в трее + OS toast; (б) PyWebView single window с фоном; (в) Service Worker + WebSocket push + Notification API. **Минимум для daily — (а)**. Альтернативно: Telegram Mini App (см. Экосистема) закрывает ту же проблему бесплатно.

- [ ] **Alerts coverage** — test harness `/debug/alerts/trigger-all` показал что 10 check'ов из 17 silent_ok на demo-данных. Пройтись по каждому, покрыть пустые условия или пометить как "not applicable on empty state". Некоторые могут нуждаться в fallback-сообщении когда данных нет.

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

- [ ] **Sandbox backends**:
  - `subprocess` (дефолт) — stripped env с timeout + file-system allowlist.
  - `docker` — требует docker-daemon.
  - `pyodide` (WASM) — безопасный Python в браузере.

- [ ] **Permission model** — 3 уровня: `read` (auto), `write_self` (свой workspace — auto), `external` (сеть / вне workspace — confirm в UI на каждый вызов).

- [ ] **UI: tool-call visualization** — в card «🛠 `weather.now(Moscow)` …» → результат inline → продолжение текста. Прозрачно что происходит.

- [ ] **Pattern × tool-invocation loop** — pattern detector видит anomaly → соответствующий tool предлагает действие (`calendar.block_time("завтрак", time)`).

### Built-in tools

Упорядочены по ценности для прайм-директивы:

- [ ] **`rag.search`** — vector search по state_graph + content graph + solved archive. «Помнишь я решал X?». Закрывает «память о прошлых решениях» без интернета.
- [ ] **`calendar.fetch_today`** — iCal/Google → plans. Морфит календарь в plan-object. Утренний брифинг получает реальный день.
- [ ] **`weather.now`** — feeds morning briefing + outdoor-активности + одежда.
- [ ] **`plan.create_from_text`** — «встречу в среду 11:00» → plan-object через LLM. Естественный ввод vs форма.
- [ ] **`file.read` + `file.write` scoped** — в `workspaces/{ws}/` или явно выбранной папке.
- [ ] **`code.run_snippet`** — Python/JS в sandbox, stdout+result.
- [ ] **`hrv.calibrate`** — 60с baseline session.

### Internet / RAG (как tool'ы, не отдельный слой)

- [ ] **Интернет поиск** — для фактчекинга в Research/Debate режимах. Реализуется как tool `web.search`.
- [ ] **LLM + поиск гибрид** — LLM генерит гипотезу → tool проверяет факты → синтез. Паттерн, не отдельный модуль.
- [ ] **Per-этап выбор модели** — local 8B для generate, cloud для doubt/essay. Инфраструктура роутинга, не tool-use.

### Специализированные интеграции (каждая — tool + data source)

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
- [ ] **Demo mode** — ускоренная симуляция «недели Baddle».
- [ ] **SSE/WebSocket** — push вместо polling для HRV/alerts (instant feel).
- [ ] **Telegram Mini App wrapper** (Activity + Briefing + Alerts). Обёртка над текущими `/activity/*` endpoints + `WebApp.sendData` для auth. Закрывает три проблемы сразу: OS-уведомления на phone+desktop бесплатно (Telegram сам push'ит), мобильный ввод activity на ходу, morning briefing как push. ~1-2 дня фронтенд.

---

## 🏗 Архитектурно открытые (edge cases)

- [ ] **UserState global per-person.** Один UserState на все workspaces. Если захочется разных `profile.food` для work vs personal — потребуется UserState per-workspace + context-switcher.
- [ ] **Patterns × intent_router auto-abandon.** Если детектор нашёл паттерн но юзер молчит 2+ недели — убирать предложение чтобы не накапливались старые alerts.
- [ ] **«Попробовать 1 неделю» кнопка в suggestion** — временная рекурсивная цель → через неделю auto-abandon если не помогает. Мягче чем «Да, создать».

---

## 🤔 Нерешённые размышления

Архитектурные вопросы без очевидного ответа — в отдельном документе: **[docs/open-questions.md](docs/open-questions.md)**. Когда направление выбрано — задача приезжает сюда в TODO.

Сейчас открыты:
- **Personal capacity estimation** — hardcoded `DAILY_ENERGY_MAX=100, LONG_RESERVE_MAX=2000` → online Bayesian update под реального юзера + rhythm-aware ceiling[weekday][time]. См. [open-questions #1](docs/open-questions.md#1-личные-лимиты-энергии-prior-не-constant).
- **4 оси DA/S/NE/burnout как user-facing** — это implementation-имена, а не что человек чувствует. Agency / meaning / relatedness / flow-vs-DMN могут быть ближе к опыту. Начать с одной `agency`. См. [open-questions #2](docs/open-questions.md#2-четыре-оси-нейрохимии-imiplementation-а-не-user-facing).
- **Валентность без антропоморфизма** — как дать метрикам «вес» (приятно/неприятно) без ложной субъективности. Путь: `valence = -Δsync_error` через наблюдение, не через subjective ratings. См. [open-questions #3](docs/open-questions.md#3-валентность-без-антропоморфизма).
- **Recovery routes memory** — как именно юзер возвращается в resonance (тишина? вопрос? предложение?). Hebbian на уровне действий sync-seeking. Зависит от #3, требует месяц данных. См. [open-questions #4](docs/open-questions.md#4-recovery-routes-memory).

---
