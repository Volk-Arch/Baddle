# CognitiveHorizon — описание реализации

> Этот файл сохранён из TODO при завершении v1.5. Справочный материал.

## Три параметра

```
Π (precision)       : 0.0–1.0. Уверенность в текущей модели.
Λ (policy_weights)  : {generate, merge, elaborate, doubt}. Веса фаз.
Γ (context_frame)   : Активная рамка — промпт, правила.
```

## Правила обновления

```
1. Precision:
   P_new = clamp(P_old + α * (surprise - target_surprise), 0.0, 1.0)
   surprise = 1 - confidence_после_SmartDC
   α = 0.05–0.2

2. Policy Weights:
   W_i += β * gradient_i;  W = normalize(W)
   gradient = +1 успех, -1 провал
   β = 0.1–0.3

3. Context Frame:
   if novelty > threshold: switch_frame()
```

## Маппинг Precision → LLM

```python
temperature = clamp(1.0 - precision, 0.1, 1.5)
top_k       = clamp(10 + 90 * (1 - precision), 10, 100)
top_p       = clamp(0.5 + 0.5 * precision, 0.7, 0.95)
```

| Precision | Temperature | Режим |
|-----------|-------------|-------|
| 0.3 | 0.7 | Широкий поиск (GENERATE, META) |
| 0.5 | 0.5 | Сбалансированный (ELABORATE) |
| 0.8 | 0.2 | Узкий фокус (DOUBT) |

## Четыре состояния

| Состояние | Precision | Триггер |
|-----------|-----------|---------|
| EXPLORATION | 0.3–0.5 | Мало гипотез, высокая entropy |
| EXECUTION | 0.7–0.9 | Есть фокус, проверяем |
| RECOVERY | 0.4→0.6 | Surprise spike |
| INTEGRATION | 0.5–0.6 | Данные собраны, синтезируем |

## Presets

```python
"horizon":    precision=0.4, target=0.3, generate=0.3
"fan":        precision=0.3, target=0.5, generate=0.5
"vector":     precision=0.7, target=0.15, doubt=0.5
"tournament": precision=0.7, target=0.15, doubt=0.5
```

## Интеграция в tick

```
tick → horizon.to_llm_params() → {temp, top_k}
    → LLM вызов
    → результат → surprise = 1 - confidence
    → horizon.update(surprise, gradient)
    → параметры обновились → следующий tick
```

## Метрики UI

```
width         = 1 / (precision + ε)
focus_entropy = -Σ(w * log₂(w))
```

Оверлей: `Step 15 · EXECUTION · Π=0.78 · 4/6 verified`
