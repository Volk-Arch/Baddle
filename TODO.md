# TODO

> Видение, концепция и архитектура → [VISION.md](VISION.md)

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
- [ ] Вынести tick()/thinking в отдельный `thinking.py`
- [ ] Увеличить context window (n_ctx=8192+) — summary обрезается на 4096
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
