# UI Split — Baddle (assistant) vs Graph Lab

> Сейчас всё в одном `templates/index.html` с табами `assist / graph / chat`.
> Файл ~35 KB, много JS-зависимостей между табами. По мере роста
> функциональности — ассистент должен жить отдельно от dev-tools для
> графа. Этот документ — **план разделения**, не реализация.

## Зачем разделять

**Два разных юзера одной системы:**

| Баддл (daily-use) | Graph Lab (research/dev) |
|-------------------|--------------------------|
| Ежедневная работа с чат-ассистентом | Визуализация мышления |
| Привычки, цели, отчёты, HRV | Ноды, рёбра, tick, modes |
| Автоматика (router, suggestions) | Ручная manipulation графа |
| Должен быть **простым** — обычный человек | Может быть сложным — dev/researcher |

### Признаки что разделение пора делать

1. В `index.html` один `<div class="baddle-sub-page">` скрыт другими — разные модели интеракции
2. JS в `assistant.js` (4000+ строк) обслуживает и graph-view, и chat-view, и tasks
3. Новому юзеру Baddle не нужен граф — но он видит вкладку и путается
4. Dev'у нужен раздельный чат для дебага graph без обращений к ассистенту

---

## Целевая архитектура

### Вариант A — Два endpoint'а на одном backend

```
/                       → Baddle (assistant) UI
/lab                    → Graph Lab UI
/api/*                  → один Flask backend (существующий)
```

Обе страницы дёргают **один** Flask сервер через `/api/*` endpoints.
Разделение только на уровне HTML/JS/CSS.

**Плюсы:**
- Один backend, никакой дублировки
- Простой deploy (один процесс)
- Easy to switch между UIs (кнопка-ссылка)

**Минусы:**
- Слой `workspace / goals / HRV` общий — dev может случайно испортить
  product data
- Один requirements.txt

### Вариант B — Два Flask-приложения

```
ui.py          → Baddle only (port 7860)
ui_lab.py      → Graph Lab (port 7861)
shared: src/   → оба читают тот же data/
```

**Плюсы:**
- Полная изоляция JS/CSS/шаблонов
- Разные permissions — можно в lab отключить write
- Можно hosting для Baddle на облаке без lab

**Минусы:**
- Два процесса
- Нужно согласовывать data-races (оба пишут в data/)

### Вариант C — Один UI + dev-mode toggle

```
/?mode=user   (default)   → скрывает graph tab
/?mode=dev               → показывает всё
```

**Плюсы:**
- Минимальные изменения (CSS hide)
- Мгновенное переключение для power-user

**Минусы:**
- Не решает проблему сложности JS
- Юзер видит dev-чек-бокс, понимает что-то есть

---

## Рекомендация: Вариант A + структурный рефакторинг JS

Прагматично:
1. **Два HTML шаблона** (`templates/index.html` = Baddle, `templates/lab.html` = Graph Lab) с разными JS bundle'ами
2. **Один Flask backend** (как сейчас)
3. **Общий /api/* namespace** — переместить все endpoints из `graph_routes.py` и `assistant.py` под префикс `/api/` (опционально, для явности)
4. **JS разнесён по доменам:**
   - `static/js/baddle/` — assistant.js, settings, chat, activity, plans, goals modal, workspace, HRV sim
   - `static/js/lab/` — graph.js, tick controls, mode chip, graph layouts, node manipulation
   - `static/js/shared/` — helpers, API client, esc-helpers

### Файл-перенос (primer)

| Сейчас | После split |
|--------|-------------|
| `templates/index.html` (35 KB) | `templates/index.html` (Baddle, ~18 KB) + `templates/lab.html` (~10 KB) |
| `static/js/assistant.js` (4000 строк) | `static/js/baddle/assistant.js` (2500) + `static/js/baddle/tasks.js` (700) + `static/js/baddle/goals.js` (500) + `static/js/shared/utils.js` (200) |
| `static/js/graph.js` | `static/js/lab/graph.js` |
| `static/js/modes.js` | остаётся; используется обоими |
| `static/js/settings.js` | остаётся |
| `static/js/chat.js` | становится частью `static/js/lab/` (чат-режим графа — dev feature) |

### Роутинг (ui.py)

```python
@app.route("/")
def baddle_home():
    return render_template("index.html")   # Baddle assistant UI

@app.route("/lab")
def lab_home():
    return render_template("lab.html")     # Graph Lab UI

# Existing /api/* endpoints (после переноса) — общие для обоих
```

### Переход в lab из Baddle

Маленькая ссылка-иконка 🧪 в углу Baddle header ведёт в `/lab`. Без
выделения — чтобы обычный юзер не замечал.

В Lab — ссылка «← back to Baddle» в header.

### Что остаётся общим

- **Все API endpoints** — backend один
- **`data/`** — goals, checkins, activity, profile, user_state — shared
- **`graphs/<ws>/`** — граф и его state
- **Workspace selector** — работает в обоих UI (в lab — граф текущего ws,
  в Baddle — цели/привычки текущего ws)

---

## Переходный план (этапы)

**Этап 1. Маркировка.** Ничего не перемещать, только **пометить** в коде
что куда относится:
- В HTML — `<!-- BADDLE -->` / `<!-- LAB -->` вокруг блоков
- В JS — `// @domain: baddle` / `// @domain: lab` в начале функций

Это нулевой риск. Можно делать сейчас.

**Этап 2. JS разнос.** Перенести функции по файлам `baddle/` и `lab/`
оставив один `index.html`. Два отдельных `<script>` включения.
- Сохранить все существующие глобальные имена функций (UI onclick'и)
- Добавить `/static/js/baddle/` папку, переместить файлы

**Этап 3. HTML split.** Вынести graph-tab в `lab.html`, sub-pages
baddle в `index.html`. Удалить табы из Baddle header.

**Этап 4. Polishing.** Разные иконки favicon, разные colors для header
(Baddle — blue/green, Lab — purple/gray). Отдельные READMEs.

---

## Что **не** разделять

- **User state** (energy, neuro, HRV) — один человек, одно состояние
- **Goals / recurring / constraints** — одни цели для обоих UI
- **Workspace** — переключаем единый контекст
- **LLM backend** (api_backend.py) — общий

Split только про **интерфейсы** вокруг общего состояния.

---

## Вопросы открытые

1. **Назначение Chat таба.** Сейчас есть третий таб `chat` — с минимальным
   функционалом (свободный LLM без графа). После split — куда?
   - Option A: часть Baddle (быстрый ответ без графа)
   - Option B: часть Lab (dev mode)
   - Option C: удалить, потому что `chat` intent уже обслуживается
     intent_router через `/assist`

2. **Mobile / desktop.** Baddle UI можно делать mobile-responsive
   (основное использование — смартфон). Lab — desktop-only (graph визуализация).
   Это упрощает Baddle layout значительно.

3. **Auth.** Если когда-то появится multi-user, Baddle может требовать
   логин, а Lab — нет (local dev tool).

---

## Приоритет

Не блокер для daily use. Хотя по мере роста фичи Baddle, split станет
естественным — просто удобнее навигация. Делать когда:
- Появятся пользователи (**не разработчик**) которым надо объяснить UI
- Графная часть станет дороже визуально — layouts d3, export, store
- Захочется mobile UI для Baddle

**Не делать сейчас** — текущие фичи важнее. Добавить в TODO как future work.

---

**Навигация:** [Docs index](../docs/README.md) · [TODO](TODO.md)
