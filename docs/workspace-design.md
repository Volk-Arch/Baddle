# Multi-graph Workspaces (v4)

> Несколько параллельных графов, переключение через UI, cross-graph bridges
> via DMN, meta-graph как derived view. Реализовано в `src/workspace.py`.

## Зачем

Один граф в `_graph` = одна область размышления. Юзер может захотеть:
- Разделить work / personal / hobby графы
- Держать long-running исследование рядом с ad-hoc задачами
- Найти **неожиданные связи** между разными контекстами (cross-graph edges)

Нейрохимия **общая** (один человек — один `CognitiveState` singleton). Меняется
только контент + его state-граф.

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
