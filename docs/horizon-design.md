# CognitiveHorizon — адаптивный контроллер

## Идея

Без Horizon все фазы tick используют одинаковые параметры: temperature=0.9, top_k=40. Brainstorm, SmartDC, Collapse — всё с одинаковой "шириной мышления". Это как ехать на одной передаче.

CognitiveHorizon (`src/horizon.py`) — контроллер между tick и LLM. Не генерирует контент — управляет **как** генерировать. Один параметр (precision) управляет всем сразу.

## Три параметра

| Параметр | Что контролирует | Как обновляется |
|----------|-----------------|-----------------|
| **Π (precision)** | Уверенность в модели мира (0–1). Управляет температурой, top_k, novelty | Prediction error: `P += α·(target_surprise - surprise)` |
| **Λ (policy_weights)** | Веса фаз {generate, merge, elaborate, doubt}. Какую фазу выбрать | Gradient: успешная фаза → вес ↑, неуспешная → вес ↓ |
| **Γ (context_frame)** | Активная рамка, промпт, правила | Переключается при высокой novelty |

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

## Четыре состояния

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

Планируется расширение до 7 состояний: +STABILIZE (сброс/калибровка), +SHIFT (поворот φ), +CONFLICT (несовместимые приоры). См. [hrv-design.md](hrv-design.md).

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

- `src/horizon.py` — CognitiveHorizon, create_horizon(), 13 presets
- `src/thinking.py` — tick() загружает/создаёт Horizon, вызывает select_phase(), передаёт horizon_params
- `src/graph_routes.py` — autorun отправляет feedback через `/graph/horizon-feedback`
