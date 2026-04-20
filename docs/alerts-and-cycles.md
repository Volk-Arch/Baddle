# Alerts and Cycles

Справочник того что Baddle делает **сам в фоне**: 17 периодических check'ов + 20+ типов alerts/cards. Нужно чтобы понять «что должно прилететь в UI и когда» и дебажить молчаливые фоны.

Всё живёт в одном процессе — `CognitiveLoop` (см. [src/cognitive_loop.py](../src/cognitive_loop.py)). Один поток крутится с adaptive-интервалом (15 сек когда HRV активно, до 60 сек в idle) и дёргает `_check_*` методы. Каждый check имеет свой throttle (см. «Throttle intervals» ниже).

---

## 17 фоновых check'ов

| # | Check | Throttle | Когда срабатывает | Alert type (если emit'ит) | Нагрузка |
|---|-------|----------|-------------------|---------------------------|----------|
| 1 | `_check_night_cycle` | 24ч | Раз в сутки: Scout pump+save, REM emotional, REM creative, consolidation | `night_cycle` | 🔴 heavy (LLM + pump) |
| 2 | `_check_dmn_continuous` | 10 мин | Юзер idle + NE low + граф ≥ 4 нод | `dmn_bridge` (если bridge.text ≥ 10 и quality > 0.5) | 🔴 heavy (LLM pump) |
| 3 | `_check_dmn_deep_research` | 30 мин | Idle + NE low + ≥ 1 open goal + граф ≤ 30 нод | `dmn_deep_research` | 🔴 heavy (execute_deep) |
| 4 | `_check_dmn_converge` | 60 мин | Idle + NE low + граф ≥ 5 нод | `dmn_converge` | 🔴 heavy (autorun loop) |
| 5 | `_check_dmn_cross_graph` | 60 мин | ≥ 2 workspace'а с embeddings | `dmn_cross_graph` + пишет `cross_edges` в `workspaces/index.json` | 🔴 heavy (cosine N×M) |
| 6 | `_check_state_walk` | 20 мин | state_graph имеет ≥ 10 past samples | `state_walk` («похожий момент: тогда я X») | 🟡 medium (embedding query) |
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

**Тяжёлые пропускаются в test harness по default** (см. ниже). Включаются `?include_heavy=1`.

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
