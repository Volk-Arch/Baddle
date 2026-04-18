# Embedding-first brainstorm — мышление без слов

> Raw мысль — не предложение, а **направление в пространстве смысла**.
> Текст — это рендер направления, который нужен только когда ты на него
> смотришь. Baddle умеет генерировать идеи сразу как векторы и разворачивать
> в слова лениво, по клику.

## Мотивация

Стандартный brainstorm: LLM генерирует N текстов, каждый embeddится, потом
сравниваются друг с другом. Из N обычно 60-80% дубликатов/почти-дубликатов.
То есть 60-80% LLM-токенов уходят впустую.

Embedding-first переворачивает порядок:
1. 1 embed-call для seed (topic)
2. N perturbation в vector space (cheap: numpy, миллисекунды)
3. Novelty-filter геометрически
4. Выжившие — ноды с embedding'ом, но без текста
5. Текст генерируется **только когда юзер открыл ноду**

Экономия:
- ≈N−1 embed-calls (batch → 1)
- ≈N·(1−render_rate) LLM text generations (типично юзер смотрит 20% из 5 идей)

## Как это работает

### `sample_in_embedding_space(seed, n, sigma, novelty_threshold, ...)`

Dimension-invariant Gaussian perturbation:
```
per_dim_stddev = sigma / sqrt(dim)
for attempt in range(max_attempts):
    noise ~ N(0, per_dim_stddev, shape=(dim,))
    candidate = seed + noise
    candidate /= ||candidate||   # unit-normalize
    if distinct(candidate, seed) > max_distance_from_seed: skip
    if any distinct(candidate, existing) < novelty_threshold: skip
    if any distinct(candidate, accepted) < novelty_threshold: skip
    accept
```

`sigma=1.0` даёт среднее `distinct(candidate, seed) ≈ 0.25` (novel но
релевантно). Через дисперсионное шкалирование `per_dim = sigma/√dim`
формула одинаково работает для 16-dim и 768-dim embeddings.

### Unrendered ноды

Поля node:
- `text = "💭"` — placeholder
- `rendered = False`
- `embedding = <unit vector>` — настоящий, участвует в distinct/routing

Ноды с `rendered=False` **полноценные** для tick'а, graph computation'ов
и state_graph — только UI рендерит их иначе (значок 💭) и ждёт клика.

### `POST /graph/render-node {index, lang}`

Lazy expand:
1. Если `rendered=True` — возвращает cached text.
2. Иначе: собирает топик + до 3 incoming directed-соседей (только их
   rendered text'ы) как контекст.
3. Один LLM-call с коротким промптом: «разверни seed-идею в одно
   предложение на тему».
4. Сохраняет text + rendered=True + last_accessed=now.
5. Embedding **не обновляется** — оригинальный perturbed vector описывал
   position ноды, новый text подстроен под эту position.

## Эндпоинты

```
POST /graph/brainstorm-seed
  { "topic": str, "n": int = 5, "sigma": float = 1.0,
    "novelty_threshold": float = 0.2 }
  → { "created": [idx, ...], "n_sampled": int, "topic": str }

POST /graph/render-node
  { "index": int, "lang": "ru" | "en" }
  → { "ok": true, "text": str, "cached": bool, "index": int }
```

## Что это даёт архитектурно

- **Чистота distinct-routing.** embedding не привязан к lexical формулировке;
  geometric perturbation → новые направления чисто по semantic space.
- **Отсутствие ghost-нод.** Раньше LLM генерировал текст → embed → novelty-
  reject. Сейчас novelty-reject до любого LLM-call — нет "пустых" нод в
  графе.
- **Text-on-demand — самостоятельная фича.** Можно пометить любую ноду как
  `rendered=False` и затереть `text=placeholder` — distinct/routing
  продолжают работать, а текст дорендеривается при необходимости.

## Что не реализовано

- **UI.** Пока unrendered ноды показываются как "💭" в существующем
  списке — нет визуального отличия "нажми чтобы развернуть". Надо
  добавить в `static/js/graph.js` рендер + click-handler на /render-node.
- **Batch render.** Нет endpoint'а «рендери всё что видно в viewport».
  При отрисовке 10 unrendered нод сейчас UI делает 10 последовательных
  запросов.
- **Re-embedding после render.** После LLM expand embedding остаётся
  оригинальный (perturbed). Можно опционально перезаписать новым embed от
  rendered text — но это теряет точку в embedding space которую хотели.

## Файлы

- [src/graph_logic.py](../src/graph_logic.py) — `sample_in_embedding_space`,
  `rendered` в `_make_node` / `_ensure_node_fields` / `_add_node`
- [src/graph_routes.py](../src/graph_routes.py) — `/graph/brainstorm-seed`,
  `/graph/render-node`
