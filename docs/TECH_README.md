# baddle

> AHI Protocol — Augmented Human Intelligence.
> Не искусственный интеллект. Усиленный человеческий.

**[Установка](../SETUP.md)** | **[TODO](../planning/TODO.md)** | **[Docs](README.md)**

---

## Что это

Второй мозг, синхронизированный с первым через разум, тело и диалог.

Пишешь ассистенту — он определяет что за задача (выбор, исследование,
привычка, дебаты), запускает подходящий алгоритм мышления, отвечает
текстом. Под капотом граф мыслей с диалектической проверкой, скрытые
мосты находятся пока ты спишь, HRV-датчик подсказывает системе что ты
устал.

Работает локально на любой LLM через OpenAI-совместимый API (LM Studio,
Ollama, OpenAI). Протестировано на 8B моделях — размер не критичен.

---

## Три контура управления

Второй мозг синхронизирован с первым через три независимых канала:

```
1. Информационный:  prediction error → precision → params → идеи → confidence → surprise → ⟲
2. Физиологический: HRV coherence → γ/τ/precision → ширина конуса → ⟲
3. Диалоговый:      ассистент задаёт вопросы → ответ → новая нода → граф растёт → ⟲
```

Любой контур работает самостоятельно. Все три вместе = резонансная
система. Истоки идеи — [foundation.md](foundation.md).

---

## Ядро

**Граф мыслей.** Нода = числовая уверенность (0–1) + embedding. Рёбра —
cosine similarity. Граф растёт, уплотняется, сходится.

**Цикл мышления** ([tick-design.md](tick-design.md)). Фазы: generate —
merge — elaborate — doubt (Smart DC) — meta — synthesize. Один движок,
**логика возникает из различий** ([nand-architecture.md](nand-architecture.md)).
Близкие идеи объединяются, далёкие спорят, серединные развиваются.
Никаких хардкод-веток по режимам.

**Адаптивный контроллер** ([horizon-design.md](horizon-design.md)).
Система подстраивает ширину мышления под задачу: высокая уверенность в
модели → узкий фокус, точные решения; низкая → широкий поиск, больше
идей. Обратная связь — если предсказание не сошлось с реальностью,
фокус расширяется автоматически. 7 внутренних состояний с плавными
переходами.

**Синхронизация с человеком.** Главная метрика — насколько точно
система предсказывает именно **тебя**, не мир вообще. Расхождение между
её предположением и твоим действием — сигнал учиться. Всё остальное
(логика, тело, нейрохимия) работает на снижение этого расхождения.

---

## Инструменты

Пять категорий, одна метафора — операции с
[байесовским конусом](cone-design.md):

| Категория | Инструмент | Операция |
|---|---|---|
| **Генеративные** | Brainstorm / Expand / Elaborate | Расширение / Ветвление / Удлинение |
| **Проверочные** | [Smart DC](thinking-operations.md#smart-dc) / Ask | Сужение / Просвечивание |
| **Структурные** | Collapse / Rephrase / META | Фиксация / Перенаправление / Переоценка |
| **Навигационные** | Walk | Обход |
| **Поисковые** | [Pump](thinking-operations.md#pump) | Поворот |

---

## 14 режимов мышления

Любая задача = три вопроса: сколько целей? как связаны? есть ли финал?

| Режим | Когда | Стоп |
|---|---|---|
| Свободный | Сам управляешь | Ручной |
| Блуждание | Серендипити | Novelty exhaustion |
| Фокус | Одна конечная задача | Цель достигнута |
| Привычка | Регулярное действие | Никогда (streak) |
| Исследование | Изучить тему вглубь | Модель исчерпалась |
| Сборка | Все части обязательны | Всё готово |
| По шагам | Строго по порядку | Последний шаг |
| Приоритеты | Срочное первым | Всё выполнено |
| Баланс | Распределить ресурсы | Никогда (snapshot) |
| Любой вариант | Неважно какой результат | Первый найден |
| Мозговой штурм | Набросать максимум идей | Идеи кончились |
| Выбор | Сравнить и выбрать один | LLM-judge решил |
| Дебаты | Столкнуть позиции | Синтез найден |
| Байесовский | Проверить гипотезу числами | Открытый |

Все 14 — настройки одного движка, не разные алгоритмы. Система сама
определяет режим из твоего сообщения и контекста.

---

## HRV и тело как вход

Опциональный пульсометр (или симулятор для демо) измеряет вариабельность
сердечного ритма — видно устал ли ты, собран, есть ли стресс.

Baddle замечает это и адаптирует советы: низкая когерентность →
предлагает паузу; плохо спал → откладывает сложные решения на утро;
высокий стресс → алерт «подыши минуту».

Тело — сигнал о **тебе**, не о системе. Её внутренняя динамика
собственная. Детали — [hrv-design.md](hrv-design.md).

---

## Проактивность

Единый фоновый контур наблюдает за графом и телом. DMN continuous (10
мин) ищет мосты между далёкими нодами, DMN deep (30 мин) углубляет open
goal, DMN converge (60 мин) доводит граф до STABLE, ночной цикл (24ч)
переваривает эпизоды и чистит слабое. HRV alerts на резкое падение
coherence. Morning briefing собирает результат ночи.

UI поллит `/assist/alerts` → уведомления в чате. Полная карта — 21
check в [alerts-and-cycles.md](alerts-and-cycles.md).

---

## Энергия

Простой счётчик дневного ресурса принятия решений. Каждое обращение
тратит немного. Плохо спал — потолок на день ниже. Уровень упал до
критического — система сама предложит отложить сложные решения до утра.
Сбрасывается в полночь, история сохраняется локально.

---

## Научные основы

Каждый компонент опирается на известный когнитивный механизм. Не
буквально — по интуиции. Формулы в design-доках; здесь суть:

| Концепция | Где в Baddle |
|---|---|
| **Active Inference** (Фристон) | Адаптивный контроллер расширяет фокус при сюрпризе, сужает при уверенности |
| **Байесовский мозг** | Confidence 0–1 на каждой ноде, обновление при новых свидетельствах |
| **Диалектика** (Гегель) | Smart DC: тезис / антитезис / синтез |
| **Хебб** | Пути в графе по которым чаще ходишь становятся вероятнее |
| **Дивергенция / конвергенция** (Гилфорд) | Фаза «генерация» расходится, «объединение» сходится |
| **Рабочая память** (Миллер) | Большие группы похожих идей сжимаются в синтезы |
| **Хабитуация** | Фильтр новизны отбрасывает повторы |
| **System 2** (Канеман) | LLM в чате = System 1. Baddle с графом = System 2 |
| **Семантические сети** (Куиллиан) | Граф + Walk + Pump = скрытые оси |
| **DMN** | Cognitive loop Scout/DMN — граф думает пока ты не думаешь |
| **NAND-логика** | `distinct()` — один примитив, вся булева логика из зон d |

**[Конвергенция, не дизайн](cone-design.md#конвергенция-не-дизайн).**
Связность не была спроектирована. Каждый компонент — ответ на конкретную
проблему, и потом оказалось что он описан в науке 30 лет назад.

---

## Карта src/

51 файл, ≈24.5k LOC (post-B5, 2026-04-26). Группировка по responsibility — не по тому где сейчас живёт код, а **что код делает**. После W11 file consolidation некоторые файлы сольются.

### Substrate — state + dynamics (≈1956 LOC)

Substrate всех state и формул. После B5 Track B closed — РГК authoritative; UserState/Neurochem stub'ы для backward-compat.

| Файл | LOC | Что |
|---|---|---|
| `rgk.py` | 1038 | **Substrate.** Resonator (5 chem-axes + R/C bit + balance), РГК (пара mirrors + auxiliaries + projectors). Все формулы и state. Single source of truth |
| `horizon.py` | 587 | `CognitiveState` — adaptive controller (precision, policy, T, KL, maturity drift, state machine). Делегирует chem/freeze в `self.rgk` |
| `ema.py` | 329 | `EMA` / `VectorEMA` primitives + `Decays` / `TimeConsts` namespaces. Правило 2 каркас |
| `user_state.py` | 393 | Backward-compat shim над `_rgk.user`. После B5 W5 final — все методы 1-line delegates. Удалится в W10 |
| `user_dynamics.py` | 118 | Filesystem-touching helpers (`update_cognitive_load`, `rollover_day`) — выделены из user_state в B5 W2 |
| `neurochem.py` | 34 | Stub после удаления `Neurochem` + `ProtectiveFreeze` в B5 W3+W4. Migration mapping в комментариях |

### Loop — background cognitive processing (≈4019 LOC)

Continuous cycle над substrate. После W14 — workspace станет central convergence-точкой.

| Файл | LOC | Что |
|---|---|---|
| `cognitive_loop.py` | 2628 | Main background loop, `_advance_tick`, `_check_*` детекторы, throttle, briefings. **W14.7 split candidate**: bookkeeping + briefings + advance_tick |
| `detectors.py` | 890 | 13 pure-function детекторов (Правило 1). `Signal`-producers, dispatch'ятся через signals.Dispatcher |
| `surprise_detector.py` | 401 | 14-й детектор по факту, отдельно по historical reasons. **W11 #1**: move в detectors.py |
| `signals.py` | 301 | `Signal` + `Dispatcher` (budget, dedup, expires, counter-wave penalty). Правило 1 каркас |

### Graph — knowledge structures (≈3527 LOC)

Граф мыслей + операции. Правило 3 (нода) + Правило 4 (distinct) каркас.

| Файл | LOC | Что |
|---|---|---|
| `graph_logic.py` | 1682 | Operations: nodes/edges, `record_action`, `_bayesian_update_distinct`, snapshot, capacity-aware Bayes-freeze. Правило 3+4 |
| `tick_nand.py` | 499 | NAND tick: distinct → Bayes → emit. Inner loop |
| `consolidation.py` | 442 | Hebbian decay, REM-style сборка, archive. **W11 #3 + W14.8 candidate**: merge с pump_logic в `dmn.py`; расширить sequential integration |
| `pump_logic.py` | 374 | Scout — поиск мостов между далёкими нодами. **W11 #3**: merge с consolidation |
| `state_graph.py` | 368 | History тиков (otdellsy от main graph). Pulse heartbeat, state replay |
| `thinking.py` | 186 | NAND helpers: classify_nodes, _filter_lineage, _pick_target, _tick_force_collapse. **W11 #2**: rename + merge в `nand.py` с tick_nand + meta_tick |
| `meta_tick.py` | 172 | Policy adaptation на основе state_graph tail (последние 20 тиков). **W11 #2**: merge в `nand.py` |
| `graph_store.py` | 122 | Persistence для graph (jsonl + atomic write) |

### IO / HTTP routes (≈5519 LOC)

Inherent IO. После W14.5 split: `assistant.py` 3105 → ~150 + `src/routes/*.py`.

| Файл | LOC | Что |
|---|---|---|
| `assistant.py` | 3105 | **Главный** Flask blueprint. 60+ endpoints: /assist + chat + alerts + goals + activity + plans + checkins + profile + briefings + sensors. **W14.5 candidate**: split в `src/routes/{chat,goals,activity,plans,checkins,profile,briefings,misc}.py` |
| `graph_routes.py` | 1912 | Graph-related Flask endpoints: /graph/*, smartdc, pump triggers, lab UI. **W6 candidate**: 9 dead routes review |
| `api_backend.py` | 437 | LLM client wrapper (OpenAI-compat), embedding cache, depth/aperture defaults |
| `chat.py` | 65 | Tiny Flask blueprint для chat endpoints. **W11 #5 candidate**: merge с chat_history + chat_commands в `src/chat/` |
| `main.py` | 65 | Entry point — gunicorn / Flask app initialization |

### Domain logic — features (≈3722 LOC)

Specific business logic слой. Не substrate, не loop — конкретные вещи которые Baddle делает.

| Файл | LOC | Что |
|---|---|---|
| `assistant_exec.py` | 1469 | Heavy execution для chat: 14 modes router (`execute_deep` + variants), prompt building, RAG, depth engine |
| `suggestions.py` | 703 | Observation → suggestion pattern (от detect к UI card) |
| `chat_commands.py` | 425 | Slash-commands (`/как я?`, `/план`, `/запусти`, `/help`, etc.). **W11 #5**: merge в `chat/` |
| `intent_router.py` | 370 | LLM-классификация intent (какой mode подходит для message) |
| `modes.py` | 288 | 14 chat modes definitions (name, intro, prompts) |
| `chat_history.py` | 189 | Chat persistence + replay. **W11 #5**: merge в `chat/` |
| `prompts.py` | 167 | LLM prompts templates |
| `dialectic.py` | 106 | Thesis/antithesis/synthesis для SmartDC + chat |
| `demo.py` | 309 | Initial seeder — demo workspaces (work-demo + personal-demo) при первом запуске |
| `defaults.py` | 60 | Ship-with-code defaults (roles, templates) — JSON если data/ пуста. **W11 #6**: merge с demo в `seed.py` |

### Tasks / goals / plans (≈1112 LOC)

Three storage layers с overlapping API patterns. **W12 candidate** (поглощается в W15.2): shared `jsonl_store.py` primitive + добавление `tasks.py`.

| Файл | LOC | Что |
|---|---|---|
| `plans.py` | 368 | Daily plans (events на конкретное время). schedule_for_day, recurring matching |
| `recurring.py` | 373 | Циклические привычки (N раз в день/неделю). Streak counter, lag detection |
| `goals_store.py` | 371 | Долгосрочные цели + violations + solved archive. Rotation cycle |

### User model — persona / activity / sentiment (≈1374 LOC)

Не substrate (то живёт в РГК), а dimensions data о юзере.

| Файл | LOC | Что |
|---|---|---|
| `activity_log.py` | 577 | TaskPlayer event log (`activity.jsonl`), surprise tracking, replay |
| `patterns.py` | 400 | Pattern detection (паттерны поведения для предложений привычек) |
| `user_profile.py` | 308 | Профиль (preferences, constraints), 5 категорий, profile.json |
| `checkins.py` | 188 | Manual check-in (energy/focus/stress/reality), `_apply_to_user_state` |
| `sentiment.py` | 100 | LLM-classify message sentiment (light feeder в `valence`) |
| `user_state_map.py` | 85 | 8-region named_state карта (Voronoi-style по chem-профилю) |

### Sensors / HRV (≈862 LOC)

**W11 #4 candidate**: `src/sensors/` package.

| Файл | LOC | Что |
|---|---|---|
| `sensor_stream.py` | 290 | Generic sensor reading stream + adapter abstraction |
| `hrv_metrics.py` | 272 | RMSSD, coherence, stress derivation из RR-интервалов |
| `hrv_manager.py` | 205 | HRV state manager (running / paused), simulator + real adapter dispatch |
| `sensor_adapters.py` | 95 | Polar H10 (in development), simulator |

### Utility / specialized helpers

| Файл | LOC | Что |
|---|---|---|
| `prime_directive.py` | 299 | Hourly write of sync_error EMA в `data/prime_directive.jsonl`; `/assist/prime-directive` aggregate endpoint |
| `solved_archive.py` | 233 | RAG over solved goals (для retrieval похожих past решений) |
| `paths.py` | 70 | Все file paths в одном месте (DATA_DIR, GRAPHS_DIR, etc.) |
| `http_utils.py` | 61 | Atomic write + thread-safe Flask helpers |
| `__init__.py` | 0 | пакет marker |

---

## Архитектура

Концептуальная вертикаль. Карта файлов — выше, путеводитель — в [docs/README.md](README.md).

```
┌─────────────────────────────────────────────────────────────┐
│  МИР           браузер · календарь · погода · сенсоры        │
├─────────────────────────────────────────────────────────────┤
│  ТЕЛО          HRV — сердечный ритм как сигнал               │
├─────────────────────────────────────────────────────────────┤
│  ЧЕЛОВЕК       чат · уточнения · декомпозиция · feedback     │
├─────────────────────────────────────────────────────────────┤
│  ЦИКЛ          непрерывное фоновое мышление,                 │
│                фокус ↔ блуждание                             │
├─────────────────────────────────────────────────────────────┤
│  СОСТОЯНИЕ     нейрохимия + адаптивный фокус + защитный      │
│                режим при перегрузке                          │
├─────────────────────┬───────────────────────────────────────┤
│  ГРАФ ИДЕЙ          │  ГРАФ СОСТОЯНИЙ                        │
│  о чём думаешь      │  как и когда система думала            │
├─────────────────────┴───────────────────────────────────────┤
│  ЯДРО          единственная операция — различие между идеями │
├─────────────────────────────────────────────────────────────┤
│  ОБЛАСТИ       несколько графов, мосты между ними            │
├─────────────────────────────────────────────────────────────┤
│  ЛОКАЛЬНАЯ LLM детерминизм + мелкие вызовы, без облака       │
└─────────────────────────────────────────────────────────────┘
```

---

## Один ход

1. Пока ты не смотришь — система в фоне гуляет по графу, ищет
   неожиданные связи
2. Ты открываешь Baddle. Внутреннее состояние восстанавливается —
   продолжается с прошлой сессии, не с нуля
3. Пишешь сообщение. Внимание переключается с фонового блуждания на
   запрос
4. Один LLM-вызов определяет: что хочешь, какой режим подходит, нужны
   ли уточнения
5. Если запрос непонятен — встречный вопрос вместо ответа
6. Если задача сложная — предложит разбить на подзадачи
7. Иначе — генерит варианты, находит противоречия, проверяет, показывает
   карточку (дебаты / сравнение / идеи / вероятность)
8. Реагируешь 👍 или 👎 — система учится
9. Если много раз подряд промахивается — защитный режим, просит
   пересинхронизации
10. Уходишь — внимание падает, фоновое блуждание возобновляется

---

## Принципы

### Три архитектурных столба

**1. Паритет примитивов** (как устроено). Чтобы быть вторым мозгом,
надо говорить на том же когнитивном языке: мышление градиентом
различия вместо булевой логики, нейрохимия вместо хардкод-констант,
тело как сигнал, непрерывное фоновое мышление вместо request-response,
степень уверенности вместо «истины», обязательный антитезис. Без
паритета можно хранить о человеке, но не **думать** с ним.

**2. Синхронизация — прайм-директива** (что делать). Baddle не мозг в
коробке, а второй мозг **под конкретного человека**. Главная метрика
— насколько точно система предсказывает твоё следующее действие. Вся
архитектура в подчинении этой цели.

**3. Локальность + гранулярность** (как запускать). Локальная LLM — не
ради приватности, а ради **детерминизма и композиции**. Детерминизм —
чтобы любой шаг был воспроизводим. Гранулярность (много мелких
фокусных вызовов) делает композицию реалистичной. С облачным API это
нереально по цене и латентности.

**Следствие:** Baddle — это **протокол**, не приложение. Архитектура
универсальна, содержание (твой граф, твоё состояние, твои паттерны) —
персонально.

### Рабочие правила

- **Человек — Root Admin.** ИИ предлагает, человек решает
- **Уверенность, не истина.** 0.73 честнее чем «я точно знаю»
- **Принудительный антитезис.** Модель обязана показать другую сторону
- **Сходимость через исчерпание.** Стоп когда модель повторяется, не
  по лимиту
- **Тело = сигнал, не команда.** HRV говорит про тебя, не управляет
  системой напрямую
- **Логика возникает, не прописывается.** Никаких if-веток по режимам

---

## AGI vs AHI

| AGI | AHI |
|---|---|
| Заменяет человека | Усиливает человека |
| Чёрный ящик | Прозрачный граф |
| «Решает за вас» | «Предлагает, решаете вы» |
| Alignment через RLHF | Alignment через архитектуру |
| Нужно много GPU | Работает локально |

---

## Docs index

Полный путеводитель и порядок чтения — [docs/README.md](README.md).
Краткая карта:

| Слой | Что читать |
|---|---|
| **Overview** | [full-cycle](full-cycle.md) — статика + динамика в одном месте |
| **Основа** | [nand-architecture](nand-architecture.md) → [cone-design](cone-design.md) → [friston-loop](friston-loop.md) → [tick-design](tick-design.md) |
| **Когнитивный** | [horizon](horizon-design.md) → [neurochem](neurochem-design.md) → [symbiosis](symbiosis-design.md) → [user-model](user-model-design.md) → [hrv](hrv-design.md) → [capacity](capacity-design.md) → [episodic-memory](episodic-memory.md) |
| **Память** | [storage](storage.md) → [activity-log](activity-log-design.md) → [task-tracker](task-tracker-design.md) → [ontology](ontology.md) |
| **Реализация** | [thinking-operations](thinking-operations.md) → [dmn-scout](dmn-scout-design.md) → [closure-architecture](closure-architecture.md) |
| **Оптика проекта** | [world-model](world-model.md) · [resonance-model](resonance-model.md) · [action-memory-design](action-memory-design.md) · [alerts-and-cycles](alerts-and-cycles.md) |

---

## License

Baddle под **dual license:**

| Use case | License |
|---|---|
| Self-hosting, personal use, research, open-source форки | **AGPLv3** |
| Embed в закрытый коммерческий продукт, Cloud/SaaS, rebrand | **Commercial** |

Commercial — one-time perpetual. Цена по запросу зависит от use case и
масштаба. [LICENSE](../LICENSE) · [LICENSE-AGPL.txt](../LICENSE-AGPL.txt)
· [LICENSE-COMMERCIAL.md](../LICENSE-COMMERCIAL.md) · [EULA.md](../EULA.md).

**Запрос:** `kriusovia@gmail.com` — use case, legal entity, масштаб.

---

*Baddle: Не искусственный интеллект. Усиленный человеческий.*
