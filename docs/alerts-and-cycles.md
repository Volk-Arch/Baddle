# Alerts and Cycles

Справочник того что Baddle делает **сам в фоне**: 21 периодический check + 20+ типов alerts/cards. Нужно чтобы понять «что должно прилететь в UI и когда» и дебажить молчаливые фоны.

Всё живёт в одном потоке — [CognitiveLoop](../src/cognitive_loop.py). Адаптивный интервал (15 секунд при активном HRV, до 60 секунд в idle) дёргает `_check_*` методы. У каждого свой throttle.

---

## 21 фоновый check

| # | Check | Throttle | Когда срабатывает | Alert type | Нагрузка |
|---|---|---|---|---|---|
| 1 | `_check_night_cycle` | 24ч † | Scout pump+save, REM emotional, REM creative, consolidation | `night_cycle` | 🔴 heavy |
| 2 | `_check_dmn_continuous` | 10 мин † | Idle + низкий NE + граф ≥ 4 нод | `dmn_bridge` (качество выше 0.5) | 🔴 heavy |
| 3 | `_check_dmn_deep_research` | 30 мин † | Idle + низкий NE + ≥ 1 open goal + граф ≤ 30 | `dmn_deep_research` | 🔴 heavy |
| 4 | `_check_dmn_converge` | 60 мин † | Idle + низкий NE + граф ≥ 5 нод | `dmn_converge` | 🔴 heavy |
| 5 | `_check_dmn_cross_graph` | 60 мин † | ≥ 2 workspace'а с embeddings | `dmn_cross_graph` + пишет кросс-рёбра | 🔴 heavy |
| 6 | `_check_state_walk` | 20 мин † | state_graph ≥ 10 past samples | `state_walk` | 🟡 medium |
| 7 | `_check_daily_briefing` | 20ч | Утро (≥ wake_hour) + не был сегодня | `morning_briefing` | 🟢 light |
| 8 | `_check_hrv_push` | 15с | HRV manager активен | — (sync HRV → UserState) | 🟢 light |
| 9 | `_check_low_energy_heavy` | ~10 мин | Юзер открыл heavy цель при энергии ниже 20 | `low_energy_heavy` | 🟢 light |
| 10 | `_check_heartbeat` | 5с | Всегда | — (debug log) | 🟢 light |
| 11 | `_check_plan_reminders` | 1 мин | За N мин до plan-события | `plan_reminder` | 🟢 light |
| 12 | `_check_recurring_lag` | 60 мин | Recurring с отставанием больше 0 за сутки | `recurring_lag` | 🟢 light |
| 13 | `_check_observation_suggestions` | 24ч | Паттерны / check-ins / стресс / weekly → draft LLM | `observation_suggestion` | 🟡 medium |
| 14 | `_check_evening_retro` | раз в день | wake_hour + 14 часов | `evening_retro` | 🟢 light |
| 15 | `_check_activity_cost` | 1 мин | Долгая активная activity | — (списывает энергию) | 🟢 light |
| 16 | `_check_ws_flush` | 5 мин | Граф изменился | — (persist to disk) | 🟢 light |
| 17 | `_check_hrv_alerts` | 1 мин | Когерентность HRV упала ниже критической | `coherence_crit` | 🟢 light |
| 18 | `_check_sync_seeking` | 2ч | давление тишины > 0.3 + idle > 2ч + 30 мин quiet | `sync_seeking` | 🟡 medium |
| 19 | `_check_agency_update` | 1ч | `schedule_for_day` непустой | — (обновляет агентность) | 🟢 light |
| 20 | `_check_action_outcomes` | 5 мин | Есть open action-ноды | — (закрывает outcome'ы) | 🟢 light |
| 21 | `_check_prime_directive_record` | 1ч | всегда | — (append `prime_directive.jsonl`) | 🟢 light |

Интервалы с † **растягиваются рассинхроном** — см. ниже. В test harness тяжёлые (🔴) пропускаются по умолчанию, включаются через `?include_heavy=1`.

---

## Adaptive idle — затухание циклов по совмещённому выгоранию

Все investigation-циклы замедляются плавно. **Отображаемое выгорание** (display_burnout) берётся как максимум трёх feeder'ов: накопитель конфликтов, давление тишины, давление дисбаланса. **Совмещённое выгорание** (combined_burnout) добавляет выгорание пользователя поверх. **Множитель замедления** (multiplier) = 1 + совмещённое · 9, диапазон от 1× до 10×.

| Feeder | Источник | Заморозка? |
|---|---|---|
| Накопитель конфликтов (conflict_accumulator) | Графовые конфликты (различие выше порога при низкой стабильности) | **ДА** (байесовская заморозка) |
| Давление тишины (silence_pressure) | Таймер молчания (+ 1 за 7 суток, − 0.05 за event) | Нет |
| Давление дисбаланса (imbalance_pressure) | EMA ошибки предсказания (4 канала PE, [friston-loop](friston-loop.md)) | Нет |
| Выгорание пользователя (user.burnout) | Число решений за день + feedback-отказы | Нет |

Пример множителя: свежий резонанс → 1.0×, 3 дня молчания → 4.9×, 7 дней → 10×. Ночные циклы тоже умножаются — структурная верность зеркала: пользователь пропал → реже всё, включая ночь. Давление тишины снижается на 5% за event (полный возврат около 20 событий). Заморозка активируется только конфликтом: молчание и расхождение ожиданий оставляют граф обучаемым под будущий сигнал.

Log: `[cognitive_loop] silence_pressure 0.750 -> 0.700 (event: user_input, multiplier now 7.30×)`.

---

## Прайм-директива

Раз в час `_check_prime_directive_record` пишет snapshot в `data/prime_directive.jsonl` через [src/prime_directive.py](../src/prime_directive.py). Запись содержит текущее рассогласование + EMA fast/slow + per-channel decomposition (конфликт / тишина / дисбаланс + разбивка по каналам PE).

**Валидация через 2 мес use:** сравнить среднюю медленную EMA рассогласования за первый месяц vs последний. Падает → резонансный протокол работает. Endpoint `GET /assist/prime-directive?window_days=30&daily=1`.

---

## Типы alerts в UI

UI poll'ит `GET /assist/alerts` примерно раз в 30 секунд. Рендеринг в `assistant.js:assistPollAlerts`.

| Alert | UI |
|---|---|
| `morning_briefing` | Rich card с секциями (☀️ Утро + 📝 Check-in + ⚡ Recovery + 🎯 Цели + 💡 Pattern) |
| `night_cycle` | Summary ночи: scout-мосты, REM merge, прунинг |
| `observation_suggestion` | Intent-confirm карточка с intro + Да/Изменить/Нет |
| `dmn_bridge` / `dmn_cross_graph` | Bridge card с A↔B + скрытая ось |
| `dmn_deep_research` | Полноценная deep-research карточка |
| `dmn_converge` | Summary server-side autorun'а |
| `state_walk` | «🕰 Похожий момент: тогда я {verb}» |
| `sync_seeking` | Soft card с tone-иконкой (🌿 caring / 💭 ambient / 👀 curious / 🔗 reference / 🤲 simple) |
| `plan_reminder` | «⏰ НАПОМИНАНИЕ: {name} · через N мин» + кнопки |
| `recurring_lag` | «отстаёт X: N/M» |
| `evening_retro` | Список unfinished + кнопка check-in |
| `low_energy_heavy` / `low_energy` | «⚠ Энергия низкая для этой задачи» |
| `coherence_crit` | HRV упал — пауза |
| `regime_rest` / `regime_protect` / `regime_confess` | Подсказки из режима синхронизации |

---

## Action Memory — типы нод

Не alerts, но часть единого графа. Все проактивные действия + пользовательские действия записываются как `type=action`. Через 30 мин / 24 ч / 7 дней (зависит от вида) закрываются `type=outcome` + ребро `caused_by`. UI Graph Lab отрисовывает их оранжевым (action) и зелёным/красным (outcome — по знаку изменения рассогласования). Детали — [action-memory-design.md](action-memory-design.md).

**На стороне Baddle:** sync_seeking · dmn_bridge · scout_bridge · suggestion_recurring/constraint/generic · morning_briefing · alert_low_energy · reminder_plan · evening_retro · baddle_reply · chat_event_*.

**На стороне пользователя:** user_chat (со sentiment) · user_accept / reject · user_goal_create_* · user_activity_start / stop · user_checkin.

`user_chat` / `baddle_reply` соединены ребром `followed_by` в хронологическую цепочку (окно 1 час). **Чат = view над графом по времени.** Endpoint `GET /graph/actions-timeline?limit=N&kinds=...&actor=...&include_outcomes=0|1`.

---

## Типы карточек в чат-ленте

Не все карточки — alerts. Многие приходят как часть ответа `/assist`. Полный список которые рендерит `assistRenderCard`:

morning_briefing · status_briefing · intent_confirm · activity_started · activity_action · instance_ack · constraint_violation · clarify · profile_clarify · habit · bridge · dialectic · deep_comparison · deep_cluster · open_modal.

---

## Live thinking-state (UI-конус)

Когда запускается тяжёлая операция, cognitive_loop помечает `_thinking_state` через `set_thinking(kind, detail)`. UI polls `/assist/state` → рисует соответствующую стадию конуса:

| kind | Когда | Визуал |
|---|---|---|
| `pump` | DMN / user pump | Dual cones + зелёный overlap-ромб |
| `scout` | Ночной цикл | Single cone + strong pulse |
| `synthesize` | execute_deep / DMN deep-research | Single cone + pulse |
| `elaborate` / `smartdc` / `think` | Соответствующий endpoint | Single cone + pulse |
| `idle` | всё остальное | Ровное, label = horizon state |

Декоратор `_with_thinking(kind)` в [graph_routes.py](../src/graph_routes.py) оборачивает 5 endpoint'ов (think / expand / elaborate / smartdc / pump). Прямые вызовы — в `execute_deep` ([assistant_exec.py](../src/assistant_exec.py)) для `/assist`.

---

## Test harness

`POST /debug/alerts/trigger-all` — прогон всех `_check_*` с force-throttle reset + diff очереди alerts. Без `?include_heavy=1` пропускает LLM-циклы (DMN, night cycle).

Ответ: `{summary: {total, alert_emitted, silent_ok, skipped_heavy, error}, results: [{name, status, elapsed_s, alerts}...]}`.

UI-кнопка: **Настройки → 🧪 Проверить алерты**. Спрашивает про heavy + пишет отчёт в чат.

**Реальный прогон на seed-данных** (demo без HRV): emit — 2 (observation_suggestion ×2, recurring_lag). Silent_ok — 10 (все legitimate: нет HRV → нет hrv_alerts; energy full → нет low_energy; нет активных задач → нет activity_cost; и т.д.). Skipped heavy — 5 (все DMN + ночь).

---

**Навигация:** [← Full cycle](full-cycle.md) · [Индекс](README.md)
