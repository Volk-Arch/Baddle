# Полный цикл Baddle — статика + динамика

Остальные design-документы описывают **куски**. Этот — как всё собирается в работающий организм. Читать если: хочешь понять что зачем, планируешь фичу и не знаешь куда приземлять, или вернулся после паузы и нужен быстрый re-boot.

---

## Два слоя

Всё что делает Baddle делится на **статику** (что ты есть и что уже сделал) и **динамику** (как ты думаешь прямо сейчас).

**Статика:**
- **Профиль пользователя** (user_profile) — 5 категорий, preferences, constraints, контекст (время подъёма, часовой пояс).
- **Хранилище целей** (goals_store) — append-only лог create / done / abandon + агрегаты.
- **Архив решённого** (solved_archive) — snapshot графа, state_trace, финальный синтез.

**Динамика** живёт в двух симметричных объектах:

- **Состояние пользователя** (UserState) — дофамин от feedback'а, серотонин от когерентности HRV, норадреналин от HRV-стресса, выгорание от отказов, валентность от подписанного feedback'а, ожидания как EMA от реальности.
- **Состояние системы** (Neurochem) — дофамин от различия (d) в тике, серотонин от стабильности изменений весов (ΔW), норадреналин от энтропии распределения весов, накопитель конфликтов от d·(1−серотонин), недавняя ошибка предсказания награды (RPE) от модуля разности posterior и prior, зрелость растёт по verifieds.

Плюс: **рассогласование** (sync_error) = ‖UserState − Neurochem‖ в 3D, **граф контента** с embedding-ами и матрицей различий в тике (зоны NAND: согласие / исследование / конфликт), **цикл когнитивной петли** (DMN 10 минут, state_walk 20 минут, ночной цикл 24 часа, briefing 24 часа, foreground-тик при `/graph/tick`), **state_graph** как append-only git-аудит (каждый тик → запись), **meta-tick** на хвосте из 20 последних состояний (паттерны stuck / rejection / rpe / monotony).

Статика хранится и меняется редко (когда явно добавляешь / решаешь). Динамика живёт каждую секунду — апдейт от сигналов, от тика, от ночного цикла. Статика читается в LLM-промпт; динамика при резолве пишется в статику (goals / архив), валентность / нейрохимия реагируют на feedback.

---

## Прайм-директива

Одна метрика меряет всё: **рассогласование** (sync_error) = ‖UserState − Neurochem‖ в 3D нейрохимическом пространстве (дофамин / серотонин / норадреналин). Фича оценивается по одному критерию — снижает ли она рассинхрон с конкретным пользователем.

Отсюда **режим синхронизации** (sync_regime) ∈ {FLOW / REST / PROTECT / CONFESS} — адаптивное поведение. Детали — [symbiosis-design.md](symbiosis-design.md). Полный предиктивный контур — [friston-loop.md](friston-loop.md).

---

## Data flow: один запрос

Пользователь пишет сообщение. Что происходит:

1. **`/assist`** эндпоинт регистрирует вход — `inject_ne(0.4)` поднимает arousal системы, `user.register_input()` обновляет timestamp, `user.update_from_energy` бьёт по выгоранию.
2. **Категория** (`_detect_category`) → food / work / health / ... Если категория профиля пуста → карточка `profile_clarify` («что любишь / избегаешь?»), parse-ответ через LLM → preferences append → auto-retry оригинала.
3. **Контекст собирается:** активные привычки и ограничения (`recurring_ctx`), RAG по архиву прошлых синтезов (`find_similar_solved`, инжектится если similarity ≥ 0.6).
4. **Intent router** (2-level LLM) решает: fact / activity / task / chat. Fact и activity быстрые (около 1.5 с), task → карточка `intent_confirm` с draft и кнопками, chat → свободный LLM-ответ. Fallthrough — полный `classify_intent_llm` → `execute_deep`.
5. **Execute** через `execute_via_zones(message, mode, profile_hint)`: LLM генерит идеи учитывая ограничения / recurring / RAG, матрица различий раскладывает на зоны согласия / исследования / конфликта, `_render_card` рендерит (tournament / builder / dispute / ...).
6. **Post-check:** `scan_message_for_violations` — LLM проверяет нарушил ли пользователь активные ограничения. Если да → `record_violation` + карточка `constraint_violation`.
7. **`_log_decision`** списывает энергию через `user.debit_energy(cost, daily_remaining)` — mode-weighted (tournament 12 энергии, fan 3, ...).
8. Пользователь жмёт 👎 → `/assist/feedback` → `cs.update_neurochem(d=0.8)` + `user.update_from_feedback("rejected")`. Дофамин и валентность падают, выгорание растёт, учитывается streak bias. Следующий запрос — в новом режиме синхронизации.

---

## Жизненный цикл цели

Статика и динамика работают вместе через весь путь цели:

1. **CREATE.** `POST /graph/add {node_type: "goal", mode, text}` → goals_store.add_goal (static), goal-нода в графе с подцелями (dynamic).
2. **PROCESS.** Пользователь пишет, делает тики, добавляет evidence. Confidence подцелей растёт через байесовские обновления. Каждый тик → запись в state_graph. Зрелость чуть-чуть растёт при пересечении 0.8.
3. **RESOLVE.** `should_stop() → True` (через зоны различия или сходимость) → `cs.note_verified()` → `solved_archive.archive_solved()` → `goals_store.complete_goal(id, reason, snapshot_ref)` → `node._goal_completed = True` (идемпотентность).
4. **RETROSPECT.** UI 🎯 Goals → archive → клик → `load_solved(ref)` показывает текст цели + финальный синтез + ноды + state_trace.

Что осталось в live-графе — временно. Что в solved archive — permanent.

---

## Ночной цикл

Пока пользователь спит, единый проход:

1. **Scout Pump+Save** — новый bridge-мост между далёкими нодами (persisted как новая гипотеза).
2. **REM Emotional** — state-ноды с модулем RPE выше 0.15 из хвоста 100 → pump между парами их `content_touched` («эмоционально насыщенные эпизоды пере-обрабатываются поверх удивлявших нод»).
3. **REM Creative** — пары с различием эмбеддингов ниже 0.2 + BFS-путь ≥ 3 → `manual_link`. «Далёкие но близкие» — ноды разных областей с одной семантикой.
4. **Consolidation** — прунинг слабых / старых / осиротевших нод + архив state_graph (старше 14 дней → `.archive.jsonl`).

Плюс независимо: **DMN 10 минут** (пробные pump без save, alerts если качество выше 0.5), **state_walk 20 минут** (query_similar в state_graph), **daily_briefing 24 часа** (утренний alert).

Подробно — [dmn-scout-design.md § Night cycle](dmn-scout-design.md#night-cycle--scout--rem--consolidation) и [episodic-memory.md § Consolidation](episodic-memory.md#consolidation--забывание-как-фича).

---

## Три контура замкнутости

**Информационный:** message → classify → execute (LLM + граф) → карточка → feedback → нейрохимия; профиль / состояние читаются при каждом запросе.

**Физиологический (UserState):** когерентность HRV / стресс → серотонин / норадреналин, акселерометр → величина активности, feedback → дофамин / валентность, timing → дофамин, стоимость решения → выгорание / долгий резерв. Итог — рассогласование → режим → advice + alert. Плюс зона активности (4 региона) → zone-specific алерты.

**Диалоговый + uncertainty learning:** профиль пуст в food → «что любишь?» → LLM-parse → preferences append → следующий раз не переспрашивает.

---

## Тонкие места

- **Race conditions при параллельных `/assist`** не обработаны. Два одновременных запроса могут дважды списать энергию (дедуп в classify-кеше прощает, но не гарантированно).
- **`goals.jsonl` растёт монотонно.** Consolidation архивит state_graph, но не goals. Для лет-пользования нужна ротация.
- **Embedding-кэш content-графа не персистится.** `_graph["embeddings"]` только в памяти, пересчитывается при старте. Embeddings state-графа — да, в `state_embeddings.jsonl`.
- **LLM-parse профиля может галлюцинировать.** Fallback на split если LLM промажет формат. Пользователь видит результат в 👤 и правит вручную.
- **Один пользователь — один контекст.** UserState глобальный per-person (HRV один на человека), профиль один — один набор preferences на всю систему. Baddle не поддерживает мульти-контексты, и не стремится: попытка разделить work / personal так и не получила живого use-case.
- **`cognitive_loop.tick_foreground`** синхронный в request-контексте Flask. Thread-safety через `graph_lock` в `_add_node` / `_remove_node`, остальное полагается на GIL.

Реестр workstreams — [planning/TODO.md](../planning/TODO.md).

---

## Где что живёт

**Статика:** [user_profile.py](../src/user_profile.py), [goals_store.py](../src/goals_store.py), [solved_archive.py](../src/solved_archive.py) → [static-storage-design.md](static-storage-design.md).

**Динамическое состояние:** [user_state.py](../src/user_state.py) + [neurochem.py](../src/neurochem.py) + [horizon.py](../src/horizon.py) → [symbiosis-design.md](symbiosis-design.md), [friston-loop.md](friston-loop.md).

**Динамическая работа:** [cognitive_loop.py](../src/cognitive_loop.py) + [tick_nand.py](../src/tick_nand.py) + [thinking.py](../src/thinking.py) + [meta_tick.py](../src/meta_tick.py) → [tick-design.md](tick-design.md), [dmn-scout-design.md](dmn-scout-design.md), [episodic-memory.md](episodic-memory.md).

**Операции:** [pump_logic.py](../src/pump_logic.py) + SmartDC + embedding-first → [thinking-operations.md](thinking-operations.md).

**Граф / persistence:** [graph_logic.py](../src/graph_logic.py) + [state_graph.py](../src/state_graph.py) + [consolidation.py](../src/consolidation.py) → [nand-architecture.md](nand-architecture.md), [episodic-memory.md](episodic-memory.md).

**UI:** `templates/index.html`, `static/js/{assistant,graph,modes}.js`, `static/css/style.css`.

Schemas всех data-файлов — [ontology.md](ontology.md).

---

**Навигация:** [← Foundation](foundation.md) · [Индекс](README.md) · [Следующее: NAND архитектура →](nand-architecture.md)
