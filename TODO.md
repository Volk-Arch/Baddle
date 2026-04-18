# TODO

## 🎯 Прайм-директива

**`sync_error = d(model_prediction, user_action)` — единственная метрика, которая
оценивает ценность любого пункта ниже.** Если пункт не снижает рассинхрон с
конкретным пользователем — низкий приоритет, даже если архитектурно красиво.

Подробнее о трёх столбах и принципах → [README](README.md).

---

# ⬆ НЕ СДЕЛАНО

## 🩺 Daily-use viability (остатки)

- [ ] **Desktop / system-tray notifications.** Alerts работают только
  пока вкладка браузера с Baddle открыта. Закрыл — morning briefing,
  DMN-мосты, night_cycle summary, zone_overload уходят в пустоту.
  Варианты: (а) `pystray` + `plyer` — system tray + OS notifications;
  (б) PyWebView-обёртка вокруг Flask → single window с фоном;
  (в) Service Worker + WebSocket push + Notification API. Минимум
  для daily — (а): иконка в трее + OS toast на каждый alert.
  *Требует pip-пакеты — отложено.*

## Тело и сенсоры

- [ ] **Polar H10 BLE** — реальный RR + accelerometer поток. `bleak` клиент,
  24/7 connect, fallback на симулятор. Сейчас только симулятор + слайдеры.
- [ ] **Apple Watch / Oura / Garmin адаптер** — другая семантика данных
  (sparse RR, HR-stream, sleep). Нужна отдельная формула coherence из
  HR-timeseries (не RR-to-RR). `hrv_manager.start(mode="apple_watch")`
  с альтернативным reader. Детали → [docs/hrv-design.md](docs/hrv-design.md)
  таблица «Источники данных».

## UI / визуализация

- [ ] **Polar H10 cone viz с θ/φ** — сейчас конус рендерится по precision +
  state. Добавить polyvagal двухпараметрическую визуализацию когда будет
  реальный сенсор. (зависит от hardware integration)

## Внешний мир (интеграции)

- [ ] **Интернет / RAG** — search для фактчекинга в Research/Debate режимах.
- [ ] **LLM + поиск гибрид** — LLM генерит гипотезу → поиск проверяет факты.
- [ ] **Per-этап выбор модели** — local 8B для generate, cloud для doubt/essay.
- [ ] **Календарь** — события → приоритизация, напоминания.
- [ ] **Погода API** — утренний брифинг + outdoor-активности + одежда.
- [ ] **Продукты/рецепты inventory** — опционально. Сейчас еда решается
  через profile.food constraints + LLM (без холодильника). Inventory
  понадобится только если захочется expiry-tracking / pantry persistence.
- [ ] **Гардероб** — что есть + погода + календарь → outfit.
- [ ] **Браузер-расширение** — impulse guard (покупки), emotion guard (письма).

## 🛠 Tool-use / sandboxed skills (inspired by Nous Hermes Agent)

Hermes Agent (100k stars) демонстрирует паттерн: **агент с tool-box-ом + sandbox**
умеет не только «думать», но и «делать». У Baddle сейчас этого слоя нет —
мы только reasoning + state-tracking. Добавить tool-use подчинённый прайм-директиве
(`sync_error = план − факт`), а не «агент-исполнитель задач general».

Отличие от Hermes: у нас **каждый tool должен снижать sync_error** — помогать
юзеру в контексте его plans / goals / activities / check-in'ов, а не выполнять
произвольные команды. Tools живут как узкие расширения ассистента, не замена.

- [ ] **`/tool/run` endpoint + registry** — whitelist известных tools с
  явными schema'ми (`{name, description, input_schema, output_schema,
  permission_level}`). LLM может вернуть `tool_call` в ответе → бэкенд
  проверяет permission, выполняет, инжектит результат обратно в следующий
  turn LLM-а. Инспирация: OpenAI function-calling / Anthropic tool-use.
- [ ] **Sandbox backends** — опционально несколько:
  - `subprocess` (базовый, дефолт) — Python-скрипты в stripped env с
    timeout + file-system allowlist. Минимум зависимостей.
  - `docker` — изоляция через контейнер (требует docker-daemon у юзера).
  - `pyodide` (WASM) — безопасное исполнение пользовательского Python
    без нативного sandbox. Плюс: работает в браузере.
  - Hermes даёт 5 бэкендов (Docker, SSH, Singularity, Modal, local) —
    нам хватит 2-3.
- [ ] **Built-in tools подчинённые прайм-директиве**:
  - `calendar.fetch_today` — читать внешний iCal/Google если подключён
    (закрывает блокер «Календарь»). Feeds в plans.
  - `weather.now` — city из profile → погода → кормит morning briefing.
  - `rag.search` — vector search по state_graph + content graph + solved
    archive. Даёт «semantic recall» («помнишь я в прошлом месяце решал X?»).
  - `file.read` + `file.write` scoped — только в `workspaces/{ws}/` или
    явно выбранной папке. Для note-taking / scratch.
  - `code.run_snippet` — Python/JS в sandbox, возвращает stdout+result.
    Для расчётов: «посчитай BMI», «сколько я заработаю за год при X».
  - `hrv.calibrate` — инициализация baseline из 60с сессии (уже есть
    endpoint, но нужен UI через tool-call).
  - `plan.create_from_text` — parse «встречу с командой в среду 11:00»
    в plan-object через LLM. Уменьшает friction добавления events.
- [ ] **Permission model** — 3 уровня: `read` (безопасно, auto-allow),
  `write_self` (пишет в свои jsonl/graph — auto-allow), `external`
  (сеть, файлы вне workspace — требует явного confirm от юзера в UI
  за каждый вызов, как у Hermes). Permission записывается в profile.
- [ ] **UI: tool-call visualization в чате** — когда LLM вызывает tool,
  в card показываем «🛠 Использую `weather.now(Moscow)` …» → результат
  inline → продолжение текста ассистента. Прозрачно видно что происходит.
- [ ] **Pattern × tool-invocation loop** — когда pattern detector видит
  anomaly («3 четверга подряд пропустил завтрак»), соответствующий tool
  может предложить конкретное действие (`calendar.block_time("завтрак", time)`).
  Это замыкает patterns → intentional action без ручного вмешательства.

**Scope guardrail:** не делать generic agent framework (это уже есть у Hermes
и кучи других). Каждый tool отвечает на вопрос: «как это снижает sync_error?».
Если ответа нет — не добавляем.

Alternatives (если tool-use окажется overengineering): оставить reasoning-only
model, а execution делегировать Hermes'у через bridge (`baddle → hermes → real
world`). Baddle — мозг, Hermes — руки. Интересный вариант партнёрства а не
конкуренции.

## Экосистема

- [ ] **Layout** — d3/dagre/ELK вместо плоской линии для графа.
- [ ] **Экспорт** — PNG / SVG / markdown / Obsidian.
- [ ] **EXE-установщик** — PyInstaller.
- [ ] **Graph Store** — маркетплейс графов, review, рейтинги.
- [ ] **Извлечение графа из текста** — статья → граф.
- [ ] **Demo mode** — ускоренная симуляция «недели Baddle».
- [ ] **SSE/WebSocket** — push вместо polling для HRV/alerts (instant feel).
- [ ] **Telegram Mini App wrapper (Activity + Briefing + Alerts)** — взять
  прототип `Time Player/v2/index.html` (его цикл Начать/Следующая/Стоп +
  шаблоны + история) как основу TMA-клиента к Baddle. Бэкенд уже готов
  (/activity/* endpoints). На входе — `tg.initDataUnsafe.user.id` как
  namespace для multi-user в будущем. Что даёт: (а) OS-уведомления
  бесплатно через Telegram на phone+desktop даже при закрытом браузере
  (закрывает блокер «Desktop / system-tray notifications»), (б) мобильный
  ввод activity на ходу, (в) morning_briefing приходит как push. Scope:
  переписать фронт HTML → обёртка над теми же endpoints + WebApp.sendData
  для auth handshake. Backend-pairing: эндпоинт `/tma/link` с OTP.

## Архитектурно открытые (edge cases, не блокеры)

Не блокеры для daily use. Всплывут при scale'е или multi-user.

- [ ] **UserState global per-person** — один UserState на все workspaces.
  Если захочется разных `profile.food` для work vs personal — потребуется
  UserState per-workspace + context-switcher.


---

# ✅ Проверить что работает

Все тесты (narrative user-кейсы A-K + живые API-проверки по блокам) вынесены в отдельный файл → [TESTS.md](TESTS.md).

Кратко что там есть:
- **🧪 Пользовательские кейсы A-K** — end-to-end сценарии через UI с «→ Ожидаешь».
- **Проверка / красный флаг** на каждую реализованную фичу (HRV, neurochem, morning briefing, state_graph, decisive cycle, DMN, Scout, symbiosis, cross-graph, embedding-first, Horizon, REM, activity log, plans, checkins, chat commands и т.д.).
- Сквозной sanity check в конце.

Актуализируй TESTS.md когда добавляешь фичу: кейс + API-проверка + красный флаг.

