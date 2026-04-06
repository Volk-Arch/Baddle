# TODO

> Видение, концепция и архитектура → [VISION.md](VISION.md)

---

## Рефакторинг

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

- Байесовская уверенность (confidence, α/β, 7 типов, auto-type, 6 типов связей)
- Марковские переходы (transition_prob, Random Walk, Хебб, детектор ловушек)
- Время (created_at/last_accessed, temporal links)
- Smart DC + автономность (тезис/антитезис/синтез → centroid, tick с фазами, Fast/Deep, BFS навигация)
- UI (grouped buttons, Run dropdown, Collapse panel, Generation Studio, контекстное меню)
- Инфраструктура (node objects, Chat↔Graph, API/Hybrid, Settings)
- Рефакторинг (src/, graph.py → 3 файла, index.html → HTML + CSS + 6 JS, Notion-тема, thinking.py)
