# Smart DC (Dialectical Compass) — диалектическая верификация

## Идея

Модель не может честно оценить свою же мысль — она склонна соглашаться. Smart DC заставляет модель посмотреть с трёх сторон **принудительно**: сгенерировать сильнейший аргумент ЗА, сильнейший ПРОТИВ, и нейтральный контекст. Три разных промпта — три разных "персоны". Результат — не ответ "верно/неверно", а **карта уверенности** с метриками.

## Алгоритм

### Фаза 1: Дивергенция — три полюса

Три параллельных запроса к LLM с разными системными промптами:

1. **Thesis** (адвокат): "Сгенерируй сильнейший аргумент ЗА"
2. **Antithesis** (критик): "Сгенерируй сильнейший аргумент ПРОТИВ"
3. **Neutral** (аналитик): "Опиши контекст и условия при которых может быть верно или нет"

Каждый полюс — max 100 слов, один абзац. Промпты в `src/prompts.py`.

### Фаза 2: Конвергенция — синтез

Четвёртый запрос получает все три полюса и генерирует **синтез** — один связный абзац, балансирующий три перспективы.

### Фаза 3: Embedding analysis — метрики

Все тексты (statement, 3 poles, synthesis) переводятся в embedding-пространство.

**Confidence** — через centroid:
```
centroid = mean(emb_thesis, emb_antithesis, emb_neutral)
distance = 1 - cosine(emb_synthesis, centroid)
confidence = clamp(1 - distance * 2, 0.3, 0.95)
```
Чем ближе синтез к центру равновесия трёх полюсов → тем он сбалансированнее → выше confidence.

**Per-pole analysis:**
```
thesis_conf   = cosine(emb_thesis, emb_statement)
antithesis_conf = cosine(emb_antithesis, emb_statement)
```

**Lean** — куда склоняется:
```
lean = thesis_conf - antithesis_conf
```
- lean > 0.05: тезис сильнее (утверждение подтверждается)
- lean < -0.05: антитезис сильнее (утверждение опровергается)
- |lean| ≤ 0.05: баланс (самый интересный случай)

**Tension** — глубина спора:
```
tension = cosine(emb_thesis, emb_antithesis)
```
- tension < 0.6: тезис и антитезис далеки → глубокий, нетривиальный спор
- tension > 0.8: тезис и антитезис близки → поверхностный, тривиальный спор

### Fallback: entropy

Если embeddings недоступны — confidence вычисляется через entropy логпробов:
```
confidence = clamp(1 - combined_entropy * 0.5, 0.3, 0.95)
combined_entropy = syn_entropy * 0.7 + avg_pole_entropy * 0.3
```

## Pump context

При верификации мостов Pump промпт содержит контекст обеих идей:
```
Связь: A='идея A' и B='идея B'. Мост: текст_моста
```
Это даёт SmartDC полный контекст для оценки качества моста.

## Что делает confidence

- Записывается в ноду
- Bayes update: `P(H|E) = P(E|H) * P(H) / P(E)`
- Управляет цветом на heatmap (зелёный → жёлтый → красный)
- Feedback в Horizon: `surprise = 1 - confidence` → корректирует precision

## Файлы

- `src/graph_routes.py` — `/graph/smartdc` (строки ~789-920)
- `src/prompts.py` — промпты dc_thesis/dc_antithesis/dc_neutral/dc_synthesis
- `src/pump_logic.py` — `_verify_bridge()` использует тот же алгоритм

---

**Навигация:** [← Ontology](ontology.md)  ·  [Индекс](README.md)  ·  [Следующее: Pump →](pump-design.md)
