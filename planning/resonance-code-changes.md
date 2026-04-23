# Резонансная модель — код-изменения (Слой 2)

Три изменения в коде, которые **реализуют** резонансную рамку из
[docs/resonance-model.md](../docs/resonance-model.md). Каждое
самостоятельно, можно брать по одному.

- Не ломают существующую архитектуру — все **аддитивны** или
  **заменяют разрозненные knobs** одним.
- Backward-compatible — старые settings/API работают с сохранёнными
  defaults.
- Оценка: aperture ~2ч, frequency_regime ~1ч, focus_residue ~1ч.

Статус: **черновик спецификации**, не начато.

---

## 1. `aperture` как единый скаляр в depth engine

### Проблема

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

### Резонансная рамка

Апертурный предел **Охват × Дальность × Детализация = const** —
это одно 3D-ограничение. Конус с единым объёмом внимания
распределяется между тремя осями. Значит управляющий параметр
должен быть **один скаляр**, а распределение по трём осям
— детерминированная функция от него.

### Спецификация

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

### Изменения в коде

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

### Миграция

1. При первом load настройки с новой версией: если `deep_aperture`
   отсутствует → попытаться вывести из текущих `deep_response_format`:
   brief→0.15, essay→0.5, article→0.8, list→0.9.
2. Старые settings продолжают работать, aperture показывается в UI
   как «текущая эффективная апертура» (derived).

### Риск

- **Power users** (сам Игорь в Lab) могут захотеть точный control.
  Решение: Advanced section с per-mode override остаётся.
- **14 режимов** имеют разную физику (tournament pairwise N²).
  Multiplier работает на top-level iterations, не меняет inner-loop
  — сохранение backward compatibility.

### Ценность

- Один скаляр в UI вместо трёх — proще для новых пользователей.
- Консистентность: невозможно выставить «brief + 25 шагов».
- Семантически связан с cone-design § Апертурный предел — UI и
  архитектура в одном языке.

### Оценка

~2ч. Средний риск (3 модуля затронуто, все в chat-layer).

---

## 2. `frequency_regime` как derived field в UserState

### Проблема

Текущий `sync_regime ∈ {FLOW, REST, PROTECT, CONFESS}` — это
классификация **по рассогласованию между user и system**. Он не
отвечает на вопрос «в какой частоте сейчас пользователь» —
только на «синхронен ли он с системой».

Резонансный взгляд: у пользователя есть **несущая частота**,
derived от ВНС-состояния + нейрохимии + темпа активности. Это
ортогональная ось к sync_regime. Baddle может использовать её
для **адаптации своего режима общения** (не «что сказать» — а
«в каком темпе»).

### Спецификация

Новое derived поле `UserState.frequency_regime` ∈
`{long_wave, short_wave, mixed, flat}`:

| Режим | Условия | Что означает |
|---|---|---|
| **long_wave** 🔵 | HRV coherence > 0.6 И RMSSD > 30 мс И NE < 0.5 | Парасимпатика, длинная λ, ассоциативный режим |
| **short_wave** 🔴 | HRV coherence < 0.4 ИЛИ LF/HF > 2.5 ИЛИ NE > 0.75 | Симпатика, короткая λ, реактивный режим |
| **mixed** | средние значения, HRV между 0.4–0.6 | Промежуточный, переключение возможно |
| **flat** | нет HRV данных + NE около baseline | Нет сигнала, не классифицируем |

**Параметры уточнимы** — через 1-2 нед реальных данных посмотреть
распределение и откалибровать пороги.

### Изменения в коде

**[src/user_state.py](../src/user_state.py):**

```python
@property
def frequency_regime(self) -> str:
    """Несущая частота текущего состояния. Derived, не persistent."""
    if self.hrv_coherence is None:
        return "flat"
    hrv = self.hrv_coherence
    rmssd = self.hrv_rmssd or 0
    ne = self.norepinephrine

    if hrv > 0.6 and rmssd > 30 and ne < 0.5:
        return "long_wave"
    if hrv < 0.4 or ne > 0.75:
        return "short_wave"
    return "mixed"
```

**[src/assistant.py](../src/assistant.py) `/assist/state`:**

Добавить в response `frequency_regime` рядом с существующим
`sync_regime`. UI (`cone_live.js`) может показывать как подсказку
«🔵 длинная волна» в header, рядом с neurochem barами.

**[templates/partials/header.html](../templates/partials/header.html) / [static/css/style.css](../static/css/style.css):**

Маленький indicator-chip рядом с HRV-бара: иконка + текст. Кликом
открывает tooltip «что это значит» + ссылку на dhana-сессию
(если активна — см. [breathing-mode.md](breathing-mode.md)).

### Использование внутри кода

1. **`_check_sync_seeking`** ([src/cognitive_loop.py](../src/cognitive_loop.py)) —
   выбирать tone по frequency_regime: long_wave → ambient/curious,
   short_wave → simple/reference (не грузить абстракциями когда
   юзер в стрессе).

2. **execute_deep** ([src/assistant_exec.py](../src/assistant_exec.py)) —
   при short_wave force `aperture = min(0.4, current)` — не
   запускать панорамное рассуждение когда юзер не может
   резонировать с длинной волной.

3. **morning briefing** ([src/suggestions.py](../src/suggestions.py)) —
   если пользователь регулярно в short_wave по утрам → briefing
   формат brief/list вместо article.

### Риск

- **Шумный signal** при редком HRV data — `flat` default фильтрует.
- **Хардкод порогов** — через 2 нед калибровать, пока это ok.
- **Может конфликтовать с sync_regime** — но они ортогональны:
  FLOW+short_wave (срочная задача, синхронно) — валидная комбинация.

### Ценность

- Честный сигнал для tone adaptation, не гадание по last_input.
- Использует существующие HRV+neurochem без новой инфраструктуры.
- Готовит почву для breathing-mode прompt (см. отдельный doc).

### Оценка

~1ч. Чистое расширение, нулевой риск регрессии (derived field).

---

## 3. `focus_residue` — счётчик рассогласования при переключениях

### Проблема

Из [friston-loop.md § Отсутствующий объект](../docs/friston-loop.md) + резонансной
рамки: **переключение контекста стоит дорого**. Новая волна
требует `E_донастройки`, фрагменты старой волны остаются как шум
(attentional residue).

Сейчас в Baddle это частично решается через suppress
observation_suggestion при `last_input < 10min`. Но это грубый
proxy — **частое переключение между модами/сессиями не ловится**.

### Резонансная рамка

«Residue» — это мера того, насколько свежие переключения
накопились. Высокий residue = много незавершённых волн →
приглушать proactive alerts, не добавлять ещё нагрузку.

### Спецификация

Новое поле `UserState.focus_residue: float ∈ [0, 1]`:

- **+0.15** при смене `mode_id` в новом запросе (внутри
  `record_action` с kind=chat_event_*).
- **+0.25** при смене workspace / abrupt session restart.
- **+0.05** при каждом rapid input (< 30 сек после предыдущего).
- **−0.05** каждую минуту без активности (естественное
  затухание residue).
- Clamp в [0, 1].

### Изменения в коде

**[src/user_state.py](../src/user_state.py):**

```python
self.focus_residue: float = 0.0
self._last_mode_id: Optional[str] = None
self._last_input_ts: Optional[float] = None

def bump_focus_residue(self, mode_id: Optional[str], now: float):
    """Вызывается из record_action при каждом user-event."""
    if self._last_input_ts and (now - self._last_input_ts) < 30:
        self.focus_residue = min(1.0, self.focus_residue + 0.05)

    if mode_id and self._last_mode_id and mode_id != self._last_mode_id:
        self.focus_residue = min(1.0, self.focus_residue + 0.15)

    self._last_mode_id = mode_id
    self._last_input_ts = now

def decay_focus_residue(self, dt_seconds: float):
    """Вызывается из _advance_tick раз в минуту."""
    self.focus_residue = max(0.0, self.focus_residue - 0.05 * (dt_seconds / 60))
```

**[src/graph_logic.py](../src/graph_logic.py) `record_action`:**

При action с actor=user — вызвать `user_state.bump_focus_residue(mode_id, ts)`.

**[src/cognitive_loop.py](../src/cognitive_loop.py) `_advance_tick`:**

Раз в минуту `user_state.decay_focus_residue(dt)`.

### Использование

1. **observation_suggestion throttle** ([src/cognitive_loop.py](../src/cognitive_loop.py)) —
   если `focus_residue > 0.5` → silent skip даже если прошло 10+
   минут. Пользователь в хаосе переключений, не добавляем новых
   сигналов.

2. **sync_seeking gate** — если residue > 0.7, заблокировать
   active sync-seeking (это ещё одно переключение, добавит
   интерференции).

3. **UI indicator** — в header маленькая иконка 🌀 при residue >
   0.6 с тултипом «Много переключений, я помолчу».

### Риск

- **Константы подобраны от балды** — через месяц калибровать
  по реальному разбросу.
- **Может переоценивать residue** при быстрой работе (power user
  делает много mode switches, это **не** хаос). Решение: через
  месяц посмотреть корреляцию с `sync_error` — если high residue
  ↔ high sync_error, значит proxy правильный.

### Ценность

- Даёт физически мотивированный gate для proactive alerts.
- Закрывает слабое место текущей логики (грубый last_input proxy).
- Будет отображаться в prime_directive.jsonl как ещё один канал для
  диагностики через 2 мес.

### Оценка

~1ч. Аддитивное расширение, нулевой риск регрессии.

---

## Порядок реализации

Рекомендуемый:

1. **`frequency_regime`** первым (чистое расширение, показывает на
   UI frequency-метку → Игорь видит данные на которых строятся
   следующие два).
2. **`focus_residue`** вторым (аддитивно, использует те же данные
   + record_action).
3. **`aperture`** последним (единственный с реальным рефакторингом
   UI + settings migration).

Или — по желанию — первым aperture если хочется максимум UI-эффекта
за сессию.

---

## Не делаем в этой сессии

- Unit-тесты для frequency_regime thresholds — через 2 нед, когда
  будут реальные данные и можно откалибровать.
- Миграция старых settings.json — aperture default 0.5 = текущее
  поведение essay+3steps+batched, для большинства invisible.
- Перевод state_graph.jsonl на новый формат — не требуется,
  frequency_regime и focus_residue это runtime-derived, не persist.

---

**Связанные docs:**
- [resonance-model.md](../docs/resonance-model.md) — единый словарь
- [cone-design.md § Апертурный предел](../docs/cone-design.md#апертурный-предел-3d) — теория
- [friston-loop.md § Отсутствующий объект](../docs/friston-loop.md#отсутствующий-объект--prediction-error--0) — обоснование focus_residue
- [breathing-mode.md](breathing-mode.md) — использует frequency_regime
- [resonance-prompt-preset.md](resonance-prompt-preset.md) — параллельная UX-фича
