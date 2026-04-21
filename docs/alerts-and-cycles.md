# Alerts and Cycles

Справочник того что Baddle делает **сам в фоне**: 21 периодический
check + 20+ типов alerts/cards. Нужно чтобы понять «что должно прилететь
в UI и когда» и дебажить молчаливые фоны.

Всё живёт в одном потоке — `CognitiveLoop` (`src/cognitive_loop.py`).
Adaptive-интервал (15 сек при активном HRV, до 60с в idle) дёргает
`_check_*` методы. У каждого свой throttle.

---

## 21 фоновый check

| # | Check | Throttle | Когда срабатывает | Alert type | Нагрузка |
|---|---|---|---|---|---|
| 1 | `_check_night_cycle` | 24ч † | Scout pump+save, REM emotional, REM creative, consolidation | `night_cycle` | 🔴 heavy |
| 2 | `_check_dmn_continuous` | 10 мин † | Idle + NE low + граф ≥ 4 нод | `dmn_bridge` (quality > 0.5) | 🔴 heavy |
| 3 | `_check_dmn_deep_research` | 30 мин † | Idle + NE low + ≥ 1 open goal + граф ≤ 30 | `dmn_deep_research` | 🔴 heavy |
| 4 | `_check_dmn_converge` | 60 мин † | Idle + NE low + граф ≥ 5 нод | `dmn_converge` | 🔴 heavy |
| 5 | `_check_dmn_cross_graph` | 60 мин † | ≥ 2 workspace'а с embeddings | `dmn_cross_graph` + пишет `cross_edges` | 🔴 heavy |
| 6 | `_check_state_walk` | 20 мин † | state_graph ≥ 10 past samples | `state_walk` | 🟡 medium |
| 7 | `_check_daily_briefing` | 20ч | Утро (≥ wake_hour) + не был сегодня | `morning_briefing` | 🟢 light |
| 8 | `_check_hrv_push` | 15с | HRV manager активен | — (sync HRV → UserState) | 🟢 light |
| 9 | `_check_low_energy_heavy` | ~10 мин | Юзер открыл heavy цель при energy < 20 | `low_energy_heavy` | 🟢 light |
| 10 | `_check_heartbeat` | 5с | Всегда | — (debug log) | 🟢 light |
| 11 | `_check_plan_reminders` | 1 мин | За N мин до plan-события | `plan_reminder` | 🟢 light |
| 12 | `_check_recurring_lag` | 60 мин | Recurring с `lag > 0` за сутки | `recurring_lag` | 🟢 light |
| 13 | `_check_observation_suggestions` | 24ч | Patterns / checkins / stress / weekly → draft LLM | `observation_suggestion` | 🟡 medium |
| 14 | `_check_evening_retro` | раз в день | `wake_hour + 14ч` | `evening_retro` | 🟢 light |
| 15 | `_check_activity_cost` | 1 мин | Долгая активная activity | — (debit energy) | 🟢 light |
| 16 | `_check_ws_flush` | 5 мин | Граф изменился | — (persist to disk) | 🟢 light |
| 17 | `_check_hrv_alerts` | 1 мин | Coherence упал ниже критической | `coherence_crit` | 🟢 light |
| 18 | `_check_sync_seeking` | 2ч | `silence_pressure > 0.3` + idle > 2ч + 30мин quiet | `sync_seeking` | 🟡 medium |
| 19 | `_check_agency_update` | 1ч | `schedule_for_day` непустой | — (обновляет `UserState.agency`) | 🟢 light |
| 20 | `_check_action_outcomes` | 5 мин | Есть open action-ноды | — (closes outcomes) | 🟢 light |
| 21 | `_check_prime_directive_record` | 1ч | всегда | — (append `prime_directive.jsonl`) | 🟢 light |

Интервалы с † **растягиваются рассинхроном** — см. ниже. В test harness
тяжёлые (🔴) пропускаются по default, включаются `?include_heavy=1`.

---

## Adaptive idle — затухание циклов по combined burnout

Все investigation-циклы замедляются плавно по
`ProtectiveFreeze.combined_burnout(user_burnout)`:

```
display_burnout = max(conflict_accumulator, silence_pressure, imbalance_pressure)
combined        = max(display_burnout, user.burnout)
multiplier      = 1 + combined × 9   # [1× ... 10×]
```

**Четыре источника замедления:**

| Feeder | Источник | Активирует freeze? |
|---|---|---|
| `conflict_accumulator` | Графовые конфликты (d > τ при низкой стабильности) | **ДА** (жёсткий Bayes-freeze) |
| `silence_pressure` | Таймер молчания (+1/7сут, −0.05 за event) | Нет |
| `imbalance_pressure` | EMA predictive error (4 PE-канала) | Нет |
| `user.burnout` | decisions_today + feedback-отказы | Нет |

`display_burnout` = три Baddle-side feeder'а, показывается как
«Усталость Baddle» в UI. `combined_burnout(user.burnout)` добавляет
юзер-усталость снаружи — не показывается как «Усталость Baddle», но
замедляет циклы. Ненавязчивое замедление вместе с юзером — тишина
сама по себе предложение.

**Как ведёт себя multiplier:**

| Сценарий | display_burnout | mult | DMN continuous | Night cycle |
|---|---|---|---|---|
| Свежий resonance | 0.0 | 1.0× | 10 мин | 24 ч |
| 3 дня молчания | 0.43 | 4.9× | 49 мин | 4.9 сут |
| 7 дней молчания | 1.0 | 10× | 100 мин | 10 сут |
| Высокий графовый конфликт | 0.6 | 6.4× | 64 мин | 6.4 сут |
| Высокий imbalance | 0.5 | 5.5× | 55 мин | 5.5 сут |

**Почему silence снижается только на 5%, не до нуля:** одно сообщение
после недели молчания не должно мгновенно вернуть полный ритм. Полный
возврат из max в 1.0× требует ~20 событий.

**Почему ночи тоже затухают:** структурная верность зеркала. Юзер
пропал — циклы реже, включая ночные. Вернулся → ночи возвращаются к 24ч.

**Почему silence и imbalance НЕ активируют freeze:** Bayes-freeze —
жёсткое замирание updates графа. Уместно при хроническом графовом
конфликте, не при молчании или расхождении ожиданий (там граф остаётся
обучаемым когда появится сигнал).

**В логе:** `[cognitive_loop] silence_pressure 0.750 -> 0.700 (event:
user_input, multiplier now 7.30×)`.

Полный разбор `imbalance_pressure` и 4 PE-каналов —
[friston-loop.md](friston-loop.md). Детали resonance protocol —
[world-model.md](world-model.md).

---

## Прайм-директива

Раз в час `_check_prime_directive_record` пишет snapshot в
`data/prime_directive.jsonl` через `src/prime_directive.py`. Запись
содержит текущий sync_error + EMA fast/slow + per-channel decomposition
(conflict / silence / imbalance + per-PE-канал).

**Валидация через 2 мес use:** сравнить mean(sync_error_ema_slow) за
первый месяц vs последний. Падает → резонансный протокол работает.
Endpoint `GET /assist/prime-directive?window_days=30&daily=1`.

---

## Типы alerts в UI

UI poll'ит `GET /assist/alerts` ~раз в 30с. Рендеринг в
`assistant.js:assistPollAlerts`.

| Alert | UI |
|---|---|
| `morning_briefing` | Rich card с sections (☀️ Утро + 📝 Check-in + ⚡ Recovery + 🎯 Цели + 💡 Pattern) |
| `night_cycle` | Summary ночи: scout мосты, REM merge, прунинг |
| `observation_suggestion` | Intent-confirm card с intro + Да/Изменить/Нет |
| `dmn_bridge` / `dmn_cross_graph` | Bridge card с A↔B + hidden axis |
| `dmn_deep_research` | Полноценная deep-research карточка |
| `dmn_converge` | Summary server-side autorun'а |
| `state_walk` | «🕰 Похожий момент: тогда я {verb}» |
| `sync_seeking` | Soft card с tone-иконкой (🌿 caring / 💭 ambient / 👀 curious / 🔗 reference / 🤲 simple) |
| `plan_reminder` | «⏰ НАПОМИНАНИЕ: {name} · через N мин» + кнопки |
| `recurring_lag` | «отстаёт X: N/M» |
| `evening_retro` | Список unfinished + кнопка check-in |
| `low_energy_heavy` / `low_energy` | «⚠ Энергия низкая для этой задачи» |
| `coherence_crit` | HRV упал — пауза |
| `regime_rest` / `regime_protect` / `regime_confess` | Подсказки из sync_regime |

---

## Action Memory node-types

Не alerts, но часть единого графа. Все proactive actions + user-actions
записываются как `type=action`. Через 30мин / 24ч / 7д (зависит от
kind) закрываются `type=outcome` + edge `caused_by`. UI Graph Lab
отрисовывает их оранжевым (action) и зелёным/красным (outcome по знаку
`delta_sync_error`). Детали — [action-memory-design.md](action-memory-design.md).

**Baddle-side:** sync_seeking · dmn_bridge · scout_bridge ·
suggestion_recurring/constraint/generic · morning_briefing ·
alert_low_energy · reminder_plan · evening_retro · baddle_reply ·
chat_event_*.

**User-side:** user_chat (со sentiment) · user_accept / reject ·
user_goal_create_* · user_activity_start / stop · user_checkin.

`user_chat` / `baddle_reply` соединены edge `followed_by` в
хронологическую цепочку (окно 1ч). **Chat = view над графом по
времени.** Endpoint `GET /graph/actions-timeline?limit=N&kinds=...&actor=...&include_outcomes=0|1`.

---

## Card types в чат-ленте

Не все карточки — alerts. Многие приходят как часть ответа `/assist`.
Полный список которые рендерит `assistRenderCard`:

- `morning_briefing` · `status_briefing` · `intent_confirm` ·
  `activity_started` · `activity_action` · `instance_ack` ·
  `constraint_violation` · `clarify` · `profile_clarify` · `habit` ·
  `bridge` · `dialectic` · `deep_comparison` · `deep_cluster` ·
  `open_modal`.

---

## Live thinking-state (UI конус)

Когда запускается тяжёлая операция, cognitive_loop помечает
`_thinking_state` через `set_thinking(kind, detail)`. UI polls
`/assist/state` → рисует соответствующую стадию конуса:

| kind | Когда | Визуал |
|---|---|---|
| `pump` | DMN / user pump | Dual cones + зелёный overlap-ромб |
| `scout` | Night cycle | Single cone + strong pulse |
| `synthesize` | execute_deep / DMN deep-research | Single cone + pulse |
| `elaborate` / `smartdc` / `think` | Соответствующий endpoint | Single cone + pulse |
| `idle` | всё остальное | Ровное, label = horizon state |

Декоратор `_with_thinking(kind)` в `graph_routes.py` оборачивает
5 endpoint'ов (think / expand / elaborate / smartdc / pump). Прямые
вызовы — в `execute_deep` (assistant_exec.py) для /assist.

---

## Test harness

`POST /debug/alerts/trigger-all` — прогон всех `_check_*` с
force-throttle reset + diff `_alerts_queue`. Без `?include_heavy=1`
пропускает LLM-циклы (DMN, night cycle).

Ответ: `{summary: {total, alert_emitted, silent_ok, skipped_heavy,
error}, results: [{name, status, elapsed_s, alerts}...]}`.

UI-кнопка: **Настройки → 🧪 Проверить алерты**. Спрашивает про heavy +
пишет отчёт в чат.

**Реальный прогон на seed-данных** (demo без HRV): emit — 2
(observation_suggestion ×2, recurring_lag). Silent_ok — 10 (все
legitimate: нет HRV → no hrv_alerts; energy full → no low_energy;
нет активных задач → no activity_cost; и т.д.). Skipped heavy — 5
(все DMN + night).

---

**Навигация:** [← Full cycle](full-cycle.md) · [Индекс](README.md)
