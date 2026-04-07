# TODO

> Видение → [VISION.md](VISION.md)

## Бэклог

- [ ] Множественные графы + cross-graph edges
- [ ] JSONL storage (lazy load)
- [ ] Автономное блуждание (ночной режим)
- [ ] Данные с девайсов (HRV/сон)
- [ ] Мета-режим А→Б (автоопределение режима по цели)

---

## v1: доработки

- [ ] Timeline player — ⏮▶⏸⏭ по timestamps (вместо удалённой Time)
- [ ] Тюнинг novelty threshold + промпты для разнообразия
- [ ] Параллельные API-запросы (ускорение цикла)
- [ ] Layout (d3/dagre/ELK)
- [ ] Тесты (unit + integration)
- [ ] Экспорт (PNG/SVG/markdown)

---

## v2: режимы мышления

### Архитектура

Три базовых цикла. Простые, без модификаторов. Разница между "исследованием" и "диагностикой" — не в коде, а в промпте и входных полях.

### 3 базовых цикла

| Цикл | Фазы | Стоп |
|------|------|------|
| **Research** | generate→merge→elaborate→doubt→meta | всё verified + novelty exhausted |
| **Choice** | generate_per_option→doubt_each→compare | winner clear |
| **Cycle** | check→act→wait | never (snapshot evaluation) |

Диагностика = Research + промпт "найди причину". Создание = Research + промпт "собери текст". Рефлексия = Research на своих данных. Не другой код — другой контент.

### Конфиг режима — минимальный

```python
{
    "cycle": "research",               # research / choice / cycle
    "fields": ["topic"],               # что ввести для старта
    "goal_prompt": "Исследуй тему",    # системный промпт для цикла
    "stop": "all_verified",            # условие остановки
}
```

Настройки инфраструктуры (не часть режима, а настройки Run):
- Какую модель использовать (local 8B / API / auto per-этап)
- Искать в интернете или нет
- Depth, essay tokens, stable threshold — уже есть

### Что реализовать

1. **Mode config** — простой dict. Хранится в goal-ноде
2. **tick(config, graph)** — три реализации (research/choice/cycle), выбор по config.cycle
3. **Goal evaluation** — discrete (достигнута/нет) или continuous (snapshot+streak)
4. **UI** — селектор режима, поля ввода из config.fields

### Goal evaluation

| Тип цели | Evaluation | Пример |
|----------|-----------|--------|
| **Discrete** | Достигнута → stable | "Раскрыто на 8/10" |
| **Continuous** | Snapshot: today + streak + trend | "3/3 ✅, streak 5д, ↑" |

Качественная проверка: LLM сравнивает goal↔result, оценивает покрытие.

### Источники данных per-этап

| Этап | LLM | Интернет | Когда что |
|------|-----|----------|----------|
| Generate | ✅ | ✅ | LLM для творческих, поиск для фактов |
| Elaborate | ✅ | ✅ | Поиск для исследования/диагностики |
| Doubt | ✅ | — | Только LLM (диалектика) |

Per-этап выбор модели (local 8B / API).

---

## Done

- [x] Цикл generate→merge→elaborate→doubt→meta
- [x] SmartDC (thesis/antithesis/synthesis via centroid)
- [x] Novelty check + lineage tracking
- [x] Infinite mode + convergence sparkline
- [x] Configurable: start ideas, depth, essay tokens, stable threshold
- [x] Вычистка мёртвого кода (~330 строк): graphTick, temporal, autorun handlers
- [x] Ask как ручной инструмент (контекстное меню + Studio + detail panel)
- [x] Generation Studio modal восстановлен
- [x] ~~Убрать мёртвый код: graphTick(), _autoRunExpand, _autoRunRephrase, _autoRunAsk, switch ветки, _detect_traps import~~
- [x] ~~Убрать temporal рёбра из _compute_edges, кнопку Time из UI~~
