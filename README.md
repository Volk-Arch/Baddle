# baddle

> AHI Protocol — Augmented Human Intelligence. Не спрашивай у ИИ ответ — управляй процессом мышления.

**[English version →](README_EN.md)**

Граф мыслей с байесовской уверенностью, марковскими переходами и диалектическим синтезом. Локально через llama.cpp или через API. Не чат-бот — здесь вы разветвляете идеи, проверяете гипотезы, и система учится вместе с вами.

**[Установка и запуск →](SETUP.md)**

---

## Граф мыслей

Ключевой режим. Вводишь тему → модель генерирует пачку мыслей → между ними строятся связи → кластеры → коллапс → повтор.

**Два режима мышления:**
- **Дивергентное** — Think генерирует пачку идей, Expand ответвляет
- **Конвергентное** — Collapse синтезирует кластер, Elaborate углубляет

### Байесовская уверенность (Камень 1)

Каждый узел имеет **confidence** (0–1). Всё определяется автоматически через LLM:

- **Auto-type + auto-confidence** — LLM классифицирует текст (hypothesis/fact/question/evidence) и оценивает начальную уверенность одним вызовом. "Муравьи лучше воробьёв" → hypothesis 35%. "Земля круглая" → fact 95%
- **Auto-evidence** — при Expand/Elaborate дочерние узлы автоматически становятся evidence. LLM определяет supports/contradicts и силу. Confidence родителя обновляется по Байесу
- **α/β модель** — для hypothesis: α = сумма supports, β = сумма contradicts. Прогресс-бар + список evidence по силе ("Что меняет мнение?")
- **Ручной evidence** — кнопка "+ Evidence", выбор supports/contradicts, strength slider
- **Типы связей** — similarity (серые), supports (зелёные →), contradicts (красные ⇢), temporal (голубые), directed (фиолетовые →)

### Марковские переходы (Камень 2)

Каждое ребро имеет **transition_prob** — вероятность перехода. Нормализуется, directed edges получают бонус.

- **Random Walk** — кнопка 🚶 Walk: "куда ведёт мысль?" 50 симуляций, top-3 конечных точки
- **Детектор ловушек** — узлы с высоким входом и низким выходом → красная обводка
- **Хебб** — при навигации между узлами transition_prob усиливается. "Нейроны, которые активируются вместе, связываются"
- **Tooltip на рёбрах** — P-значения, similarity, тип связи

### Временные связи (Камень 3)

Каждый узел хранит `created_at` и `last_accessed`.

- **Temporal links** — автосвязи между узлами одной сессии (5 мин). Голубые, скрыты по умолчанию (кнопка ⏰ Time)
- **Timestamps** в detail panel

### Smart DC — диалектический синтез (Камень 4)

**Два режима автоматического мышления (🔄 Run):**

**Fast** — приоритетный. Идёт по списку проблем: слабое → чинит → следующее. Сходится как только может.

**Deep** — фазовый. Проходит ВСЕ узлы через ВСЕ фазы. Не останавливается пока каждый не обработан.

Оба используют одинаковые инструменты:
1. **Think** — генерация идей (10 за раз)
2. **Elaborate** — добавление evidence (α/β без изменения confidence)
3. **Smart DC (Verify)** — диалектическая проверка: тезис/антитезис/нейтраль → синтез → confidence из centroid distance (embeddings)
4. **Ask** — "почему я так думаю?" (question-node, вскрывает допущения)
5. **Rephrase** — переформулировать если evidence не помогает (max 1 per node)
6. **Expand** — ответвления для изолированных узлов
7. **Collapse** — синтез верифицированных кластеров
8. **META** — "что я упустил?" (ещё раунд Think после верификации)
9. **Summary** — финальный текст → linked to goal (essay/brief/list/none)

Общие механизмы: BFS к goal (кратчайший путь), exploration (если очевидное не работает → менее очевидное), trap avoidance (обход тупиков)
- **🔄 Run** — полный автоматический цикл: tick → действие → ... до stable. Настраиваемые шаги, stable threshold, output format (essay/brief/list/none). При старте без goal — предлагает задать цель. При stable — финальный документ (join всех мыслей + выводы) → linked to goal. Exploration: если лучший путь не работает — пробует менее очевидный
- **Типы: goal / action** — goal = целевое состояние (точка B), action = выполненное действие. Навигация A→B: система ведёт от текущего состояния к цели. Goal автоматически связывается со всеми hypothesis
- **Контекстное меню** — правый клик: Expand, Elaborate, Rephrase, Verify, Walk, Evidence, Chat, Edit, тип узла, Delete

### Generation Studio

Универсальная модалка: Rephrase, Elaborate, Expand, Collapse, Freeform. Пакетная генерация N вариантов, сравнение, Apply.

### Интерфейс графа

- Правый клик → контекстное меню, drag → перетаскивание, scroll → зум, drag фона → pan
- Link mode, Undo (Ctrl+Z), Delete, Esc
- → Flow / свободный граф, ⟳ Layout, ↓ Save / ↑ Load
- threshold — пересчёт связей в реальном времени
- Список мыслей с номерами, типами [H][E][F][Q], сортировка по кластерам
- Detail panel: heatmap по токенам, confidence slider, source tracking, connected edges с P-значениями

---

## Другие режимы

### `step` — пошаговая генерация

Один токен за раз. Распределение вероятностей (top-10), редактирование текста, heatmap.

### `parallel` — два промпта / compare

Два промпта параллельно, каждый с temp/top_k. Чекбокс **compare** — один промпт, два конфига, badge расхождения.

### `chat` — разговор с моделью

Chat template (ChatML / Jinja2). Continue, heatmap.
**Context sidebar** — контекст из графа (→ Chat), из ответов (→ ctx), вручную. **→ graph** — текст из чата в граф.

---

## Общие возможности

- **Heatmap уверенности** — токены окрашены по энтропии, настраиваемая шкала
- **Роли** — пресеты из `roles.json`, переключатель EN/RU
- **Settings** — Local / API / Hybrid, подгрузка моделей из API, горячая смена, `settings.json`
- **Embedding model** — отдельная модель для embeddings (similarity + Smart DC centroid). В Settings dropdown из доступных API/local моделей
- **Similarity** — Embedding / Jaccard / Off, auto-fallback

> 💡 **Рекомендация**: основная модель (Qwen3-8B) для генерации + отдельная embedding-модель (nomic-embed-text) для similarity и Smart DC centroid. Через LM Studio обе работают параллельно без конфликтов.

---

📄 [Видение и архитектура](VISION.md) · 📋 [TODO](TODO.md) · 📝 [Статья (взгляд AI)](Article/ARTICLE_AI_VIEW.md)
