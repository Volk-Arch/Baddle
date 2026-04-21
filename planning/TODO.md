# TODO

## 🎯 Прайм-директива

**`sync_error = d(model_prediction, user_action)` — единственная метрика**, которая оценивает ценность любого пункта ниже. Если пункт не снижает рассинхрон с конкретным пользователем — низкий приоритет, даже если архитектурно красиво.

**Измерение:** `sync_error_ema_slow` пишется раз в час в [`data/prime_directive.jsonl`](../src/prime_directive.py). Endpoint `GET /assist/prime-directive?window_days=30&daily=1` даёт aggregate + trend verdict. Через 2 мес use сравнить mean(first third) vs mean(last third) — если `trend_slow_delta < 0`, механики работают. Прайм-директива валидирует сама себя.

Подробнее о трёх столбах и принципах → [README](../README.md).

---

## ✅ Закрытое ядро (на 2026-04-23)

1. **Resonance protocol — 5 механик.** Hebbian decay ([episodic-memory § Consolidation](../docs/episodic-memory.md#consolidation--забывание-как-фича)) · Adaptive idle по silence/imbalance ([alerts-and-cycles.md](../docs/alerts-and-cycles.md)) · Active sync-seeking · System-burnout от persistent desync (merged) · Action Memory ([action-memory-design.md](../docs/action-memory-design.md)).
2. **Friston loop** — 2 предиктора + 5 PE-каналов + агрегация → `ProtectiveFreeze.imbalance_pressure`. Single source of truth: [docs/friston-loop.md](../docs/friston-loop.md).
3. **Прайм-директива измерима.** `sync_error_ema_{fast,slow}` + per-channel decomposition (`user_imbalance` / `self_imbalance` / `agency_gap` / `hrv_surprise`) + `trend_verdict` endpoint.
4. **PE attribution (ex-OQ #6).** `UserState.attribution` / `attribution_magnitude` / `attribution_signed` — какая ось доминирует в 3D surprise.
5. **User surprise detection (ex-OQ #7, MVP A+B+C).** [`src/surprise_detector.py`](../src/surprise_detector.py) — HRV σ-threshold + text markers + LLM fallback (borderline, cached). `UserState.apply_surprise_boost(3)` → fast-decay expectation. Integration в `cognitive_loop._check_user_surprise`.

**Валидация:** ждём 2 мес реального use. До этого — не тратим сессии на расширение scoring / добавление новых feeders. Данные решат что работает.

---

## 🔬 Кристаллизация (активный workstream)

После friston-loop.md (2026-04-23) пришло время уплотнить код и docs: убрать параллельные структуры (EMA scattered по 12+ местам, 4 предиктора в UserState, stale timestamps в docs). Подробный план с чекбоксами и ссылками на file:line → **[crystallization-plan.md](crystallization-plan.md)**.

Порядок: docs refresh (Stage 1, ~60 мин) → `EMA` class (Stage 2, ~2ч) → `Predictor` base (Stage 3, ~2.5ч) → опц. `Accumulator` (Stage 4).

---

## 📌 Ближайшее (≈ несколько часов каждое)

Эти в любой момент можно взять в сессию, независимы друг от друга:

- [ ] **README_EN.md** — английская версия корневого README для международной GitHub-аудитории. Без дословного перевода. ~1ч.
- [ ] **Desktop / system-tray notifications.** Alerts работают только пока вкладка браузера открыта. Закрыл — morning briefing / DMN-мосты / night_cycle summary уходят в пустоту. MVP: `pystray` + `plyer` — иконка в трее + OS toast. Альтернатива: Telegram Mini App (см. Экосистема) закрывает ту же проблему бесплатно. ~2-3ч.
- [ ] **Alerts coverage.** Test harness `/debug/alerts/trigger-all` показал что 10 check'ов из 21 silent_ok на demo-данных. Пройтись по каждому, покрыть пустые условия или пометить как «not applicable on empty state». ~2ч.
- [ ] **Patterns × intent_router auto-abandon.** Если детектор нашёл паттерн но юзер молчит 2+ недели — убирать предложение чтобы не накапливались старые alerts. ~1ч.
- [ ] **«Попробовать 1 неделю» кнопка в suggestion** — временная рекурсивная цель, через неделю auto-abandon если не помогает. Мягче чем «Да, создать». ~1-2ч.
- [ ] **`plan.create_from_text`** — «встречу в среду 11:00» → plan-object через LLM. Естественный ввод vs форма. ~2ч.
- [ ] **Предложение еды без tool-use** ([mockup.html](../docs/mockup.html)). Реактивное: юзер пишет «что поесть?» → 3 варианта из `profile.food.preferences + constraints` через LLM. Проактивное в morning briefing: если pattern-detector видит «пропускаешь завтрак по четвергам → energy crash к 14:00» — секция «Завтрак» с обоснованием паттерна. Реализация: mode в `suggestions.py` + pattern в `patterns.py`. ~3ч.
- [ ] **META-вопросы — ночная генерация «что ты не заметил»** ([mockup.html](../docs/mockup.html) строка 172). Когда два scout-моста обнаруживают **общий абстрактный паттерн** («single point of failure» в auth-модуле И в energy-понедельниках) — сгенерить **вопрос**: «какие ещё SPoF у тебя есть?». Отдельная секция в briefing. Зависит от того что scout реально находит мосты (граф должен быть нетривиальный). ~2-3ч.
- [ ] **Специализированные card-рендеры для `fan` / `rhythm`.** Сейчас оба падают в `deep_research` card. `fan` (Мозговой штурм) = generate-list с ranging по новизне; `rhythm` (Привычка) = habit-tracker view с streak + next-occurrence. ~3ч.

---

## 🧬 Сенсоры и устройства

MVP sensor stream работает ([hrv-design.md](../docs/hrv-design.md#sensor-stream-multi-source-polymorphism)): `SensorReading{ts, source, kind, metrics, confidence}` + `latest_hrv_aggregate(window_s)` + симулятор. Осталось подключить реальные источники и довести миграцию.

### Миграция UserState на stream (блокирует реальные адаптеры)

- [ ] **UserState → sensor stream.** Сейчас `UserState.update_from_hrv` кормится через `hrv_manager.get_baddle_state()`. Мигрировать на `stream.latest_hrv_aggregate()` + `stream.recent_activity()` — тогда **любой** источник влияет на UserState напрямую без мостика через HRVManager. ~15 call-sites. Средняя сложность.

### Адаптеры

- [ ] **`PolarH10Adapter`** — `bleak` + `bleakheart`, async BLE loop. Push `rr_ms` + accelerometer. Каждые 15с агрегат через `calculate_hrv_metrics` → `push_hrv_snapshot`. Требует физ. устройство. ~2-3ч.
- [ ] **Apple Watch.** HealthKit XML export (one-shot история) + iOS shortcut → локальный HTTP endpoint (continuous). Confidence 0.8.
- [ ] **Oura Ring.** REST v2 API, personal token, polling утреннего sleep+HRV snapshot. Confidence 0.9.
- [ ] **Garmin.** `garminconnect` pip, HR-stream + stress + body battery. Требует login.

### Калибровка и визуализация

- [ ] **`data/sensor_baselines.json`** — per-source calibration (chest-strap vs optical — разные шкалы).
- [ ] **Conflict resolution** — при расхождении одновременных источников > threshold log'ировать.
- [ ] **Polar H10 cone viz с θ/φ** — polyvagal двухпараметрическая визуализация, когда реальный сенсор подключён.

---

## 🛠 Tool-use — слой действий

Baddle сейчас умеет думать и трекать. Слой execution (делать вещи в мире: календарь / погода / интернет / файлы) отсутствует.

**Scope guardrail:** не делать generic agent framework. Каждый tool отвечает на «как это снижает sync_error?». Нет ответа — не добавляем.

### Инфраструктура

- [ ] **`/tool/run` endpoint + registry** — whitelist tools с явными schema (`{name, description, input_schema, output_schema, permission_level}`). LLM возвращает `tool_call` → бэкенд проверяет permission → выполняет → инжектит результат в следующий turn.
- [ ] **Permission model** — 3 уровня: `read` (auto), `write_self` (свой workspace — auto), `external` (сеть / вне workspace — confirm в UI на каждый вызов).
- [ ] **UI: tool-call visualization** — в card «🛠 `weather.now(Moscow)` …» → результат inline → продолжение текста. Прозрачно что происходит.
- [ ] **Pattern × tool-invocation loop** — pattern detector видит anomaly → соответствующий tool предлагает действие (`calendar.block_time("завтрак", time)`).

### Built-in tools

- [ ] **`rag.search`** — vector search по state_graph + content graph + solved archive. «Помнишь я решал X?». Без интернета.
- [ ] **`calendar.fetch_today`** — iCal/Google → plans. Утренний брифинг получает реальный день.
- [ ] **`weather.now`** — feeds morning briefing + outdoor-активности + одежда.
- [ ] **`file.read` + `file.write` scoped** — в `workspaces/{ws}/` или явно выбранной папке.
- [ ] **`hrv.calibrate`** — 60с baseline session.
- [ ] **Интернет поиск** (`web.search`) — для фактчекинга в Research/Debate режимах.
- [ ] **Reasoning-backend для heavy modes** (`dispute` / `tournament` / `smartdc`). Reasoning-модели делают thinking внутри одного вызова. Гибрид: если в settings доступна reasoning-модель — тяжёлые режимы роутятся туда как single-shot. Лёгкие режимы остаются на local 8B. ~1 день.
- [ ] **Per-этап выбор модели** — local 8B для generate, cloud для doubt/essay. Инфраструктура роутинга, не tool-use.

### Опционально (nice-to-have для tool-layer)

- [ ] **Продукты / рецепты inventory** — для expiry-tracking.
- [ ] **Гардероб** — что есть + погода + календарь → outfit через связку tool'ов.
- [ ] **Браузер-расширение** — impulse guard (покупки), emotion guard (письма). Input канал, не tool.

---

## 📈 Экосистема / scale

- [ ] **Layout** — d3/dagre/ELK вместо плоской линии для графа.
- [ ] **Экспорт** — PNG / SVG / markdown / Obsidian.
- [ ] **EXE-установщик** — PyInstaller.
- [ ] **Graph Store** — маркетплейс графов, review, рейтинги.
- [ ] **Извлечение графа из текста** — статья → граф.
- [ ] **SSE/WebSocket** — push вместо polling для HRV/alerts (instant feel).
- [ ] **Telegram Mini App.**

---

## 🔬 Ждём данных (пассивно — Baddle собирает сама)

Эти пункты не требуют кода сейчас — нужны недели-месяцы реальных данных чтобы иметь base rate для решения.

- [ ] **Прайм-директива trend_slow_delta.** 2 мес use → посмотреть `GET /assist/prime-directive?window_days=60`. Если `< -0.02` — резонансный протокол работает. Если `≈ 0` — пересматриваем механики. Если `> 0` — что-то важное упущено.
- [ ] **Agency (OQ #2) включать в sync_error?** Через 2-3 недели измерений сравнить: коррелирует ли с trend?  Если да — расширить `vector()` до 4D. Если шумит — убрать.
- [ ] **Доминирующий PE-канал.** Через 2 мес смотреть какие каналы двигают `imbalance_pressure`: `mean_pe_user` / `_self` / `_agency` / `_hrv`. Если один всегда 0 — убирать; если один доминирует — проверять корректен ли он.
- [ ] **Counterfactual honesty для sync-seeking.** Намеренно не действовать в 5–10% случаев для baseline recovery-time. Нужна минимум месяц sync-seeking истории.

---

## 🤔 Открытые архитектурные вопросы

Архитектурные вопросы без очевидного ответа — [open-questions.md](open-questions.md). Когда направление выбрано → задача сюда в TODO.

Всё ещё открыто:
- **#1 Personal capacity** — Bayesian online estimation. Отложено ≥ 1 мес use (без реальных данных — гадание).
- **#2 Agency как 5-я ось** — 🔬 в процессе measurements. Решение — при анализе 2-месячных данных (см. выше).
- **#5 Workspace attractors** — аттракторы в neurochem-пространстве для разных контекстов. Следующее архитектурное.

Resolved (перенесено в «Закрытое ядро» или merged):
- #3 Валентность → merged в [Action Memory](../docs/action-memory-design.md).
- #4 Recovery routes → merged туда же.
- #6 PE как вектор → 3D surprise + attribution.
- #7 Surprise detection → `src/surprise_detector.py`.

---

## 🏗 Edge cases (может всплыть при расширении)

- [ ] **UserState global per-person.** Один UserState на все workspaces. Если захочется разных `profile.food` для work vs personal — потребуется UserState per-workspace + context-switcher.
- [ ] **Attention-weighted PE.** Сейчас 4 канала равновесно normalized и max'ом. Можно ввести precision-weights: каналы с низкой precision (шумные) получают меньший вес при агрегации. Классический Фристон. Не блокер, но если `mean_pe_hrv` через 2 мес окажется гораздо шумнее `mean_pe_user` — precision-gating решит.

---

## Опциональное (не приоритет)

Полезно но не срочно — делаем если появится свободный раунд:

- [ ] **Chat-timeline view в Lab UI** — рендер `/graph/actions-timeline` как листающийся список actions с click → focus в графе.
- [ ] **Расширение `score_action_candidates`** на другие proactive checks — когда через месяц станет видно где реальный разброс outcomes.
- [ ] **Dialog pivot detection** для surprise detector — резкое изменение темы через embedding distance между последовательными user-сообщениями.

---
