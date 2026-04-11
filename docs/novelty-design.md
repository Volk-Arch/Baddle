# Novelty Check — фильтр повторов

## Проблема

LLM склонны повторяться — особенно маленькие модели (8B). Без фильтра граф за 10 шагов autorun заполняется парафразами одной и той же мысли. Нужен механизм, который отсекает дубли, но не убивает полезные вариации.

## Алгоритм

### 1. Embedding similarity

Каждая новая мысль сравнивается со **всеми существующими нодами** через cosine similarity:

```
sim = cosine(embedding(new_thought), embedding(existing_node))
if sim > novelty_threshold → candidate for rejection
```

Embeddings вычисляются через API (`api_get_embedding`), кешируются в ноде.

### 2. Rephrase-before-reject

Если similarity выше порога — мысль **не отбрасывается сразу**. Вместо этого:

1. LLM переформулирует мысль ("rephrase, keep core meaning")
2. Новый embedding сравнивается заново
3. Если после rephrase similarity ≤ threshold → мысль принимается с новой формулировкой
4. Если всё ещё выше → reject

Зачем: модель может сгенерировать **новую идею** похожими словами. Rephrase меняет слова, сохраняя суть. Если суть действительно новая — embedding сместится. Если реальный дубль — останется близко.

### 3. Адаптивный threshold через Horizon

Порог novelty — не фиксированный. CognitiveHorizon управляет им через precision:

```python
novelty_threshold = 0.85 + 0.1 * precision
```

- **EXPLORATION** (precision 0.3): threshold ≈ 0.88 — мягче, пропускаем больше
- **EXECUTION** (precision 0.8): threshold ≈ 0.93 — строже, только действительно новое

Один механизм (precision) управляет temperature, top_k **и** novelty одновременно.

### 4. Skip при малом графе

Когда в графе < 5 нод — novelty check пропускается. На старте нужно набрать массу, фильтровать рано.

## Файлы

- `src/graph_routes.py` — novelty check в `/graph/think` (строки ~130-190)
- `src/horizon.py` — `to_llm_params()` → `novelty_threshold`
- `src/graph_logic.py` — `_ensure_embeddings()`

## Метрики

В логе сервера:
```
[think] novelty reject: 'текст...' sim=0.94 with #3 'существующий...'
[think] novelty rephrase saved: 'оригинал...' → 'переформулировка...' sim 0.93→0.87
[think] 5 new, 2 novelty-rejected
```
