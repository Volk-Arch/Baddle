# TODO

## 🎯 Прайм-директива

**`sync_error = d(model_prediction, user_action)` — единственная метрика, которая
оценивает ценность любого пункта ниже.** Если пункт не снижает рассинхрон с
конкретным пользователем — низкий приоритет, даже если архитектурно красиво.

Подробнее о трёх столбах и принципах → [README](README.md).

---

# ⬆ НЕ СДЕЛАНО

## Тело и сенсоры

- [ ] **Polar H10 BLE** — реальный RR-поток вместо симулятора. `bleak` клиент,
  24/7 connect, fallback на simulator. Сейчас только симулятор с слайдерами.
- [ ] **Валентность эмоций юзера** (приятно/неприятно, не только arousal) —
  HRV ловит возбуждение, DA даёт внутреннюю валентность. Внешняя — от юзера —
  не считывается. Возможно через время отклика + длину сообщений, или
  отдельный канал (мимика через камеру).

## Автономность и память

- [ ] **Консолидация** — прунинг слабых веток content-графа и state-графа.
  Без этого файлы растут линейно, мусор не отфильтровывается. Аналог
  «забывания» как феномена, не бага.
- [ ] **Intrinsic pull в DMN** — `target = argmax(dopamine · novelty · relevance)`
  в `_find_distant_pair`/pump выборе. Сейчас рандом. Без этого нет куриoсити
  как эмерджентного свойства.
- [ ] **RPE автоматически** — сейчас только manual через feedback endpoint.
  Нужно хранить `predicted_confidence_change` при doubt/elaborate и сравнивать
  с фактом → автономный dopamine drift без юзера.
- [ ] **Meta-tick на state-графе** — tick читает хвост (20 последних),
  адаптирует policy. Пример: 10 шагов в EXECUTION с неизменным sync_error →
  emit "ask" (уже триггерится по простым условиям, но не по паттернам).
- [ ] **DMN walks на state-графе** — Scout'ы гуляют не только по content,
  но и по собственной истории.
- [ ] **Полный REM-цикл** — Scout 3h ≈ slow-wave sleep (уже есть). Добавить
  быстрый-сон аналог: эмоциональная переработка (прогон state_nodes с высоким
  |rpe| через Pump) + творческий merge (collapse далёких но близких в
  embedding кластеров). Объединить Scout + Consolidation + REM в один ночной
  цикл, а не три параллельных.

## Ум расширенный

- [ ] **Генерация в embedding space** — brainstorm без текста, только векторы.
  Текст рендерится по клику. Ускорение + чистота distinct-routing.
- [ ] **Text on-demand для нод** — сейчас текст всегда есть при создании.
  Лениво генерировать когда юзер смотрит ноду.
- [ ] **Horizon precision drift** от 0.2 (младенец, всё возможно) к 0.7+
  (взрослый, конус сужается) по мере накопления verified — сейчас
  precision статически в preset'ах.
- [ ] **Cross-graph seed**: выводы одной сессии → seed следующей через
  state-граф. Continuity между днями.

## UI / визуализация

- [ ] **Sync-dashboard** — график sync_error во времени + топ-3 области
  где система чаще всего ошибается. Главный honest KPI.
- [ ] **Meta-graph UI overlay** — endpoint `/workspace/meta` готов, рендер
  в advanced view не сделан. Graph-of-graphs визуально.
- [ ] **Polar H10 cone viz с θ/φ** — сейчас конус рендерится по precision +
  state. Добавить polyvagal двухпараметрическую визуализацию когда будет
  реальный сенсор.
- [ ] **Weekly review с графиками** — chart.js для HRV trend, streaks,
  mode distribution. Сейчас только текст.
- [ ] **Neurochem history sparkline** — S/NE/DA во времени поверх баров,
  чтоб видеть дрейф, а не только мгновенный срез.
- [ ] **Timeline UI** — кнопка ⏱ открывает список, но хочется вертикальную
  ленту с цветовыми мазками по state_origin и группировкой по сессиям.

## Внешний мир (интеграции)

- [ ] **Интернет / RAG** — search для фактчекинга в Research/Debate режимах.
- [ ] **LLM + поиск гибрид** — LLM генерит гипотезу → поиск проверяет факты.
- [ ] **Per-этап выбор модели** — local 8B для generate, cloud для doubt/essay.
- [ ] **Календарь** — события → приоритизация, напоминания.
- [ ] **Погода API** — утренний брифинг + outdoor-активности + одежда.
- [ ] **Продукты/рецепты** — что есть → XOR выбор блюда.
- [ ] **Гардероб** — что есть + погода + календарь → outfit.
- [ ] **Браузер-расширение** — impulse guard (покупки), emotion guard (письма).

## Экосистема

- [ ] **Layout** — d3/dagre/ELK вместо плоской линии для графа.
- [ ] **Экспорт** — PNG / SVG / markdown / Obsidian.
- [ ] **EXE-установщик** — PyInstaller.
- [ ] **Graph Store** — маркетплейс графов, review, рейтинги.
- [ ] **Извлечение графа из текста** — статья → граф.
- [ ] **Demo mode** — ускоренная симуляция «недели Baddle».
- [ ] **SSE/WebSocket** — push вместо polling для HRV/alerts (instant feel).

## Автоопределение намерения (детали → Done/Classify)

- [ ] **Декомпозиция в подграфы разных режимов** — сложная задача
  разбивается не на плоский список подзадач, а на AND-часть + XOR-часть
  + research-часть. Сейчас `/assist/decompose` даёт плоский список.
- [ ] **Cache classify результатов** — если один и тот же message прилетает
  повторно (reload, retry) — не делать лишний LLM-вызов.

## Архитектурный collapse (когда уберётся параллельная машинерия)

Эти не блокеры. Делать только когда тестовая нагрузка покажет что стоит.
По духу — то же что v8d сделал с primitive-switches: **слить две штуки в одну**.

- [ ] **Tick + Watchdog → один когнитивный loop** (`src/cognitive_loop.py`).
  Сейчас tick_emergent и watchdog.py — два параллельных механизма. Объединение
  = один фоновый loop с NE-бюджетом. `/graph/tick` станет ping'ом в loop.
- [ ] **14 modes → parameter presets**. Сейчас `modes.py` ~300 строк с
  primitive/strategy полями (мёртвые после v8d). Свести к кортежу
  `(S₀, NE₀, τ_in, τ_out, policy, renderer_key)`. 300 → 60 строк.
- [ ] **5 renderers → 1 `render_card(zone, style)`**. dispute/tournament/bayesian/
  ideas_list/habit — похожие карточки. Шаблон + параметры стиля = −150 строк.

---
---

# ⬇ СДЕЛАНО — как проверить что работает

Формат каждого блока: **что делает** → **как проверить** → **на что влияет** →
**красный флаг если сломано**.

## Симбиоз — UserState ↕ SystemState + sync_regime

**Что.** Прайм-директива теперь вычисляется, не декларируется.
`UserState` ([src/user_state.py](src/user_state.py)) — зеркало Neurochem,
питается сигналами юзера (HRV, тайминги, длина сообщений, feedback, energy).
`sync_error = ‖user_vec − system_vec‖` (L2 в 4D). `sync_regime` ∈
{FLOW, REST, PROTECT, CONFESS} — derived из (error, user_level, system_level).
Детали → [docs/symbiosis-design.md](docs/symbiosis-design.md).

**Проверка.**
```
GET /assist/state → {
  neurochem: {dopamine, serotonin, norepinephrine, burnout, ...},
  user_state: {dopamine, serotonin, norepinephrine, burnout, hrv},
  sync_error: 0.05,
  sync_regime: "flow",
  ...
}
```

**Влияет на:**
- `/assist/alerts` — regime добавляет советы (protect / confess / rest) к жёстким флорам
- UI — две симметричные панели «ТЫ / BADDLE» с sync-индикатором посередине
- `CognitiveState.sync_error` / `sync_regime` / `hrv_*` — все derived properties
  читающие UserState

**Живые тесты.**
- Напиши сообщение → user.dopamine вырастет через timing (<30с от следующего)
- Напиши 3+ сообщения примерно одной длины → user.serotonin медленно растёт (variance низкий)
- Запусти HRV симулятор → coherence → user.serotonin, stress → user.norepinephrine
- Нажми 👍 5 раз подряд → user.dopamine→0.9; 👎 5 раз → user.burnout растёт
- Выстави user в «устал» (dopamine=0.1, burnout=0.7) при свежей системе →
  `sync_regime` станет `protect`, alert «возьму на себя» появится в `/assist/alerts`

**Красный флаг.**
- `/assist/state` не имеет `sync_regime` / `user_state` → старый код
- `sync_error` всегда 0.0 → UserState не питается (проверить что /assist
  вызывает update_from_timing/message/energy)
- Sync всегда `flow` при явной разнице user/system → пороги не срабатывают
  (проверить STATE_HIGH/LOW_THRESHOLD в user_state.py)

## Ядро мышления — NAND-emergent tick

**Что.** Единый tick engine, логика возникает из зон `distinct(a,b)`:
CONFIRM/EXPLORE/CONFLICT. Никаких if-switch по primitive.

**Проверка.**
```
POST /graph/tick {"threshold":0.91,"sim_mode":"embedding"}
  → должен вернуть {"action": ..., "tick_engine": "nand", "horizon_metrics": {...}}
```
В ответе всегда `tick_engine: "nand"`. Если `"classic"` — критический регресс.

**Влияет на:** всю автономную работу. Run-кнопка, watchdog DMN, autorun.
Если не работает — система не может думать, только чат с LLM без графа.

**Красный флаг.**
- `primitive`/`strategy`/`goal_type` возвращаются из `/graph/tick` — значит
  classic путь где-то остался
- `action: "compare"` не триггерится при нескольких verified в CONFLICT-зоне
- subgoals передаются но hypothesis-фильтр их не применяет

## Нейрохимия — dopamine / serotonin / norepinephrine / burnout

**Что.** Три скаляра + защитный режим. `Neurochem` EMA:
dopamine (новизна) ← d, serotonin (стабильность) ← 1−std(ΔW),
norepinephrine (неопределённость) ← entropy(W). γ derived:
`γ = 2.0 + 3.0·NE·(1−S)`. `ProtectiveFreeze` накапливает при d > 0.6
и низкой стабильности, триггерит PROTECTIVE_FREEZE при accumulator > 0.15,
выход при < 0.08 (гистерезис).
Детали → [docs/neurochem-design.md](docs/neurochem-design.md).

**Проверка.**
```
GET /assist/state → {neurochem: {dopamine, serotonin, norepinephrine,
                                 burnout, freeze_active, state_origin}}
```
Все поля присутствуют. При рестарте сервера значения = defaults
(все 0.5, burnout=0, freeze_active=false).

**Влияет на:**
- **serotonin**: стабильность. Низкий → γ растёт → резче Bayes
- **norepinephrine**: внимание. Высокий → Horizon budget, T_eff обостряется
- **dopamine**: новизна. В DMN тянет к нестандартным парам (todo)
- **burnout** (`freeze.accumulator`): PROTECTIVE_FREEZE блокирует Bayes update

**Живые тесты.**
- **NE spike**: отправь любое сообщение в `/assist` → `norepinephrine`
  скачок к 0.5-0.7 (inject_ne(0.4) в `assist()`). Подожди несколько minutes →
  decay к 0.3 (watchdog loop).
- **Dopamine feedback**: нажми 👍 на карточке → `d=0.2` подаётся в EMA,
  dopamine слабо смещается к низу. Нажми 👎 → `d=0.8`, dopamine растёт +
  `freeze.accumulator` растёт.
- **Freeze**: симулируй высокий d подряд (batch `update_neurochem(d=0.9)`
  30+ раз при низком serotonin) → `freeze.accumulator > 0.15`, state →
  `protective_freeze`, `apply_to_bayes` возвращает prior без изменений.
- **Recovery**: после FREEZE подай низкий d несколько раз → accumulator
  упадёт < 0.08 → выход из FREEZE (гистерезис).
- **Tick feeds chem**: сделай `/graph/tick` на графе с 5+ hypothesis →
  `dopamine` обновляется от mean_d, `norepinephrine` от entropy(confidences).

**Красный флаг.**
- `serotonin` застрял на 0.5 после feedback → EMA не применяется
- `norepinephrine` не падает со временем → watchdog loop не идёт (AttributeError на legacy ключи)
- PROTECTIVE_FREEZE не выходит даже при низком d → гистерезис сломан
- `/assist/state` возвращает legacy ключи `S/NE/DA_tonic` — значит где-то остался старый путь

## State-граф — история жизни системы

**Что.** Append-only `state_graph.jsonl`, каждый tick → одна строка.
hash/parent chain, embedded CognitiveState snapshot. Детали →
[docs/state-graph-design.md](docs/state-graph-design.md).

**Проверка.**
```
GET /graph/self?limit=5 → {entries: [...], total: N, last_hash: ...}
```
Файл `state_graph.jsonl` в корне растёт после каждого tick.

**Влияет на:**
- Self-model (через episodic query)
- Git-аудит (detrmenistic replay теоретически возможен)
- UI timeline (кнопка ⏱ в neurochem панели)

**Живые тесты.**
- Выполни 3 tick'а → в файле 3 строки. Parent каждой = hash предыдущей.
- `POST /graph/self/similar {"query":"doubt hypothesis"}` → возвращает k
  ближайших state_nodes через distinct на embedding'ах (если есть кэш).

**Красный флаг.**
- Parent chain сломан (несколько корней) → concurrent write без lock
- State_origin всегда `1_rest` → NE и burnout не читаются в state_origin_hint

## Когнитивный цикл — watchdog с NE-бюджетом

**Что.** Фоновый поток читает `CognitiveState.NE`, разделяет бюджет между
Horizon (активная работа) и DMN (Scout/pump). Низкий NE → DMN активнее.

**Проверка.**
```
GET /watchdog/status → {running: true, alerts_pending: N, last_scout: ts, last_dmn: ts}
```

**Влияет на:**
- Фоновые инсайты (Scout bridges)
- DMN-цикл пока юзер не смотрит
- Feedback в dopamine от качественных bridges (низкое d при найденном мосте)

**Живые тесты.**
- Добавь 5+ hypothesis в граф, подожди 10 минут → watchdog должен
  запустить DMN, найти bridge. Появится в `/assist/alerts`.
- При `norepinephrine > 0.55` (только что был input) DMN на паузе.
  При `< 0.55` активен.

**Красный флаг.**
- `last_dmn` не обновляется → background thread не идёт
- DMN запускается при высоком NE → NE-гейт не работает

## Третий контур — диалог

**Что.** `/graph/assist` endpoint: система задаёт уточняющий вопрос, ответ
юзера становится нодой (evidence/subgoal/seed в зависимости от mode). Кнопка
"?" в UI + pause-on-question в autorun.

**Проверка.**
```
POST /graph/assist {lang:"ru"} → {question, mode, answer_kind, goal_idx}
POST /graph/assist {lang:"ru", answer:"...", mode:"bayes"} → {ok, node_idx, kind}
```

**Влияет на:**
- Sync с юзером (главный канал пересинхрона)
- dopamine EMA на ответ юзера (engagement: answer → d=0.2 feed)
- Pause-on-question во время autorun

**Живые тесты.**
- Кликни "?" в baddle-tab → появляется вопрос. Ответь → проверь:
  в `/graph/self` последняя запись — action того типа, что видно в UI.
- Запусти Run с малым графом + без goal → через несколько tick'ов должен
  эмитнуться `action: "ask"`, autorun остановится с alert'ом.

**Красный флаг.**
- Вопрос один и тот же каждый раз → LLM не получает context
- Ответ не записывается как node → `answer` path в `/graph/assist` сломан
- Autorun игнорирует `action: "ask"` → pause-on-question handler не подключён

## HRV — тело как вход

**Что.** Симулятор RR-интервалов с RSA-модуляцией. HRV хранится в
`CognitiveState.hrv_*` полях (coherence/rmssd/stress), **не** модулирует
внутреннюю химию системы. Используется в: советы юзеру (`/assist/alerts`),
расчёт energy recovery, UI-индикаторы.

**Проверка.**
```
POST /hrv/start {mode: "simulator"} → {ok}
GET /hrv/status → {running: true}
GET /hrv/metrics → {baddle_state: {coherence, rmssd, stress, energy_recovery}}
```

**Влияет на:** советы юзеру + energy. Внутренняя нейрохимия системы
эволюционирует по собственным сигналам графа. См. docs/neurochem-design.md
секция «HRV НЕ влияет на нейрохимию».

**Живые тесты.**
- Запусти HRV → panel в header должен показать coherence/RMSSD
- Передвинь слайдер coherence вниз к 0.2 → через ~10с в `/assist/alerts`
  появится low_coherence, в `/assist/state.hrv` coherence обновлён
- Низкая coherence + burnout → совет «сделай паузу», но внутренние
  скаляры (dopamine/serotonin/norepinephrine) не меняются

**Красный флаг.**
- Изменения слайдеров не отражаются в `/assist/state.hrv` через 10-15с →
  HRV manager не пишет в CognitiveState
- При низкой coherence меняются `dopamine/serotonin/norepinephrine` —
  значит HRV decouple откатили, надо чинить

## Multi-graph workspaces

**Что.** Несколько графов, переключение через dropdown в header'е. Нейрохимия
общая, контент + state-граф per-workspace. Cross-graph edges для serendipity.

**Проверка.**
```
GET /workspace/list → {workspaces: [{id, title, active, node_count}, ...], active}
POST /workspace/create {id: "work", title: "Work"}
POST /workspace/switch {id: "work"} → reload page → граф пустой
POST /workspace/find-cross {k: 5, tau_in: 0.3} → {hits, saved}
GET /workspace/meta → {nodes, edges, active}
```

**Влияет на:** Разделение контекстов. Без этого один большой граф =
каша из всех областей жизни.

**Живые тесты.**
- Создай "personal", переключи → graph.json сохранился в `graphs/main/`,
  пустой граф загрузился из `graphs/personal/`
- Добавь ноды в оба workspace'а, нажми `/workspace/find-cross` → если есть
  похожие пары — сохраняется cross_edge. В `/workspace/meta` они появятся

**Красный флаг.**
- Switch не сохраняет текущий граф (потеря данных при переключении)
- Cross-graph edges дублируются → dedupe в `add_cross_edge` сломан

## Камера (сенсорная депривация)

**Что.** Флаг `llm_disabled` в CognitiveState. При True tick пропускает
generate/elaborate/smartdc (они требуют LLM), только distinct-based actions
(collapse/compare/pump).

**Проверка.**
```
POST /assist/camera {enabled: true} → {ok: true, camera: true}
/assist/state → {llm_disabled: true}
```

**Влияет на:** Возможность думать без API. Graceful degradation когда LLM
недоступен. Медитация для графа.

**Живые тесты.**
- Включи camera, запусти Run → tick должен эмитить collapse/compare/stable,
  не think_toward/elaborate/smartdc
- Во время camera: `/graph/add` не делает `api_get_embedding` (новые ноды
  получают embedding=None)

**Красный флаг.**
- tick всё равно вызывает LLM (думающие действия) при camera=true → бага в
  tick_nand

## UI-визуализация

**Что.**
- **Neurochem панель** (baddle-tab header): S/NE/DA/burnout бары + mode/action/
  origin бейджи + camera/timeline toggle, polling 3с
- **Cone viz** (graph advanced view): SVG конус с apex, half-angle из precision,
  цвет из state. Два конуса для Pump с bridge zone
- **State-graph timeline** (кнопка ⏱): последние 20 действий с timestamps
- **Workspace selector** (header): dropdown + создание
- **Feedback buttons** (на карточках): 👍/👎/— → `/assist/feedback`

**Проверка.** Открыть preview, кликнуть по вкладкам, убедиться что видимо.

**Влияет на:** Понимание юзером что система сейчас думает. Debugging через
глаза. Демо-эффект на показе.

**Живые тесты.**
- Отправь сообщение → `Напряжение` bar должен визуально скакнуть
- Запусти Run → cone меняет цвет при смене state
- При pump action → cone становится dual
- Нажми 👍 на карточке → `Интерес` (dopamine) и `Стабильность` (serotonin) сдвинутся в сторону низкого d

**Красный флаг.**
- Консоль browser'а с ошибками → JS сломан
- Bars не двигаются после sync-действий → polling не работает

## Habit persistence (repeatable)

**Что.** `user_state.json[habit_history]` хранит {date, streak, CognitiveState
snapshot} per-habit. `habit_snapshots` — 7-day trend + completion count.

**Проверка.**
```
POST /assist {message: "каждый день зарядка"} → карточка habit с streak, trend
```

**Влияет на:** Адаптацию S к юзеру через паттерн завершения/пропуска.

**Живые тесты.**
- Выполни одну привычку 3 дня подряд → streak=3, trend=[1,2,3],
  completion_7d=3
- Пропусти день, добавь → streak=4, но trend показывает gap

**Красный флаг.**
- streak не растёт между днями → `last_entry.date` check сломан

## Classify — LLM вместо хардкодов

**Что.** Один `classify_intent_llm()` вызов заменяет старые `detect_mode`
(keyword) + `detect_intent` (keyword). LLM получает message + context из
state-графа + состояние CognitiveState, возвращает `{mode, intent, confidence}`.
`execute()` диспатч ужался до 2 специальных случаев (rhythm, bayes).
Все 14 режимов идут через единый `execute_via_zones` со style-preset'ом.

**Проверка.**
```
POST /assist {"message": "BMW vs Tesla", "lang": "ru"}
  → {mode: "tournament", intent: "complex_goal", confidence: 0.85,
     classify_source: "llm", cards: [...]}

POST /assist {"message": "?", "lang": "ru"}
  → {mode: "free", intent: "ambiguous", confidence: 0.95,
     classify_source: "fast", cards: [{type: "clarify"}]}
```

**Влияет на:** всё что раньше было хардкодом маппинга message→mode→renderer.
Mode выбирается из контекста (state, history), а не только ключевых слов.

**Живые тесты.**
- Сложная цель в 3+ строк → intent=complex_goal → inline decompose suggestion
- Короткое/неясное → intent=ambiguous → clarify question вместо ответа
- После нескольких rejections → LLM может классифицировать в более тихие
  режимы (если state_hint попадает в промпт)

**Красный флаг.**
- `classify_source: "fallback"` часто → LLM недоступен или отвечает криво
- Один mode повторяется для явно разных сообщений → LLM prompt слишком
  generic, добавить контекст
- `execute()` делает `if mode_id == X` больше чем 2 раза → чей-то regress

---

## Assistant — чат с графом под капотом

**Что.** `/assist` endpoint: `detect_mode()` → `execute_via_zones` или
renderer по mode_id → карточка. 14 режимов, distinct-matrix генерирует зоны.

**Проверка.**
```
POST /assist {message: "BMW vs Tesla", lang: "ru"}
  → {mode: "tournament", cards: [{type: "comparison", winner_idx, ...}]}
```

**Влияет на:** Главный UX — чат.

**Живые тесты.** По одному сообщению в каждый режим (dispute, tournament,
bayes, fan, rhythm, horizon), проверить что карточка соответствует.

**Красный флаг.**
- `mode_id` определяется не туда куда ожидалось → `detect_mode` keywords
- execute_via_zones медленно (>10с) → N embedding-вызовов при distinct matrix
  без кэша, профилировать

## Embeddings-first (v8b частично)

**Что.** Node хранит `embedding` как поле. `_ensure_embeddings` зеркалит
cache в node. distinct() читает с node напрямую.

**Проверка.**
```
POST /graph/add {...} → response.nodes[N].embedding: [...]  # уже заполнен
```

**Влияет на:** Скорость distinct-routing (1мс vs 2с LLM-вызова).

**Красный флаг.**
- node.embedding всегда None → cache не зеркалится

---

## Как понять что всё сломано целиком

Единый sanity check:
```
curl /assist/state      # returns neurochem? Ядро живо.
curl /graph/self?limit=1  # state_graph пишется?
curl /workspace/list    # workspace инициализирован?
POST /graph/tick        # tick_engine="nand"? Не classic?
```

Если все 4 OK — ядро в порядке. Остальное — UI + integrations.
