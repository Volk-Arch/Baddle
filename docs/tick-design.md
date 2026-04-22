# Tick Cycle — автономный цикл мышления

Тик — один атомарный шаг мышления: сгенерируй / объедини / углуби / проверь. Какой именно — решает когнитивное состояние (CognitiveState) на основе текущего состояния графа. Аналогия: один вдох-выдох мышления. Autorun = серия вдохов.

---

## Фазы

- **GENERATE** — пакет идей (с проверкой новизны)
- **MERGE** — объединить похожее, не тратить работу на дубли
- **ELABORATE** — углубить уникальные, добавить evidence
- **DOUBT** — Smart DC на углублённые, но непроверенные
- **GENERATE+** — всё проверено? искать пробелы (META)
- **SYNTHESIZE** — ничего нового → стабильно → финальный итог

Порядок не жёсткий. Horizon выбирает фазу по **весам политики** (policy_weights) — успех растит вес, провал снижает. Не round-robin, система адаптируется.

---

## Классификация нод перед тиком

| Категория | Критерий | Зачем |
|---|---|---|
| **bare** (голая) | Нет evidence, не verified, не `collapsed_from` | Нужен elaborate |
| **unverified** | confidence ниже stable_threshold | Кандидаты для doubt |
| **verified** | confidence ≥ stable_threshold | Готовы |
| **doubt_candidates** | unverified минус bare | Doubt только после elaborate |

**Голые ноды не идут в doubt.** Сначала elaborate (добавить аргументы), потом doubt. SmartDC на голой гипотезе без контекста — слабая проверка.

---

## Выбор фазы

`select_phase(available)` смотрит доступные фазы и выбирает по наибольшему весу политики. Фаза доступна если есть работа: generate — если гипотез меньше минимума и ещё не генерировали; merge — если есть группа похожих; elaborate — если есть голые ноды; doubt — если есть doubt_candidates.

Если ничего не доступно: META («что упустил?», с контекстом verified) или SYNTHESIZE (граф стабилен, цикл завершён).

---

## Выбор цели

Какую ноду обрабатывать (`_pick_target`):
- BFS-расстояние до цели через взвешенные рёбра — ближе к цели = приоритетнее.
- Каждый 3-й вызов — случайный выбор (разнообразие, не застревать).
- Без цели — наименее уверенная нода.

---

## Интеграция с Horizon

Каждый тик возвращает параметры для LLM (температура, top_k, порог новизны) и метрики горизонта (точность, состояние, веса политики — для UI-overlay). Autorun после SmartDC отправляет обратно **удивление** (surprise = 1 − confidence) → `horizon.update(surprise)` → точность корректируется → следующий тик с новыми параметрами.

---

## NAND-emergent — единственный путь

Классический тик с ветвлением по primitive удалён. Все 14 режимов проходят через **один тик** ([tick_emergent](../src/tick_nand.py)). Логика возникает из зон различия:

- различие ниже порога согласия (τ_in) → зона согласия → collapse (merge)
- различие в интервале (τ_in, τ_out) → зона исследования → pump / elaborate
- различие выше порога конфликта (τ_out) → зона конфликта → smartdc (doubt)

**Emergent compare:** несколько verified + конфликтные пары между ними → действие «compare» (LLM-judge выбирает лучший).

**Scout / DMN:** pump между самой дальней парой, запись моста.

### Условия остановки

Единая функция `should_stop()`, не зависит от primitive:

- различие между целью и лучшим verified ниже порога согласия → цель достигнута
- Сходимость: 3+ verified, средний confidence выше 85%, нет pending
- Исчерпание новизны: точность выше 0.85 и нет работы

**Для целей с подцелями — AND vs OR эмерджентно по среднему различию между ними:**

| Среднее различие | Семантика | Правило |
|---|---|---|
| ≤ τ_in | Подцели близки (альтернативы: React/Vue/Svelte) | **OR**: первый verified хватит |
| ≥ τ_out | Подцели разнесены (части целого: frontend/backend/db) | **AND**: все должны быть verified |
| промежуточное | Не резолвим, продолжаем | — |

Режим (tournament / builder / pipeline) не задаёт это явно — различие между подцелями само показывает характер задачи.

### Режим как preset

Поля `primitive` / `strategy` / `goal_type` удалены из [modes.py](../src/modes.py). Режим — компактный кортеж `(name, name_en, goals_count, fields, ..., preset)`. Preset (точность, политика, целевое удивление) читается через `get_mode(mode_id)`, `create_horizon(mode_id)` забирает оттуда. Runtime не свитчится на mode — логика эмерджентна из зон различия.

---

## Pause-on-question

Тик эмитит действие «ask» когда:
- рассогласование (sync_error) выше 0.6 (система не понимает пользователя), ИЛИ
- норадреналин ниже 0.35 + много unverified (блуждание в неопределённости)

Autorun в `graph.js` ловит это, показывает alert и останавливается. Пользователь отвечает → спайк NE + ответ становится нодой.

---

## Camera mode — сенсорная депривация

Если `cs.llm_disabled == True`, тик пропускает generate / elaborate / smartdc (требуют LLM) и работает только на различии между существующими нодами: collapse / compare / pump. Найти паттерны в том что уже есть.

---

## Merge lineage

Merge отслеживает происхождение: `collapsed_from: [3, 5, 7]`. `_filter_lineage` не даёт объединять ноды с общим происхождением — предотвращает «перемалывание» одного материала. Группировка сначала по embedding-кластерам, fallback по topic.

---

## Multi-goal

Для режимов с `goals_count: "2+"` (AND/OR/XOR-like) при создании цели multiline-текст разбивается: первая строка = текст цели, остальные = ноды-гипотезы (подцели). Цель хранит `subgoals: [idx1, idx2, ...]`. `tick_emergent` фильтрует classify только по нодам-подцелям.

---

## Защита от циклов

- Флаг `_generated` — не генерировать заново если набрали минимум гипотез.
- Merge всегда `no_merge=false` в autorun (originals удаляются).
- SmartDC всегда в режиме `replace` (не создаёт дочерние ноды).
- `meta_count < max_meta` — максимум 2 META-запроса за цикл.

---

## Hook в state-граф

После каждого emit в `state_graph.jsonl` добавляется запись с действием / фазой / затронутыми нодами / полным снимком CognitiveState. Git-аудит + эпизодическая память в одной структуре — [episodic-memory.md](episodic-memory.md).

---

## Где в коде

- [src/tick_nand.py](../src/tick_nand.py) — `tick_emergent()` (единственный tick engine)
- [src/cognitive_loop.py](../src/cognitive_loop.py) — `CognitiveLoop.tick_foreground()` для `/graph/tick` + фоновый поток
- [src/thinking.py](../src/thinking.py) — helpers (`classify_nodes`, `_find_similar_group`, `_pick_target`, `_pick_distant_pair`)
- [src/horizon.py](../src/horizon.py) — `select_phase`, `update`, `to_llm_params`, `apply_to_bayes`
- [src/state_graph.py](../src/state_graph.py) — hook на каждый тик emit
- Endpoint `/graph/tick` → `loop.tick_foreground()`
- `static/js/graph.js` — autorun с обработкой действия «ask»

---

**Навигация:** [← Конус (метафора + ритм)](cone-design.md) · [Индекс](README.md) · [Следующее: Horizon →](horizon-design.md)
