# CognitiveState — адаптивный контроллер

> С v8d/v5d класс называется **`CognitiveState`** (alias `CognitiveHorizon`
> для backward compat). Это **единый объект**: Horizon-слой (precision, policy,
> γ, τ) + нейрохимический слой (S, NE, DA, burnout). Спецификация нейрохимии →
> [neurochem-design.md](neurochem-design.md).

## Идея

Без контроллера все фазы tick'а использовали бы одинаковые LLM-параметры:
temperature=0.9, top_k=40. Brainstorm, SmartDC, Collapse — всё с одинаковой
«шириной мышления». Это как ехать на одной передаче.

`CognitiveState` (`src/horizon.py`) — контроллер между tick и LLM. Не
генерирует контент — управляет **как** генерировать. Один параметр
(precision) управляет Horizon-слоем; нейрохимия модулирует параметры
динамически (γ·S в Байесе, NE → T, и т.д.).

**Глобальный singleton** `get_global_state()` — один `CognitiveState` на систему
(одна нейрохимия на человека, sync-prime). Workspace'ы имеют свой Horizon
snapshot в graph state, но neurochem остаётся общим.

## Параметры

Horizon-слой:
| Параметр | Что контролирует | Как обновляется |
|----------|-----------------|-----------------|
| **Π (precision)** | Уверенность в модели мира (0–1). Управляет temp, top_k, novelty | Prediction error: `P += α·(target − surprise)` |
| **Λ (policy_weights)** | Веса фаз {generate, merge, elaborate, doubt} | Gradient: успех → вес ↑, провал → вес ↓ |
| **γ (gamma)** | Байесовская чувствительность (NAND) | Autocal через EMA(d(A,A)) + HRV nudge |
| **T (temperature_nand)** | «Резкость» выбора в NAND | `T₀·(1−κ·NE) + T_floor` — NE обостряет |
| **τ_in / τ_out** | Пороги distinct-зон CONFIRM/EXPLORE/CONFLICT | HRV nudge + S modulation |

Нейрохимический слой (детально → [neurochem-design.md](neurochem-design.md)):
| Скаляр | Роль | Влияние |
|--------|------|---------|
| **serotonin** | Стабильность весов, уверенность | входит в γ: низкий S → γ растёт |
| **norepinephrine** | Arousal, Horizon/DMN бюджет | `T_eff`, `budget_H`, входит в γ |
| **dopamine** | Новизна (EMA от distinct) | «тянет в сторону нового» в DMN |
| **freeze.accumulator** | Хронический конфликт | Триггер `PROTECTIVE_FREEZE` |

γ — derived property: `γ = 2.0 + 3.0 · norepinephrine · (1 − serotonin)`.
Отдельного поля gamma нет.

## Maturity drift (младенец → зрелый)

Отдельный скаляр `maturity ∈ [0, 1]` растёт логистически на verified-
события (node crossed conf ≥ 0.8, или goal resolved). **Effective precision**
= `raw_precision + 0.4 · (maturity − 0.5)` — центр диапазона сдвигается
на ±0.2 вокруг raw.

```
maturity=0.0  → effective = raw − 0.2   (младенец, wide cone, temp высокая)
maturity=0.5  → effective = raw         (нейтрально)
maturity=1.0  → effective = raw + 0.2   (зрелый, narrow cone, temp низкая)
```

`to_llm_params()` и `_target_state` читают **effective**, не raw. UI видит
оба через `/assist/state: {precision, effective_precision, maturity}`.

Параметры: `MATURITY_GROWTH_RATE = 0.003`, `MATURITY_GAIN = 0.4`. ~1000
verifications нужно для maturity ≈ 0.95 — медленный биологический рост.

## Precision → параметры LLM

```python
temperature      = clamp(1.0 - precision, 0.1, 1.5)
top_k            = clamp(10 + 90 * (1 - precision), 10, 100)
top_p            = clamp(0.5 + 0.5 * precision, 0.7, 0.95)
novelty_threshold = 0.85 + 0.1 * precision
```

| Precision | Temperature | top_k | Novelty | Режим |
|-----------|-------------|-------|---------|-------|
| 0.3 | 0.7 | 73 | 0.88 | Широкий поиск, креативность |
| 0.5 | 0.5 | 55 | 0.90 | Сбалансированный |
| 0.8 | 0.2 | 28 | 0.93 | Узкий фокус, точность |

Один параметр — четыре эффекта. Precision ↓ = конус расширяется. Precision ↑ = конус сужается.

## Обратная связь

```
surprise = 1 - confidence_после_SmartDC

surprise > target → "хаотично"     → precision ↑ → конус сужается
surprise < target → "предсказуемо" → precision ↓ → конус расширяется
surprise ≈ target → зона потока
```

target_surprise > 0 **всегда**. Система хочет чтобы реальность чуть не совпадала с ожиданием — иначе зачем думать.

## Семь состояний (было 4, расширено)

| Состояние | Precision | Триггер |
|-----------|-----------|---------|
| **EXPLORATION** | 0.3–0.5 | Мало гипотез, высокая entropy. Широко ищем |
| **EXECUTION** | 0.7–0.9 | Есть фокус, проверяем. Узко и точно |
| **RECOVERY** | 0.4→0.6 | Surprise spike (Δsurprise > 0.3). Расширяем обратно |
| **INTEGRATION** | 0.5–0.6 | Данные собраны, low novelty. Синтезируем |

### Плавные переходы (гистерезис)

Пороги зависят от текущего состояния — разрыв 0.05 устраняет дребезг:

```
EXPLORATION → выход:  precision > 0.45 (не 0.40)
EXECUTION   → выход:  precision < 0.65 (не 0.70)
```

Система "залипает" в текущем состоянии, пока precision не уйдёт достаточно далеко. Предотвращает колебания на границах.

**Расширенный набор (активен):**
- EXPLORATION / EXECUTION / RECOVERY / INTEGRATION — базовые 4, precision-driven
- STABILIZE — сработает при HRV coherence < 0.3 (сброс/калибровка)
- CONFLICT — при sync_error > 0.75 (система не понимает юзера)
- **PROTECTIVE_FREEZE** — при `freeze.accumulator > 0.15` (THETA_ACTIVE).
  Блокирует Bayes обновления (`apply_to_bayes` возвращает prior). Recovery
  гистерезисом: выход при `accumulator < 0.08` (THETA_RECOVERY).
  См. [neurochem-design.md](neurochem-design.md).

## Выбор фазы

`select_phase(available)` — из доступных фаз выбирает с наибольшим policy weight.

Веса обновляются после каждого шага:
- Фаза дала результат (confidence выросла, новые ноды) → вес ↑
- Фаза не помогла → вес ↓
- Нормализация: сумма = 1.0, minimum = 0.05

Это не round-robin — система адаптируется к тому, что работает.

## Presets (13 режимов)

Каждый из 13 режимов = preset для Horizon:

| Режим | Precision | Target surprise | Policy акцент |
|-------|-----------|----------------|--------------|
| Исследование | 0.4 | 0.3 | generate 0.3 |
| Мозговой штурм | 0.3 | 0.5 | generate 0.5 |
| Фокус | 0.7 | 0.15 | doubt 0.5 |
| Выбор | 0.7 | 0.15 | doubt 0.5 |

Один движок, 13 настроек. Не 13 алгоритмов — один Horizon с разными пресетами.

## Интеграция

```
tick() → horizon.select_phase(available)   → какую фазу запустить
      → horizon.to_llm_params()            → {temp, top_k, novelty}
      → LLM вызов с этими параметрами
      → результат → surprise = 1 - confidence
      → horizon.update(surprise, gradient)  → precision обновляется
      → следующий tick с новыми параметрами
```

UI overlay: `Step 15 · EXECUTION · Π=0.78 · 4/6 verified`

## Файлы

- `src/horizon.py` — `CognitiveState`, `get_global_state()`, `create_horizon()`,
  14 presets. Методы: `apply_to_bayes`, `update_neurochem`, `update_from_hrv`,
  `inject_ne`, `effective_temperature`, `horizon_budget`, `get_metrics`,
  `to_dict`/`from_dict`. Композиция: `self.neuro` (Neurochem) + `self.freeze`
  (ProtectiveFreeze)
- `src/neurochem.py` — `Neurochem` + `ProtectiveFreeze`, 3 скаляра + derived γ
- `src/tick_nand.py` — tick загружает/создаёт Horizon, считает distinct-matrix,
  кормит нейрохимию (`update_neurochem(d, weights)`), маршрутизирует по зонам
- `src/graph_routes.py` — autorun отправляет feedback через `/graph/horizon-feedback`
