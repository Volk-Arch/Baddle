# baddle

> AHI Protocol — Augmented Human Intelligence.
> Не искусственный интеллект. Усиленный человеческий.

**[Установка](SETUP.md)** | **[TODO](TODO.md)** | **[Docs](docs/)**

---

## Что это

Второй мозг, синхронизированный с первым через разум, тело и диалог.

Пишешь ассистенту — он определяет что за задача (выбор, исследование, привычка, дебаты...), запускает подходящий алгоритм мышления, отвечает текстом. Под капотом — граф мыслей с диалектической проверкой, скрытые мосты находятся пока ты спишь, HRV-датчик подсказывает системе что ты устал.

Работает локально на любой LLM через OpenAI-совместимый API (LM Studio, Ollama, OpenAI). Протестировано на 8B моделях, но архитектурно размер не критичен.

---

## Как выглядит

Чат, не граф. Граф живёт под капотом (tab `graph` для любопытных).

```
Ты  > Какую машину купить?
B   > Сравню варианты и выберу лучший. Дай мне список.
       [режим: Выбор · энергия 88/100 · HRV 0.73]

Ты  > BMW, Tesla, Toyota
B   > [запускает верификацию каждого через Smart DC,
       считает lean/tension, затем LLM-судья выбирает]
       → Tesla (экологичность перевешивает цену)
         confidence 91%, regret risk 12%

B   > ⚠ Энергия 15/100. Отложим сложные решения до утра.
```

Полный демо-мокап: [Article/mockup.html](Article/mockup.html). Питч: [Article/PITCH.md](Article/PITCH.md).

---

## Три контура управления

Второй мозг синхронизирован с первым через три независимых канала:

```
1. Информационный:  prediction error → precision → params → идеи → confidence → surprise → ⟲
2. Физиологический: HRV coherence → γ/τ/precision → ширина конуса → ⟲
3. Диалоговый:      ассистент задаёт вопросы → ответ → новая нода → граф растёт → ⟲
```

Любой контур работает самостоятельно. Все три вместе = резонансная система. Источник идеи → [docs/origin-story.md](docs/origin-story.md).

---

## Ядро

### Граф мыслей

Нода = числовая уверенность (0–1) + embedding. Рёбра = cosine similarity. Граф растёт, уплотняется, сходится.

### Цикл мышления ([подробнее](docs/tick-design.md))

```
GENERATE    мало идей?           → Brainstorm
MERGE       есть похожие?        → Collapse
ELABORATE   есть голые?          → Elaborate
DOUBT       есть непроверенные?  → Smart DC (тезис/антитезис/синтез)
META        всё проверено?       → "Что упустил?"
SYNTHESIZE  ничего нового        → Финальный текст
```

Один движок — **NAND emergent** ([v8](docs/nand-architecture.md)): действие определяется зонами distinct()-расстояния между нодами. Classic (switch по primitive AND/OR/XOR) удалён в v8d — primitive/strategy/goal_type остались только как UI-metadata в `modes.py`.

### CognitiveHorizon ([подробнее](docs/horizon-design.md))

Один параметр **precision** управляет всем:

| Precision | Temperature | top_k | Novelty | Режим |
|-----------|-------------|-------|---------|-------|
| 0.3 | 0.7 | 73 | 0.88 | Широкий поиск, креативность |
| 0.5 | 0.5 | 55 | 0.90 | Баланс |
| 0.8 | 0.2 | 28 | 0.93 | Узкий фокус, точность |

Обратная связь: `surprise = 1 - confidence` → precision корректируется. Замкнутый контур.

С HRV добавляется: `γ = γ₀ + η·(stress − coherence)`. Тело модулирует строгость проверки. Высокая когерентность → мягкое обучение, низкая → жёсткая фильтрация.

5 состояний: EXPLORATION, EXECUTION, RECOVERY, INTEGRATION, + STABILIZE/CONFLICT через HRV. Переходы — с гистерезисом и debounce.

### sync_error

Главный метрик персонализации: `d(model_prediction, user_action)`. Система учится предсказывать не только мир, но конкретного тебя.

---

## Инструменты

Пять категорий, одна метафора — операции с [байесовским конусом](docs/cone-design.md):

| Категория | Инструмент | Что делает | Операция |
|-----------|-----------|-----------|----------|
| **Генеративные** | Brainstorm | N независимых идей от ноды | Расширение |
| | Expand | Новый ракурс (sibling) | Ветвление |
| | Elaborate | Углубление + evidence | Удлинение |
| **Проверочные** | [Smart DC](docs/smartdc-design.md) | Тезис/антитезис/синтез + per-pole analysis | Сужение |
| | Ask | Probing-вопрос | Просвечивание |
| **Структурные** | Collapse | Группа → один синтез | Фиксация |
| | Rephrase | Переформулировка | Перенаправление |
| | META | "Что упустил?" | Переоценка |
| **Навигационные** | Walk | Random Walk по transition probabilities | Обход |
| **Поисковые** | [Pump](docs/pump-design.md) | Две идеи → скрытые оси связи | Поворот |

---

## 14 режимов мышления

Любая задача = три вопроса: сколько целей? как связаны? есть ли финал?

| Режим | Когда | Пример | Стоп |
|-------|-------|--------|------|
| **Свободный** | Хочешь сам управлять | Ручной режим | Сам решаешь |
| **Блуждание** | Нет цели, ищешь неожиданное | Серендипити | Novelty exhaustion |
| **Фокус** | Одна конечная задача | "Написать статью" | Цель достигнута |
| **Привычка** | Регулярное действие | "Зарядка каждый день" | Никогда (streak) |
| **Исследование** | Изучить тему вглубь | "Разобраться в экономике" | Модель исчерпалась |
| **Сборка** | Все части обязательны | "Подготовить документы" | Всё готово |
| **По шагам** | Строго по порядку | "Рецепт" | Последний шаг |
| **Приоритеты** | Срочное первым | "Очередь задач" | Всё выполнено |
| **Баланс** | Распределить ресурсы | "Работа/семья/здоровье" | Никогда (snapshot) |
| **Любой вариант** | Неважно какой результат | "Найти такси" | Первый найден |
| **Мозговой штурм** | Набросать максимум идей | "Варианты решения" | Идеи кончились |
| **Выбор** | Сравнить и выбрать один | "Какую машину купить" | LLM-judge решил |
| **Дебаты** | Столкнуть позиции | "За и против" | Синтез найден |
| **Байесовский** | Проверить гипотезу числами | "Вероятность дождя" | Открытый |

Под капотом — 5 примитивов (`none / focus / AND / OR / XOR / bayes`) × тип цели × стратегии. Один движок, 14 настроек.

Ассистент определяет режим автоматически из текста (`detect_mode()` в modes.py) — пользователь не выбирает.

---

## HRV и тело как вход

Опциональный Polar H10 датчик (или встроенный симулятор для демо):

```
RR-интервалы → RMSSD/SDNN/coherence/LF-HF
            → stress, coherence, energy_recovery
            → Horizon: γ, τ_in, τ_out, α, precision
            → 5 состояний с HRV-триггерами
```

Симулятор (`HRVSimulator`) генерирует реалистичные RR-интервалы с RSA-модуляцией. Можно крутить ползунки HR/coherence в UI — видно как система реагирует.

Детали: [docs/hrv-design.md](docs/hrv-design.md)

---

## Проактивность (watchdog)

Фоновый поток наблюдает за графом и телом:

- **Scout ночной** (каждые 3 часа): Pump между случайными далёкими нодами, сохраняет лучший мост
- **DMN continuous** (каждые 10 минут): короткий Pump, предлагает инсайты не сохраняя
- **HRV alerts**: coherence < 0.25 → auto-alert "сделай паузу"
- **Morning briefing**: при первом открытии после 6 утра — восстановление % + энергия + план
- **Weekly review**: паттерны и streaks за 7 дней

UI поллит `/assist/alerts` → всплывают уведомления в чате.

---

## Энергия

Простой счётчик: `energy = max(0, 100 - decisions_today * 6)`, модулируется HRV recovery.

- Каждое сообщение ассистенту = +1 decision
- При HRV: `max = 40 + 60 * recovery` (плохо спал → низкий потолок)
- При energy < 20 → proactive alert "отложи сложное"
- Сбрасывается в полночь

Персистится в `user_state.json` с историей последних 200 взаимодействий.

---

## Научные основы

Каждый компонент — реализация концепции из нейробиологии. Не математически строго — та же интуиция, рабочее решение.

| Концепция | Компонент | Как работает |
|-----------|-----------|-------------|
| **Active Inference** (Фристон) | [CognitiveHorizon](docs/horizon-design.md) | Precision → temperature. Surprise → расширить/сузить конус |
| **Байесовский мозг** | Confidence + Bayes + Bayesian mode | `P(H\|E) = P(E\|H)×P(H)/P(E)`. Beta-распределения для приоров — не только вероятность, но и уверенность в ней |
| **Диалектика** (Гегель) | [Smart DC](docs/smartdc-design.md) | За/против/контекст → centroid → синтез |
| **Хебб** | Transition prob | `tp += lr × (1-tp)`. Частые пути усиливаются |
| **Дивергенция/конвергенция** (Гилфорд) | [Tick cycle](docs/tick-design.md) | Brainstorm = расширение, Collapse = сужение |
| **Рабочая память** (Миллер) | Collapse | 50 нод → 5 синтезов |
| **Хабитуация** | [Novelty check](docs/novelty-design.md) | Embedding sim + rephrase-before-reject + адаптивный threshold |
| **System 2** (Канеман) | Весь инструмент | LLM в чате = System 1. LLM в Baddle = System 2 |
| **Семантические сети** (Куиллиан) | Граф + Walk + [Pump](docs/pump-design.md) | Pump = скрытые оси |
| **DMN** (default mode network) | Watchdog Scout/DMN | Граф думает пока ты не думаешь |
| **NAND-логика** | [distinct()](docs/nand-architecture.md) | Один примитив, вся булева логика возникает из зон d |

### [Конвергенция, не дизайн](docs/convergence-divergence.md)

Связность не была спроектирована. Каждый компонент — ответ на конкретную проблему, и потом оказалось что он описан в науке 30 лет назад.

---

## Архитектура

```
┌────────────────────────────────────────────────────────┐
│  ASSISTANT (assistant.py, assistant.js)                │
│  chat-first UI · detect_mode() · energy counter        │
│  /assist · /assist/morning · /assist/weekly · alerts    │
├────────────────────────────────────────────────────────┤
│  WATCHDOG (watchdog.py)                                │
│  background: Scout Pump (3h) · DMN (10min) · HRV alerts│
├────────────────────────────────────────────────────────┤
│  РЕЖИМЫ (modes.py)                                     │
│  14 конфигов + detect_mode + stop conditions           │
├────────────────────────────────────────────────────────┤
│  HORIZON (horizon.py)                                  │
│  precision · γ · sync_error · τ_in/τ_out · 5 states    │
│  update_from_hrv()                                     │
├────────────────────────────────────────────────────────┤
│  TICK (tick_nand.py)                                   │
│  NAND emergent: зоны distinct() → generate/merge/doubt │
├────────────────────────────────────────────────────────┤
│  HRV (hrv_manager.py, hrv_metrics.py)                  │
│  PolarH10 (todo) | HRVSimulator | RMSSD/LF-HF/coherence│
├────────────────────────────────────────────────────────┤
│  ГРАФ (graph_logic.py)                                 │
│  Ноды + рёбра + Байес (classic + distinct) + Beta(α,β) │
│  Smart DC · Pump · Collapse · Walk · Compare           │
├────────────────────────────────────────────────────────┤
│  NAND core (main.py)                                   │
│  distinct() · distinct_decision() · Bayes via distinct │
└────────────────────────────────────────────────────────┘
         ↕ API (api_backend.py)
   LM Studio / llama-server / OpenAI / ...
```

---

## Принципы

- **Человек — Root Admin.** ИИ предлагает, человек решает
- **Confidence, не истина.** 0.73 честнее чем "я уверен"
- **Принудительный антитезис.** Модель обязана показать другую сторону
- **Сходимость через исчерпание.** Стоп когда модель повторяется, не по лимиту
- **Тело = вход, не метрика.** HRV влияет на систему, а не наоборот
- **Feature flags, не перезаписи.** NAND рядом с classic, не вместо

---

## AGI vs AHI

| AGI | AHI |
|---|---|
| Заменяет человека | Усиливает человека |
| Чёрный ящик | Прозрачный граф (tab `graph`) |
| "Решает за вас" | "Предлагает, решаете вы" |
| Alignment через RLHF | Alignment через архитектуру |
| Нужно много GPU | Работает локально |

---

## Docs

| Документ | Что описывает |
|----------|--------------|
| [cone-design](docs/cone-design.md) | Байесовский конус — метафора, 5 операций, prediction error |
| [tick-design](docs/tick-design.md) | Tick cycle — фазы, dispatcher, subgoals, stop conditions |
| [horizon-design](docs/horizon-design.md) | CognitiveHorizon — precision, policy weights, состояния |
| [smartdc-design](docs/smartdc-design.md) | Smart DC — 3 полюса, centroid, per-pole analysis |
| [pump-design](docs/pump-design.md) | Pump — облака, мосты, итеративный поиск |
| [novelty-design](docs/novelty-design.md) | Novelty check — embedding sim, rephrase-before-reject |
| [hrv-design](docs/hrv-design.md) | HRV — тело как вход, резонансный режим |
| [nand-architecture](docs/nand-architecture.md) | NAND — distinct(A,B) вместо всей логики, feature flag |
| [life-assistant-design](docs/life-assistant-design.md) | Life Assistant — энергия, питание, задачи, HRV |
| [convergence-divergence](docs/convergence-divergence.md) | Конвергенция/дивергенция — теоретический фундамент |
| [origin-story](docs/origin-story.md) | Как пять проектов сошлись в одном |
| [epilogue](docs/epilogue.md) | Последняя страница |

---

## Статус

**Работает:**
- Chat-first интерфейс, ассистент **реально запускает граф** на запрос
- 7 режимов с исполнителями: Dispute (FOR/AGAINST/SYNTHESIS карточки), Tournament (winner + reason), Bayesian (prior/posterior%), Research/Focus (ideas + Smart DC), Brainstorm, Habit
- **Visible thinking** — steps анимируются ("✓ шаг 1, ✓ шаг 2") перед ответом
- **Scout bridges в чате** — watchdog фоном находит связи, они всплывают как сообщения с карточкой
- HRV симулятор + метрики + интеграция в Horizon
- Watchdog с реальным Pump (Scout 3ч + DMN 10мин) в фоне
- NAND — единственный tick engine (v8d, все зоны distinct, нет primitive-switch)
- Энергия + проактивные алерты + morning briefing + weekly review

**В разработке:**
- Остальные 7 режимов (AND семейство) — интро пока без исполнения
- Реальный Polar H10 BLE (сейчас только симулятор)
- Cone визуализация в UI
- v8 embeddings-first, state_origin, 3-layer pipeline
- SSE/WebSocket вместо polling для instant updates

Установка и запуск → [SETUP.md](SETUP.md)

*Baddle: Не искусственный интеллект. Усиленный человеческий.*
