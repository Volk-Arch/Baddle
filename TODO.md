# TODO

> Видение, концепция и архитектура → [VISION.md](VISION.md)

---

## Рефакторинг — привести код в порядок

### Структура кода
- [ ] **Разбить `graph.py` (2045 строк)** — вынести промпты (`_PROMPTS`) в `prompts.py`/JSON, роуты в `graph_routes.py`, оставить чистую логику графа
- [ ] **Разбить `index.html` (4577 строк)** — вынести JS в отдельные модули (graph-renderer, settings-panel, tabs), CSS в отдельный файл
- [ ] **Вынести tick()/thinking в `thinking.py`** (уже было в UX, переношу сюда)

### Надёжность
- [ ] **Thread safety** — добавить `threading.Lock` на мутирующие операции графа (Flask многопоточный, глобальное состояние = гонка)
- [ ] **Retry + backoff в `api_backend.py`** — HTTP-запросы без retry при timeout/5xx дают непонятные ошибки
- [ ] **Валидация JSON от LLM** — `_auto_type_and_confidence()` тихо фоллбэчит на `thought/0.5`, стоит логировать и предупреждать

### Безопасность
- [ ] **`api_key` из `settings.json` → `.env`** — ключ в открытом виде, рискованно при публикации
- [ ] **`settings.json` в `.gitignore`** — персональные настройки не должны попадать в репо

### Тесты
- [ ] **Базовые unit-тесты** — покрыть детерминированную логику: `_bayesian_update()`, `cosine_similarity()`, `_compute_edges()` с фиксированными эмбеддингами
- [ ] **Интеграционные тесты роутов** — Flask test client, моки для LLM

### Мелочи
- [ ] **`setup.py` — предупреждение для не-Windows** — сейчас молча не качает llama-server на Linux/Mac
- [ ] **Увеличить n_ctx (8192+)** — summary обрезается на 4096 (переношу из UX)

---

## Следующие шаги

### Масштаб
- [ ] **Множественные графы** — вкладки, отдельный save/load, теги/слои
- [ ] **Cross-graph edges** — связи между графами. `serendipity_engine`
- [ ] **JSONL storage** — `nodes.jsonl` + `edges.jsonl` + `meta.json`, lazy load

### Поиск и память
- [ ] Семантический + контекстный поиск (embedding + confidence + время)
- [ ] Ассоциативное воспоминание — прямое (по смыслу) и непрямое (паттерны)
- [ ] Распространение активации — "всплеск" в графе
- [ ] Режим "Время" — слайдер эволюции графа

### Автономность
- [ ] **Автономное блуждание** — ночной режим, целенаправленный, поиск мостов
- [ ] **`watchdog.py`** — проактивный помощник
- [ ] **`tie_breaker`** — паралич выбора → марковские последствия
- [ ] Консолидация — сжатие слабых веток, прунинг. Забывание как фича

### UX
- [ ] Auto-save после каждого действия
- [ ] Compare hypotheses — 2 hypothesis рядом, α/β, Марков

### Экосистема
- [ ] EXE-установщик
- [ ] Экспорт/импорт (PNG/SVG/markdown/Obsidian)
- [ ] Graph Store + Git Verify
- [ ] Данные с девайсов (HRV/сон)

### Полезные фичи
- [ ] 3D граф (three.js)
- [ ] Constraint-based layout (d3/dagre/ELK)
- [ ] Параллельные API-запросы (3-6x ускорение)
- [ ] Извлечение графа из текста (статья → граф)
- [ ] REST API

---

## Done

**Камень 1 — Байесовская уверенность:** confidence 0-1, α/β модель, 7 типов узлов (thought/hypothesis/evidence/fact/question/goal/action), auto-type + auto-confidence через LLM, auto-evidence relation через LLM, Байесовское обновление, 6 типов связей (similarity/supports/contradicts/temporal/directed/manual), embedding model в Settings

**Камень 2 — Марковские переходы:** transition_prob на рёбрах, нормализация, Random Walk (no-backtrack, top-3), детектор ловушек, Хебб, tooltip с P-значениями

**Камень 3 — Время:** created_at/last_accessed, temporal links (5 мин), timestamps в detail panel

**Камень 4 — Smart DC + автономность:** диалектический синтез (тезис/антитезис/нейтраль → centroid → confidence), Verify + рекурсия, tick() с фазами (EXPLORE→DEEPEN→VERIFY→META→SYNTHESIZE), два режима (Fast/Deep), навигация A→B (BFS + exploration + trap avoidance), auto-run с фазовым маркером (collapse at N), batch collapse по 5, context overflow protection

**UI:** topic над графом, grouped buttons (Select/Collapse/View/Think/File), Run dropdown (steps/stable/mode/verify/output), unified Collapse panel, Goal-rooted tree, verify replace/expand mode, Generation Studio, контекстное меню (10 действий)

**Инфраструктура:** node objects, Chat↔Graph мост, API/Hybrid mode, Settings с embedding model, code review, auto-questions, exploration tracking
