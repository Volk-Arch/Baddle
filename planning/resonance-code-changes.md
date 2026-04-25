# Резонансная модель — `aperture` спецификация

`aperture` — единый скаляр в depth engine, заменяющий 3 несвязанных
параметра. Реализует апертурный предел из [docs/resonance-model.md](../docs/resonance-model.md)
и [docs/cone-design.md § Апертурный предел](../docs/cone-design.md#апертурный-предел-3d).

> **Контекст:** другие два пункта пакета (`frequency_regime`, `focus_residue`)
> завершены 2026-04-25 — см. `UserState.frequency_regime`, `UserState.focus_residue`.
> Aperture отложен в Tier 2 потому что требует settings UI rework.

Статус: **спецификация, не начато.** Оценка ~2ч код + ~1ч UI.

---

## Проблема

Сейчас глубокое рассуждение настраивается **тремя несвязанными
параметрами**:

- [`deep_mode_steps`](../src/api_backend.py) — dict per-mode, сколько итераций
  (5 для horizon, 3 для scout, ...)
- [`deep_response_format`](../src/api_backend.py) — brief / essay / article / list
- [`deep_batched_synthesis`](../src/api_backend.py) — bool, пирамидальный
  collapse или один call

Эти три knob'а **взаимозависимы**: article формат с 3 шагами
бессмыслен (не хватит нод), brief с 25 шагами — тоже бессмыслен
(сжимаем в 3 предложения огромное исследование). Юзеру сложно
подобрать консистентную комбинацию.

## Резонансная рамка

Апертурный предел **Охват × Дальность × Детализация = const** —
это одно 3D-ограничение. Конус с единым объёмом внимания
распределяется между тремя осями. Значит управляющий параметр
должен быть **один скаляр**, а распределение по трём осям
— детерминированная функция от него.

## Спецификация

Новый параметр `aperture: float ∈ [0, 1]` в settings.

| aperture | Форма конуса | Mode-steps | Format | Batched |
|---|---|---|---|---|
| **0.0–0.2** | игла / фокус | min(5, current) | brief | False (один call) |
| **0.2–0.4** | узкий луч | min(10, current) | essay | False |
| **0.4–0.7** | сбалансированный | current (as-is) | essay | True |
| **0.7–0.9** | широкий обзор | 1.5 × current | article | True |
| **0.9–1.0** | панорамный | 2 × current | article + list | True |

Текущие `deep_mode_steps` сохраняются как **base-профиль** для
каждого режима — aperture мультиплицирует их, не заменяет. Horizon
останется «глубже чем scout» на любой апертуре; aperture меняет
**общий масштаб**.

## Изменения в коде

**[src/api_backend.py](../src/api_backend.py):**

```python
# Дефолт в _settings
"deep_aperture": 0.5,  # сбалансированный

def get_aperture() -> float:
    return max(0.0, min(1.0, float(_settings.get("deep_aperture", 0.5))))

def get_mode_depth(mode_id: str) -> int:
    base = _get_mode_base_depth(mode_id)  # существующая логика
    aperture = get_aperture()
    multiplier = _aperture_to_depth_mult(aperture)  # 0.5→0.5, 0.5→1.0, 0.9→2.0
    return max(1, min(200, int(base * multiplier)))

def get_deep_response_format() -> str:
    aperture = get_aperture()
    if aperture < 0.25: return "brief"
    if aperture < 0.7: return "essay"
    if aperture < 0.9: return "article"
    return "article"  # with list extensions

def is_deep_batched() -> bool:
    return get_aperture() >= 0.4  # узкий фокус не нуждается в pyramid
```

Старые ключи (`deep_response_format`, `deep_batched_synthesis`) —
**deprecated but respected**: если юзер их выставил явно, override'ят
aperture-derived значения. Через 1-2 мес можно убрать совсем.

**[src/assistant_exec.py](../src/assistant_exec.py):**

В `execute_deep` использовать derived format/batched через getters — один вызов
get_aperture() в начале, остальные derived функции читают тот же
state (consistency).

**[static/js/settings.js](../static/js/settings.js) + [templates/partials/settings_modal.html](../templates/partials/settings_modal.html):**

Заменить три контрола (format selector, batched toggle, per-mode depth table)
на один **slider** `Апертура` 0–1 с четырьмя метками под ним:
`🎯 Фокус | 📘 Эссе | 📖 Статья | 🌐 Панорама`.

Per-mode depth table — оставить как **Advanced override**, свёрнутая
секция для power users.

### Дополнительно: `frequency_regime` integration

После реализации aperture добавить связь с уже существующим
`UserState.frequency_regime`:

- При `short_wave` (стресс/симпатика) — force `aperture = min(0.4, current)`
  в `execute_deep`. Не запускать панорамное рассуждение когда юзер не может
  резонировать с длинной волной.
- При `long_wave` (парасимпатика) — апертура работает как заданная.

## Миграция

1. При первом load настройки с новой версией: если `deep_aperture`
   отсутствует → попытаться вывести из текущих `deep_response_format`:
   brief→0.15, essay→0.5, article→0.8, list→0.9.
2. Старые settings продолжают работать, aperture показывается в UI
   как «текущая эффективная апертура» (derived).

## Риск

- **Power users** (сам Игорь в Lab) могут захотеть точный control.
  Решение: Advanced section с per-mode override остаётся.
- **14 режимов** имеют разную физику (tournament pairwise N²).
  Multiplier работает на top-level iterations, не меняет inner-loop
  — сохранение backward compatibility.

## Ценность

- Один скаляр в UI вместо трёх — проще для новых пользователей.
- Консистентность: невозможно выставить «brief + 25 шагов».
- Семантически связан с cone-design § Апертурный предел — UI и
  архитектура в одном языке.
- Динамическая адаптация через `frequency_regime` — апертура
  автоматически сужается в стрессе.

## Оценка

~2ч код + ~1ч UI. Средний риск (3 модуля затронуто, все в chat-layer).

---

**Связанные docs:**
- [resonance-model.md](../docs/resonance-model.md) — единый словарь
- [cone-design.md § Апертурный предел](../docs/cone-design.md#апертурный-предел-3d) — теория
- [breathing-mode.md](breathing-mode.md) — может использовать aperture для калибровки во время breathing-сессий
- [resonance-prompt-preset.md](resonance-prompt-preset.md) — параллельная UX-фича
