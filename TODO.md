# TODO

> Концепция, 12 режимов и финальное видение → [README](README.md) и [VISION](VISION.md)
>
> Этот файл — что осталось сделать, в порядке приоритета.

---

## v1: доработки текущего режима (Горизонт/Веер)

Сейчас работает исследовательский цикл (один режим). Нужно довести его до production-качества.

- [ ] **Тюнинг novelty threshold** — 0.92 может быть слишком грубо, проверить на разных темах
- [ ] **Промпты** — усилить разнообразие начальных идей (сейчас все начинаются одинаково)
- [ ] **Параллельные API-запросы** — 3-6x ускорение цикла
- [ ] **Layout** — d3/dagre/ELK вместо плоской линии
- [ ] **Timeline player** — ⏮▶⏸⏭ по timestamps (вместо удалённой Time)
- [ ] **Тесты** — unit (`_bayesian_update`, `cosine_similarity`, `_compute_edges`) + integration (Flask + моки)
- [ ] **Экспорт** — PNG / SVG / markdown / Obsidian
- [ ] **EXE-установщик**

### Chat mode

Сейчас chat отдаёт ответ одним чанком (API в `api_chat_completion` не streaming).
Это работает, но теряется ощущение живой печати.

- [ ] **Настоящий streaming** — переписать `api_chat_completion` с поддержкой `stream: true`,
      использовать SSE от OpenAI-совместимого API и пробрасывать токены через Flask SSE-relay
- [ ] **Continue** — сейчас работает через append assistant-сообщения, но модели не всегда
      понимают что надо продолжить. Проверить на практике, при необходимости — добавить
      инструкцию "continue the previous response"
- [ ] **Heatmap в UI** — `toks` и `ents` приходят одним чанком в конце, проверить что
      frontend их отрисовывает

### Удалённые модули (v2 — если понадобятся)

Step и Parallel удалены при переходе на API-only. При необходимости можно восстановить:
- **Step** — чанковая генерация через API с logprobs для heatmap и top-10 кандидатов.
  Полезно как демо "где модель уверена, где гадает"
- **Parallel** — два параллельных запроса с side-by-side выводом. Полезно для A/B
  сравнения параметров или промптов

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

---

## v6: мета-режим А→Б

- [ ] **Автоопределение режима** — по промпту/намерению пользователя, без ручного селектора
- [ ] **Декомпозиция цели** — разбивка сложной задачи на подграфы разных режимов

---

## v7: экосистема

- [ ] **Graph Store** — маркетплейс графов знаний
- [ ] **Git Verify** — MR для знаний, review, рейтинги
- [ ] **Baddle Desktop** — EXE с локальным LLM
- [ ] **Извлечение графа из текста** — статья → граф

---

## Done

### Текущий цикл мышления
- [x] Четырёхфазный цикл: generate → merge → elaborate → doubt → meta
- [x] SmartDC (thesis/antithesis/synthesis через embedding centroid)
- [x] Novelty check при генерации (embedding similarity)
- [x] Lineage tracking (`collapsed_from`) — не перемалывает одно и то же
- [x] Infinite mode с естественной сходимостью
- [x] Convergence sparkline в оверлее Run

### Настройки и UX
- [x] Configurable: start ideas, depth, essay tokens, stable threshold
- [x] Batched essay (пирамидальный синтез для больших графов)
- [x] Live-метрики: Step / Phase / Hyp / Verified / Avg + sparkline

### Инструменты
- [x] Ask как ручной инструмент (контекстное меню + Studio + detail panel)
- [x] Generation Studio с режимами rephrase/elaborate/expand/collapse/ask/freeform

### Чистка
- [x] Вычистка мёртвого кода (~330 строк): graphTick, temporal, autorun handlers
- [x] Убрана кнопка Time и temporal-рёбра (timestamps есть в нодах)
- [x] Generation Studio modal восстановлен

### API-only переход
- [x] Удалён `server_backend.py` (llama-server subprocess)
- [x] `main.py` 537→40 строк (удалены llama_cpp wrapper, batch decode, sampling)
- [x] `api_backend.py` упрощён: только API, без local/hybrid режимов
- [x] `chat.py` переписан под API
- [x] `ui.py` 251→140 строк (убраны CLI args модели, --server, --gpu-layers, dual/to-step)
- [x] `setup.py` 242→44 строки (только `pip install flask numpy`)
- [x] `SETUP.md` переписан под LM Studio вместо llama.cpp сборки
- [x] Убрана зависимость `llama-cpp-python` (~85 MB + CUDA сборка)
- [x] Удалены Step и Parallel режимы (`src/step.py`, `src/parallel.py`, `static/js/step.js`, `static/js/parallel.js`) и их UI
- [x] Settings modal упрощён: только API URL / key / chat model / embedding model / ctx
- [x] Итого: -1200 строк Python/JS, -5 зависимостей, установка за секунды вместо минут
