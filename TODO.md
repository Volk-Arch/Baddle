# TODO

## 🎯 Прайм-директива

**`sync_error = d(model_prediction, user_action)` — единственная метрика**, которая оценивает ценность любого пункта ниже. Если пункт не снижает рассинхрон с конкретным пользователем — низкий приоритет, даже если архитектурно красиво.

Подробнее о трёх столбах и принципах → [README](README.md).

---

## 📌 Ближайшее

Конкретные задачи в работе или в очереди.

- [ ] **README_EN.md** — английская версия корневого README для международной GitHub-аудитории. Без дословного перевода. ~1ч.

- [ ] **UserState → sensor stream migration.** `UserState.update_from_hrv` сейчас кормится через `hrv_manager.get_baddle_state()` (вызывается из `CognitiveLoop._check_hrv_push` раз в 15с). Мигрировать на `stream.latest_hrv_aggregate()` + `stream.recent_activity()` — тогда любой источник (Polar / Apple / Oura / manual) будет влиять на UserState напрямую без мостика через HRVManager. ~15 call-sites в cognitive_loop/assistant. Средняя сложность.

- [ ] **Desktop / system-tray notifications.** Alerts работают только пока вкладка браузера открыта. Закрыл — morning briefing, DMN-мосты, night_cycle summary уходят в пустоту. Варианты: (а) `pystray` + `plyer` — иконка в трее + OS toast; (б) PyWebView single window с фоном; (в) Service Worker + WebSocket push + Notification API. **Минимум для daily — (а)**. Альтернативно: Telegram Mini App (см. ниже в Экосистема) закрывает ту же проблему бесплатно.

- [ ] **Alerts coverage** — test harness `/debug/alerts/trigger-all` показал что 10 check'ов из 17 silent_ok на demo-данных. Пройтись по каждому, покрыть пустые условия или пометить как "not applicable on empty state". Некоторые могут нуждаться в fallback-сообщении когда данных нет.

---

## 🧬 Sensors (polymorphism — осталось после MVP)

MVP sensor stream работает ([docs/hrv-design.md](docs/hrv-design.md#sensor-stream-multi-source-polymorphism)). Реальные источники подключить:

- [ ] **`PolarH10Adapter`** — `bleak` + `bleakheart`, async BLE loop. Push `rr_ms` каждый beat + accelerometer magnitude. Каждые 15с агрегат через `calculate_hrv_metrics` → `push_hrv_snapshot`. Требует физ. устройство для теста. ~2-3ч.
- [ ] **Apple Watch.** HealthKit XML export parser (one-shot импорт истории). Для continuous — iOS shortcut → локальный HTTP endpoint. Confidence 0.8.
- [ ] **Oura Ring.** REST v2 API, personal token, polling утреннего sleep+HRV snapshot. Confidence 0.9 за свежий снимок.
- [ ] **Garmin.** `garminconnect` pip, HR-stream + stress + body battery. Требует login.
- [ ] **`data/sensor_baselines.json`** — per-source calibration (chest-strap vs optical значения разные, нужна нормализация относительно baseline каждого устройства).
- [ ] **Conflict resolution** — при расхождении одновременных источников > threshold log'ировать (Polar отвалился? Oura устарел?).

## 🎨 UI / визуализация

- [ ] **Polar H10 cone viz с θ/φ** — добавить polyvagal двухпараметрическую визуализацию когда появится реальный сенсор. (зависит от hardware integration)

## 🌐 Внешний мир / интеграции

- [ ] **Интернет / RAG** — search для фактчекинга в Research/Debate режимах.
- [ ] **LLM + поиск гибрид** — LLM генерит гипотезу → поиск проверяет факты.
- [ ] **Per-этап выбор модели** — local 8B для generate, cloud для doubt/essay.
- [ ] **Календарь** — события → приоритизация, напоминания. (лучше через Tool-use)
- [ ] **Погода API** — утренний брифинг + outdoor-активности + одежда.
- [ ] **Продукты/рецепты inventory** — опционально. Сейчас еда решается через profile.food constraints + LLM (без холодильника). Нужно только для expiry-tracking.
- [ ] **Гардероб** — что есть + погода + календарь → outfit.
- [ ] **Браузер-расширение** — impulse guard (покупки), emotion guard (письма).

## 🛠 Tool-use / sandboxed skills

Baddle сейчас умеет только думать и трекать. Слой execution — когда
ассистент может **делать** вещи в мире: читать календарь, запускать код,
сохранять файл — пока отсутствует.

**Scope guardrail:** не делать generic agent framework. Каждый tool
отвечает на вопрос «как это снижает sync_error?» — т.е. помогает юзеру
в контексте его plans / goals / activities / check-in'ов. Если ответа
нет — не добавляем.

- [ ] **`/tool/run` endpoint + registry** — whitelist tools с явными schema
  (`{name, description, input_schema, output_schema, permission_level}`).
  LLM возвращает `tool_call` → бэкенд проверяет permission, выполняет,
  инжектит результат в следующий turn. Паттерн как OpenAI function-calling
  или Anthropic tool-use.
- [ ] **Sandbox backends**:
  - `subprocess` (дефолт) — stripped env с timeout + file-system allowlist.
  - `docker` — требует docker-daemon.
  - `pyodide` (WASM) — безопасный Python в браузере.
- [ ] **Built-in tools** (подчинённые прайм-директиве):
  - `calendar.fetch_today` — iCal/Google → plans.
  - `weather.now` — feeds morning briefing.
  - `rag.search` — vector search по state_graph + content graph + solved archive. «Помнишь я решал X?»
  - `file.read` + `file.write` scoped — в `workspaces/{ws}/` или явно выбранной папке.
  - `code.run_snippet` — Python/JS в sandbox, stdout+result.
  - `hrv.calibrate` — 60с baseline session.
  - `plan.create_from_text` — «встречу в среду 11:00» → plan-object через LLM.
- [ ] **Permission model** — 3 уровня: `read` (auto), `write_self` (свой workspace — auto), `external` (сеть / вне workspace — confirm в UI на каждый вызов).
- [ ] **UI: tool-call visualization** — в card «🛠 `weather.now(Moscow)` …» → результат inline → продолжение текста. Прозрачно что происходит.
- [ ] **Pattern × tool-invocation loop** — pattern detector видит anomaly → соответствующий tool предлагает действие (`calendar.block_time("завтрак", time)`).

## 📈 Экосистема / scale

- [ ] **Layout** — d3/dagre/ELK вместо плоской линии для графа.
- [ ] **Экспорт** — PNG / SVG / markdown / Obsidian.
- [ ] **EXE-установщик** — PyInstaller.
- [ ] **Graph Store** — маркетплейс графов, review, рейтинги.
- [ ] **Извлечение графа из текста** — статья → граф.
- [ ] **Demo mode** — ускоренная симуляция «недели Baddle».
- [ ] **SSE/WebSocket** — push вместо polling для HRV/alerts (instant feel).
- [ ] **Telegram Mini App wrapper** (Activity + Briefing + Alerts). Обёртка над текущими `/activity/*` endpoints + `WebApp.sendData` для auth. Закрывает три проблемы сразу: OS-уведомления на phone+desktop бесплатно (Telegram сам push'ит), мобильный ввод activity на ходу, morning briefing как push. ~1-2 дня фронтенд.

## 🏗 Архитектурно открытые (edge cases)

Не блокеры. Всплывут при scale'е или multi-user.

- [ ] **UserState global per-person.** Один UserState на все workspaces. Если захочется разных `profile.food` для work vs personal — потребуется UserState per-workspace + context-switcher.
- [ ] **Patterns × intent_router auto-abandon.** Если детектор нашёл паттерн но юзер молчит 2+ недели — убирать предложение чтобы не накапливались старые alerts.
- [ ] **«Попробовать 1 неделю» кнопка в suggestion** — временная рекурсивная цель → через неделю auto-abandon если не помогает. Мягче чем «Да, создать».

---

## 📚 Что уже сделано

TODO держит только **будущее**. История проекта и актуальное состояние системы — в документации и design-docs:

- [docs/closure-architecture.md](docs/closure-architecture.md) — замыкание основных петель (intent router, observation → suggestion, workspace scoping, solved archive → RAG)
- [docs/alerts-and-cycles.md](docs/alerts-and-cycles.md) — 17 фоновых check'ов, типы alerts, test harness, thinking-state
- [docs/hrv-design.md](docs/hrv-design.md) — HRV как вход, sensor stream polymorphism
- [docs/ui-split-plan.md](docs/ui-split-plan.md) — разделение `/` Baddle и `/lab` Graph Lab
- [docs/README.md](docs/README.md) — полный index дизайн-docs (32 файла, порядок чтения)
- [README.md](README.md) — продуктовое описание: что Baddle умеет, наука под капотом, быстрый старт
