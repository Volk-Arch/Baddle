# Episodic memory — жизнь системы во времени

Content-граф отвечает *что* думает Baddle. Этот слой отвечает *как и когда*
она это делала. Три компонента образуют один pipeline:

**state_graph** (накопление) → **meta-tick** (поиск паттернов) → **consolidation** (чистка).

Собраны в один doc потому что никогда не используются независимо:
state_graph без meta-tick — просто лог, без consolidation — файл на
гигабайт.

---

## State-граф

Второй граф рядом с content-графом. Каждый тик — одна нода (одна строка
JSONL). Унифицирован с Git-аудитом из [nand-architecture.md](nand-architecture.md).

**Зачем.** Без self-модели система не учится на собственных паттернах;
каждая сессия начинается с нуля; детерминистский replay невозможен;
meta-tick нечего читать.

**Что записывается.** Каждая тик-нода содержит хеш и ссылку на предыдущий (DAG-цепочка), timestamp, действие и фазу, индексы content-нод которые тик тронул, полный snapshot метрик когнитивного состояния (точность, нейрохимия, HRV, **рассогласование с пользователем** — sync_error). Плюс опциональные **ошибка предсказания награды** (RPE) и **реакция пользователя** (user_feedback), когда они измеримы. Хеш — sha1-префикс от канонического JSON фиксированных полей; при рестарте StateGraph сканирует хвост файла чтобы восстановить цепочку родителей.

**Где лежит.** `data/state_graph.jsonl` для default workspace, или
`graphs/{ws}/state_graph.jsonl` в мульти-графе. Плюс ленивый кэш
embedding'ов в `state_embeddings.jsonl`. Append-only — запись быстрая,
файл растёт, никаких rewrites.

**Эпизодическая память через различение.** Та же **мера различия** (d = distinct(a, b)) что работает на content-графе применяется к state-графу. Embedding state-ноды — конкатенация «действие:фаза | состояние | state_origin | S=X NE=Y DA=Z | причина», лениво считается при первом `query_similar`, кэшируется.

Use case: «когда в последний раз система была в похожем состоянии — что
она делала и что сработало?» Это основа meta-tick и heartbeat substrate
для DMN walks.

**API:** `GET /graph/self` (c фильтрами), `POST /graph/self/similar`
(топ-k похожих прошлых состояний).

---

## Meta-tick

Tick первого порядка смотрит на мгновенный граф и решает что делать.
Meta-tick смотрит на **собственную историю** (tail 20) и замечает
паттерны, которые не видны в одном кадре: «я застрял», «юзер меня не
принимает», «я системно переоцениваю ответы».

Это замыкает петлю: tick пишет state_graph → state_graph читается
следующим tick'ом → поведение адаптируется.

### Пять паттернов

| Паттерн | Условие | Действие |
|---|---|---|
| **stuck_execution** | 9/10 последних в EXECUTION, `Δsync_error < 0.05` | `ask` |
| **high_rejection** | 3/5 последних с `rejected` | `ask` + nudge `{doubt +0.1, generate/elaborate −0.05}` |
| **rpe_negative_streak** | 6/10 последних `rpe < −0.05` | `stabilize` (force INTEGRATION) + nudge `{merge +0.1, generate −0.1}` |
| **action_monotony** | 5 одинаковых action подряд | `compare` + nudge `{doubt +0.1, merge/generate −0.05}` |
| **normal** | ничего | Обычный routing |

Приоритет: rejection → stuck → rpe streak → monotony. Rejection —
сигнал от юзера, перекрывает всё. Stuck — максимальный desync. RPE
streak — системная проблема. Monotony — локальная, самая слабая.

Policy nudge добавляет delta к весам фаз с полом 0.05 и нормализацией
суммы к 1. Следующий tick через `horizon.select_phase()` выберет другую
фазу.

---

## Consolidation

> Граф не должен расти линейно в количестве тиков. Слабая старая
> информация должна уходить, освобождая внимание. Биологический аналог
> — memory consolidation во время slow-wave sleep: недавно активные
> связи укрепляются, неактивные тают.

Ночной цикл: **decay → prune → archive**.

### Hebbian decay

Каждое обращение к ноде (`elaborate` / `smartdc` / `pump` / `navigate`
/ `render-node` / `add-evidence`) вызывает `touch_node(idx)` — обновляет
`last_accessed` и добавляет +0.02 к confidence. **Использованная связь
крепнет.**

Раз в сутки `decay_unused_nodes` проходит по `hypothesis` / `thought`
без обращений > 1 дня и снижает confidence на −0.005. Минимум 0.05 —
чтобы ноды могли ожить при случайном пересечении.

Баланс подобран мягко: безубыточно — одно обращение в 4 дня. От
стартовых 0.8 до порога prune (0.3) — ~100 дней без обращений. Свежие
(< 1 дня) неприкосновенны.

### Pruning

Удаляет слабые старые орфанные hypothesis/thought-ноды. Кандидат должен
одновременно: confidence < 0.3, не было обращений > 30 дней, не в
subgoals какой-либо цели, нет входящих directed от goal/fact/action,
нет evidence-нод указывающих на неё. Topic-roots (depth=−1), facts,
goals, actions, evidence, questions, outcomes — **никогда не трогаются**.

### State-graph archiving

Tick-снапшоты старше 14 дней переносятся из `state_graph.jsonl` в
`state_graph.archive.jsonl`. Основной файл переписывается атомарно через
`.tmp` rename. Parent-цепочка переживает архив: последний retained entry
чейнится через `parent=<hash>` на архивный, но `read_all()` по умолчанию
архив игнорирует (cold storage).

Без архива файл вырос бы в гигабайт за месяцы активной работы.

### Триггеры

Вручную — `POST /graph/consolidate` с `dry_run=true` показывает что
удалилось бы.

Автоматически — `_check_night_cycle` раз в 24ч при низком NE (sleep-like).
В alert-поток пишется summary: `decayed N / pruned M / archived K`.

---

## Почему так

**LRU кэш?** Память — не ёмкость. Консолидация удаляет нерелевантное, не
«самое старое». Старая `goal = "написать диплом"` с субголами остаётся
через год.

**Просто удалять state_graph?** Git-аудит → детерминистический replay.
Архив холодный, но читаемый. Если нужно доказать что система прошла
через N состояний 3 месяца назад — JSONL там.

**Раз в 24ч?** Биологически — slow-wave sleep цикл консолидации.
Прагматически — ежедневный sanity check без overhead.

**NE gate?** Не стоит чистить граф пока юзер активно взаимодействует.
Ночная работа, не прерывание.

**Тот же `distinct()` для state_graph что и content?** Экономия кода —
NAND primitive один. Семантически оправдано: оба графа описывают
«мысли» (content — о мире, state — о самой системе).

---

## Sync-first интерпретация

Паттерны юзера становятся эмерджентными свойствами state-графа.
Например, кластер (`user_feedback == "rejected"` И `action == "compare"`)
→ этот юзер не любит long compare-карточки → следующее сравнение
попробовать dispute вместо. S адаптируется к конкретному носителю, не
хардкодом.

---

## Где в коде

- `src/state_graph.py` — `StateGraph` class: `append`, `read_all`,
  `tail`, `query_similar`, `ensure_embedding`
- `src/nand.py` — `analyze_tail`, `apply_policy_nudge` (секция meta-tick);
  также `tick_emergent`: `analyze_tail` в ASK check, `_emit()` добавляет
  state-ноды
- `src/consolidation.py` — `decay_unused_nodes`, `consolidate_content_graph`,
  `consolidate_state_graph`, `consolidate_all`
- `src/graph_logic.py` — `touch_node` (hebbian boost)
- `src/cognitive_loop.py` — `_check_night_cycle` вызывает
  `consolidate_all`, `_check_heartbeat` пишет periodic state-ноды
- `src/graph_routes.py` — endpoints `/graph/consolidate`,
  `/graph/self`, `/graph/self/similar`

**Открыто:** ротация archive.jsonl (gzip / cut-off), lineage-aware prune,
калибровка DECAY_PER_RUN после недели реальных данных
(см. [OQ #1](../planning/TODO.md)), multi-parent state_graph для
CONFLICT-ветвления, DMN walks по state-графу.

---

**Навигация:** [← Capacity](capacity-design.md) · [Индекс](README.md) · [Следующее: Storage →](storage.md)
