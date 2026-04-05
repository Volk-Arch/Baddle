# TODO

> Видение, концепция и архитектура → [VISION.md](VISION.md)

---

## Критический путь

### Камень 3 (остаток)
- [ ] Режим "Время" — слайдер эволюции графа
- [ ] Ассоциативное воспоминание — прямое (по смыслу) и непрямое (паттерны)

---

### Камень 4 (остаток)
- [ ] **Автономное блуждание** — ночной режим, целенаправленный, поиск мостов
- [ ] **`serendipity_engine`** — cross-graph walk, неожиданные связи
- [ ] Консолидация — сжатие слабых веток, прунинг. Забывание как фича

---

### Камень 5: множественные графы
- [ ] Вкладки, отдельный save/load, теги/слои
- [ ] Cross-graph edges
- [ ] Данные с девайсов — HRV/сон как граф-слой

---

### Камень 6: JSONL storage
- [ ] `nodes.jsonl` + `edges.jsonl` + `meta.json`, папка `graphs/`
- [ ] Lazy load, git-friendly

---

### Камень 7: Поиск
- [ ] Семантический + контекстный поиск (embedding + confidence + время)
- [ ] Распространение активации — "всплеск" в графе
- [ ] **`watchdog.py`** — проактивный помощник
- [ ] **`tie_breaker`** — паралич выбора → марковские последствия

---

### Камень 8: Экосистема
- [ ] EXE-установщик
- [ ] Экспорт/импорт (PNG/SVG/markdown/Obsidian)
- [ ] Graph Store + Git Verify

---

## UX

- [ ] Quick add от выделенного узла (дочерний с типом)
- [ ] Вынести tick()/thinking в отдельный `thinking.py`
- [ ] Увеличить context window (n_ctx=8192/16384) — финальный summary обрезается на 4096
- [ ] Умный промпт для summary — top-N по confidence вместо всех узлов
- [ ] Chunked summary — collapse по частям → collapse коллапсов
- [ ] **Auto-save** — автосохранение после каждого действия
- [ ] **Compare hypotheses** — 2 hypothesis рядом, α/β, Марков

## Полезные фичи

- [ ] 3D граф (three.js, 100+ узлов)
- [ ] Constraint-based layout (d3/dagre/ELK)
- [ ] Навигация стрелками
- [ ] Параллельные API-запросы (3-6x ускорение)
- [ ] Пересчёт ветки с новыми вводными
- [ ] Извлечение графа из текста (статья → граф)
- [ ] Хранение документов в узле (PDF/URL)
- [ ] Кластеризация (spectral/DBSCAN)
- [ ] REST API

---

## Done

- [x] **Камень 1:** Confidence, α/β, типизация узлов (thought/hypothesis/evidence/fact/question/goal/action), auto-type + auto-confidence через LLM, auto-evidence relation через LLM, Байесовское обновление, типы связей (similarity/supports/contradicts/temporal), embedding model в Settings
- [x] **Камень 2:** transition_prob, нормализация, directed bonus, Random Walk (no-backtrack, top-3), детектор ловушек, Хебб, tooltip на рёбрах
- [x] **Камень 3:** created_at/last_accessed, temporal links (5 мин, ⏰ Time), timestamps в detail panel
- [x] **Камень 4:** Smart DC (тезис/антитезис/нейтраль → синтез), centroid confidence через embeddings, ⚡ Verify + Accept/Add, триггер, рекурсия (↻ Deepen), tick() (🧠 Auto), навигация A→B (Марков mini-walks, reach%, exploration vs exploitation, trap avoidance), 🔄 Run (auto-cycle, настраиваемые шаги + stable threshold, финальный summary с keep mode → goal), rephrase в auto-run, контекстное меню
- [x] Quick add с типом (dropdown goal/action/...), auto-run, auto-goal prompt, summary при stable, output format (essay/brief/list/none)
- [x] Auto-questions (Ask — "почему я так думаю?", question-node вскрывает допущения)
- [x] Exploration vs exploitation (tried tracking, fallback на менее очевидные пути)
- [x] Настраиваемый stable threshold, skip intermediate collapse, финальный join + conclusions
- [x] Структура данных — node objects, связи и мета отдельно
- [x] Generation Studio — rephrase/elaborate/expand/collapse/freeform, пакетная генерация
- [x] Chat↔Graph мост, context sidebar
- [x] API/Hybrid mode, Settings, горячая смена модели
- [x] Объединение parallel/compare, save/load, undo, flow/free layout
- [x] Code review: imports top-level, _bayesian_update helper, dead code removed, null checks, deque BFS, promptWrap fix, .catch() на fetch
