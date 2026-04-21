# Multi-graph Workspaces

Несколько параллельных графов + привычек + ограничений. Переключение
через UI. Cross-graph bridges через DMN. Meta-graph как derived view.

---

## Зачем

Один граф = одна область размышления. Юзер может захотеть:
- Разделить work / personal / hobby
- Держать long-running исследование рядом с ad-hoc задачами
- Находить **неожиданные связи** между разными контекстами
- Чтобы «стендап» в work не путался с «йогой» в personal

**Нейрохимия общая** — один человек, один `CognitiveState` singleton.
Меняется контент + его state-граф + scope recurring/constraint целей.

---

## Layout на диске

```
graphs/
  main/
    graph.json           # nodes, edges, embeddings, meta
    state_graph.jsonl    # per-workspace история
    state_embeddings.jsonl
    meta.json            # title, tags, created, last_active
    solved/              # архив goal-resolved snapshots
  work/ ...
  personal/ ...

workspaces/
  index.json             # {active_id, workspaces: {id: info}, cross_edges: [...]}

# Глобально, не per-workspace:
user_state.json          # энергия, нейрохимия, предиктивный слой
settings.json            # API, модели
goals.jsonl              # с полем workspace на событиях
```

---

## WorkspaceManager

Singleton через `get_workspace_manager()`.

**Switch mechanics:** flush текущего `_graph` в `graphs/{active}/graph.json` →
load target из `graphs/{target}/graph.json` в `_graph` in-place → rebind
`StateGraph` на `graphs/{target}/` → update `active_id` в index.json →
UI делает `location.reload()` для полной перерисовки.

Нейрохимия и CognitiveState **не сбрасываются** при switch — continuity
с человеком, не с графом.

---

## Scope целей / привычек / ограничений

Recurring / constraint / oneshot цели привязаны к workspace через поле
`workspace` в `goals.jsonl` create-event'е:

| Ситуация | Что видно |
|---|---|
| `/assist` в work — recurring-match | work + global (где `workspace=None`) |
| `build_active_context_summary(workspace="work")` | то же |
| `scan_message_for_violations(workspace="work")` | constraints work + global |
| `/activity/start` в work | recurring/constraints work + global |
| `find_similar_solved(query)` | **НЕ scoped** — RAG смотрит все архивы (cross-context полезна) |

**Global цели** — `workspace=None` в create-event. Видны из всех
контекстов. Для базовых привычек («пить воду 4 раза/день») не
зависящих от контекста работы. Создание глобальной — через
`/goals/update` с явным `workspace=null` или прямой правкой jsonl
(UI создаёт в активном ws).

---

## Когда делить

✅ **Разумно:**
- work + personal — разные домены решений
- research_<topic> — временный длинный проект с деталями
- health_tracking — long-term sensor data отдельно

❌ **Не стоит:**
- Каждая мелкая задача — один workspace. Цель ws — контекст, не tag.
  Используй category / priority.
- Разделять по дням или неделям — для этого plans + recurring goals.

---

## Типичный сценарий

1. Создать workspace через UI (header → 🗂 → `+`)
2. Переключиться (клик → reload)
3. Создать цели в контексте («стендап ежедневно» recurring + ws=work;
   «не работать после 23» constraint + ws=work)
4. Переключиться в personal — «сделал стендап» там **не** матчится к
   work-цели. То же сообщение в work → instance +1.
5. DMN раз в час сканит пары workspaces → alerts
   `🔗 Cross-graph мост: work ↔ personal` при близких по смыслу нодах.

### Универсальные сообщения

«Пить воду» — не work, не personal:
- **Global цель** — одна recurring, видна везде (единый счётчик)
- **Дубли** — одна в work, одна в personal (разные счётчики, редко
  имеет смысл)

По умолчанию цели per-workspace. Для global — явный выбор.

---

## Cross-graph edges (serendipity engine)

`find_cross_candidates` берёт рандомные ноды из активного графа и
случайных других, считает pairwise `distinct(a, b)`. Пары с `d < τ_in`
(близкие в embedding space, хотя из разных контекстов) записываются в
`workspaces/index.json → cross_edges`.

**Use case:** work «делегирование задач команде» + personal
«разграничить время с детьми». `distinct` мал → cross_edge. Обе ноды
про управление границами, система заметила хотя юзер держал их в
разных контекстах.

Вызывается: вручную через `POST /workspace/find-cross` или из DMN (сейчас
только on-demand). Дедупликация в `add_cross_edge` (одна уникальная пара
раз). Cap 500, старые archived.

---

## Meta-graph (derived view)

`POST /workspace/meta` возвращает:
- **nodes** — workspaces как super-nodes (с title / tags)
- **edges** — cross_edge density между парами ws (вес = количество
  cross_edges)

Вычисляется из `cross_edges` каждый раз, отдельного хранилища нет.
UI рендер — TODO (overlay в advanced view).

---

## Endpoints

```
GET  /workspace/list                  → все ws + active + node counts
POST /workspace/create  {id,title,tags}
POST /workspace/switch  {id}          → reload page
POST /workspace/save                  → flush active
POST /workspace/delete  {id}
GET  /workspace/cross-edges?workspace=X
POST /workspace/find-cross {k,tau_in}
GET  /workspace/meta                  → meta-graph derived view
```

---

## Где в коде

- `src/workspace.py` — `WorkspaceManager` + все методы
- `src/graph_routes.py` — `/workspace/*` endpoints
- `static/js/assistant.js` — `workspaceRefresh`, `workspaceSwitch`,
  `workspaceNewPrompt`

**Открыто:** JSONL-storage для content-графа (сейчас `graph.json` один
файл — при > 10k нод per-line append), автоматический cross-edge scan в
DMN-тике, UI для meta-graph overlay, per-workspace Horizon preset
(сейчас общий).

---

*Workspaces — не multi-tenant, а multi-context у одного человека. Граф
знания vs граф проектов vs граф отношений живут отдельно, но связаны
через serendipity-мосты и общую нейрохимию.*

---

**Навигация:** [← Cross-graph](cross-graph-design.md) · [Индекс](README.md) · [Следующее: Storage layout →](storage-layout.md)
