# Cross-graph seed — continuity между сессиями

> Новая сессия не с пустого листа. Что Baddle решила вчера, остаётся
> доступным сегодня — как seed-ноды с унаследованными embedding'ами.
> Это не импорт документа и не LLM-résumé, а перенос позиций в
> пространстве смысла через state_graph.

## Как это работает

1. **Conclusions живут в state_graph.** Каждый tick-emission записывается
   append-only. Значимые моменты (`action=stable` с "GOAL REACHED",
   `action=pump` с сохранённым bridge, `action=collapse`, `action=compare`,
   `action=smartdc`) — это conclusions.
2. **`extract_conclusions(days, limit, graph_id)`** читает log, фильтрует
   по timestamp + action-priority, дедупит по `content_touched`, сортирует
   по (priority, recency). Возвращает top-K entries.
3. **`seed_from_history(...)`** создаёт seed-ноды в текущем `_graph` для
   каждого conclusion:
   - `ensure_embedding(entry)` — читает из `state_embeddings.jsonl` или
     считает через api_get_embedding
   - `_add_node(text="💭 <reason>", rendered=False, embedding=<inherited>)`
   - `seeded_from=<hash>` + `seeded_action` + `seeded_timestamp` на ноду
4. **Дедуп.** Если `_graph.nodes[i].seeded_from == hash` уже есть — skip.
   Можно вызывать seed-from-history многократно без накопления копий.

## Приоритет actions

Таблица (меньше = важнее):

| Action      | Priority | Почему |
|-------------|----------|--------|
| stable (GOAL REACHED / synthesize) | 1 | Явно разрешённая цель, высшая ценность |
| compare     | 2 | XOR-судейство (выбрали одно из нескольких) |
| collapse    | 3 | Кластер сложился в synthesis |
| pump        | 4 | Scout bridge — возможная связь областей |
| smartdc     | 5 | Отдельное doubt-подтверждение |

`think_toward` / `elaborate` / `ask` / `none` — НЕ conclusions (это
процесс, не результат).

## Интеграция с workspace

**Автоматический seed при switch:**

```
POST /workspace/switch {"id": "work"}
  → если target graph пустой, вызывается seed_from_history(days=7, limit=3)
    с graph_id="work" (same-workspace only, не кросс-поллинация между
    разными воркспейсами)
  → response.seeded = {created, ...}
```

Отключить: `{"auto_seed": false}`.

**Ручной seed** на любом непустом графе:

```
POST /workspace/seed-from-history
     {"days": 30, "limit": 10, "graph_id": "personal"}
```

## Что даёт continuity

- **Память между днями.** Открыл через неделю — seed'ы подтягиваются
  автоматически, distinct/tick работают поверх них сразу.
- **Non-intrusive.** Seeds приходят с `rendered=False` → не загромождают
  UI текстом. Видны как "💭 GOAL REACHED: ..." чипы. Клик → render.
- **Provenance.** Каждый seed хранит `seeded_from` (hash state_node) —
  можно проследить откуда пришла идея. Встраивается в будущий audit trail.

## Что не реализовано

- **Cross-workspace seeding.** Сейчас `graph_id` фильтрует same-workspace.
  Можно было бы подбрасывать conclusions из "work" в "personal" по
  sync_regime (когда уместно) — это будущая фича "ассоциативная pollination".
- **Weighted by similarity.** Seeds просто берутся топ-K по приоритету.
  Можно было бы query_similar к текущему topic и брать наиболее
  релевантные.
- **Forgetting linkage.** Если consolidation удаляет seed, его
  `seeded_from` тоже уходит — дубликат может создаться при следующем
  seed-from-history. Можно добавить отдельный log «уже импортированные
  hashes».
- **UI-интеграция.** Seeds показываются в общем списке как 💭; нет
  отдельного chip'а «seed из вчера» с hover-подсказкой. Будущая работа.

## Файлы

- [src/cross_graph.py](../src/cross_graph.py) — `extract_conclusions`,
  `seed_from_history`
- [src/graph_routes.py](../src/graph_routes.py) — `/workspace/seed-from-history`,
  auto-seed в `/workspace/switch`
- [src/state_graph.py](../src/state_graph.py) — источник conclusions,
  embedding cache

---

**Навигация:** [← Novelty](novelty-design.md)  ·  [Индекс](README.md)  ·  [Следующее: Workspace →](workspace-design.md)
