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
- [ ] **Apple Watch.** HealthKit XML export (one-shot история) + iOS shortcut → локальный HTTP endpoint (continuous). Confidence 0.8.
- [ ] **`data/sensor_baselines.json`** — per-source calibration (chest-strap vs optical — разные шкалы).
- [ ] **Conflict resolution** — при расхождении одновременных источников > threshold логировать.
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
