# Multi-graph Workspaces

> Несколько параллельных графов + привычек + ограничений, переключение
> через UI, cross-graph bridges via DMN, meta-graph как derived view.
> Реализовано в `src/workspace.py`.

## Зачем

Один граф = одна область размышления. Юзер может захотеть:
- Разделить work / personal / hobby графы
- Держать long-running исследование рядом с ad-hoc задачами
- Найти **неожиданные связи** между разными контекстами (cross-graph edges)
- Чтобы «стендап» в work не путался с «йога» в personal

Нейрохимия **общая** (один человек — один `CognitiveState` singleton). Меняется
контент + его state-граф + **scope recurring/constraint целей** (см. ниже).

## Layout на диске

```
graphs/
  main/
    graph.json            # существующий формат (nodes, edges, embeddings, meta)
    state_graph.jsonl     # per-workspace история
    state_embeddings.jsonl
    meta.json             # title, tags, created, last_active
  work/
    ...
  personal/
    ...

workspaces/
  index.json              # {active_id, workspaces: {id: info}, cross_edges: [...]}

user_state.json           # глобально (энергия, привычки)
settings.json             # глобально (API, модели)
```

## WorkspaceManager

Singleton через `get_workspace_manager()`. Основные операции:

```python
wm.list_workspaces()        # [{id, title, tags, active, node_count}, ...]
wm.create(id, title, tags)  # создать новый workspace
wm.switch(id)               # flush current → load target in-place
wm.save_active()            # flush текущий _graph в graph.json
wm.delete(id)               # удалить (кроме 'main' и active)

wm.add_cross_edge(from_graph, from_node, to_graph, to_node, d)
wm.find_cross_candidates(k=5, tau_in=0.3)
wm.meta_graph()             # derived view: graph-of-graphs
```

### Switch mechanics

1. Flush текущего `_graph` в `graphs/{active_id}/graph.json`
2. Load target из `graphs/{target_id}/graph.json` в `_graph` in-place
3. Rebind `StateGraph` singleton на `graphs/{target_id}/`
4. Update `active_id` в `workspaces/index.json`
5. UI делает `window.location.reload()` для полной переrisовки

Нейрохимия и CognitiveState **не сбрасываются** при switch — это continuity
с человеком, не с графом.

---

## Workspace scoping для целей/привычек/ограничений

После введения intent_router (→ [closure-architecture.md](closure-architecture.md))
**recurring / constraint / oneshot goals** привязаны к workspace через поле
`workspace` в `goals.jsonl` create-event'е. Правила scope'а:

| Ситуация | Что видно |
|----------|-----------|
| `/assist` в work, ищем recurring-match | Цели с `workspace="work"` + цели без workspace поля (global) |
| `build_active_context_summary(workspace="work")` в LLM prompt | То же — только work + global |
| `scan_message_for_violations(workspace="work")` | Constraints work + global |
| `/activity/start` в work | Матчит recurring/constraints work + global |
| `find_similar_solved(query)` | **Не scoped** — RAG смотрит все архивы (семантика cross-context полезна) |

**Global цели** — это цели у которых `workspace=None` в create-event. Такие
цели видны из всех воркспейсов. Используется для базовых привычек («пить
воду 4 раза в день») которые не зависят от контекста работы.

**Как создать global recurring:** сейчас через UI цели создаются в активном
workspace. Чтобы сделать global — редактировать `goals.jsonl` напрямую
(удалить `workspace` ключ) или через `/goals/update` с явным `workspace=null`.

---

## Solved archive per-workspace

Когда tick эмитит `action=stable, reason=GOAL REACHED`, `archive_solved()`
пишет snapshot в `graphs/<workspace>/solved/{snapshot_ref}.json`.
Поэтому архив цели всегда живёт **рядом со своим графом**.

`find_similar_solved(query_text)` (RAG в `/assist`) читает все архивы из
всех workspaces — даёт cross-context continuity («ты это решал в personal
2 недели назад»).

---

## User flow: практический workflow

Базовое использование Baddle → один `main`. Создавать дополнительные
workspace'ы стоит если есть чёткая **граница контекстов**.

### Когда делить

✅ **Разумно:**
- `work` + `personal` — разные домены решений
- `research_<topic>` — временный длинный проект с деталями
- `health_tracking` — long-term sensor data отдельно

❌ **Не стоит:**
- Каждая мелкая задача — один workspace. Цель workspace — **контекст**, не
  tag. Используй category / priority вместо этого.
- Разделять по дням или неделям — для этого есть plans + recurring goals.

### Типичный сценарий

1. **Создать workspace:** Header → 🗂 → `+` → ввести id (`work`) и title
2. **Переключиться:** клик по workspace в списке → `window.location.reload()`
3. **Создавать цели в этом контексте:**
   - «стендап ежедневно» (recurring, ws=work)
   - «не работать после 23» (constraint, ws=work)
4. **Переключиться в personal:**
   - В chat написать «сделал стендап» → router проверит recurring в
     personal + global, **не найдёт** → классифицирует как `thought`
   - То же сообщение в work → match к «стендап ежедневно» → instance +1
5. **Cross-graph bridges:** DMN раз в час сканит пары workspaces,
   находит связи. Alert `🔗 Cross-graph мост: work ↔ personal` если
   нашёл близкие по смыслу ноды.

### Когда сообщение универсальное

«Пить воду» — не work, не personal. Две опции:
- **Global цель**: одна recurring, видна в обоих контекстах (трекается
  независимо от workspace)
- **Дубль**: одна в work, одна в personal — разные счётчики (редко
  имеет смысл)

По умолчанию цели **per-workspace**. Для global — явный выбор через API.

---

## Cross-graph edges (serendipity engine)

Периодический scan (`find_cross_candidates`) берёт рандомные ноды из
активного графа и случайных других, считает pairwise `distinct(a, b)`.
Пары с `d < τ_in` (близкие в embedding space, хотя из разных контекстов)
записываются в `workspaces/index.json → cross_edges`.

**Use case:** в work-графе есть «делегирование задач команде», в
personal-графе есть «разграничить время с детьми». `distinct` мал → cross_edge.
Это **serendipity**: обе ноды про управление границами, система это заметила
хотя юзер держал их в разных контекстах.

Может вызываться:
- Юзером вручную через `POST /workspace/find-cross`
- DMN периодически (TODO — сейчас только on-demand)
- Когда новая нода добавлена в активный граф, сверить с N ближайшими в других

Rate-limiting: дедупликация в `add_cross_edge` (одна уникальная пара только раз).
Cap: 500 cross_edges, старые archived.

## Meta-graph (derived view)

`POST /workspace/meta` возвращает:
```json
{
  "nodes": [{id, title, tags}, ...],       # workspaces as super-nodes
  "edges": [{a, b, weight}, ...],           # cross_edge density = weight
  "active": "main"
}
```

Не отдельное хранилище — вычисляется из `cross_edges` каждый раз. Вес ребра
между двумя workspace'ами = количество cross_edges между ними.

UI рендеринг через обычный SVG в advanced view (TODO: overlay-режим).

## API endpoints

```
GET  /workspace/list                   → все workspaces + active + node counts
POST /workspace/create  {id,title,tags} → создать
POST /workspace/switch  {id}            → переключиться (reload page)
POST /workspace/save                    → flush active
POST /workspace/delete  {id}
GET  /workspace/cross-edges?workspace=X → фильтр по вовлечённым
POST /workspace/find-cross {k,tau_in}   → скан + сохранение найденных
GET  /workspace/meta                    → meta-graph derived view
```

## UI (FE-4)

Селектор в header:
```html
<select id="workspace-select" onchange="workspaceSwitch(this.value)">
  <option value="main">Main (5)</option>
  <option value="work">Work (23)</option>
</select>
<button onclick="workspaceNewPrompt()">+</button>
```

При switch полная перезагрузка страницы — это гарантирует что все кэши
в JS (`graphData`, embeddings, etc) перечитаны с нового графа.

## Что осталось открытым

- **JSONL storage** для content-графа — сейчас `graph.json` один файл на граф.
  При росте > 10k нод стоит переходить на per-line append. Отложено (пользователь
  решил: JSON остаётся)
- **Автоматический cross-edge scan** в DMN-тике — сейчас только on-demand
- **UI для meta-graph** — endpoint есть, overlay-рендер в advanced view TODO
- **Per-workspace Horizon preset** — сейчас Horizon общий. Возможно стоит
  хранить preset (tau, policy) per-workspace, а нейрохимию оставить общей

## Где живёт код

- `src/workspace.py` — `WorkspaceManager`, все методы
- `src/graph_routes.py` — `/workspace/*` endpoints
- `static/js/assistant.js` — `workspaceRefresh`, `workspaceSwitch`, `workspaceNewPrompt`

---

*Workspaces — это не multi-tenant, это multi-context у одного человека.
Граф знания vs граф проектов vs граф отношений — все живут отдельно, но
связаны через serendipity-мосты и общую нейрохимию.*

---

**Навигация:** [← Cross-graph](cross-graph-design.md)  ·  [Индекс](README.md)  ·  Конец пути ✓
