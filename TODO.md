# TODO

> Концепция, архитектура и научные основы → [README](README.md)
>
> Этот файл — что осталось сделать, в порядке приоритета.

---

## v1: стабилизация

- [ ] **Дубль эссе в финале** — модель повторяет введение при длинной генерации. Возможно continuation в collapse создаёт второй блок. Промпт "Do NOT repeat" добавлен, проверить
- [ ] **Novelty check оптимизация** — 30 embedding API-вызовов = медленно. Варианты: batch, Jaccard → embedding двухступенчато

---

## Целевая архитектура

```python
def tick(graph, horizon):
    horizon.update(surprise, gradient, novelty)
    phase = horizon.select_phase()    # policy weights → выбор фазы
    params = horizon.to_llm_params()  # precision → temperature/top_k/top_p

    if phase == "generate":  return brainstorm(graph, params)
    if phase == "merge":     return collapse_similar(graph, params)
    if phase == "elaborate": return elaborate(graph, params)
    if phase == "doubt":     return smartdc(graph, params)
```

Один tick, один horizon, четыре функции. Режим = preset. Текущий if/else в thinking.py — промежуточное состояние.

---

## ~~v1.5: CognitiveHorizon~~ ✅ реализован

`src/horizon.py` — адаптивный контроллер: precision → temperature/top_k, surprise → обратная связь, 4 состояния, 13 presets. Интегрирован в tick, autorun, UI overlay. Детали → [docs/horizon-design.md](docs/horizon-design.md)

---

## ~~v1.6: Pump (Накачка)~~ ✅ реализован

`src/pump_logic.py` — накачка облаков → 3 моста → авто-SmartDC на каждый → quality (lean/tension/balance). UI: контекстное меню "Pump to..." → клик → авто-верификация → ранжирование. Детали → [docs/pump-design.md](docs/pump-design.md)

Доработки на потом:
- [ ] Визуализация облаков на SVG (два hull расширяющихся навстречу)
- [ ] Pump в autorun для режима Разведка (когда будет реализован)

---

## v2: алгебра режимов

Реализация оставшихся 11 режимов из [README](README.md). Цель — один tick, 12 конфигов.

### Шаг 1: инфраструктура конфига

- [ ] **Mode config в goal-ноде** — dict с `(mode, primitive, strategy, goals, goal_type, fields)`:
  - `primitive`: `none / and / or / xor` (четыре примитива)
  - `strategy`: `unordered / seq / priority / balance` (для AND) или `comparative / dialectical` (для XOR) или `null`
  - `goal_type`: `finite / repeatable / open`
- [ ] **Goal-нода как structured object** — сейчас только text, нужны дополнительные поля
- [ ] **Stop condition framework** — функции `(graph_state) → bool | snapshot`. По типу цели:
  - finite: `confidence ≥ threshold` → RESOLVED
  - repeatable: `step_complete + trigger` → SCHEDULED
  - open: `diminishing_returns ИЛИ budget` → PARKED
- [ ] **Goal evaluation** — LLM-as-judge: "достигли цели или нет?", сравнение goal↔result

### Шаг 2: диспетчер tick

- [ ] **tick(config, graph)** — читает `primitive` + `strategy` и вызывает реализацию

Четыре примитива:
- [ ] **none** (Scout) — без целей, дивергентное блуждание
- [ ] **AND** — все цели должны быть verified
- [ ] **OR** — первая достигнутая завершает цикл
- [ ] **XOR** — выбрать ровно одну из множества

Стратегии обхода AND (четыре режима):
- [ ] **unordered** — Конструктор: любой порядок
- [ ] **SEQ** — Конвейер: по зависимостям
- [ ] **PRIORITY** — Каскад: по важности
- [ ] **BALANCE** — Весы: пропорциональная аллокация над нефинитными

Стратегии разрешения XOR (два режима):
- [ ] **comparative** — Турнир: сравнение независимых опций
- [ ] **dialectical** — Диспут: синтез противоречивых утверждений через SmartDC-on-graph

Варианты OR (по типу цели):
- [ ] **OR finite** — Гонка (первая цель завершает)
- [ ] **OR open** — Веер (уже работает как текущий цикл)

### Шаг 3: режимы single-goal по типу цели

- [ ] **Вектор** (finite) — один фокус, сходится к RESOLVED
- [ ] **Ритм** (repeatable) — heartbeat, snapshot evaluation, streak/trend
- [ ] **Горизонт** (open) — уже работает как текущий цикл

### Шаг 4: UI

- [ ] **Селектор режима** — 12 опций с подсказками
- [ ] **Динамическая форма** — поля ввода из `config.fields` под режим
- [ ] **Display целей** — goal-нода показывает структуру (список целей + оператор)
- [ ] **Snapshot для repeatable** — виджет streak/today/trend вместо sparkline сходимости

### Шаг 5: персистентность

- [ ] **State beyond session** — Ритм работает днями/неделями, Вектор — месяцами
- [ ] **History log** — timestamps, changes, confidence evolution
- [ ] **Автосохранение** — уже есть, нужна проверка для long-running режимов

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

## v5: автономность

- [ ] **Автономное блуждание** — ночной режим, целенаправленный обход, поиск мостов
- [ ] **`watchdog.py`** — проактивный помощник, уведомления по триггерам
- [ ] **Консолидация** — прунинг слабых веток, "забывание" как фича
- [ ] **Данные с девайсов** — HRV, сон, шаги → для режима Ритм

### Бесцелевое сознание (Default Mode Network для графа)

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

## v6: мета-режим А→Б

- [ ] **Автоопределение режима** — по промпту/намерению пользователя, без ручного селектора
- [ ] **Декомпозиция цели** — разбивка сложной задачи на подграфы разных режимов

---

## v7: экосистема и полировка

- [ ] **Layout** — d3/dagre/ELK вместо плоской линии
- [ ] **Тесты** — unit + integration
- [ ] **Параллельные API-запросы** — threading, ускорение цикла
- [ ] **Timeline player** — ⏮▶⏸⏭ по timestamps
- [ ] **Экспорт** — PNG / SVG / markdown / Obsidian
- [ ] **EXE-установщик** — PyInstaller, ~15-20 MB
- [ ] **Graph Store** — маркетплейс графов знаний
- [ ] **Git Verify** — MR для знаний, review, рейтинги
- [ ] **Извлечение графа из текста** — статья → граф

---

## Сделано, не тестировано

- **Промпты разнообразия** — think-промпт перечисляет 10 измерений (economic, social, technical...), new_idea подсказывает конкретные варианты. Окно existing 5→10
- **Novelty threshold в UI** — поле в Run settings, default 0.92, прокидывается в `/graph/think`
- **API-only переход** — удалён llama_cpp, server_backend, step, parallel. Всё через OpenAI API
- **Settings modal** — упрощён до API URL / key / model / embedding / ctx
- **Chat упрощение** — убраны SSE/Continue/Stop, ответ приходит одним чанком
- **Ask** — контекстное меню + Studio + detail panel, передаёт текст ноды в промпт
- **Generation Studio modal** — восстановлен, с режимом Ask
- **Вычистка кода** — graphTick, temporal рёбра, autorun handlers, ~1200 строк убрано
- **settings.json** — автосоздание с дефолтами при первом запуске
- **modes.py** — структура 12 режимов (`MODES` dict), роут `/modes`, `get_mode()`, `list_modes()`
- **Mode selector в UI** — dropdown в toolbar, заполняется из `/modes`, передаёт mode при создании goal
- **Goal-нода хранит mode_id** — записывается при создании, tick() читает из goal
- **Полный путь данных** — UI → goal-нода → tick() → (пока только horizon)
- **Mode selector redesign** — первый элемент в toolbar, крупный, с tooltip режима. Placeholder поля ввода меняется под режим. Mode хранится в graph meta + goal-ноде
