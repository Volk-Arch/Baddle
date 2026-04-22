# TODO

## 🎯 Прайм-директива

**`sync_error = d(model_prediction, user_action)` — единственная метрика**, которая оценивает ценность любого пункта. Если пункт не снижает рассинхрон — низкий приоритет, даже если архитектурно красиво.

**Измерение:** `sync_error_ema_slow` пишется раз в час в [`data/prime_directive.jsonl`](../src/prime_directive.py). Endpoint `GET /assist/prime-directive?window_days=30&daily=1` даёт aggregate + trend verdict. Через 2 мес use сравнить mean(first third) vs mean(last third) — если `trend_slow_delta < 0`, механики работают.

---

## 🧹 Упразднить workspace — один граф вместо многопространства

Baddle для одного человека, один контекст. 2 workspace в `graphs/` (`personal-demo` 398 нод, `work-demo` 25 нод) — реально используется один. `WorkspaceManager` + `cross_edges` + `find_cross_candidates` — академическая красота без живого use-case. Upshot: −545 строк `src/workspace.py`, −2 doc'а (`workspace-design.md` + `cross-graph-design.md`), упрощение 7 зависимых файлов. Плюс закрывает OQ #5 (аттракторы workspace → неактуально).

- [ ] Слить `graphs/personal-demo/graph.json` в `graphs/main/graph.json` (или выбрать один как canonical).
- [ ] Удалить `src/workspace.py` (545 строк).
- [ ] Удалить `src/cross_graph.py` + `docs/cross-graph-design.md` + `docs/workspace-design.md`.
- [ ] Убрать `WorkspaceManager` вызовы из `assistant.py`, `graph_routes.py` (endpoints `/workspace/*`), `cognitive_loop.py`, `suggestions.py`, `state_graph.py`, `search.py`, `demo.py`.
- [ ] Убрать `workspace` поле из goals / activity / actions.
- [ ] UI: убрать workspace-switcher в `index.html` / `lab.html`.
- [ ] Обновить `README.md`, `docs/README.md`, ссылки в `full-cycle.md`, `closure-architecture.md`, `ontology.md`.
- [ ] Чистить `data/user_profile.json` — убрать workspace-scoped preferences если были.

---

## 📌 Задачи
- [ ] **Embeddings** Убрать хранение embeddings
- [ ] **Унифицировать связи графов и их хранения** Очень много файлов непонятно зачем
- [ ] **Desktop notifications.** Alerts работают только пока вкладка открыта. Закрыл → morning briefing / DMN-мосты / night cycle уходят в пустоту. MVP: `pystray` + `plyer` (иконка в трее + OS toast). ~2-3ч.
- [ ] **Alerts coverage — проверить что 21 check работает.** `/debug/alerts/trigger-all` показывал 10 silent_ok на demo-данных. Пройтись по каждому, покрыть пустые условия или пометить «not applicable on empty state». ~2ч.
- [ ] **Patterns × intent_router auto-abandon.** Если детектор нашёл паттерн, но юзер молчит 2+ недели — убирать предложение чтобы не накапливались старые alerts. ~1ч.
- [ ] **Constraint expansion через LLM.** Юзер добавил `"лактоза"` → LLM раз генерит синонимы `["молоко", "кефир", "сметана", ...]` → сохранить в `profile.categories[cat].constraints_expanded`. `profile_summary_for_prompt` инжектит расширенный вид. Закрывает кейс «8B Q4 предложила кефир как замену молока» (2026-04-24). ~1-2ч.
- [ ] **Auto-parse constraints из message.** «не ем / аллергия / не перевариваю / без X» в чате → LLM-parse → draft-card через существующий `make_draft_card` flow → юзер подтверждает. Закрывает случай когда ограничение упомянуто в чате но не закреплено в профиле. ~2ч.
- [ ] **`plan.create_from_text`** — «встречу в среду 11:00» → plan-object через LLM. Естественный ввод вместо формы. ~2ч.
- [ ] **Предложение еды без tool-use** ([mockup.html](../docs/mockup.html)). Реактивное: «что поесть?» → 3 варианта из `profile.food.preferences + constraints` через LLM. Проактивное: pattern-detector видит «пропускаешь завтрак по четвергам → energy crash к 14:00» → секция «Завтрак» в morning briefing с обоснованием паттерна. Реализация: mode в `suggestions.py` + pattern в `patterns.py`. ~3ч.
- [ ] **META-вопросы — ночная генерация «что ты не заметил»** ([mockup.html](../docs/mockup.html) строка 172). Когда два scout-моста обнаруживают общий абстрактный паттерн («single point of failure» в auth-модуле И в energy-понедельниках) — генерить вопрос: «какие ещё SPoF у тебя есть?». Отдельная секция в briefing. Зависит от того что scout реально находит мосты — граф должен быть нетривиальный. ~2-3ч.
- [ ] **Специализированные card-рендеры для `fan` / `rhythm`.** Сейчас оба падают в `deep_research` card. `fan` (Мозговой штурм) = generate-list с ranging по новизне; `rhythm` (Привычка) = habit-tracker view с streak + next-occurrence. ~3ч.
- [ ] **Расширение `score_action_candidates`** на другие proactive checks помимо `_check_sync_seeking` — когда через месяц станет видно где реальный разброс outcomes по action_kind. Сейчас только tone-selection в sync_seeking. Кандидаты: suggestion-tone в observation→suggestion, morning-briefing section prioritization, recurring-lag reminder timing.
- [ ] **Dialog pivot detection** в surprise detector. Резкое изменение темы через embedding distance между последовательными user-сообщениями: если `distinct(msg_prev, msg_curr) > τ_out` при коротком временном окне → candidate pivot-event. Третий канал OR рядом с HRV+text markers. Стоит только если false-positive rate низкий на реальных chat-логах. ~2ч.

---

## 🧬 Сенсоры

MVP stream работает ([hrv-design.md](../docs/hrv-design.md#sensor-stream-multi-source-polymorphism)): `SensorReading{ts, source, kind, metrics, confidence}` + `latest_hrv_aggregate(window_s)` + симулятор.

- [ ] **UserState → sensor stream.** Сейчас `UserState.update_from_hrv` через `hrv_manager.get_baddle_state()`. Мигрировать на `stream.latest_hrv_aggregate()` + `stream.recent_activity()` — любой источник влияет на UserState напрямую. ~15 call-sites. Блокирует реальные адаптеры.
- [ ] **`PolarH10Adapter`** — `bleak` + `bleakheart`, async BLE loop. Push `rr_ms` + accelerometer. Каждые 15с агрегат через `calculate_hrv_metrics` → `push_hrv_snapshot`. ~2-3ч.
- [ ] **Polar H10 cone viz с θ/φ** — polyvagal двухпараметрическая визуализация когда реальный сенсор подключён.

---

## 🛠 Tool-use

- [ ] Слой действий (calendar / weather / web.search / file / rag / permission model) — отдельная сессия когда появится необходимость. Пока не делаем.

---

## 🔬 Ждём данных (2 мес реального use)

- [ ] **Прайм-директива trend_slow_delta.** `GET /assist/prime-directive?window_days=60`. `< -0.02` → резонансный протокол работает. `≈ 0` → пересматриваем механики. `> 0` → что-то важное упущено.
- [ ] **Agency (OQ #2) — включать в `vector()`?** Через 2-3 нед измерений сравнить: коррелирует ли `mean_pe_agency` с общим `sync_error_ema_slow`. Если да → расширить 3D→4D. Если шумит → оставить feeder'ом.
- [ ] **Доминирующий PE-канал.** Какие каналы двигают `imbalance_pressure` (`mean_pe_user` / `_self` / `_agency` / `_hrv`). Один всегда 0 → убирать; один доминирует → проверять корректен ли.
- [ ] **Counterfactual honesty для sync-seeking.** Намеренно не действовать в 5-10% случаев для baseline recovery-time. Нужна минимум месяц sync-seeking истории.
- [ ] **Checkin decays** (`Decays.CHECKIN_ENERGY=0.85 / STRESS=0.7 / FOCUS=0.7 / VALENCE=0.6` — агрессивнее 0.9-0.99 остальных намеренно). Через 2 мес проверить не слишком ли жёстко.
- [ ] **`sync_regime` FLOW-dominance.** Если FLOW >80% → упростить до `sync_healthy: bool`, удалить REST/PROTECT/CONFESS. Сбор: counter of regime transitions в `prime_directive.jsonl`.

---

## 🤔 Открытые архитектурные вопросы

### #1 Personal capacity — prior, не constant
**Проблема:** `LONG_RESERVE_MAX=2000, DAILY_ENERGY_MAX=100` хардкод. Один юзер — физик 14ч/день, другой — бабушка; система обрабатывает одинаково, sync_error растёт.
**Направление:** Bayesian online EMA (α≈0.95) на `daily_spent + stop_events + nightly_hrv_rmssd` → per-user `{daily_max, long_reserve_max, daily_max_by_hour[weekday]}` в `user_profile.json`.
**Критерий:** A/B 2 недели `ceiling_static=100` vs `ceiling_estimated`. Принимаем если avg `sync_error` падает >15%.
**Где:** новый `src/capacity_estimator.py`, кормит `UserState._compute_energy`.
**Блок:** минимум 1 мес реальных данных — иначе гадание.

### #2 Agency как 5-я ось (в процессе)
**Статус:** `UserState.agency` уже собирается (EMA decay 0.95, UI 5-я карточка), но **не** в `vector()` 3D→4D.
**Направление:** через 2-3 недели смотреть `mean_pe_agency` в `data/prime_directive.jsonl`. Если ≥20% контрибьюта в `imbalance_pressure` → добавить в `vector()`. Если шумит → оставить feeder'ом.
**Дальше (если validated):** `meaning` / `relatedness` как 6-я ось. VAD модель (Valence/Arousal/**Dominance**) — `dominance ≡ agency` в psychological lit.

---

## 🏗 Edge cases

- [ ] **Attention-weighted PE.** Сейчас 4 PE-канала normalized + max. Можно ввести precision-weights: шумные каналы получают меньший вес при агрегации. Классический Фристон. Не блокер, но если `mean_pe_hrv` через 2 мес окажется заметно шумнее `mean_pe_user` — precision-gating решит.

---

## 💡 Бэклог идей (думать, не делать)

Записано 2026-04-25. Не задачи — направления. Оценка P×R = полезность × реалистичность (из 5).

**Метанаблюдение.** Половина идей — вариации одного паттерна «переписать всё через одну абстракцию» (граф = мозг, всё остальное лишнее). Это то же искушение что NAND-эксперимент 2026-04-24 (null-result: красивая единая теория не работает на реальных задачах). Unified abstractions приятны для архитектора, но часто проигрывают гибриду где каждая структура оптимизирована под свой use-case. Рекомендация: **добавлять функции** (RAG, 2D affect, outcome UI — реально новое) важнее чем **перестраивать инфраструктуру** (constraints-узлы, циклы→DMN — эстетика вместо работы).

### Зависимости и порядок

**Блокер для большей части бэклога:** упразднение workspace (секция 🧹 выше) + 1-2 мес реальных данных через прайм-директиву. Без этого re-foundation через граф = гадание.

Граф зависимостей внутри бэклога:
- **#10** ≡ summary эффекта от `#3 + #5 + #6 + #13`. Не отдельная задача.
- **#3** (убрать циклы) требует доказательства что `#15` (история в reasoning) работает — иначе DMN будет крутиться впустую.
- **#5** (recurring = граф) → открывает `#6` (цели дня как узлы) → открывает `#13` (constraints как узлы).
- **#12** (pruning похожих) ⇐ **#11** (working/long-term tiers). Нельзя pruning'ить без выделенных tier'ов.
- **#14** (emergent emotions) ⇐ **#2** (Рассел 2D). Квадранты без осей не построить.
- **#7** (outcome узел) — частично закрыт в Action Memory, нужен только UI поверх.

**Быстрые wins (можно брать изолированно):**
- **#9** (один чат в графовом режиме, P3/R5) — чистка UX на пару часов, не блокируется ничем.
- **#7** (manual outcome UI поверх Action Memory, P3/R4) — форма + endpoint, не требует re-foundation.
- **#8** (кластеры в Lab по color, P2/R4) — косметика, d3-layout уже есть.

**Не делать без статистики (≥ 1 мес use):**
- **#4** (пересмотр 14 режимов) — нужны use-counts какие реально вызываются.
- **#3** (циклы→DMN) — нужно доказать что DMN находит нетривиальное на живом графе.

### Пакет «Всё через граф» (архитектурная трансформация)

Фундаментальная идея: убрать параллельные хранилища (goals_store, user_profile, recurring) и процедурные циклы (21 check в cognitive_loop). Оставить один граф, где DMN блуждает и всё находит эмерджентно. Это **многонедельная работа**, возможна только после упразднения workspace и накопления месяца реальных данных для валидации.

- **#3 Зачем циклы если всё в графе?** (P5/R3) DMN блуждает по нодам, находит паттерны в activity/habits → предлагает. Сейчас 21 check — процедурщина, «пустышки» на empty state. Блок: нужно сначала доказать что DMN **реально** находит нетривиальные связи. Связано с #15.
  *Мнение:* **не делать.** Замена предсказуемой системы на stochastic. Процедурные check'и — safety net с контрактом (раз в 5 мин проверил silence, среагировал). DMN — без гарантии timing'а; алерты могут не сработать когда нужно. Плюс на 398 текущих нодах DMN не обнаружил ни одного значимого моста — заменять tests на то что **пока не работает** = плохая ставка.

- **#5 Recurring = циклический goal-узел.** (P4/R3) «покушать 3 раза» — goal с `repeat: daily × 3` + 3 instance-ноды. Закрывает goals_store + recurring.py в граф. Упрощает.
  *Мнение:* только **после** доказательства #3. Иначе «выпил воды» не посчитается в recurring без процедурного tracker'а.

- **#6 Цели дня = 3 узла графа.** (P4/R2) Каждый приём пищи / задача = отдельная нода графа с типом `goal_instance`. Двигается/закрывается. Связь с #5.
  *Мнение:* **не делать полную замену.** Goals имеют transactional семантику (atomic `record_instance`) — граф append-only, при crash half-states. Поиск «вчерашние 3 instance» = O(log N) traversal вместо O(1) dict lookup. Компромисс: `goal_instance` как узел **с ref** в goals_store, не замена store'а.

- **#7 Узел выполнения (manual или auto-linked).** (P3/R4) Почти есть в Action Memory (outcome). Добавить manual creation через UI + auto-link на упоминание в чате через entity matching.
  *Мнение:* **да, быстрый win.** Action Memory делает 80%, остаётся форма + endpoint. Доводит систему до полного контура обучения.

- **#9 Один чат (убрать «обычный чат с ИИ» в графовом режиме).** (P3/R5) Сейчас два входа. Оставить deep-mode/fastpath/scout как single entry. Простая чистка UX.
  *Мнение:* **да, быстрый win.** Legacy UX на пару часов работы. Минимальный риск.

- **#10 Один большой круглый граф как мозг.** (P∞/R—) — это summary после #3/#5/#6/#13. Не отдельная задача, а видение.
  *Мнение:* видение, не план. Оценивать эффект только **когда** появится реальная emergent польза из #3/#15. Сейчас красивая метафора без рабочего выигрыша.

- **#13 Constraints/preferences как узлы графа.** (P4/R2) Вместо `user_profile.json` — ноды `constraint`/`preference` с hebbian decay, которые участвуют в reasoning естественно. Риск: `profile.json` удобно читать руками и делать backup. Может быть гибрид — node references внешний JSON.
  *Мнение:* **не делать.** Constraint — декларативный факт, не воспоминание. «Не ем лактозу» не должно затухать из-за того что две недели не ел молочного, а hebbian decay именно так и работает. Plus `profile.json` editable руками, git-backup, viewable в 3 строки — перенос в граф = data lock-in. Relevance gate (сегодняшний fix) уже работает на JSON.

- **#15 История чата влияет на reasoning.** (P4/R3) Сейчас старые ноды оживают только через DMN. Нужен retrieval step в `execute_deep` и `_fastpath_chat`: перед LLM-генерацией — similar past nodes/outcomes через `distinct()` к query, инжект в prompt. Это уже **частично** есть (pump, RAG), нужно довести до всех путей.
  *Мнение:* **делать, самая правильная из списка.** Сейчас 90% старого контекста не участвует в новых рассуждениях. Классический RAG, добавляет context, не меняет поведение. Низкий риск, большой эффект. Первым в пакете.

### Пакет «Память и pruning»

- **#11 Оперативная vs долговременная память.** (P4/R3) Working memory = recent nodes + hot cluster; long-term = archived + consolidated. Развитие существующего consolidation (`episodic-memory.md`). Нужен explicit tier в node-схеме + ночной transfer oper→long.
  *Мнение:* **не проактивно.** Красивая нейробиологическая модель, но текущий consolidation уже решает 80% простым age-based archive. Начинать tiering когда появится реальная боль (reasoning тормозит, граф раздулся), не заранее.

- **#12 Pruning: коллапсировать похожие, переносить oper→long.** (P3/R4) Связано с #11. Технически: nightly job на `distinct(a,b) < 0.1` в oper-layer → merge + transfer. У нас есть `_rem_creative` для paradox, нужен similar для consolidation.
  *Мнение:* то же что #11. Откладывать. Но `_rem_creative` как шаблон — действительно удобно, когда время придёт.

### Пакет «Эмоциональная модель»

- **#2 Модель Рассела (Valence × Arousal, 2D).** (P4/R4) UI показывает юзеру где он сейчас на 2D-карте аффекта — grasp'able способ «как я?». Валентность уже есть (`UserState.valence` через sentiment + accept/reject). Arousal = функция от `norepinephrine` / `HRV stress`. Отдельный view + morning briefing.
  *Мнение:* **да, лёгкий win.** Надстройка над существующими данными, не refactor. 2D точка графически понятнее чем 4 нейрохимических скаляра. Рабочая психологическая модель с литературной основой. Делать после ближайшего ядра задач.

- **#14 Emergent emotions (отчаяние, радость) из 2D.** (P3/R2) Расселл разбивает 2D-плоскость на квадранты: high-neg-arousal+negative = anger/despair, low-arousal+negative = apathy. Можно назвать зоны и показывать как label. Зависит от #2. Риск: эмоции не должны быть UI-primary — мы не терапевт.
  *Мнение:* **не делать.** Один и тот же point в (V, A) может быть «отчаянием» или «grief» в зависимости от контекста — дискретизация навязчива. Риск false positive «ты в отчаянии» → psychologically concerning. Baddle не терапевт. Координаты пусть будут координаты, без ярлыков.

### Пакет «Capacity: три контура нагрузки»

- [ ] Заменить dual-pool energy (хардкод 100 + 2000) на трёхконтурную модель: физио / эмо / когнитивный, с capacity-зоной как derived state. Миграция фазами (наблюдение → параллельное вычисление → дубляж decision gate → переключение). Подробности, формулы, поля, call-sites в [docs/capacity-design.md](../docs/capacity-design.md).

### Пакет «Задачный слой — backlog + auto-scheduling»

- [ ] Расширение taskplayer'а на полный задачный слой: отдельное хранилище `data/tasks.jsonl` (append-only), оценка сложности при создании, auto-scheduling в план дня через capacity-зону, возврат незавершённого с флагом `touched_today`, калибровка оценки через `surprise_at_start`. Структура, схема, алгоритм matching'а, API, миграция — в [docs/task-tracker-design.md](../docs/task-tracker-design.md). Связан с [capacity-design.md](../docs/capacity-design.md) и идеей #6 из пакета «Всё через граф». Архитектурная граница: Baddle **предлагает**, не настаивает — никакой streak-gamification («никакой логики оптимизирующей время в приложении» из [world-model](../docs/world-model.md)).

### Пакет «UX/работа графа»

- **#4 Пересмотр 14 режимов.** (P3/R4) Каких реально юзаешь? Dispute/tournament/fan/rhythm/horizon/... — отсев не-используемых, merge похожих. Требует статистики за N недель чтобы знать какие activate часто.
  *Мнение:* **да, но через месяц use-counts.** Уверен что реально работают 5-7 режимов, остальное — на всякий случай. Удалить неиспользуемые = −300 LOC в `modes.py`. Сейчас инструментация: добавить counter в `prime_directive.jsonl` per-mode, смотреть через месяц.

- **#8 Визуализация кластеров по типу в Lab.** (P2/R4) Цветовая группировка habits/actions/thoughts/constraints в Graph Lab. Косметика, но нетрудно — d3/force-layout уже есть.
  *Мнение:* **по возможности.** Действительно полезно видеть habits отдельно от actions. Низкий приоритет — не критично, но быстро.

### Долгосрочно

- **#1 Определение СДВГ и прочих паттернов.** (P?/R1) Классификатор по накопленному поведению. Нужен корпус, ethical review, сильно позже. Метка чтобы не забыть.
  *Мнение:* **правильно что в конце.** Без корпуса + без ethical layer — false positives могут навредить. Возвращаться через 6+ мес реальных данных и только в формате «ты можешь посмотреть такой-то паттерн», без диагноза.
