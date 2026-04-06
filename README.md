# baddle

> AHI Protocol — Augmented Human Intelligence.
> Не спрашивай у ИИ ответ — управляй процессом мышления.

**[English version →](README_EN.md)** · **[Установка и запуск →](SETUP.md)**

Граф мыслей с байесовской уверенностью, марковскими переходами и диалектическим синтезом. Задаёшь цель → система автоматически исследует тему, проверяет гипотезы, синтезирует результат. Локально через llama.cpp или через API. Бесплатно, на квантизированной 8B модели.

---

## Как это работает

1. Задаёшь **цель** (goal) — "Доказать что прокрастинация полезна"
2. Нажимаешь **🔄 Run**
3. Система автоматически:
   - Генерирует 10 гипотез
   - Собирает evidence для каждой
   - Проверяет диалектически (тезис/антитезис → синтез через embedding centroid)
   - Задаёт себе вопросы ("а что я упустил?")
   - Коллапсирует верифицированные кластеры
   - Повторяет цикл
4. Получаешь **структурированное эссе** с аргументацией

Реальный пример: "Доказать что материя не первична" → 51 шаг → три раунда Think/Verify/Collapse → эссе с аргументами из квантовой физики, философии сознания, эмерджентности.

---

## Граф мыслей

### Байесовская уверенность

Каждый узел имеет **confidence** (0–1). Определяется автоматически:

- **LLM классификация** — тип (hypothesis/fact/question/evidence/goal/action) + начальная уверенность одним вызовом
- **Auto-evidence** — при Elaborate дочерние узлы автоматически становятся evidence. LLM определяет supports/contradicts
- **Smart DC** — диалектическая проверка: тезис + антитезис + нейтраль → центроид в embedding-пространстве → confidence из cosine similarity
- **α/β модель** — supports vs contradicts, прогресс-бар, "Что меняет мнение?"

### Марковские переходы

Каждое ребро имеет **transition_prob**. Random Walk показывает куда ведёт мысль. Детектор ловушек. Хебб: часто используемые пути усиливаются.

### Временные связи

Узлы одной сессии связаны контекстуально (temporal links). Timestamp на каждом узле.

### Навигация A→B

Задаёшь goal → BFS находит кратчайший путь → система ведёт вдоль него. Exploration: если очевидное не работает → пробует менее очевидное. Trap avoidance.

---

## Автоматическое мышление

**Два режима (🔄 Run):**

| | Fast | Deep |
|---|---|---|
| Подход | По приоритетам: чинит слабое | По фазам: обрабатывает всё |
| Think | При < 3 гипотез | При < 5 гипотез, ×10 |
| Ask | 1 вопрос | 3 вопроса |
| META | При ≥ 3 verified | При ≥ 5 verified |

**Инструменты (оба режима):**
Think → Elaborate → Verify (Smart DC) → Ask → Rephrase → Expand → Collapse → META → Summary

**Фазовый маркер:** поле "Collapse at N" — после N шагов система начинает принудительное сжатие порциями по 5 узлов. Hard stop на 2N.

**Verify mode:** replace (заменяет узел синтезом) или expand (добавляет синтез как дочерний, оригинал остаётся).

**Output format:** essay / brief / list / none.

---

## Интерфейс

- **Topic + Add** — над графом, с выбором типа (auto/hypothesis/goal/fact/...)
- **Parameters** — сворачиваемая панель (thoughts, similarity, threshold, temp, top_k, seed, max tokens)
- **Кнопки**: Select / All / Collapse(badge) | Link / Flow / Time / Layout | Auto / Run▾ | Undo / Save / Load / Reset
- **Collapse** — dropdown: авто-кластеры + ручной выбор → Studio
- **Detail panel** — heatmap по токенам, confidence, тип, α/β, connected edges, Walk, Verify
- **Контекстное меню** — правый клик: Expand, Elaborate, Rephrase, Verify, Walk, Evidence, Chat, Edit, тип, Delete
- **Generation Studio** — универсальная модалка для всех генераций с пакетными вариантами

---

## Другие режимы

**step** — токен за токеном, распределение вероятностей, heatmap
**parallel** — два промпта параллельно + compare mode
**chat** — с context sidebar (→ Chat из графа, → Graph из чата)

---

## Настройки

- **Local / API / Hybrid** — генерация + embeddings раздельно
- **Embedding model** — отдельная модель (nomic-embed-text) для similarity и centroid
- **Heatmap** — настраиваемая шкала энтропии

> 💡 Qwen3-8B для генерации + nomic-embed-text для embeddings. Через LM Studio обе параллельно.

---

📄 [Видение и архитектура](VISION.md) · 📋 [TODO](TODO.md) · 📝 [Статья (взгляд AI)](Article/ARTICLE_AI_VIEW.md)
