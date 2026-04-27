# Workspace — рабочая память между генерацией и графом

Между divergent generation (детекторы, scout, brief, chat reply) и committed graph живёт **workspace** — короткий слой активной обработки. Это не очередь сообщений и не буфер: это пред-сознательное пространство где кандидаты могут быть обработаны между собой до broadcast'а в чат и graph.

В существующей архитектуре workspace **отсутствует**: каждый источник либо emit'ит сразу, либо теряется. Это делает Baddle чисто реактивной — detect → emit, без cross-кандидатной обработки и без «дать настояться».

---

## Откуда идея

Принцип автора (2026-04-27): **«не ограничиваем систему в действиях, но выбираем из того что она сделала»**. Дивергенция → конвергенция как универсальный паттерн ([universe-as-git § Глава 8](universe-as-git.md)). Сейчас дивергенция есть (13 детекторов, scout, briefings, observation, dmn-bridge), конвергенция — нет: каждый источник встроен в свой UI-путь.

Параллель в когнитивной науке — **Global Workspace Theory** (Bernard Baars, 1988): сознание как broadcast в общее пространство, куда бессознательные процессы конкурируют за доступ. Победитель broadcasts → доступен всем. У Baddle workspace выполняет эквивалентную роль: разные источники соревнуются и при необходимости синтезируются перед commit'ом в граф и чат.

---

## Что это даёт

**Cross-кандидатная обработка.** Если 3 sync_seeking накопились с похожим тоном за час — scout видит паттерн «юзер молчит вечерами», и вместо трёх отдельных alert'ов Baddle выдаёт один **insight** с высокой urgency. 5 observation_suggestions с overlapping topic — consolidate в один summarized message. Это **не** post-hoc фильтр, это активная обработка.

**Темпоральный буфер.** Low-urgency кандидаты не теряются если момент не подошёл. Scout-мост может ждать в workspace до часа — пока не появится контекст где он уместен (юзер вернулся к теме / morning briefing включает related insight / SmartDC выбирает его как ответ).

**Низкий barrier на новый источник.** Добавил callsite `workspace.add(...)` — всё остальное (timing, dedup, синтез, селекция) приходит автоматически из существующих graph operations.

**Single source of truth для chat output.** Debug проще: «почему Baddle написал X сейчас?» — `workspace_buffer.jsonl` показывает кандидатов, селекцию, причину commit'а.

---

## Архитектурный принцип

Workspace — **не отдельная подсистема**, это **temporal scope над графом**. Реализация: ноды графа с `scope="workspace"` и `expires_at`. Все существующие graph operations работают:

| Операция | Что делает в workspace |
|---|---|
| `distinct(a, b)` | мера сходства между кандидатами — для dedup и кластеризации |
| pump scout | находит мост между накопленными кандидатами → новый кандидат-insight |
| SmartDC | decision при overlapping content — какой кандидат resonant с текущим состоянием |
| consolidation (REM-style) | сливает similar entries в один summary |
| confidence + Beta-prior | каждый кандидат имеет evidence accumulator |

Это выражение [Правила 3](architecture-rules.md#правило-3--любое-знание-это-нода-графа) — **любое знание это нода графа**. Workspace не нарушает правило, расширяет его: знание может иметь разный `scope`. Long-term (graph) и short-term (workspace) живут в одном substrate с разным TTL.

---

## Кратковременная и долговременная память

Это закрывает разрыв между cognitive ambition и текущей архитектурой. Сейчас Baddle не имеет working memory — только LTM (граф). Это значит:

- Нет места **подержать** мысль до publication.
- Нет cross-источниковой обработки до commit.
- Каждый detect → immediate emit или потеря.

Workspace вводит **STM-слой**:

| Слой | Срок жизни | Содержимое |
|---|---|---|
| **Workspace (STM)** | минуты-часы | кандидаты в публикацию, активная обработка, cross-merge |
| **Граф (LTM)** | пермаментно (с consolidation/pruning) | committed мысли, узлы целей, action memory |

**Перенос STM → LTM** — это уже существующая `consolidation.py` логика, переосмысленная: ночной cycle забирает workspace-ноды которые **выжили** (frequent access, used in synthesis, accumulated supporting evidence) и переводит их в LTM (убирает `expires_at`). Остальные expire или archive — отбор по тем же hebbian-принципам что в [world-model § Естественный отбор мыслей](world-model.md).

Это закрывает [TODO Backlog #11](../planning/TODO.md) «Оперативная vs долговременная память» — естественное следствие workspace-as-scope, не отдельная подсистема.

---

## Дневной и ночной циклы

Два режима работы вытекают естественно из STM/LTM разделения. Параллель с биологическим sleep architecture: NREM делает consolidation (гиппокамп → cortex), REM ищет novel associations и insights — именно ночная фаза где случаются озарения.

### Дневной режим

Основная работа происходит в workspace. LTM присутствует **в фоне**: подтягивается когда нужно через **activation**.

```
user input → workspace.add(immediate)
           → embedding-similarity к LTM
           → top-K «толстых» нод (confidence > 0.6, evidence > N)
           → activation: workspace.add(source="ltm_recall", scope="workspace")
           → SmartDC между user_msg и активированными → insight кандидат
           → cross-обработка между similar workspace-кандидатами
           → select() → broadcast в чат + commit в граф
```

«Толстые идеи» — high-confidence, frequently-accessed, evidence-rich LTM-ноды (главные цели, принципы, key insights). Они доминируют в priming, аналогично tip-of-the-tongue activation в человеческой памяти: думая про X, активируется related context Y, Z. Реализационно — подмножество существующего RAG в `execute_deep`, перенесённое в общий workspace path.

**Activation ≠ копирование.** LTM-нода не клонируется в workspace; создаётся reference-нода `source="ltm_recall"` с указанием на оригинал. После expiry она исчезает, оригинал в LTM не страдает.

### Ночной режим

Юзер спит. Workspace становится местом для двух фаз:

**Фаза 1 — consolidation (STM → LTM transfer).** `consolidation.py` ночной cycle проходит по `scope="workspace"`-нодам:
- **Promote** в LTM (`scope="graph"`, `expires_at=None`): used in synthesis (referenced by committed node), accumulated Beta-prior evidence > threshold, survived multiple selection cycles.
- **Archive**: low evidence, no references, expired без commit. Мягкий delete или archive для post-hoc analysis.
- **Decay confidence**: hebbian-style, в соответствии с world-model механикой 3.

**Фаза 2 — REM scout (deep bridges в LTM).** Здесь основные инсайты:
- `pump_logic` запускается над **полным** графом, не только над workspace.
- Берёт **давно-неактивированные** кластеры (low touched_at), ищет мосты к **толстым** активным core'ам.
- Найденные bridges с quality > 0.5 → workspace candidates на **следующее утро** (`expires_at = next_morning + 24h`, urgency middling).
- Морнинг briefing включает их как «Ночные находки» — это закрывает [TODO Tier 2 «META-вопросы — ночная генерация»](../planning/TODO.md).

Это resonance с biological sleep:
- **NREM** (early night) — consolidation focus, фаза 1
- **REM** (later night) — novel associations, фаза 2 (Walker, Stickgold)

### Циклический поток

```
DAY:
  user input → workspace ← LTM activation (relevant толстые ноды)
                         → cross-обработка (scout/SmartDC между similar)
                         → selection
                         → broadcast (chat) + commit (graph)

NIGHT:
  workspace pruning (expired без commit)
  STM → LTM promotion (winners → scope="graph")
  REM scout: deep bridges в полном LTM
  bridges с quality > threshold → next-morning workspace candidates

NEXT MORNING:
  workspace warm с overnight insights
  briefing включает «ночные находки» если quality высокая
  цикл начинается заново
```

**Прогностическая сила.** Эта схема **предсказывает** что должны быть наблюдаемые feedback loops:
- Хороший сон (по HRV) → больше REM bridges → утро с более глубоким briefing.
- Плохой сон → меньше night-cycle activity → утро без overnight insights.
- Хроническое перегрузка workspace днём (много кандидатов, мало select) → ночная consolidation overflow → archive вместо promotion → потеря возможных insights.

Эти связи можно валидировать через `data/prime_directive.jsonl` aggregate.

### Что это **не**

- Не имитация мозга. Параллель — описательная (helps говорить про систему), не цель.
- Не симуляция сна. Ночной cycle — реальная background work, не dream-generation.
- Не предположение что юзер спит. Ночной cycle активируется по `_idle_multiplier > threshold` — независимо от того ночь или день. Если юзер работает в 3 утра — workspace продолжает дневной режим.

---

## Селекция и broadcast

Cycle workspace → chat:

1. **Add.** Источник вызывает `workspace.add(candidate)`. Кандидат — нода графа с `scope="workspace"`, `expires_at`, `urgency`, `accumulate` flag, `dedup_key`, `metadata`.

2. **Process** (опционально, periodic). При накоплении similar candidates — scout/SmartDC/consolidation работают над ними как над любыми нодами графа. Результат — новый кандидат с reference на исходные.

3. **Select.** Convergence rule: drop expired → immediate-flag preempts → counter-wave penalty (если `r.user.mode == 'C'` push-style получают −0.3 urgency) → budget per window → urgency-sort → top-K.

4. **Broadcast.** Selected → ноды графа меняют `scope="workspace"` на `scope="graph"`, теряют `expires_at`. Это и есть «commit в LTM». Параллельно — отправка в chat history (UI poll-able через `/chat/recent`).

Источники, по умолчанию:

| Источник | accumulate | urgency | Когда select |
|---|---|---|---|
| User message | False | 1.0 | immediate |
| `/assist` reply | False | 1.0 | immediate (на user-message) |
| Critical alert (zone_overload, plan через 5 мин) | False | 0.85+ | immediate |
| Morning/weekly briefing | True | 0.6 | next select cycle |
| Scout / dmn-bridge | True | 0.4 | budget per hour |
| Observation suggestion | True | 0.5 | budget |
| Sync-seeking | True | 0.3-0.7 | budget, скейлится по silence |

User message проходит через тот же workspace, просто immediate — нет специального path.

---

## Связь с другими каркасами

- [Signal/Dispatcher (Правило 1)](architecture-rules.md#правило-1--любое-событие-к-юзеру-это-signal). Dispatcher остаётся для UI-overlay alerts (баннеры в шапке вне chat-ленты). Workspace — chat-history convergence. Возможна полная унификация — решить после prototype.
- [Action Memory](action-memory-design.md). Workspace-кандидаты могут стать action-нодами при commit (если actor=baddle). Outcome-tracking работает после broadcast (как сейчас).
- [Friston-loop / PE (Правило 5)](architecture-rules.md#правило-5--pe-единственный-драйвер-автономного-поведения). Workspace накопление = форма PE: чем дольше кандидат не пробивается в broadcast, тем выше его pressure (через urgency growth). Можно ввести linear ramp как у silence_pressure.
- [DMN/REM (`pump_logic` + `consolidation`)](world-model.md). Workspace становится их естественным **target**: ночной cycle прогоняет workspace-ноды через те же scout/REM операции, transfer в LTM выживших.

---

## Что это **не**

- **Не messaging queue.** In-process буфер с jsonl persistence для restart-safety.
- **Не замена Signal/Dispatcher.** Dispatcher для UI overlay alerts может остаться (или мигрировать — TBD).
- **Не отдельная подсистема.** Это scope-flag над существующим графом + temporal TTL.
- **Не «всё через граф».** Не повтор neon NAND-experiment 2026-04-24 (TODO Backlog meta-наблюдение). Это конкретное расширение: добавить scope, не унифицировать всё в граф.

---

## Открытые вопросы

1. **Workspace vs Dispatcher — слить или раздельно?** Dispatcher работает с immediate alerts; workspace — с накоплением. Возможна полная унификация (workspace c immediate flag = dispatcher behavior). Решить после prototype.
2. **`accumulate=True/False` — hardcoded per source или driven by `r.user.mode`?** В `mode=C` всё переходит на accumulate (counter-wave: пауза вместо давления)?
3. **Persistence overhead.** jsonl на каждый add/process/commit может стать hot path. Возможна in-memory + flush on tick.
4. **Как scout/SmartDC активируются в workspace?** Trigger по counter > N similar candidates? Или каждый workspace tick? Performance vs latency.
5. **Pruning policy.** Что с ноды которые expire без commit? Полный delete или archive (для post-hoc analysis «что не дошло до broadcast»)?

---

**Связано:** [architecture-rules § Правило 3](architecture-rules.md), [universe-as-git § Глава 8 (divergence/convergence)](universe-as-git.md), [world-model § Механика 3+4 (естественный отбор + затухание циклов)](world-model.md), [planning/workspace-design.md](../planning/workspace-design.md) — implementation план.
