# DEMO — неделя с Baddle

Пошаговый сценарий на 7 дней. Каждый шаг — действие, ожидаемый результат,
и что **должно быть видно** в системе. Предполагается что `python ui.py`
запущен, LM-сервер доступен, и ты прошёл первый старт (см.
[SETUP.md](../SETUP.md)).

Каждый день = 5-15 минут активного взаимодействия. Остальное — система
работает в фоне, собирает данные.

**Подходит и для человека, и для LLM-агента** (например Claude Code):
в конце каждого дня есть «как проверить через API» — можно прогнать
автоматически без клика по UI.

---

## ⚙ Перед началом

Базовая конфигурация:
1. `python ui.py` → http://localhost:7860 открылся
2. В Settings задан `api_url` и `api_model` (обычно LM Studio на :1234)
3. В таб **baddle** кликни 👤 **Profile** — убедись что **ЕДА/ПИТАНИЕ**
   содержит хотя бы что-то (добавь если пусто: например `здоровое питание`
   в «Нравится»). Без профиля первый же вопрос про еду сработает как
   «profile_clarify» и ничего не ответит.
4. Если есть симулятор HRV — включи (пункт HRV в меню → ▶).

---

## День 1 — знакомство и первая цель

**Цель дня:** понять как работает ответ и увидеть визуальное мышление.

### Шаги

1. В баддл-табе напиши: **«чем мне заняться в выходные?»**
2. → **Ожидаешь:** появится анимация шагов (① записал цель → ② brainstorm →
   ③ углубил → ④ SmartDC). Финальный `synthesis` — абзац с рекомендацией.
3. Переключись на таб **graph** — увидишь созданные ноды (goal, hypothesis,
   evidence). Для тех кому интересно механика — вот это работа.
4. Кликни 🎯 **Цели** → в «Открытые цели» появился узел с твоим вопросом.
5. Закрой вопрос вручную если хочешь: кнопка ✓.

### Как проверить через API

```bash
# Есть ли новая цель?
curl http://localhost:7860/goals?status=open
# Должен быть хотя бы один goal с text = «чем мне заняться в выходные»
```

### Что делает система в фоне

- Записывает твой ввод в `state_graph.jsonl` (action=user_initiated).
- Эндорфинная реакция: `dopamine` растёт от новизны запроса.
- Через 10 мин запустится DMN — будет искать связи между нодами которые
  ты создал (пока их мало, вряд ли что-то найдёт).

---

## День 2 — добавь привычку

**Цель дня:** опробовать recurring-цель, научить систему отслеживать.

### Шаги

1. 🎯 **Цели** → секция **Привычки (recurring)** → жми `＋`.
2. Введи: `пить воду 4 раза в день`, интервал `4`, категория `health`,
   → **добавить**.
3. Запей стакан воды. Кликни **+1** на карточке привычки.
4. → **Ожидаешь:** прогресс стал `1/4`.
5. Через час-два (после реального стакана) повтори. Ещё раз **+1**. `2/4`.
6. Позже проверь: если не отметил 3-й стакан к ~18:00, система в chat
   сама пришлёт карточку `recurring_lag` — «⏰ «пить воду 4 раза»
   отставание 1». Это делает `_check_recurring_lag` раз в 30 мин.

### Как проверить через API

```bash
# Список recurring с прогрессом
curl http://localhost:7860/goals/recurring
# Должен вернуть recurring с done_today, expected_by_now, lag

# Записать ещё один instance вручную
curl -X POST http://localhost:7860/goals/instance \
  -H 'Content-Type: application/json' \
  -d '{"id":"<goal_id>","note":"стакан"}'
```

### Что делает система

- Каждые 30 мин `_check_recurring_lag` сканит отставания.
- Dedup: не спамит чаще чем раз в 60 мин на одну цель.
- Если ты делаешь `+1` — фронт подтягивает новый прогресс без рестарта.

---

## День 3 — добавь ограничение

**Цель дня:** посмотреть как работает constraint (автодетект + ручная отметка).

### Шаги

1. 🎯 → **Ограничения (constraints)** → `＋`.
2. Текст: `не ем сахар после обеда`, polarity: `избегать`, категория `food`.
3. В баддл-табе напиши: **«хочу что-то сладкое, мороженое или торт»**
4. → **Ожидаешь:** обычная карточка с идеями **плюс** отдельная карточка
   `⚠ Зафиксировал нарушение ограничений: «не ем сахар после обеда»` —
   это LLM-автодетект сработал.
5. 🎯 → секция ограничений: `сегодня: 1`.
6. Попробуй ручную отметку: клик `−1` на карточке ограничения, в promt
   напиши «ел шоколад в 15:00».
7. → **Ожидаешь:** счётчик вырос до `сегодня: 2`.

### Как проверить через API

```bash
# Статус ограничений
curl http://localhost:7860/goals/constraints
# violations_today и violations_7d покажут счётчики

# Отправить нейтральное сообщение — violation не должен записаться
curl -X POST http://localhost:7860/assist \
  -H 'Content-Type: application/json' \
  -d '{"message":"подумай что почитать","lang":"ru"}'
# В ответе cards НЕ должно быть constraint_violation
```

### Красный флаг

- LLM-скан пишет violation на каждый запрос → система слишком чувствительна,
  проверь `scan_message_for_violations` в [src/recurring.py](../src/recurring.py).
- При явной фразе про сахар не срабатывает → LLM может быть слишком
  консервативный. Логи `[scan_violations]` покажут промпт и ответ.

---

## День 4 — сложная цель с подзадачами

**Цель дня:** увидеть AND-семантику decompose.

### Шаги

1. В баддл-табе: **«собрать MVP для проекта»** (без уточнений).
2. → **Ожидаешь:** classify решит что это `complex_goal`, появится
   карточка `decompose_suggestion` — «Задача выглядит сложной. Разбить?»
3. Жми **Разбить**. LLM разложит на 3-5 подзадач (AND-семантика).
4. Переключись на **graph** — видишь goal + subgoals + рёбра.
5. Маркируй 2 subgoal'а как done (кликни по ноде → context menu →
   confidence 0.9, или через tick просто подожди).
6. → **Ожидаешь:** когда все subgoals закрыты, цель автоматически
   переедет в «Завершённые / архив решений».

### Как проверить через API

```bash
# Goal архивирован?
curl http://localhost:7860/goals/solved | head -c 500
# Должны быть snapshot_ref + goal_text

# Посмотреть archived graph
curl http://localhost:7860/goals/solved/<snapshot_ref>
```

### Что ожидать

- `should_stop()` Case 1 (AND/OR): если subgoals далеко в embedding
  (разные части) — требуется ВСЕ verified. Если похожи (альтернативы) —
  хватит одного.

---

## День 5 — два workspace и cross-graph bridge

**Цель дня:** почувствовать multi-context + DMN связи между темами.

### Шаги

1. Клик 🗂 (workspaces) → «Создать новый». Назови `personal`.
2. В `personal` напиши: **«как наладить вечерний ритуал чтобы засыпать быстрее»**
3. Дождись ответа (5-10 сек).
4. Переключись обратно на `main`. Напиши: **«хочу меньше стресса на работе»**
5. Дождись.
6. Теперь подожди ~1 час (или ускорь через `DMN_CROSS_GRAPH_INTERVAL`
   в [src/cognitive_loop.py](../src/cognitive_loop.py)) — DMN просканит
   пары воркспейсов.
7. → **Ожидаешь:** алерт `🔗 Cross-graph мост найден: main ↔ personal`
   (если тема «стресс на работе» семантически близка к «засыпать
   быстрее» — они про recovery).

### Как проверить через API

```bash
# Список workspaces
curl http://localhost:7860/workspaces

# Meta-граф с cross_edges
curl http://localhost:7860/workspace/meta
# Должен показать nodes=[main, personal] и edges с connections

# Подсмотреть текущие alerts
curl http://localhost:7860/assist/alerts
```

---

## День 6 — HRV и activity zone

**Цель дня:** увидеть как тело влияет на advice.

### Шаги

1. В HRV-панели (🫀) включи симулятор.
2. Сдвинь coherence на `0.3`, rmssd на `20`, activity на `2.0`
   (движешься на стрессе = overload).
3. → **Ожидаешь:** в status badge видишь 🔴 `overload`, alert
   `zone_overload` в сhat.
4. В баддл: **«что мне сделать сейчас?»**
5. → **Ожидаешь:** совет учтёт физиологию — «остановись, отдохни».
   В `profile_hint` LLM видит `activity_zone=overload`.
6. Теперь coherence `0.8`, rmssd `60`, activity `0.1`
   (лежу, спокойный). → зона 🟢 `recovery`.

### Как проверить через API

```bash
# Текущее состояние тела
curl http://localhost:7860/assist/state | python -c \
  'import sys,json; d=json.load(sys.stdin); print(d["user_state"]["activity_zone"])'

# Симулировать ручкой
curl -X POST http://localhost:7860/hrv/simulate \
  -H 'Content-Type: application/json' \
  -d '{"coherence":0.3,"rmssd":20,"activity":2.0}'
```

---

## День 7 — retro + weekly review + reset

**Цель дня:** посмотреть панораму недели, разобраться с накопленным шумом.

### Шаги

1. Вечером: 📆 **Weekly review** — график решений по дням, распределение
   режимов, streaks привычек.
2. 🎯 **Цели** → посмотри:
   - Сколько recurring не забыто (streaks)
   - Сколько раз нарушил constraints
   - Какие complex goals закрыл через decompose
3. Если хочешь начать с нуля: Settings → **Danger zone** → 🗑 «Reset
   all user data» → введи `RESET` для подтверждения. Удалит всё кроме
   settings/roles/templates + рестарт.

### Как проверить через API

```bash
# Weekly
curl http://localhost:7860/assist/weekly-review

# Reset (осторожно, удалит данные!)
curl -X POST http://localhost:7860/data/reset \
  -H 'Content-Type: application/json' \
  -d '{"confirm":"RESET"}'
```

---

## День 8 — intent router и связка taskplayer ↔ recurring

**Цель дня:** увидеть как двухуровневый classifier распределяет ввод
по kind'ам и как taskplayer автоматически засчитывает прогресс
recurring-целей.

### Что делает router

Любое сообщение в baddle проходит через `intent_router.route(message)`:
- **Уровень 1** определяет *что это вообще* — `task` / `fact` /
  `constraint_event` / `chat`
- **Уровень 2** — подтип — `new_goal` / `new_recurring` / `instance` / ...
- Для instance/violation — матчинг к активным recurring/constraint целям
  через LLM

Результат — быстрая ветка вместо полного `execute_deep` (экономит ~8-10с
на сообщениях где думать не надо).

### Шаги

**(а) Chat fast-path.** Напиши: **«привет, как дела?»**
  → **Ожидаешь:** ответ за ~1.5 сек, мода `chat`, карточек нет
  — просто текст. Никаких нод в графе не создаётся.

**(б) Instance fast-path.** Сначала убедись что есть recurring цель про
воду (сделано в день 2). Напиши: **«только что выпил стакан воды»**
  → **Ожидаешь:** карточка `instance_ack` с зелёным фоном:
  «♻✓ пить воду 4 раза в день — 2/4».
  Счётчик в 🎯 Цели тоже вырастет без полного execute_deep.

**(в) Draft-confirm для новой привычки.** Напиши: **«хочу начать бегать
каждое утро»**
  → **Ожидаешь:** карточка `intent_confirm` фиолетовым — «♻ Создать
  привычку?» с кнопками **Нет / Изменить / Да, создать**.
  Жми **Да** — привычка появится в 🎯 Цели → Привычки.
  Или **Изменить** — откроется форма с предзаполненным текстом для корректировки.

**(г) Draft для constraint.** Напиши: **«хочу меньше пить кофе»**
  → **Ожидаешь:** карточка «⛔ Создать ограничение?» (polarity=avoid).

**(д) Taskplayer → goals (симметрия слева).** Открой **Tasks** sub-page.
  1. Клик **＋ Начать**, вводишь: **«Обед»** → Enter.
  2. → **Ожидаешь:** в chat'е system-сообщение:
     «♻✓ Засчитал в «покушать 3 раза в день» — 1/3 сегодня»
     (если такая recurring цель есть из day 2).
  3. Открой 🎯 Цели → счётчик привычки вырос.
  4. На активной задаче появилась кнопка **? Помощь** — жми → авто-
     переключение в chat с pre-fill «Помоги с задачей «Обед» — что
     делать дальше?». Можно задать уточняющий вопрос.

**(е) Chat → taskplayer (симметрия справа).** Напиши: **«начал тренировку»**
  → **Ожидаешь:** синяя карточка `🎬 Трекер запущен: «Тренировка» (health)`
  с кнопкой **отменить**. В Tasks sub-page activity-bar уже показывает
  запущенную задачу и тикает таймер. Если у юзера есть recurring цель
  про тренировки — она тоже получит +1 instance автоматически.

  Варианты триггеров: «пошёл гулять» → «Прогулка», «сейчас пишу код» →
  «Код», «обедаю» → «Обед» + instance если есть recurring про еду.

**(ж) Help из любой точки.** В 🎯 Цели на каждой recurring/constraint
  строке кнопка **?** — открывает chat с «Помоги с «[текст цели]» —
  что делать?». Можно спрашивать совет по любой из активных целей.

### Как проверить через API

```bash
# Chat fast-path
curl -X POST http://localhost:7860/assist \
  -H 'Content-Type: application/json' \
  -d '{"message":"привет","lang":"ru"}' | python -c \
  'import sys,json; d=json.load(sys.stdin); print("mode:", d.get("mode"), "ir:", (d.get("intent_router") or {}).get("kind"))'
# → mode: chat  ir: chat

# Instance fast-path (требует активной recurring)
curl -X POST http://localhost:7860/assist \
  -H 'Content-Type: application/json' \
  -d '{"message":"только что выпил воды","lang":"ru"}' | python -c \
  'import sys,json; d=json.load(sys.stdin); print("mode:", d.get("mode"), "cards:", [c.get("type") for c in d.get("cards") or []])'
# → mode: instance_ack  cards: ["instance_ack"]

# Taskplayer → goals: start + auto-match
curl -X POST http://localhost:7860/activity/start \
  -H 'Content-Type: application/json' \
  -d '{"name":"Обед"}' | python -c \
  'import sys,json; d=json.load(sys.stdin); print("matched:", d.get("matched_recurring"))'
# → matched: {goal_id, goal_text, progress}

# Chat → taskplayer: auto-start трекера
curl -X POST http://localhost:7860/assist \
  -H 'Content-Type: application/json' \
  -d '{"message":"начал тренировку","lang":"ru"}' | python -c \
  'import sys,json; d=json.load(sys.stdin); print("mode:", d.get("mode")); cards=d.get("cards") or []; print("cards:", [c.get("type") for c in cards]); [print("  name:", c.get("activity_name"), "cat:", c.get("category")) for c in cards if c.get("type")=="activity_started"]'
# → mode: activity_started  cards: ["activity_started"]  name: Тренировка cat: health
```

### Что делает система

- `intent_router._classify_top()` — один короткий LLM-call (max_tokens=15)
- `intent_router._classify_subtype_*()` — второй LLM-call только для
  task/fact. Для chat — пропускается.
- Кэш LRU-100 с TTL 5 мин: повторные одинаковые вопросы — без LLM-токенов.
- `try_match_recurring_instance()` — внутри `/activity/start`, безопасный
  fallback: если LLM упал или не нашёл match — activity работает как раньше.

### Красный флаг

- «привет» идёт в полный execute_deep → router упал или кэш сломан.
  Логи `[intent_router]` должны показывать top classify.
- «только что поел» не матчит рекурсивную цель → проверь что у цели
  `category="food"` и что `list_recurring()` её возвращает `active_only=True`.
- Все новые цели создаются с `kind=oneshot` → router всегда отдаёт
  `question` вместо `new_recurring/new_constraint`. Проверь LLM-ответ
  на subtype classify.

---

## Итоги недели

К концу семи дней в `data/` у тебя должно быть:

- **goals.jsonl** — события: 1-2 complex goals (закрыты или открыты),
  1-2 recurring с instances по всем дням, 1-2 constraints с violations
- **state_graph.jsonl** — сотни тиков (по heartbeat каждые 5 мин = ~2000/неделю)
- **user_state.json** — dopamine/serotonin/NE отражают твои feedbacks,
  `long_reserve` упал на 30-60 пунктов, `valence` колеблется в ±0.3
- **workspaces/index.json** — 2 воркспейса, возможно 1-3 cross_edges

**Что точно должно работать:**
- Мышление с учётом profile constraints (нет орехов в совете)
- LLM авто-детект нарушений constraints
- Recurring lag алерты
- Cross-graph мосты между темами

**Что может не работать:**
- Polar H10 BLE (сейчас симулятор)
- Календарь / Погода (не интегрированы, см. TODO.md)
- Tool-use (deferred)
- Мобильный UI (Telegram Mini App — в планах)

---

## Если что-то сломалось

Логи в stdout процесса. Типичные симптомы:

| Симптом | Где копать |
|---------|-----------|
| LM не отвечает | `/assist/state` → `api_health.status`; LM Studio → Local Server → Start |
| Recurring lag не алертит | Логи `[cognitive_loop] recurring_lag`, возможно NE > 0.55 блокирует DMN |
| Violation не детектится | Логи `[scan_violations]`, constraint должен быть `active_only=True` |
| `/assist` возвращает 400 | UTF-8 encoding тела; используй `--data-binary` в curl |
| Кнопки в модале не работают | F12 → Console — проверь нет ли «is not defined» |

Быстрый sanity check всей системы:
```bash
curl http://localhost:7860/assist/state | python -c \
  'import sys,json; d=json.load(sys.stdin); print("api:", d.get("api_health",{}).get("status")); print("neurochem:", d["user_state"]["dopamine"], d["user_state"]["serotonin"])'
```

---

**Навигация:** [← README](../README.md) · [SETUP](../SETUP.md) · [TESTS](TESTS.md) · [TODO](../TODO.md)
