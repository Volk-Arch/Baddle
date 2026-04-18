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

**Влияет на:** всю автономную работу. Run-кнопка, cognitive_loop DMN, autorun.
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
  decay к 0.3 (cognitive loop).
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
- `norepinephrine` не падает со временем → cognitive loop не идёт (AttributeError на legacy ключи)
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

## Horizon precision drift — младенец → зрелый

**Что.** `CognitiveState.maturity` скаляр [0, 1], растёт логистически
(`MATURITY_GROWTH_RATE = 0.003 · (1 − maturity)`) на каждое
verified-событие: нода пересекла `confidence ≥ 0.8` через Bayes update,
либо цель resolved по `should_stop` в tick. **Effective precision** =
`self.precision + MATURITY_GAIN · (maturity − 0.5)` — центр диапазона
сдвигается на ±0.2 вокруг raw precision. Младенец (maturity=0) →
effective_precision = 0.3 (широкий конус, temp 0.7, вся вселенная
возможностей). Зрелый (maturity≈1) → 0.68 (узкий, temp 0.32, ответ
один). Реализовано в [src/horizon.py](src/horizon.py).

**Проверка.**
```
GET /assist/state → {precision, effective_precision, maturity, ...}
```
Свежий singleton: `maturity=0.0, effective_precision ≈ raw − 0.2`. После
~1000 verified events: `maturity ≈ 0.95, effective_precision ≈ raw + 0.19`.

**Влияет на:**
- `to_llm_params()` использует effective_precision — temperature/top_k/
  top_p/novelty shift постепенно к точности
- `_target_state` тоже читает effective — младенец сидит в EXPLORATION
  чаще, зрелый переходит в EXECUTION при меньшем raw precision
- `get_metrics()` surface-ит `maturity` + `effective_precision` отдельно
  от raw precision — UI может показывать оба

**Живые тесты.**
- Свежий сервер: `/assist/state.maturity = 0.0`, `effective_precision` на
  0.2 ниже raw precision.
- Добавь evidence с высокой strength пока `confidence` не перейдёт 0.8 →
  `maturity` растёт на ~0.003 каждое пересечение.
- Сделай `/graph/tick` до resolved-goal → `maturity` тоже бампится.
- 1000 verifieds → `effective_precision > raw + 0.18`.

**Красный флаг.**
- maturity = 1.0 уже через 50 verifieds → `MATURITY_GROWTH_RATE` слишком
  агрессивен, или логистика (`1 − maturity`) не применяется.
- maturity = 0.0 после сотен evidence добавлений → `_bayesian_update_distinct`
  не ловит threshold crossing (проверь `prior < 0.8 and posterior >= 0.8`).

## DMN walks на state-графе — эпизодическая память

**Что.** Третий фоновый канал CognitiveLoop (рядом со Scout и DMN-content):
раз в 20 мин embeddит текущую сигнатуру `(state, neurochem, topic, goal)`
и ищет в `state_graph` похожие моменты из прошлого. Если top-match
похож и не тривиально-свежий (>1ч) — эмитит alert типа `state_walk`.
Реализовано в [src/cognitive_loop.py](src/cognitive_loop.py)
`_check_state_walk` + `_build_current_state_signature`.

**Проверка.**
- `/assist/alerts` возвращает `{type: "state_walk", match: {hash, action,
  reason, timestamp}}` когда фоновый walk нашёл эпизодический резонанс.
- Embeddings кэшируются лениво: первый walk после рестарта прогревает
  до 30 последних entries через `sg.ensure_embedding`.

**Влияет на:** эпизодическая память. Раньше state_graph только писался
(Git-аудит), теперь ещё и читается системой в реальном времени для
самоузнавания. «Я была в этом состоянии раньше — тогда делала X». Без
этого жизнь Baddle амнезийна.

**Живые тесты.**
- Запусти autorun на час → state_graph наберёт 50+ entries.
- Измени topic на что-то похожее на старый → через 20 мин `/assist/alerts`
  покажет `state_walk` матч со старым моментом.
- Embed кэш проверяется в `state_embeddings.jsonl` — должен расти после
  первого walk'а.

**Красный флаг.**
- `/assist/alerts` никогда не показывает state_walk → либо в state_graph
  < 10 entries, либо `api_get_embedding` возвращает None (проверь
  доступность embedding endpoint у LLM сервера).
- Постоянно матчит одно и то же (дубликаты) → dedupe по типу работает,
  но разные hash-и проходят. Можно добавить dedupe по hash.

## Meta-tick — tick второго порядка через state-граф

**Что.** Перед выбором action, tick читает хвост state_graph (последние 20)
и детектит паттерны, невидимые в моменте:

| Паттерн | Триггер | Рекомендация |
|---------|---------|--------------|
| stuck_execution | 9/10 подряд в EXECUTION, sync_error Δ < 0.05 | emit `ask` |
| high_rejection | 3/5 последних с `user_feedback=rejected` | emit `ask` + nudge doubt |
| rpe_negative_streak | 6/10 `recent_rpe < −0.05` | force INTEGRATION + nudge merge |
| action_monotony | 5 одинаковых action подряд | emit `compare` + nudge doubt |
| normal | ничего | продолжаем нормальный routing |

Модуль [src/meta_tick.py](src/meta_tick.py). Tick ([src/tick_nand.py](src/tick_nand.py))
вызывает `analyze_tail()` после ASK CHECK, применяет рекомендацию:
emit action, либо `apply_policy_nudge()` (±0.1 к policy_weights с
нормализацией — повлияет на следующий tick через `select_phase`).

**Проверка.**
```python
from src.meta_tick import analyze_tail
tail = [{'action':'smartdc','state_snapshot':{'state':'execution','sync_error':0.4}} for _ in range(10)]
analyze_tail(tail)  # → {pattern: stuck_execution, recommend: ask}
```

**Влияет на:** self-awareness второго порядка. Tick теперь не только видит
текущий граф, но и **себя во времени** — замечает когда застрял и ломает
паттерн. Это закрывает петлю «граф думает → state_graph пишется →
следующий tick читает state_graph → адаптирует policy».

**Живые тесты.**
- Запусти autorun на простом графе без явной стопки → после 10 тиков в
  EXECUTION система сама эмитит ask (проверь в `/graph/self`).
- Ручную серию rejects 3 раза подряд через `/assist/feedback` →
  следующий tick должен детектить high_rejection, policy doubt подскочит.
- Монотония smartdc → compare через 5 шагов.

**Красный флаг.**
- `/graph/self/tail` показывает одно и то же с reason=META но action не
  меняется → рекомендация не применяется (проверь try/except в tick_nand).
- Policy_weights всегда идентичные → nudge не нормализуется или перекрывается
  обычным policy update в `horizon.update()`.

## Консолидация — забывание слабого, архив старого

**Что.** Два процесса в [src/consolidation.py](src/consolidation.py):

1. **Content-graph pruning** — удаляет hypothesis/thought ноды где
   одновременно: `confidence < 0.3`, last_accessed > 30 дней, не в subgoals
   цели, нет входящих directed-связей от goal/fact/action, нет evidence
   на них. Всё остальное защищено.
2. **State-graph archiving** — переносит tick-записи старше 14 дней из
   `state_graph.jsonl` в `state_graph.archive.jsonl`. Парент-цепочка
   переживает архив: старые хэши продолжают быть валидными в archive
   файле. Атомарный rename через `.tmp`.

Триггер: вручную через `POST /graph/consolidate` (с опцией `dry_run`),
автоматически CognitiveLoop раз в 24ч когда NE низкое (sleep-like).

**Проверка.**
```
POST /graph/consolidate {"dry_run": true}
  → {content: {candidates, total_before}, state: {archived, retained}}

POST /graph/consolidate {}
  → реально удаляет + архивирует
```

**Влияет на:** прайм-директива в контексте времени. Граф перестаёт расти
линейно; старая слабая информация уходит, освобождая внимание для
релевантной. state_graph.jsonl не вырастает в гигабайт за месяцы.

**Живые тесты.**
- Создай 5 hypothesis с `confidence=0.2` и подделай `last_accessed` на
  40 дней назад → `/graph/consolidate {dry_run:true}` вернёт их в
  candidates.
- Защищённые категории не удаляются: goal, fact, evidence, свежие (<30д),
  подцели goal'а, цели evidence.
- После `/graph/consolidate` на state_graph с entries старше 14 дней
  проверь `state_graph.archive.jsonl` — старые там, main очищен.

**Красный флаг.**
- Consolidation удалила goal/fact → нода без защиты, проверь условие
  `type not in ("hypothesis", "thought")` в фильтре кандидатов.
- Archive cyclically растёт и не очищается → archive предполагается
  cold storage, если всё-таки нужна ротация — отдельный таск.
- Парент-цепочка сломана: `_last_hash` в StateGraph ссылается на entry
  которого нет ни в main ни в archive → recovery logic в
  `_recover_last_hash` не учитывает archive.

## RPE — автономный dopamine drift из Bayes-обновлений

**Что.** Каждый Bayes update (`_bayesian_update_distinct` в graph_logic.py)
кормит **reward prediction error** в нейрохимию: `actual = |posterior −
prior|` сравнивается со скользящим baseline (mean последних 20 Δ).
Положительный RPE (больше информации чем обычно) → фазовый bump dopamine
(+0.15·RPE). Отрицательный → слабый dip. Dopamine теперь сдвигается от
**неожиданности** изменений в графе, а не просто от новизны. Автономно,
без фидбэка юзера. Реализовано в [src/neurochem.py](src/neurochem.py)
`Neurochem.record_outcome`.

**Проверка.**
```
GET /assist/state → {neurochem: {recent_rpe: 0.0-ish, dopamine: ...}}
```
После прогона `/graph/add-evidence` или `/graph/expand` (с live_bayes) —
`recent_rpe` отражает последнюю Δconfidence vs baseline.

**Влияет на:** dopamine как сигнал неожиданности. Baddle теперь сама
«расстраивается» если ожидала сильное уточнение а получила слабое.
Intrinsic pull (см. ниже) использует этот dopamine для выбора DMN-пары —
это замыкает петлю: удачные мосты → DA spike → сильнее тянет к новому.

**Живые тесты.**
- Добавь серию слабых evidence (strength=0.3) → baseline запомнит малые Δ.
  Потом добавь сильную evidence (strength=0.9) → `recent_rpe > 0`, dopamine
  подпрыгнет.
- Подряд одинаково-сильные evidence → RPE≈0 после baseline (привыкание).
- Smart DC который сильно не сдвинул confidence → отрицательный RPE,
  dopamine слегка упадёт.

**Красный флаг.**
- `recent_rpe` всегда 0 → `record_outcome` не вызывается из
  `_bayesian_update_distinct`.
- Dopamine убегает к 0 или 1 за несколько шагов → `RPE_GAIN` слишком высок,
  или baseline не обновляется (проверить `_delta_history` растёт).

## Intrinsic pull — DMN тянет туда где любопытно

**Что.** DMN (и Scout) выбирают пару нод не случайным pivot'ом, а по
`score = novelty(a,b) · relevance(a) · relevance(b)`, где `relevance(n) =
recency(n) · uncertainty(n)` (недавно тронутое + неочевидное с
confidence≈0.5). Выбор через softmax с температурой `T = 1.1 − dopamine`:
высокий DA → резкий argmax (любопытство), низкий DA → плоский выбор
(ангедония). Реализовано в [src/cognitive_loop.py](src/cognitive_loop.py)
`_find_distant_pair`.

**Проверка.**
```python
from src.cognitive_loop import _find_distant_pair
from src.horizon import get_global_state
cs = get_global_state()
cs.neuro.dopamine = 0.9   # острый argmax
pair = _find_distant_pair(nodes)
# Под high DA стабильные (conf>0.9) ноды почти не попадают в pair
```

**Влияет на:** куриосити как эмерджентное свойство. Граф сам тянет Baddle
к новым связям между неочевидными нодами; стабильные, давно не тронутые,
игнорируются. Без этого DMN блуждал рандомно — любопытство было только
в имени.

**Живые тесты.**
- Пусти `/graph/tick` на графе где часть нод имеет `confidence=0.95` →
  Scout через 3ч выберет пары между `conf≈0.5` нодами.
- Установи `neurochem.dopamine=0.05` → Scout начнёт брать и «скучные»
  пары (система в ангедонии, любопытство выключено).

**Красный флаг.**
- Scout всё время берёт одну и ту же пару → softmax не работает, чекни что
  `np.random.choice(p=probs)` вызывается, а не argmax руками.
- Под high DA выбираются стабильные ноды с conf>0.9 → `relevance` не
  падает на стабильных → проверь формулу `uncertainty = 1 − |conf−0.5|·2`.

## Когнитивный цикл — CognitiveLoop с NE-бюджетом

**Что.** Единый фоновый контур [src/cognitive_loop.py](src/cognitive_loop.py)
владеет foreground тиком (`tick_foreground` для `/graph/tick`) И фоном
(Scout/DMN/NE decay/HRV alerts). Координация через общие timestamps:
`last_foreground_tick`, `last_scout`, `last_dmn`. Бэкграунд не лезет
следующие 30 сек после юзер-тика — общий NE-бюджет.

**Проверка.**
```
GET /loop/status → {running, alerts_pending, last_scout, last_dmn,
                    last_foreground_tick}
POST /graph/tick → foreground путь, обновляет last_foreground_tick
```
(Алиас `/watchdog/*` сохранён для обратной совместимости.)

**Влияет на:**
- Фоновые инсайты (Scout bridges → сохраняются в граф)
- DMN-цикл пока юзер не смотрит (предложения, не сохраняются)
- Feedback в dopamine от качественных bridges (низкое d при найденном мосте)
- Координация NE: после `/graph/tick` DMN пауза 30 сек, после ввода в
  `/assist` NE подпрыгивает → фоновый контур уходит в минимум

**Живые тесты.**
- Добавь 5+ hypothesis в граф, подожди 10 минут без активности →
  CognitiveLoop запустит DMN, найдёт bridge. Появится в `/assist/alerts`.
- При `norepinephrine > 0.55` (только что был input) DMN на паузе.
  При `< 0.55` активен.
- Сделай `POST /graph/tick` → затем сразу посмотри `/loop/status`:
  `last_foreground_tick` ≈ now, следующие 30с DMN не лезет.

**Красный флаг.**
- `/loop/status` не существует, только `/watchdog/status` — значит URL
  alias'ы пропали. Проверить `assistant.py` add_url_rule.
- Watchdog AttributeError на legacy ключи в логах — значит где-то остался
  импорт `from .watchdog`, надо мигрировать.

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
