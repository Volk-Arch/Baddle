# Нейрохимия Baddle

Три скаляра + защитный режим. Минимальный набор сигналов, который даёт
системе «настроение», любопытство и защиту от перегрузки — без того
чтобы копировать состояние пользователя.

---

## Идея

Мышление не равно чистой логике. В реальном мозге баланс нейромедиаторов
модулирует скорость обучения, остроту внимания и тягу к исследованию.
Baddle использует три скаляра с чёткими ролями — каждый обновляется
одной EMA-формулой на основе событий в графе:

- **dopamine** — реакция на новизну. EMA от `d = distinct(a, b)`
- **serotonin** — стабильность весов. EMA от `1 − std(ΔW)`
- **norepinephrine** — неопределённость. EMA от энтропии распределения `W`

Плюс **ProtectiveFreeze** — защитный режим накапливающий «усталость» и
блокирующий Bayes update при перегрузке.

---

## γ derived

Чувствительность не отдельное поле, а производное:

```
γ = 2.0 + 3.0 · norepinephrine · (1 − serotonin)
```

Спокойный уверенный режим → γ ≈ 2.0 (baseline). Возбуждённый ищущий →
γ → 5.0. Напряжение при нестабильности → повышенная Bayesian-чувствительность.

---

## Bayes update через distinct

Signed форма — знаковая:

```
logit(post) = logit(prior) + γ · (1 − 2d)
```

`d = 0` (совпадение) → максимальное усиление. `d = 0.5` (неясно) → нет
изменения. `d = 1` (противоречие) → максимальное ослабление.

---

## RPE — автономный dopamine drift

Помимо EMA от `d`, dopamine получает **фазовые спайки от reward
prediction error** при каждом Bayes-обновлении:

```
actual    = |posterior − prior|       # реальная Δ
predicted = mean(last 20 deltas)      # baseline ожидание
rpe       = actual − predicted
dopamine += 0.15 · rpe
```

Если система привыкла к слабым уточнениям, сильное изменение confidence
даёт положительный RPE → спайк DA. Ожидала большой инсайт, получила
слабый → RPE отрицательный, DA падает. Чувствительность к **неожиданности**,
не просто новизне. Автономно — без feedback от юзера.

---

## Откуда приходят сигналы

Три скаляра питаются **динамикой графа**, не прямыми сигналами юзера:

| Скаляр | Источник | Что измеряет |
|---|---|---|
| dopamine | `d` из distinct в tick'е | Новизна — насколько новое пришло |
| serotonin | ΔW от Bayes-updates | Стабильность — меняются ли убеждения |
| norepinephrine | Энтропия weights | Неопределённость — размыто ли распределение |

Юзерский feedback конвертируется в **pseudo-d**: accepted → `d = 0.2`
(слабая новизна = подтверждение), rejected → `d = 0.8` (сильная новизна
= система была неправа). Feedback входит через тот же канал что и весь
граф.

---

## HRV НЕ влияет на нейрохимию

Важное решение: HRV — сигнал тела **пользователя**, не системы. Он идёт в:
- Советы юзеру («ты устал, отложи»)
- Energy recovery (потолок дневного ресурса)
- UI-показ (coherence / rmssd / stress)

Но **не трогает** DA / S / NE / freeze. Юзер устал — система замечает и
помогает, не «устаёт вместе». Внутренняя динамика эволюционирует по
собственным сигналам от графа.

---

## ProtectiveFreeze — защитный режим

Три независимых накопителя → один `display_burnout`:

| Feeder | Откуда | Активирует Bayes-freeze? |
|---|---|---|
| **conflict_accumulator** | EMA графовых конфликтов (d > τ при низкой стабильности) | **ДА** |
| **silence_pressure** | Линейный timer: +dt/7сут, −0.05 на user-event | Нет |
| **imbalance_pressure** | EMA aggregated PE (см. friston-loop) | Нет |

`display_burnout = max` всех трёх. В UI — «Усталость Baddle».
`combined_burnout(user_burnout) = max(display, user)` используется в
`_idle_multiplier` — эмпатия встроена: юзер устал → Baddle тоже тише.

**Только conflict активирует Bayes-freeze** — жёсткий режим где
`apply_to_bayes` возвращает prior, обновления weights блокируются. Вход
при accumulator > 0.15, выход при < 0.08 (гистерезис). Silence и
imbalance только **замедляют** background-циклы через
`_throttled_idle`, не блокируют обучение графа.

Подробно про 4 PE-канала агрегирующиеся в `imbalance_pressure` — в
[friston-loop.md](friston-loop.md).

---

## Self-prediction (симметрия Friston-loop)

Помимо предсказания юзера, Baddle предсказывает **себя**:

- `Neurochem.expectation_vec` — 3D EMA `[DA, S, NE]`, baseline «что
  Baddle ждёт от себя»
- `self_surprise_vec = vector() − expectation_vec`
- `self_imbalance = ‖self_surprise_vec‖` — Baddle PE на самой себе

Обновляется в `tick_expectation()` в том же `_advance_tick`. Когда Baddle
уезжает далеко от baseline (граф ушёл в экзотику / LLM деградирует) —
это пятый канал в `imbalance_pressure`.

---

## Порядок в тике

```
1. d = distinct(a, b)                       # сравнение идей
2. apply_to_bayes(prior, d) → posterior     # (γ derived, freeze-aware)
3. chem.update(d, ΔW, weights)               # три скаляра
4. freeze.update(d, serotonin)               # conflict accumulator
5. Git-audit: записать в state_graph        # commit с полной трассой
```

Остальное (silence, imbalance, sync_error EMA, self-prediction) — в
`_advance_tick` background-цикла, см. friston-loop.md.

---

## UI

Панель в header показывает 4 бара:

- 🟢 Интерес (dopamine)
- 🟣 Стабильность (serotonin)
- 🟠 Напряжение (norepinephrine)
- 🔴 Усталость (display_burnout)

Тултипы: технические имена (Dopamine / Serotonin / Norepinephrine / Burnout).

---

## Параметры

| Параметр | Значение | Что делает |
|---|---|---|
| `decay_DA` | 0.9 | Быстрая реакция на новизну |
| `decay_S` | 0.95 | Медленная (стабильность копится) |
| `decay_NE` | 0.9 | Быстрая (реакция на неопределённость) |
| `RPE_GAIN` | 0.15 | Сила RPE-спайка |
| `TAU_STABLE` | 0.6 | Порог d за которым начинается conflict |
| `THETA_ACTIVE` | 0.15 | Вход во freeze |
| `THETA_RECOVERY` | 0.08 | Выход из freeze (гистерезис) |

Все EMA-константы живут в `src/ema.py::Decays`.

---

## Где в коде

- `src/neurochem.py` — `Neurochem` class + `ProtectiveFreeze`
- `src/horizon.py::CognitiveState` держит `self.neuro` + `self.freeze`,
  делегирует через `apply_to_bayes`, `update_neurochem`, `inject_ne`
- `src/tick_nand.py` — после distinct-matrix вызывает
  `update_neurochem(d=mean_d, weights=confidences)` — замыкает контур:
  граф → нейрохимия → apply_to_bayes → граф
- `src/ema.py::Decays` — все decay константы в одном месте
- `/assist/state` endpoint выдаёт `neurochem: {dopamine, serotonin,
  norepinephrine, burnout, gamma, freeze_active, ...}`

**Открыто:** REM-переработка эпизодов с высоким |rpe| через Pump,
intrinsic pull в DMN (`target = argmax(dopamine · novelty)`), circadian
baseline drift (DA утром, S вечером).

---

**Навигация:** [← Horizon](horizon-design.md) · [Индекс](README.md) · [Следующее: Symbiosis →](symbiosis-design.md)
