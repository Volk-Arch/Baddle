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

## Автономность и память


## Ум расширенный

## UI / визуализация

- [ ] **Polar H10 cone viz с θ/φ** — сейчас конус рендерится по precision +
  state. Добавить polyvagal двухпараметрическую визуализацию когда будет
  реальный сенсор. (зависит от hardware integration)

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


## Архитектурный collapse (когда уберётся параллельная машинерия)

Эти не блокеры. Делать только когда тестовая нагрузка покажет что стоит.
По духу — то же что v8d сделал с primitive-switches: **слить две штуки в одну**.

---
---

# ⬇ СДЕЛАНО — как проверить что работает

Две разновидности тестов:
- **🧪 Пользовательские кейсы** ниже — narrative сценарии через UI,
  воспроизводи по шагам, сравнивай с ожидаемым. Основной способ убедиться
  что архитектура работает end-to-end.
- **Проверка / Живые тесты** внутри каждого блока дальше — технические
  API-проверки для debugging и «красный флаг если сломано».

## 🧪 Пользовательские кейсы

Запусти `python ui.py`, открой браузер, работай через UI-интерфейс.
Везде где видишь «→ Ожидаешь» — должно совпасть; не совпало = бага,
ищи «красный флаг» в соответствующем блоке ниже.

### A. Профиль пищи + рекомендация блюда
1. Кликни **👤** (в neurochem-панели `baddle`-таба) → секция
   **ЕДА / ПИТАНИЕ** → поле **Избегаю** → введи `не ем орехи` → `+`.
2. В том же разделе **Нравится** → `здоровое питание` → `+`.
3. Закрой модал, напиши в чат: `хочу покушать что-то простое`.
4. → **Ожидаешь:** карточка с 3-5 блюдами. Ни в одном нет орехов.
   Если блюда ориентированы на здоровое (овощи/каши/белок) — идеально.
5. Кликни **📊** → в sync-dashboard видна линия sync_error после запроса.

### B. Обучение профиля при пустой категории
1. Убедись что категория **ЗДОРОВЬЕ/ТЕЛО** пуста в 👤. Закрой.
2. Напиши: `как улучшить тренировки`.
3. → **Ожидаешь:** карточка фиолетовая «👤 Чтобы помочь лучше, мне
   нужно знать твои предпочтения и ограничения…».
4. В textarea введи `люблю бег утром, не переношу зал`. Жми **Сохранить**.
5. → **Ожидаешь:** ответ «✓ Запомнил: 1 предпочтение, 1 ограничение».
   Через ~1 сек появится повтор исходного запроса с обычным ответом.
6. Открой **👤** → в ЗДОРОВЬЕ/ТЕЛО видны chips `люблю бег утром`
   и `не переношу зал`. Они persist'ятся в `user_profile.json`.

### C. Goal-lifecycle через граф
1. Открой таб **graph**. В селекторе режима выбери **Выбор (tournament)**.
   В placeholder'е впиши `выбрать фреймворк\nReact\nVue\nSvelte` (enter
   между строками — это subgoals), type=goal, жми **+ Add**.
2. Кликни **🎯** — в списке **Открытые** появилась цель «выбрать
   фреймворк» с workspace=main, mode=tournament.
3. Вернись в graph, раскрой гипотезы (React/Vue/Svelte) — поставь
   им confidence >= 0.8 (контекст-меню на ноде → Confidence slider).
4. Жми **Run** — tick эмитит `action=stable, reason=GOAL REACHED`.
5. Снова **🎯** → цель переехала в **Завершённые / архив решений**
   с meta типа `4 нод · сегодня`.
6. → **Ожидаешь:** в `/goals/stats` completion_rate растёт, `solved/`
   содержит JSON-файл с final_synthesis.

### D. Feedback → нейрохимия и valence
1. Открой **📈** (sparkline). Посмотри текущие линии DA/S/NE/burnout.
2. Напиши любой запрос → дождись карточки.
3. Жми **👎** (rejected) 3-4 раза подряд.
4. → **Ожидаешь:**
   - В ТЫ-панели bar **Интерес** поднимается (reject = high d = новизна).
   - В ТЫ-панели bar **Усталость** поднимается понемногу.
   - Sparkline: dopamine-линия подняласьsharp, burnout-линия чуть вверх.
   - Если повторишь 4 раза → `sync_regime` может переключиться на
     `PROTECT` или `CONFESS`; alert в чате.

### E0. Activity zone (HRV × движение, 4 региона)
1. Жми **HRV on** → дождись coherence ~0.7.
2. Раскрой **⚙ HRV simulator** → появится слайдер **Activity** (0-3).
3. Наблюдай badge справа от named-state:
   - Activity 0 + coherence 0.7 → 🟢 **Восстановление**
   - Activity 2 + coherence 0.7 → 🔵 **Здоровая нагрузка**
   - Coherence 0.15 + Activity 2 → 🔴 **Перегрузка** + alert в чате
   - Coherence 0.15 + Activity 0 → 🟡 **Стресс в покое** + alert
4. Включи Activity=2, кликни **📈** → bar `norepinephrine` слегка
   выше из-за physical arousal (через named_state A-ось).

### E. Симбиоз режима (FLOW / REST / PROTECT / CONFESS)
1. Запусти HRV: клик **HRV on** → дождись coherence ~0.7.
2. → **Ожидаешь:** ⚡ FLOW, sync ~95%+ (оба state умеренные, близкие).
3. Сдвинь HRV-симулятор coherence → 0.1, HR → 110.
4. → через 5-10 сек NE вырастет, HRV stress растёт. Но system-сторона
   спокойна (граф пустой) → PROTECT или REST.
5. Открой **📊** — в sync-dashboard жёлтая линия пересекла 0.3-threshold.

### F. Утренний briefing
1. В 👤 → **КОНТЕКСТ** → поставь Подъём на текущий час (или −1).
2. Перезапусти сервер: `python ui.py`.
3. Жди до минуты — фоновый cognitive_loop сделает первый iter.
4. → **Ожидаешь:** в чате появится сообщение от «Утро»:
   `Доброе утро. Восстановление XX%. Состояние: нейтральное.
   Долгий резерв XX%. Открытых целей: N. …`
5. `/loop/status` → `last_briefing` > 0.
6. Если поставишь wake=23 и перезапустишь — briefing НЕ появится
   (локальный час < 23).

### G. Night cycle (требует 24h ожидания ИЛИ ручного триггера)
Ручной вариант (без ожидания):
1. В консоли: `POST /graph/consolidate {}` — проверь отдельно
   прунинг + архив.
2. Для REM-фаз надо набить state_graph тиками с разным |rpe|.
   Это требует реального live-использования → наблюдай за
   `/loop/status.last_night_cycle` на следующий день.
3. → **Ожидаешь** через 24h: alert `night_cycle` с summary:
   «Scout +мост · REM эмо pump 2 · REM merge 3 · прунинг 0 / архив 14».

### H. Continuity между сессиями
1. Реши 2-3 цели (см. кейс C), дождись archive.
2. Создай новый workspace: в header-dropdown `+ `; id=`test2`.
3. Switch на него — граф пустой.
4. → **Ожидаешь:** через ~1 сек 3 seed-ноды «💭 GOAL REACHED…»
   (наследуют embedding'и от прошлых решений).
5. Клик на ноду → `POST /graph/render-node {index, lang}` → появляется
   полный текст.

### I. Embedding-first brainstorm
1. `POST /graph/brainstorm-seed {"topic":"ML архитектура","n":5}`.
2. → **Ожидаешь:** 5 нод с `text="💭", rendered=false`, каждая с
   уникальным embedding. В UI отображаются как 💭-маркеры.
3. Клик на каждую → `render-node` разворачивает в осмысленный текст,
   сохраняя позицию в embedding-пространстве.

### J. Декомпозиция сложной цели в 3 группы
1. Напиши: `организовать день рождения другу`.
2. → intent=complex_goal → в карточке кнопка **Разбить**.
3. Жми → `/assist/decompose` returns:
   - AND: `[выбрать дату, составить список гостей, ...]`
   - XOR: `[ресторан или дома, ...]`
   - RESEARCH: `[предпочтения гостей, ...]`
4. Каждая группа имеет `mode_suggestions`: and=builder, xor=tournament,
   research=horizon.

### K. Меню дня и burnout risk prediction
1. `POST /assist/simulate-day` с planом из 9 tournament/dispute.
2. → **Ожидаешь:** `end_of_day.daily_remaining=0`, `long_reserve`
   просел на 10-20 единиц, `burnout_risk` вырос на 1-2%,
   `predicted_named_state.key` может быть `apathy` или `stress`.

---

Формат каждого блока ниже: **что делает** → **как проверить** (API-
уровень, для debugging) → **на что влияет** → **красный флаг если
сломано**.

## Activity zone — HRV × движение (4 региона)

**Что.** `UserState.activity_magnitude` — отдельный канал от акселерометра
(либо симулятор-слайдер, либо Polar BLE когда будет подключён).
Derived `activity_zone` классифицирует `(coherence, activity)` в 4 региона
(из прототипа HRV-Reader Игоря):
- `recovery`  🟢 — здоровое восстановление (HRV ok, движения нет)
- `stress_rest` 🟡 — беспокойство в покое (низкий HRV, движения нет)
- `healthy_load` 🔵 — здоровая нагрузка (HRV ok, движение есть)
- `overload` 🔴 — перегрузка (низкий HRV, высокая активность)

Плюс `activity_magnitude` подмешивается в Voronoi named_state
(`A = 0.7·cog_arousal + 0.3·min(1, activity/2)`) — бегущий юзер не
попадёт в «медитацию» даже при низких когнитивных скалярах.

Alerts в `/assist/alerts`: `zone_overload` (warning), `zone_stress_rest`
(info). Recovery / healthy_load — позитивные, не звонят.

Details → [docs/user-model-design.md](docs/user-model-design.md) секция 1c.

**Проверка (live test):**
```
1. HRV on → ждёшь coherence ~0.7
2. Activity slider 0 → badge 🟢 Восстановление
3. Activity slider 2 → badge 🔵 Здоровая нагрузка
4. Coherence slider 0.15 + activity 2 → badge 🔴 Перегрузка + alert
5. Coherence 0.15 + activity 0 → badge 🟡 Стресс в покое + alert
```

**Влияет на:** physical context в UserState. Одинаковые HRV/когнитивные
скаляры значат разное когда юзер лежит vs бежит. Советы Baddle через
alerts теперь учитывают physical load, не только mental.

**Красный флаг.**
- `activity_zone.key = None` при запущенном HRV → `hrv_coherence` не
  приходит в UserState (проверь `/hrv/metrics` endpoint).
- Badge не переключается при сдвиге слайдера → hrv_manager не
  прокидывает `activity_magnitude` в `get_baddle_state`.
- Бегущий юзер в «meditation» → named_state A-weight не применён
  (проверь `0.7·cog + 0.3·phys` в user_state.py).

## Morning briefing push + wake/sleep context

**Что.** `CognitiveLoop._check_daily_briefing()` раз в ~сутки (после
`profile.context.wake_hour`, default 7) генерит короткий morning-briefing
и кладёт в alert queue. Текст собирается без LLM из:
- HRV energy_recovery % (если HRV запущен)
- `UserState.named_state` label (лёгкая оценка)
- `long_reserve` процент
- Кол-во open goals + первая
- Совет по recovery (сложные задачи / беречь энергию)

Alert type `morning_briefing` рендерится UI как normal assistant-сообщение
с `mode_name: "Утро"`. Dedupe по типу — один раз в сутки.

Editor wake/sleep/profession добавлен в 👤 Profile modal (блок «Контекст»).

**Проверка.**
```
GET /loop/status → last_briefing > 0 после первого тика после wake_hour
/assist/alerts → содержит {type: "morning_briefing", text, hour}
Profile modal → блок «Контекст»: wake (0-23) / sleep (0-23) / профессия
```

**Живые тесты.**
- Поставь `wake_hour=23` → briefing не триггерится ночью.
- Поставь `wake_hour=5`, перезапусти → в 5:00 утра cognitive_loop
  эмитит alert, UI показывает: «Доброе утро. Восстановление 82%.
  Состояние: поток. Долгий резерв 75%. Открытых целей: 3. ...»
- dedupe: второй вызов `_check_daily_briefing()` в тот же день не
  добавляет повтор.

**Красный флаг.**
- `/loop/status.last_briefing = 0` спустя час после wake_hour →
  `_check_daily_briefing` не вызывается (проверь что он в `_loop`
  независимо от NE-гейтинга).
- Briefing срабатывает каждый раз после рестарта → BRIEFING_INTERVAL
  (20h) не соблюдается при `last_briefing=0` + локальный час > wake_hour.
  Это by-design: fresh start после дня молчания → уместно показать briefing.

## Static storage — Profile / Goals / Archive + uncertainty-learning

**Что.** До этого Baddle была только динамикой (tick, sync, neurochem).
Статики — «кто юзер, что решает, как решал раньше» — не было; всё жило
в эфемерных нодах графа и умирало при reset. Этот блок закрывает gap.

**Три хранилища:**
1. **User Profile** ([src/user_profile.py](src/user_profile.py),
   `user_profile.json`) — 5 категорий (food/work/health/social/learning)
   × {preferences, constraints} + context (profession/wake/sleep/tz).
2. **Goals Store** ([src/goals_store.py](src/goals_store.py),
   `goals.jsonl` append-only) — events: create/complete/abandon/update.
   Replay → current state + stats (completion_rate, avg_time, by_mode/category).
3. **Solved Archive** ([src/solved_archive.py](src/solved_archive.py),
   `solved/{ref}.json`) — snapshot графа + state_trace + final_synthesis
   при goal-resolved. Полный replay «как решалась задача».

**Замкнутый цикл (profile-aware flow):**
```
message → _detect_category (keyword)
  ├─ category NONE или profile пуст → profile_clarify card
  │       юзер отвечает → parse_category_answer (LLM) → add_item → auto-retry
  └─ profile_hint = summary(category) → classify_intent_llm(...hint)
                                     → execute_via_zones(..., hint)
                                     → LLM генерирует учитывая constraints
```

**Lifecycle hooks:**
- `/graph/add {node_type:"goal"}` → `goals_store.add_goal()`, `goal_id` на ноде
- `tick_nand` STOP CHECK → `archive_solved()` + `complete_goal(snapshot_ref)`,
  `_goal_completed` flag на ноде чтобы не дублировать

**Endpoints:**
```
GET/POST /profile, /profile/add, /profile/remove, /profile/context, /profile/learn
GET /goals?status=open&category=food&workspace=main
POST /goals/add, /goals/complete, /goals/abandon, /goals/update
GET /goals/stats
GET /goals/solved, /goals/solved/{ref}
```

**UI:** 👤 Profile modal + 🎯 Goals modal + `profile_clarify` in-chat card
(textarea + Save/Skip + auto-retry original message).

Details → [docs/static-storage-design.md](docs/static-storage-design.md).

**Проверка.**
```
1. Fresh: GET /profile → все 5 категорий пустые
2. /assist {message:"хочу покушать"} → profile_clarify card
3. User: "не ем орехи, люблю курицу" → /profile/learn → profile updated
4. Auto-retry → 3 варианта блюд учитывая constraints
5. /graph/add {node_type:goal, ...} → GET /goals/open → появилась
6. tick до goal-resolved → GET /goals/solved → snapshot в архиве
```

**Влияет на:** замыкание daily-use цикла. Juzer перестаёт повторять одно
и то же каждый раз. Goals aggregate в статистику. Прошлые решения можно
открыть и увидеть как думал.

**Красный флаг.**
- profile_clarify не всплывает при пустой food-категории → `_detect_category`
  не матчит keyword (добавь ключевое слово в `_CATEGORY_KEYWORDS`).
- goal completed но нет записи в /goals/solved → `_goal_completed` flag
  не установлен, либо archive_solved упал (check logs).
- Profile modal не закрывается → CSS `.weekly-modal {display: flex !important}`
  переопределяет inline style none. Поправлено селектором `[style*="flex"]`.

## UI-батч: sparkline + sync-dash + weekly + timeline + meta-graph

**Что.** Пять UI-фич одним ходом, все на чистом inline SVG без Chart.js.

1. **Neurochem sparkline** — 4 цветные линии (dopamine/serotonin/NE/burnout)
   поверх баров, 30 последних тиков. Кнопка 📈 (toggle). Источник:
   `GET /assist/history?limit=30`.
2. **Sync-dashboard** — collapsible панель с line-chart `sync_error` во
   времени + reference line 0.3 (порог sync-high) + топ-3 режима где были
   rejects. Кнопка 📊. Источник: `GET /assist/history?limit=80`.
3. **Weekly review modal** — кнопка 📆 открывает modal с тремя SVG-чартами:
   решения по дням (7 bars), распределение режимов (horizontal bars),
   streaks привычек (horizontal bars с green gradient). Источник:
   `POST /assist/weekly` + расширенное поле `daily_series`.
4. **Timeline UI polish** — `.neuro-timeline-item` получил цветовой border-
   left по `state_origin` (orange held / grey rest) + по action
   (pink `ask` / green `stable`). Вертикальные «мазки» как в TODO.
5. **Meta-graph overlay** — кнопка 🗺 в graph-tab разворачивает SVG-
   наложение с circular-layout workspaces + edges (cross_graph bridges).
   Источник: `GET /workspace/meta`. Активный workspace подсвечен
   purple + stroke.

Плюс **named user-state badge** ("😐 нейтральное" / "🌊 поток" / …) —
derived property `UserState.named_state` visible в header.

**Проверка.**
```
GET /assist/history?limit=30 → {entries, top_rejected_modes, count}
GET /assist/history?limit=80 → full timeseries with sync_error per tick
POST /assist/weekly → +daily_series (7 дней bucket'ы) + hrv_trend
```
UI: открой baddle tab → кликни 📈 / 📊 / 📆 / ⏱ → все панели открываются
и заполняются. graph tab → кнопка 🗺 показывает meta-graph.

**Влияет на:** видимость динамики. До этого UI был frozen snapshot —
мгновенные значения скаляров. Теперь виден **тренд**: как sync_error
менялся, куда дрейфует DA, какие режимы чаще дают rejects, какой день
недели был продуктивный.

**Живые тесты.**
- Кликни 📈 → sparkline появляется, 4 цветные линии видны.
- Кликни 📊 → sync-dashboard разворачивается с yellow-линией + dashed
  0.3 threshold.
- Кликни 📆 → весь week отчёт в modal, закрывается × или кликом вне.
- Timeline-items после кликов ⏱ имеют разные цвета border-left в
  зависимости от state_origin и action.

**Красный флаг.**
- `/assist/history.entries = []` при непустом state_graph.jsonl → parse
  error в endpoint (проверь `datetime.fromisoformat(...)` в выводе).
- Sparkline plos → line flat → проверь что `entry[key]` в числе (не None).
- Modal не закрывается → inline `display:none` overridden CSS `!important`
  (использовать `[style*="flex"]` селектор — уже поправлено).

## Cache classify результатов

**Что.** `classify_intent_llm` в [src/assistant.py](src/assistant.py) теперь
мемоизирует результат в module-level TTL-LRU кэше:
- Ключ: `(message.strip().lower()[:300], lang)`
- TTL: 5 минут (после дня настроение юзера меняется, перекласифицирует)
- Capacity: 100 entries (LRU — самый старый вытесняется)
- Только `source="llm"` результаты кешируются; fast-path / default /
  LLM-failures не кешируются (чтобы восстановившийся LLM сразу начал работать)

На cache hit возвращает dict с `source="cache"` — UI видит откуда пришёл.

**Проверка.**
```
1-й `POST /assist {"message":"BMW vs Tesla какую выбрать"}`
  → classify_source: "llm", ~2.3s elapsed

2-й тот же запрос в течение 5 мин
  → classify_source: "cache", ~1.5s (сэкономили ~800ms LLM classify)
  (execute всё ещё делает LLM-calls для карточек, полного zero-time нет)
```

**Влияет на:** экономия токенов при reload/retry и повторных одинаковых
запросах. Особенно важно при UI refresh, обратной навигации, или
демо-показе когда одно и то же сообщение гоняется несколько раз.

**Живые тесты.**
- Два одинаковых `/assist` запроса подряд → второй с `classify_source: "cache"`.
- Разный case/whitespace → тот же cache hit (нормализация в ключе).
- Разные lang (ru/en) → разные entries в кэше.
- Через 5 минут — TTL expired, снова miss.

**Красный флаг.**
- `source` всегда "llm" → cache не пишется (проверь `_classify_cache_put`
  после `_parse_classify_output`).
- `source="cache"` возвращает mismatched mode → проверь что ключ включает
  lang (разные языки дают разные classifiers).

## Декомпозиция в подграфы разных режимов

**Что.** `POST /assist/decompose` вместо плоского `[подзадача 1, 2, 3, ...]`
теперь возвращает **3 labeled группы**:
- `and` — все обязательны (сборка, шаги плана) → `mode_suggestions.and = "builder"`
- `xor` — выбор одного варианта → `mode_suggestions.xor = "tournament"`
- `research` — открытое исследование → `mode_suggestions.research = "horizon"`

Parser в [src/assistant.py](src/assistant.py) `_parse_decompose_groups`
читает строки формата `AND: ... / XOR: ... / RESEARCH: ...` (case-insensitive,
терпит bullets/numbering), раскладывает в dict из 3 bucket'ов.
Backward compat — `subgoals` в ответе остаётся как concat (and+xor+research).

**Проверка.**
```
POST /assist/decompose {"message":"организовать день рождения"}
→ {
    "groups": {
      "and": ["выбрать дату", "подготовить стол"],
      "xor": ["ресторан или дома"],
      "research": ["предпочтения гостей"]
    },
    "mode_suggestions": {"and":"builder","xor":"tournament","research":"horizon"},
    "subgoals": [все 4 конкатом],
    "raw": "..."
  }
```

**Влияет на:** сложная задача раскладывается на три независимых
subgraph'а каждый со своим режимом Horizon'а — вместо одного monolithic
goal с плоским списком. UI может создать 3 goal-ноды с правильными
пресетами precision/policy. Или юзер явно выбирает какую группу взять.

**Живые тесты.**
- `/assist/decompose {"message":"выбрать машину для семьи"}` → обычно
  возвращает XOR (выбор между типами) + RESEARCH (отзывы/стоимость) +
  AND (безопасность/комфорт как требования).
- Простая задача где всё equals → groups.and содержит все, xor/research
  пустые. mode_suggestions содержит только `and`.

**Красный флаг.**
- `groups` всегда пустые при непустом `raw` → парсер не матчит префикс
  (LLM может выдать "1. **AND**:" с жирным или вариации — добавить в
  startswith список).
- `mode_suggestions` не содержит некоторых bucket'ов → это нормально,
  они добавляются только при непустой группе.

## REM-цикл + unified night cycle

**Что.** Ночной 24-часовой цикл в [src/cognitive_loop.py](src/cognitive_loop.py)
объединяет три раньше-параллельных механизма в одну последовательность:

1. **Scout** (pump+save persistent bridge) — раньше 3h interval, теперь
   фаза 1 ночного цикла
2. **REM emotional** (новое) — находит state_nodes с `|recent_rpe| > 0.15`
   в последних 100 записях, запускает Pump между парами их
   `content_touched`. «Эмоционально-насыщенные эпизоды перерабатываются
   поверх тех нод которые удивили».
3. **REM creative** (новое) — ищет пары content-нод с `distinct(emb) < 0.2`
   И BFS-path ≥ 3 («близкие в смысле, далёкие в пути»). Топ-3 по
   парадоксальности получают manual_link. Collapse в synthesis — явно
   юзер через `/graph/collapse` (ночью дорого LLM-синтез).
4. **Consolidation** — прунинг слабых нод + архив state_graph (было 24h
   separate).

DMN continuous (10min) и state-walk (20min) остаются отдельными —
это не ночные события, а постоянный фоновый пульс.

Один alert на ночь вместо 4 разных:
```
Ночной цикл: Scout +мост · REM эмо pump 2 · REM merge 3 · прунинг 1 / архив 14
```

**Проверка.**
```
GET /loop/status
  → last_night_cycle: timestamp последнего прохода
  → last_dmn / last_state_walk / last_foreground_tick — отдельные
  → поле last_scout убрано (фолдировано)
```

**Влияет на:** архитектурная чистота. Scout + REM + Consolidation
последовательны (как slow-wave → REM → cleanup в биологии), а не три
параллельных check'а. Новая REM creative фаза находит неочевидные связи —
ноды в разных областях графа с одинаковым смыслом.

**Живые тесты.**
- После 24h работы → `last_night_cycle` обновится. `/assist/alerts` покажет
  одну `night_cycle`-запись с summary.
- Подай серию evidence которая даёт high RPE → state_nodes с |rpe|>0.15
  накопятся. Следующий night cycle REM emo запустит Pump по их content.
- Создай 6+ нод в двух кластерах с одной парой похожих-но-неподключённых
  → REM creative добавит `manual_link` между ними (видно в graph tab).

**Красный флаг.**
- `rem_emotional.candidates` всегда 0 → `recent_rpe` не пишется в
  state_snapshot (проверь что tick_nand сохраняет snapshot через
  horizon.get_metrics()).
- `rem_creative.merged` всегда 0 при большом графе → пороги слишком
  жёсткие (поправь `REM_CREATIVE_DIST_MAX` / `_PATH_MIN`).
- `last_scout` всплыл в /loop/status → фалбек на старое поле; нужно
  почистить.

## Валентность эмоций юзера

**Что.** UserState получил отдельный скаляр `valence ∈ [−1, 1]` — знак
переживания (приятно/неприятно), независимый от arousal (который уже
ловили через D/NE). Сигналы EMA:
- `accepted` feedback → +0.7 вклад (95% decay)
- `rejected` feedback → −0.7 вклад + streak bias: 3+ rejects подряд вычитают дополнительные −0.05·(overshoot)
- quick input (<30с gap) → слабый +0.2
- long silence (>5мин gap) → слабый −0.2

Реализовано в [src/user_state.py](src/user_state.py). Surface в `to_dict`
+ `/assist/state.user_state.valence`, персистится между сессиями.

**Проверка.**
```
POST /assist/feedback {"feedback":"rejected"} × 7
GET /assist/state → user_state.valence ≈ −1.0 (streak bias полностью выжат)

POST /assist/feedback {"feedback":"accepted"} × 3
GET /assist/state → user_state.valence ≈ +0.19
```

**Влияет на:** UX-понимание юзера. Раньше видно было только arousal
(напряжение/интерес), но не знак: высокий DA мог быть и любопытством,
и стрессом. Теперь `valence` даёт ответ. Будущий advice-слой может
учитывать: low valence + low arousal = apathy (разный fix от "reject
streak"); low valence + high arousal = stress.

**Живые тесты.**
- Кликни 👍 3 раза → `/assist/state.user_state.valence` вырастет ~0.2.
- Кликни 👎 3 раза → спад, возможно в отрицательные значения.
- Оставь графа без interaction на 10 мин, потом напиши → valence чуть просядет.

**Красный флаг.**
- valence всегда 0 → feedback endpoint не вызывает `update_from_feedback`
  (проверь что в assistant.py feedback handler дёргает UserState).
- 7 rejects не выжигают valence до −1.0 → streak bias не триггерится.

## Предиктивная user-модель (prototype integration из MindBalance)

**Что.** UserState обогащён четырьмя концептами из прототипов Игоря:

1. **Signed prediction error.** `UserState.expectation` (медленный EMA
   reality), `surprise = reality − expectation` signed ∈ [−1, 1],
   `imbalance = |surprise|` (MindBalance ID). Направление ошибки
   сохраняется — подъём ≠ спад.
2. **Named user-states.** [src/user_state_map.py](src/user_state_map.py)
   содержит 10 регионов в (T=serotonin, A=(D+N)/2) пространстве:
   flow, inspiration, curiosity, gratitude, neutral, meditation, apathy,
   stress, disappointment, burnout. `user.named_state` — derived @property.
3. **Dual-pool energy.** `long_reserve` (2000-capacity) в UserState,
   cascading debit: при daily<20 налог 30% уходит в long. Ночное
   восстановление через HRV. `burnout_risk = 1 − long/max`.
4. **Decision cost by mode.** `_MODE_COST` таблица: simple (fan/scout/free=3)
   → critical (tournament/dispute=12). `_log_decision` списывает по mode_id.

Plus: **`POST /assist/simulate-day`** endpoint для прогноза «если я
запланирую X, Y, Z — что будет к концу дня» (clones UserState, шагает,
возвращает predicted named_state + burnout_risk).
Details → [docs/user-model-design.md](docs/user-model-design.md).

**Проверка.**
```
GET /assist/state
  → user_state.expectation/reality/surprise/imbalance/named_state/long_reserve

POST /assist/simulate-day {"plan":[{"mode":"tournament"}×9]}
  → {end_of_day: {daily_remaining:0, long_reserve:~1495,
                   predicted_named_state: {...}}}

GET /assist/named-states
  → {states: [10 regions...]}
```

**Влияет на:** UserState stops being "just a sync partner" — она теперь
**физическая система** со своей энергетикой, прогнозами и узнаваемыми
эмоциональными паттернами. Ключевое: `surprise` signed (знает: реальность
лучше/хуже ожиданий), `named_state` даёт UX-читаемую метку вместо 4
скаляров, `long_reserve` моделирует хронический износ.

**Живые тесты.**
- Тратя 9 решений mode=tournament (12 каждое) → `daily_remaining=0`,
  `long_reserve` тапается на ~6 единиц (tax 30% при low daily).
- Юзер в состоянии dopamine=0.85, serotonin=0.85, norepinephrine=0.9 →
  `named_state.key == "flow"`.
- После полуночи (`last_reset_date` < today) → `long_reserve` +=
  ~110·hrv_recovery (ночное восстановление).
- Перезапуск сервера → UserState сохраняется (user_state.json).

**Красный флаг.**
- `surprise` всегда 0 → `tick_expectation` не вызывается (проверь, что
  хуки после _clamp в update_from_* стоят).
- `named_state.key` всегда "neutral" → T/A координаты не меняются (все
  скаляры застряли на 0.5).
- `long_reserve` не восстанавливается после сна → `recover_long_reserve`
  не зовётся в `_ensure_daily_reset`.

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

## Архитектурный collapse — modes + renderers

**Что.** Две параллельные чистки:

1. **14 modes → compact tuples.** MODES dict теперь — кортеж на режим
   `(name_ru, name_en, goals, fields, placeholder_ru, placeholder_en,
   intro_ru, intro_en, renderer_style, preset)`. Убраны неиспользуемые
   поля (`description*`, `tooltip`). Политики вынесены в `_P_*` константы
   (4 базовых шаблона покрывают все 14). Presets (precision/policy/target)
   фолдированы в MODES — **один источник истины**; дубликат PRESETS dict
   в `create_horizon` удалён. `get_mode(id)` собирает flat-dict из кортежа
   (backward compat для callers).

2. **5 renderers → `_resolve_renderer()` + `_render_card()`.** В
   `execute_via_zones` убрана дупликация style×zone branching.
   `_resolve_renderer(style, zones)` → final renderer ∈
   {dialectical, comparative, cluster, explore}. `_render_card(renderer,
   ideas, zones, lang)` — одна функция собирает карточку. Не трогает
   `card.type` значения (dialectic/comparison/ideas_list остаются
   теми же, frontend не ломается).

**Проверка.**
- `list_modes()` → 14 records, UI селектор показывает все 14 опций.
- `POST /graph/add {mode: "tournament", node_type: "goal"}` → нода с
  `mode="tournament"` сохранена, subgoals парсятся если `goals_count=="2+"`.
- `POST /graph/tick` на нём → `tick_engine="nand"`, preset применяется.
- `/assist {message: "BMW vs Tesla"}` → classify выбирает tournament,
  card.type="comparison" приходит на фронт.

**Влияет на:** код-квалитет, не поведение. Меньше повторяющихся строк,
один источник истины для preset'ов. Frontend invariants нетронуты.

**Красный флаг.**
- Селектор UI пустой / < 14 → MODES dict порядок сломан или
  `list_modes()` не возвращает нужного формата.
- `create_horizon(mode_id)` даёт странные precision — значит `_preset`
  helper вернул не тот dict (`preset["precision"]` vs `preset.precision`).
- `execute_via_zones` возвращает `card.type` отличный от ожидаемых
  (dialectic/comparison/ideas_list) → frontend потеряется.

## Cross-graph seed — continuity между сессиями

**Что.** Выводы прошлых сессий (записанные как tick-entries в state_graph)
извлекаются и инжектятся в новый content-граф как **seed-ноды** с
унаследованными embedding'ами. Модуль [src/cross_graph.py](src/cross_graph.py)
фильтрует state_graph по приоритету action'ов (stable GOAL REACHED →
collapse → pump-bridge → smartdc → compare), дедупит по content_touched,
сортирует по важности/свежести. `seed_from_history()` создаёт ноды с
`rendered=False`, `seeded_from=<state_hash>`, `seeded_timestamp=...` для
провенанса.

**Проверка.**
```
POST /workspace/seed-from-history
     {"days": 7, "limit": 5, "graph_id": "main"}
  → {created: [idx, ...], skipped_dup, skipped_no_emb, total_considered}

POST /workspace/switch {"id": "work"}
  → если target пустой, auto_seed=true (по умолчанию) подбросит 3 seed'а
```

**Влияет на:** жизнь между днями. Новая сессия не с пустого листа —
система помнит «что решила вчера», даже если юзер открыл workspace
впервые за неделю. Embedding'и из state_embeddings переживают в новый
граф, distinct/tick работают поверх них сразу. Текст seed-ноды — stub
"💭 reason...", разворачивается через `/graph/render-node`.

**Живые тесты.**
- Закрой Baddle, подожди день, открой новый workspace на ту же тему →
  `POST /workspace/seed-from-history` покажет conclusions из старых
  sessions.
- `/workspace/switch` на пустой workspace → автоматически 3 seeds.
  `auto_seed=false` в body отключает.
- Второй вызов `seed-from-history` не дублирует — проверяет `seeded_from`
  в существующих нодах.

**Красный флаг.**
- `skipped_no_emb` равен `total_considered` → embedding cache пуст
  (не было `ensure_embedding` у старых entries → проверь api_get_embedding).
- `created: []` при непустом state_graph → возможно `graph_id` не
  совпадает, или все conclusions старше `days`.

## Embedding-first brainstorm + text-on-demand

**Что.** Два связанных endpoint'а:

1. **`POST /graph/brainstorm-seed`** — принимает topic, embeddит его один
   раз, затем генерирует N **vector-seeds** через Gaussian perturbation в
   embedding space (dimension-invariant: `sigma` — desired L2 norm шума).
   Novelty-фильтрация против существующих embeddings + уже принятых.
   Создаёт ноды с `text="💭", rendered=false, embedding=<perturbed>`. **LLM
   текстовой генерации нет** — только 1 embed-call на seed.
2. **`POST /graph/render-node`** — разворачивает unrendered-ноду в текст
   лениво, когда юзер её открыл. Контекст для LLM: topic + до 3 соседних
   rendered текстов. Idempotent (возвращает cached если уже rendered).

Плюс `rendered: bool` в каждой node (default `True` для backward compat).
distinct/routing работают сразу поверх unrendered — embedding есть.

Реализация: `sample_in_embedding_space()` в [src/graph_logic.py](src/graph_logic.py)
+ endpoints в [src/graph_routes.py](src/graph_routes.py).

**Проверка.**
```
POST /graph/brainstorm-seed {"topic":"...","n":5}
  → {created: [idx1, ...], n_sampled: 5}

POST /graph/render-node {"index": idx1, "lang":"ru"}
  → {text: "...", cached: false}
POST /graph/render-node {"index": idx1}
  → {cached: true}
```

**Влияет на:** скорость brainstorm-фаз. Вместо N LLM-генераций +
N embed-calls + novelty-reject: 1 embed + N perturbation + novelty
геометрически + LLM текст только для кликнутых (≈20% при типовом use).
Также чистота distinct-routing: embedding не смещён lexical choice,
он исходит из геометрии.

**Живые тесты.**
- `POST /graph/brainstorm-seed {"topic":"экономика","n":5}` → 5 нод
  с 💭, все с embedding. `/graph/recalc` покажет распределение сходств.
- `POST /graph/render-node {"index":0}` → реальный текст одним LLM-call.
  Повторный вызов → cached:true, тот же текст.
- `distinct` между seed-нодами: большинство пар в 0.4–0.5 диапазоне
  (новизна соблюдена).

**Красный флаг.**
- `n_sampled` всегда < `n_requested` → novelty_threshold слишком жёсткий
  или max_distance_from_seed слишком маленький.
- Ноды сохраняют `💭` после render-node → endpoint не обновляет
  `node["text"]` + `node["rendered"] = true`.

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
