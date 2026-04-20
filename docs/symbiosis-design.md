# Симбиоз — два state-вектора

> Прайм-директива в коде: `sync_error = ‖user − system‖` — геометрическое
> расстояние между двумя 4-мерными точками нейрохимического состояния.
> Не декларация, а вычисляемая метрика.

## Идея

Было (до v8 «Симбиоз»): `sync_error` — скаляр в `CognitiveState`, который
никто не вычислял, `update_sync_error(distance)` вызывался ноль раз.
Прайм-директива жила в README и не влияла на код.

Стало: **две симметричные структуры** — `Neurochem` (система) и `UserState`
(пользователь) — одинаковой формы. Sync-метрика — L2-норма разности их
векторов.

```
UserState:   [dopamine, serotonin, norepinephrine, burnout]  ← ты
SystemState: [dopamine, serotonin, norepinephrine, burnout]  ← Baddle
                                                 (neuro + freeze)

sync_error = ‖user_vec − system_vec‖
```

## Источники сигналов

**SystemState** (`Neurochem` + `ProtectiveFreeze`) питается **динамикой графа**
— см. [neurochem-design.md](neurochem-design.md).

**UserState** питается **наблюдаемыми сигналами юзера**:

| Канал | → | UserState скаляр |
|-------|---|------------------|
| HRV coherence | → | `serotonin` EMA (спокойствие = стабильность) |
| HRV stress / (80−RMSSD)/80 | → | `norepinephrine` EMA (напряжение) |
| Интервал между сообщениями | → | `dopamine` EMA (<30с → интерес, >5мин → охлаждение) |
| Variance длины сообщений (rolling 10) | → | `serotonin` EMA (стабильный юзер = уверенный) |
| Feedback 👍 | → | `dopamine` EMA +0.9 |
| Feedback 👎 | → | `dopamine` EMA +0.2 + `burnout += 0.05` |
| `decisions_today × 6 / max_budget` | → | `burnout` EMA |

Все EMA — одна строка, как в Neurochem. Максимально простой контракт.

## Режимы симбиоза

Из `(sync_error, user_state, system_state)` deriveится один из 4 режимов:

| Режим | Условие | Поведение assistant'а |
|-------|---------|------------------------|
| **FLOW** | `sync_error < 0.3`, оба `mean(D,S) > 0.55` | Полный объём, сложные задачи |
| **REST** | `sync_error < 0.3`, оба `< 0.35` | Предлагает паузу |
| **PROTECT** | `sync_error ≥ 0.3`, user low + system high | Берёт на себя: короче ответы |
| **CONFESS** | `sync_error ≥ 0.3`, user high + system low | «Дай мне время подумать» |
| *fallback* | любые промежуточные | FLOW (работаем, но sync_error сам по себе сигнал) |

Это **третий контур** (диалог) замкнутый через sync-метрику: до сих пор он
был «задаю вопрос», теперь — **адаптивное поведение на основе взаимного
состояния**.

Advice-слой в [`/assist/alerts`](../src/assistant.py) выводит подсказки из
regime, а не из жёстких порогов на каждое поле по отдельности.

## Пороги

В [src/user_state.py](../src/user_state.py):

```python
SYNC_HIGH_THRESHOLD   = 0.3    # error < 0.3 → sync высокий (L2-norm на [0,2])
STATE_HIGH_THRESHOLD  = 0.55   # mean(D,S) > 0.55 → state высокий
STATE_LOW_THRESHOLD   = 0.35   # mean(D,S) < 0.35 → state низкий
```

Максимум `sync_error` ≈ 2.0 (каждая ось в [0,1], 4 оси). UI пересчитывает
в «sync %» как `100 · (1 − error/2)`.

## Архитектурные следствия

1. **`CognitiveState.sync_error` → derived property** — читает глобальные
   UserState + Neurochem + ProtectiveFreeze. `update_sync_error` удалён.
2. **`CognitiveState.sync_regime` → derived property** — аналогично.
3. **HRV полностью переехал в UserState**. `CognitiveState.update_from_hrv`
   удалён. `hrv_coherence/stress/rmssd` на CognitiveState остались как
   **passthrough @property** (читают из UserState) — для backward-compat
   _target_state (STABILIZE при низкой coherence).
4. **`/hrv/metrics` endpoint** не обновляет per-graph horizon; вместо этого
   вызывает `get_user_state().update_from_hrv(...)`.
5. **`/assist/state` endpoint** возвращает `{user_state, neurochem,
   sync_error, sync_regime, hrv}` — UI рисует две симметричные панели.
6. **`/assist/alerts`** — режим симбиоза поверх старых hard-floor алертов
   (энергия < 20, coherence < 0.25 — всё ещё звенят независимо).

## Жизненный цикл сигнала

```
Пользователь пишет сообщение
  ↓
/assist обработчик:
  • cs.inject_ne(0.4)                     system: NE spike
  • user.register_input()                  user: last_input_ts (для UI / sync-seeking)
  • user.update_from_energy(decisions)     user: burnout
  ↓
classify_intent_llm использует state_hint = текущая системная химия
  ↓
execute_via_zones(mode) → карточка
  ↓
Пользователь жмёт 👍/👎
  • cs.update_neurochem(d=0.2/0.8)        system: feedback в химию
  • user.update_from_feedback(kind)        user: feedback в user-химию
  ↓
UI polling (3s):
  sync_error = ‖user_vec − system_vec‖
  sync_regime = derived из (error, user_level, system_level)
  → рендер двух панелей + sync-индикатор
  ↓
/assist/alerts:
  regime = PROTECT/CONFESS/REST/FLOW
  → подсказка «я возьму на себя / дай мне время / сделаем паузу»
```

## UI

Header `baddle`-таба: **две симметричные панели** вокруг sync-индикатора.

```
┌─────────────────────────────────────────────────────────┐
│ ТЫ     │ Интерес │ Стабильность │ Напряжение │ Усталость │
│                                                           │
│               ⚡ FLOW  sync 78%                           │
│                                                           │
│ BADDLE │ Интерес │ Стабильность │ Напряжение │ Усталость │
└─────────────────────────────────────────────────────────┘
```

Одинаковый цветовой код обеих сторон (зелёный/фиолетовый/оранжевый/красный)
+ симметричная вёрстка = визуально видно **где расходимся и куда тянет**.
Цвет пилюли режима: FLOW зелёный, REST серый, PROTECT оранжевый, CONFESS
малиновый.

## Что не реализовано

- Dynamic adjustment поведения `execute_via_zones` в зависимости от regime
  (сейчас только alerts меняются). PROTECT мог бы снижать `n_ideas`,
  CONFESS — эмитить `action: "ask"` автоматически.
- Persistence UserState между рестартами (сейчас default 0.5 на старте).
- Метрика sync во времени (sync-dashboard — в TODO/UI visualisation).

---

**Навигация:** [← Neurochem](neurochem-design.md)  ·  [Индекс](README.md)  ·  [Следующее: User Model →](user-model-design.md)
