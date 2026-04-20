# Alerts and Cycles

Справочник того что Baddle делает **сам в фоне**: 20 периодических check'ов + 21+ типов alerts/cards. Нужно чтобы понять «что должно прилететь в UI и когда» и дебажить молчаливые фоны.

Всё живёт в одном процессе — `CognitiveLoop` (см. [src/cognitive_loop.py](../src/cognitive_loop.py)). Один поток крутится с adaptive-интервалом (15 сек когда HRV активно, до 60 сек в idle) и дёргает `_check_*` методы. Каждый check имеет свой throttle (см. «Throttle intervals» ниже).

---

## 20 фоновых check'ов

| # | Check | Throttle | Когда срабатывает | Alert type (если emit'ит) | Нагрузка |
|---|-------|----------|-------------------|---------------------------|----------|
| 1 | `_check_night_cycle` | 24ч † | Раз в сутки: Scout pump+save, REM emotional, REM creative, consolidation | `night_cycle` | 🔴 heavy (LLM + pump) |
| 2 | `_check_dmn_continuous` | 10 мин † | Юзер idle + NE low + граф ≥ 4 нод | `dmn_bridge` (если bridge.text ≥ 10 и quality > 0.5) | 🔴 heavy (LLM pump) |
| 3 | `_check_dmn_deep_research` | 30 мин † | Idle + NE low + ≥ 1 open goal + граф ≤ 30 нод | `dmn_deep_research` | 🔴 heavy (execute_deep) |

Интервалы с † **растягиваются рассинхроном** — см. «Adaptive idle» ниже.
| 4 | `_check_dmn_converge` | 60 мин † | Idle + NE low + граф ≥ 5 нод | `dmn_converge` | 🔴 heavy (autorun loop) |
| 5 | `_check_dmn_cross_graph` | 60 мин † | ≥ 2 workspace'а с embeddings | `dmn_cross_graph` + пишет `cross_edges` в `workspaces/index.json` | 🔴 heavy (cosine N×M) |
| 6 | `_check_state_walk` | 20 мин † | state_graph имеет ≥ 10 past samples | `state_walk` («похожий момент: тогда я X») | 🟡 medium (embedding query) |
| 7 | `_check_daily_briefing` | 20ч | Утро (≥ wake_hour из profile) + не был сегодня | `morning_briefing` с sections (sleep/recovery/energy/bridges/goals/pattern) | 🟢 light |
| 8 | `_check_hrv_push` | 15с | HRV manager активен | — (sync HRV → UserState, не alert). **Примечание:** после HRV polymorphism читает из sensor stream `latest_hrv_aggregate` когда миграция завершится; сейчас через `hrv_manager.get_baddle_state()` | 🟢 light |
| 9 | `_check_low_energy_heavy` | ~10 мин | Юзер пытается открыть тяжёлую цель с energy < 20 | `low_energy_heavy` | 🟢 light |
| 10 | `_check_heartbeat` | 5с | Всегда | — (debug log `[heartbeat]`) | 🟢 light |
| 11 | `_check_plan_reminders` | 1 мин | За N мин до события в плане дня | `plan_reminder` | 🟢 light |
| 12 | `_check_recurring_lag` | 60 мин | У recurring-цели `lag > 0` за сутки | `recurring_lag` | 🟢 light |
| 13 | `_check_observation_suggestions` | 24ч | Паттерны / checkins / stress / weekly → draft через LLM | `observation_suggestion` (intent_confirm card) | 🟡 medium (1 LLM call) |
| 14 | `_check_evening_retro` | раз в день | `wake_hour + 14ч` — вечер настал | `evening_retro` (unfinished plans + check-in hint) | 🟢 light |
| 15 | `_check_activity_cost` | 1 мин | Долгая активная activity съедает energy | — (debit energy) | 🟢 light |
| 16 | `_check_ws_flush` | 5 мин | Граф изменился с последнего сохранения | — (persist to disk) | 🟢 light |
| 17 | `_check_hrv_alerts` | 1 мин | Coherence упал ниже критической | `coherence_crit` | 🟢 light |
| 18 | `_check_sync_seeking` | 2ч | `desync_pressure > 0.3` + idle > 2ч + 30мин quiet после других proactive | `sync_seeking` (LLM-генерация, soft card) | 🟡 medium (1 LLM call) |
| 19 | `_check_agency_update` | 1ч | `schedule_for_day()` непустой | — (обновляет `UserState.agency` EMA из completed/planned) | 🟢 light |
| 20 | `_check_action_outcomes` | 5мин | Есть open action-ноды (закрытых нет — пропускает) | — (закрывает action-ноды outcome'ами через forced reaction match или timeout) | 🟢 light |

**Тяжёлые пропускаются в test harness по default** (см. ниже). Включаются `?include_heavy=1`.

### † Adaptive idle — плавное затухание циклов при рассинхроне + эмпатия

Механики #2+#4 из [resonance protocol](world-model.md) плюс **эмпатия юзеру**. Все investigation-циклы замедляются плавно по объединённой метрике **combined burnout**:

```
combined = max(
    freeze.conflict_accumulator,    # графовые конфликты Baddle
    freeze.desync_pressure,         # хронический рассинхрон Baddle
    user.burnout                    # усталость ЮЗЕРА — эмпатия
)
multiplier = 1 + combined × 9        # [1× ... 10×]
```

**Три источника замедления, одна семантика «усталости»:**

| Feeder | Источник | Активирует freeze? | Семантика |
|---|---|---|---|
| `freeze.conflict_accumulator` | графовые конфликты (d > τ при низкой стабильности) | **ДА** (жёсткий Bayes-freeze) | Baddle запутался |
| `freeze.desync_pressure` | рассинхрон с юзером по времени (+1/7сут, -0.05 за event) | нет | Baddle оторвался |
| `user.burnout` | decisions_today, feedback-отказы | нет | **юзер устал** |

`freeze.display_burnout = max(conflict_accumulator, desync_pressure)` — то что юзер видит как **«усталость Baddle»** в UI. `cognitive_loop._idle_multiplier()` берёт **max** этого и `user.burnout` — замедляется и когда сам устал, и когда юзер устал. Это и есть «ненавязчиво замедляться вместе». Явный вербальный «отдохни» не нужен — тишина сама по себе предложение.

**Рост / снижение `desync_pressure`:**

| Сигнал | Δ |
|---|---|
| Время без user-событий | +1/7сут (линейно) |
| 1 user-event (сообщение / foreground tick) | −0.05 |
| Cap | [0, 1] |

**Что такое user-event:** `/assist/chat/append` с `role=user`, `tick_foreground()` (`/graph/tick`), любой явный `signal_user_input()` call. **НЕ** считаются: HRV push, автоматическое создание нод через pump/scout/converge, heartbeat — это внутренний пульс.

**Multiplier = 1 + display_burnout × 9** применяется ко всем investigation-throttle через `_throttled_idle()`:

| Сценарий | display_burnout | mult | DMN continuous | DMN deep | Night cycle |
|---|---|---|---|---|---|
| Свежий resonance | 0.0 | 1.0× | 10 мин | 30 мин | 24 ч |
| 3 дня молчания | 0.43 | 4.9× | 49 мин | 2.4 ч | 4.9 сут |
| 7 дней молчания | 1.0 | 10× | 100 мин | 5 ч | 10 сут |
| Высокий графовый конфликт | 0.6 | 6.4× | 64 мин | 3.2 ч | 6.4 сут |
| Оба (конфликт + долгое молчание) | max(conflict, desync) | min{10, 1+max×9} | — | — | — |

**Почему 5% drop на событие** (не обнуление): одно сообщение после недели молчания не должно мгновенно возвращать полный рабочий ритм. «Поддерживает активность, не полностью восстанавливает». Полный возврат из max-desync в 1.0× требует **~20 событий** — реальную сессию общения.

**Почему ночи тоже затухают:** структурная верность зеркала. Юзер пропал — циклы везде реже, включая ночные (Scout, REM-merge). Неделю молчит → scout раз в 10 суток, а не форсированно. Это не баг, это **замирание** вместе с юзером. Вернулся к активному общению — ночи сами возвращаются к 24ч.

**Почему desync НЕ активирует freeze:** Bayes-freeze — жёсткое замирание updates графа. Уместно при хроническом графовом конфликте, **не** при молчании юзера (тогда просто нечего обновлять). Рассинхрон замедляет циклы, но граф остаётся обучаемым когда юзер вернётся.

**В логе:** `[cognitive_loop] desync_pressure 0.750 -> 0.700 (event: user_input, multiplier now 7.30×)`.

Реализация: `src/neurochem.py::ProtectiveFreeze` (хранит оба feeder'а + `display_burnout`), `src/cognitive_loop.py::_advance_desync / _register_user_event / _idle_multiplier / _throttled_idle`, `src/horizon.py::get_metrics` (UI передача `burnout`, `burnout_conflict`, `burnout_desync`).

---

## Типы alerts в UI

UI poll'ит [`GET /assist/alerts`](../src/assistant.py) (обычно раз в 30 сек) и отрисовывает каждый тип по-своему в [`assistant.js:assistPollAlerts`](../static/js/assistant.js).

| Alert type | Источник | UI |
|------------|----------|-----|
| `morning_briefing` | cognitive_loop | Rich card с sections (☀️ Доброе утро + 📝 Check-in + ⚡ Recovery + 🔋 Резерв + 🎯 Цели + 💡 Pattern) |
| `night_cycle` | cognitive_loop | Summary ночи: Scout мосты, REM merge, прунинг |
| `observation_suggestion` | cognitive_loop | Intent-confirm card с intro «💡 Я заметил паттерн — предлагаю:» и кнопками Да/Изменить/Нет |
| `dmn_bridge` | cognitive_loop | Bridge card «🔗 DMN-инсайт» с A↔B и скрытой осью |
| `dmn_cross_graph` | cognitive_loop | Cross-workspace мост в alert-stream |
| `dmn_deep_research` | cognitive_loop | Polноценная deep-research карточка: trace + synthesis |
| `dmn_converge` | cognitive_loop | Summary server-side autorun'а |
| `state_walk` | cognitive_loop | «🕰 Похожий момент (дата): тогда я {verb}» (human-mapping action→глагол) |
| `sync_seeking` | cognitive_loop | Soft card — Baddle пишет первым когда давно молчали. LLM-генерит текст, иконка по tone (🌿 caring / 💭 ambient / 👀 curious / 🔗 reference / 🤲 simple), фоновый цвет карточки по tone. Подпись «Baddle не слышит тебя Nч». Без кнопок — ожидание живого ответа |

### 🧠 Action Memory node-types (не alerts, но часть единого графа)

Все proactive actions + user-actions записываются в **граф** как ноды `type=action`. Через 30мин/24ч/7д (зависит от kind) закрываются нодой `type=outcome` связанной edge `caused_by`. UI Graph Lab отрисовывает их оранжевым (action) и зелёным/красным (outcome по знаку delta_sync_error). См. [action-memory-design.md](action-memory-design.md).

**Baddle-side** (записываются из cognitive_loop): `sync_seeking` · `dmn_bridge` · `scout_bridge` · `suggestion_recurring`/`constraint`/`generic` · `morning_briefing` · `alert_low_energy` · `reminder_plan` · `evening_retro` · `baddle_reply` (из /assist) · `chat_event_*` (из `_push_event_to_chat`).

**User-side** (записываются из endpoints): `user_chat` (с sentiment в context) · `user_accept`/`user_reject` (из /assist/feedback) · `user_goal_create_*` (из /goals/add) · `user_activity_start`/`user_activity_stop` (из /activity/*) · `user_checkin` (из /checkin).

`user_chat` / `baddle_reply` соединены edge `followed_by` в хронологическую цепочку через `link_chat_continuation` (окно 1ч). Это дает **chat = view над графом по времени**.

Endpoint: `GET /graph/actions-timeline?limit=N&since_ts=...&kinds=...&actor=...&include_outcomes=0|1` возвращает отсортированный список.
| `plan_reminder` | cognitive_loop | «⏰ НАПОМИНАНИЕ: {name} · через N мин» + кнопки Начать/Пропустить/Позже |
| `recurring_lag` | cognitive_loop | Напоминание «отстаёт X: N/M» |
| `evening_retro` | cognitive_loop | Список unfinished + кнопка открыть check-in |
| `low_energy_heavy` | cognitive_loop | «⚠ Энергия низкая для этой задачи» |
| `coherence_crit` | cognitive_loop | HRV упал — пауза |
| `regime_rest` | assist.py (/assist/alerts) | «Оба устали. Пауза.» (из sync_regime) |
| `regime_protect` | assist.py | «Ты устал — возьму на себя» |
| `regime_confess` | assist.py | «Мне нужно подумать — дай минуту» |
| `low_energy` | assist.py | Hard-порог для очень низкой энергии |

---

## Card types в чат-ленте

Не все карточки — alerts (многие приходят как часть ответа `/assist`). Полный список типов которые умеет рендерить [`assistRenderCard`](../static/js/assistant.js):

- `morning_briefing` — утренний брифинг в формате sections
- `intent_confirm` — draft-подтверждение (новая цель / привычка / ограничение)
- `status_briefing` — «как я?» (резерв / нейрохим / активность / план)
- `activity_started` — «🎬 Трекер запущен: {name}»
- `activity_action` — start/stop подтверждение
- `instance_ack` — «♻✓ +1 к привычке»
- `constraint_violation` — «⚠ нарушено ограничение X»
- `clarify` — ask-gate, «уточни X»
- `profile_clarify` — уточнение категории профиля
- `habit` — streak-статус
- `bridge` — мост (Scout/DMN) с text_a↔text_b + hidden axis + synthesis
- `dialectic` — pro/contra + synthesis
- `deep_comparison` — сравнение опций (tournament)
- `deep_cluster` — кластерное исследование (builder/pipeline/cascade)
- `open_modal` — команда из чата открывает модал (Check-in)

---

## Throttle intervals reference

Константы в `CognitiveLoop` ([src/cognitive_loop.py](../src/cognitive_loop.py) строки 135-174):

```python
TICK_INTERVAL              = 60        # базовый цикл
HRV_PUSH_INTERVAL          = 15        # _check_hrv_push
HEARTBEAT_INTERVAL         = 5
STATE_WALK_INTERVAL        = 20 * 60
NIGHT_CYCLE_INTERVAL       = 24 * 3600
BRIEFING_INTERVAL          = 20 * 3600
DMN_INTERVAL               = 10 * 60   # _check_dmn_continuous
DMN_DEEP_INTERVAL          = 30 * 60
DMN_CONVERGE_INTERVAL      = 60 * 60
DMN_CROSS_INTERVAL         = 60 * 60
SUGGESTIONS_CHECK_INTERVAL = 24 * 3600
FOREGROUND_COOLDOWN        = 5 * 60    # сколько idle = DMN-eligible
NE_HIGH_GATE               = 0.55      # DMN блокируется если NE выше
```

Attribute'ы которые хранят `last_run` timestamp'ы (полный список для monkey-patch в test harness):

```python
_last_dmn, _last_state_walk, _last_night_cycle, _last_briefing,
_last_hrv_push, _last_foreground_tick, _last_ws_flush,
_last_activity_tick, _last_low_energy_check, _last_plan_reminder_check,
_last_heartbeat, _last_dmn_deep, _last_dmn_converge, _last_dmn_cross,
_last_recurring_check, _last_suggestions_check
```

Throttle-helper — `CognitiveLoop._throttled(attr, interval_s)`: возвращает `True` если прошло достаточно, и обновляет timestamp. Test harness monkey-патчит эту функцию (force-reset timestamp + вернуть True).

---

## Test harness

Один endpoint — прогон всех `_check_*` с forced-throttle reset + diff `_alerts_queue`:

```
POST /debug/alerts/trigger-all          # быстрый прогон (пропускает heavy)
POST /debug/alerts/trigger-all?include_heavy=1   # полный (LLM-циклы — минута+)
```

Ответ:

```json
{
  "summary": {
    "total": 17,
    "alert_emitted": 2,
    "silent_ok": 10,
    "skipped_heavy": 5,
    "error": 0,
    "include_heavy": false
  },
  "results": [
    {"name": "_check_night_cycle", "heavy": true, "status": "skipped_heavy"},
    {"name": "_check_observation_suggestions",
     "status": "alert_emitted", "elapsed_s": 0.312,
     "alerts": [{"type": "observation_suggestion", "text": "💡 ..."}]},
    {"name": "_check_heartbeat", "status": "silent_ok", "elapsed_s": 0.001},
    ...
  ]
}
```

UI-кнопка: **Настройки → 🧪 Проверить алерты** (см. [`debugAlertsCheck` в assistant.js](../static/js/assistant.js)). Спрашивает про heavy + пишет отчёт в чат (остаётся в history).

**Реальный прогон на seed-данных** (work-demo + personal-demo без HRV):

```
alert_emitted: 2
  _check_observation_suggestions → [observation_suggestion × 2]
  _check_recurring_lag           → [recurring_lag]
silent_ok: 10
  _check_activity_cost, _check_daily_briefing, _check_evening_retro,
  _check_heartbeat, _check_hrv_alerts, _check_hrv_push,
  _check_low_energy_heavy, _check_plan_reminders, _check_state_walk,
  _check_ws_flush
skipped_heavy: 5 (все DMN + night)
errors: 0
```

Silent — legitимно: каждый молчит по своей причине (нет HRV → no hrv_alerts; не вечер → no retro; energy full → no low_energy; нет активных задач → no activity_cost).

---

## Live thinking-state (для UI-конуса)

Когда любая из тяжёлых операций запускается — cognitive_loop помечает `_thinking_state` через `set_thinking(kind, detail)`. UI polls `/assist/state` → читает `thinking.kind` → рисует соответствующую стадию конуса:

| `thinking.kind` | Когда | Визуал конуса |
|-----------------|-------|---------------|
| `pump` | DMN / user-triggered pump | Dual cones + зелёный overlap-ромб (glow) |
| `scout` | Night cycle | Single cone + strong pulse |
| `synthesize` | `execute_deep` (основной путь /assist) или DMN deep-research | Single cone + pulse |
| `elaborate` | `/graph/elaborate`, `/graph/expand` | Single cone + pulse |
| `smartdc` | `/graph/smartdc` | Single cone + pulse |
| `think` | `/graph/think` | Single cone + pulse |
| `idle` | всё остальное | Ровное состояние, label = horizon state |

См. [static/js/cone_live.js](../static/js/cone_live.js) — poll + render.

Thinking trackerится через:
- `set_thinking` / `clear_thinking` в `cognitive_loop.py` (pump, scout, synthesize)
- Декоратор `_with_thinking(kind)` в `graph_routes.py` — 5 endpoint'ов (think/expand/elaborate/smartdc/pump)
- Прямые вызовы в `execute_deep` в `assistant_exec.py` — основной путь /assist

---

## Как добавить новый check

1. Написать `_check_newthing` в `CognitiveLoop` (src/cognitive_loop.py).
2. Добавить `self._last_newthing = 0.0` в `__init__`.
3. В начале метода: `if not self._throttled("_last_newthing", self.NEWTHING_INTERVAL): return`.
4. Вызвать из `_run` loop в главном тике (вокруг строки 320-365).
5. Если тяжёлый — добавить в `_HEAVY_CHECKS` в [assistant.py](../src/assistant.py).
6. Если emit'ит новый alert type — добавить обработку в [assistPollAlerts](../static/js/assistant.js).
7. Обновить таблицы в этом doc.

---

**Навигация:** [← Full cycle](full-cycle.md) · [Индекс](README.md)
