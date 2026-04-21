# CognitiveState — адаптивный контроллер

Один объект контроля для всей системы: Horizon-слой (precision, policy,
γ, τ) + нейрохимия через композицию с [Neurochem + ProtectiveFreeze](neurochem-design.md).

Без контроллера все фазы tick'а использовали бы одинаковые LLM-параметры
(temperature=0.9, top_k=40) — brainstorm, SmartDC, collapse в одной
«ширине мышления». Это как ехать на одной передаче. `CognitiveState`
(`src/horizon.py`) — контроллер между tick и LLM: не генерирует контент,
управляет **как** генерировать.

`get_global_state()` — singleton, один на систему (одна нейрохимия на
человека, sync-prime). Workspace'ы имеют свой Horizon snapshot в graph
state, но neurochem общий.

---

## Параметры

### Horizon-слой

| Параметр | Что контролирует | Как обновляется |
|---|---|---|
| **Π (precision)** | Уверенность в модели мира (0–1). Управляет temp, top_k, novelty | `P += α·(target − surprise)` |
| **Λ (policy_weights)** | Веса фаз {generate, merge, elaborate, doubt} | Успех → вес ↑, провал → вес ↓ |
| **γ (gamma)** | Байесовская чувствительность (NAND) | Autocal через EMA(d(A,A)) + HRV nudge |
| **T (temperature_nand)** | «Резкость» выбора в NAND | `T₀ · (1 − κ·NE) + T_floor` |
| **τ_in / τ_out** | Пороги distinct-зон CONFIRM / EXPLORE / CONFLICT | HRV nudge + S modulation |

### Нейрохимический слой

| Скаляр | Роль |
|---|---|
| **serotonin** | Стабильность весов → входит в γ (низкий S → γ растёт) |
| **norepinephrine** | Arousal → T_eff, horizon_budget, входит в γ |
| **dopamine** | Новизна (EMA от distinct) — тянет в сторону нового в DMN |
| **freeze.accumulator** | Хронический конфликт → триггер PROTECTIVE_FREEZE |

γ — derived: `γ = 2.0 + 3.0 · NE · (1 − S)`. Отдельного поля нет.
Детали — [neurochem-design.md](neurochem-design.md).

---

## Maturity drift

Скаляр `maturity ∈ [0, 1]` растёт логистически на verified-события
(нода прошла conf ≥ 0.8, goal resolved). **Effective precision** =
`raw + 0.4 · (maturity − 0.5)`:

```
maturity=0.0  → effective = raw − 0.2   (младенец — wide cone, temp высокая)
maturity=0.5  → effective = raw         (нейтрально)
maturity=1.0  → effective = raw + 0.2   (зрелый — narrow cone, temp низкая)
```

`to_llm_params()` читает effective. ~1000 verifications для maturity ≈ 0.95 —
медленный биологический рост.

---

## Precision → параметры LLM

```
temperature       = clamp(1.0 − precision, 0.1, 1.5)
top_k             = clamp(10 + 90·(1 − precision), 10, 100)
top_p             = clamp(0.5 + 0.5·precision, 0.7, 0.95)
novelty_threshold = 0.85 + 0.1·precision
```

| Precision | Temperature | top_k | Novelty | Режим |
|---|---|---|---|---|
| 0.3 | 0.7 | 73 | 0.88 | Широкий поиск, креативность |
| 0.5 | 0.5 | 55 | 0.90 | Сбалансированный |
| 0.8 | 0.2 | 28 | 0.93 | Узкий фокус, точность |

Один параметр — четыре эффекта. Precision ↓ = конус расширяется.

---

## Обратная связь

```
surprise = 1 − confidence_после_SmartDC

surprise > target → «хаотично»    → precision ↑ → конус сужается
surprise < target → «предсказуемо» → precision ↓ → конус расширяется
surprise ≈ target → зона потока
```

`target_surprise > 0` **всегда**. Система хочет чтобы реальность чуть
не совпадала с ожиданием — иначе зачем думать.

---

## Семь состояний

| Состояние | Precision | Триггер |
|---|---|---|
| **EXPLORATION** | 0.3–0.5 | Мало гипотез, высокая entropy |
| **EXECUTION** | 0.7–0.9 | Есть фокус, проверяем |
| **RECOVERY** | 0.4 → 0.6 | Surprise spike (Δ > 0.3) |
| **INTEGRATION** | 0.5–0.6 | Данные собраны, low novelty → синтез |
| **STABILIZE** | — | HRV coherence < 0.3 (калибровка / сброс) |
| **CONFLICT** | — | sync_error > 0.75 |
| **PROTECTIVE_FREEZE** | — | `freeze.accumulator > 0.15` — блокирует Bayes updates |

Переходы с гистерезисом — система «залипает» в текущем пока precision
не уйдёт достаточно далеко (разрыв 0.05 устраняет дребезг).

---

## Выбор фазы

`select_phase(available)` — из доступных выбирает с наибольшим policy
weight. Веса адаптируются: успех (confidence выросла, новые ноды) →
вес ↑, провал → вес ↓. Нормализация: сумма = 1.0, floor = 0.05. Не
round-robin, система учится что работает.

---

## Presets (13 режимов)

Каждый из 13 режимов = preset для Horizon:

| Режим | Precision | Target surprise | Policy акцент |
|---|---|---|---|
| Исследование | 0.4 | 0.3 | generate 0.3 |
| Мозговой штурм | 0.3 | 0.5 | generate 0.5 |
| Фокус | 0.7 | 0.15 | doubt 0.5 |
| Выбор | 0.7 | 0.15 | doubt 0.5 |

Один движок, 13 настроек. Не 13 алгоритмов — один Horizon с разными
пресетами.

---

## Цикл

```
tick() → horizon.select_phase(available)   → какую фазу запустить
      → horizon.to_llm_params()            → {temp, top_k, novelty}
      → LLM вызов
      → surprise = 1 − confidence
      → horizon.update(surprise, gradient)  → precision обновляется
      → следующий tick с новыми параметрами
```

UI overlay: `Step 15 · EXECUTION · Π=0.78 · 4/6 verified`.

---

## Где в коде

- `src/horizon.py` — `CognitiveState`, `get_global_state()`,
  `create_horizon()`, 14 presets; методы `apply_to_bayes`,
  `update_neurochem`, `effective_temperature`, `horizon_budget`,
  `get_metrics`, `to_dict`/`from_dict`
- `src/neurochem.py` — composition (`self.neuro`, `self.freeze`)
- `src/tick_nand.py` — tick загружает Horizon, считает distinct-matrix,
  кормит нейрохимию, маршрутизирует по зонам
- `/graph/horizon-feedback` — autorun отправляет surprise обратно

---

**Навигация:** [← Tick](tick-design.md) · [Индекс](README.md) · [Следующее: Neurochem →](neurochem-design.md)
