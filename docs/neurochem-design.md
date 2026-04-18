# Нейрохимия Baddle

> Три скаляра + один защитный режим. Минимальный набор сигналов, который даёт
> системе «настроение», любопытство и защиту от перегрузки — без того чтобы
> копировать состояние пользователя.

## Идея в одной фразе

Мышление не равно чистой логике. В реальном мозге баланс нейромедиаторов
модулирует скорость обучения, остроту внимания и тягу к исследованию. Baddle
использует три скаляра с **чёткими ролями**, каждый обновляется **одной EMA-
формулой** на основе событий в графе.

```
dopamine       — реакция на новизну (EMA от d = distinct-расстояний)
serotonin      — стабильность весов (EMA от 1 − std(ΔW))
norepinephrine — неопределённость (EMA от энтропии распределения W)
```

Плюс один защитный режим:

```
ProtectiveFreeze — накапливается при хроническом конфликте + нестабильности.
                   При пороге θ блокирует Bayes update.
```

## Формулы

Все обновления — одна строка EMA с decay:

```python
# При каждом тике цикла мышления:
chem.dopamine       = 0.9  * chem.dopamine       + 0.1  * d
chem.serotonin      = 0.95 * chem.serotonin      + 0.05 * (1 − std(W_change))
chem.norepinephrine = 0.9  * chem.norepinephrine + 0.1  * normalized_entropy(W)
```

### γ derived (не сохраняется отдельно)

```
γ = 2.0 + 3.0 · norepinephrine · (1 − serotonin)
```

Высокое напряжение при низкой стабильности → повышенная Bayesian-
чувствительность. Спокойный уверенный режим → γ ≈ 2.0 (baseline). Возбуждённый
ищущий → γ → 5.0.

### Bayes update через distinct

```
logit(post) = logit(prior) + γ · (1 − 2d)
```

Signed знаковая форма: d=0 (совпадение) → максимальное усиление, d=0.5 (неясно)
→ нет изменения, d=1 (противоречие) → максимальное ослабление. γ из формулы
выше — чувствительность масштаба.

### Burnout → PROTECTIVE_FREEZE

```python
conflict_signal = max(0, d − 0.6)            # порог conflict
instability     = 1 − serotonin
accumulator     = 0.95 · accumulator + 0.05 · conflict_signal · instability

if accumulator > 0.15:  active = True        # freeze
if accumulator < 0.08 and active: active = False  # recovery (гистерезис)
```

Freeze блокирует `apply_to_bayes` — возвращает prior. Система **замораживает
обновления**, логирует события, продолжает DMN. Выход — при восстановлении
стабильности.

## Что откуда приходит

Три скаляра питаются **динамикой графа**, не прямыми сигналами юзера:

| Скаляр | Источник | Что измеряет |
|--------|----------|-------------|
| dopamine | `d` из distinct(a,b) в tick'е | Новизну — насколько новое пришло |
| serotonin | ΔW от Bayes-updates | Стабильность — меняются ли убеждения |
| norepinephrine | Энтропия текущих weights | Неопределённость — размыто ли распределение |

Юзерский feedback (кнопки 👍/👎) конвертируется в **pseudo-d**:
- accepted → d = 0.2 (слабая новизна = подтверждение)
- rejected → d = 0.8 (сильная новизна = система была неправа)

Так feedback входит через тот же канал, что и весь граф.

## HRV НЕ влияет на нейрохимию

Важное решение: HRV — сигнал тела **пользователя**, не состояние системы.
Он идёт в:
- **Советы юзеру** («ты устал, отложи», «coherence низкая, подыши»)
- **Расчёт energy recovery** (потолок дневного ресурса)
- **Хранение HRV-полей** (coherence/rmssd/stress — для UI и алертов)

Он **не трогает** dopamine/serotonin/norepinephrine/freeze. Юзер устал — система
замечает и помогает, а не «устаёт» вместе с ним. Внутренняя динамика
эволюционирует по собственным сигналам от графа.

## Порядок вызовов в тике

```
1. d = distinct(a, b) → сравнение идей
2. apply_to_bayes(prior, d) → posterior (γ derived, freeze-aware)
3. chem.update(d=d, w_change=ΔW, weights=current_W) → обновить три скаляра
4. freeze.update(d, serotonin=chem.serotonin) → накопитель + вход/выход из freeze
5. Git-audit: commit {action, chem.to_dict(), freeze.to_dict(), d, prior, post}
```

## Интеграция

### Структура

- `src/neurochem.py` — `Neurochem` class (3 скаляра + derived gamma + apply_to_bayes) +
  `ProtectiveFreeze` class (отдельный защитный режим)
- `src/horizon.py` — `CognitiveState` держит `self.neuro` + `self.freeze`,
  делегирует через `apply_to_bayes`, `update_neurochem`, `inject_ne`.
  Legacy свойств `S`/`NE`/`DA_tonic` больше нет — single-path.
- `/assist/state` endpoint выдаёт `neurochem: {dopamine, serotonin, norepinephrine,
  burnout, gamma, freeze_active, state_origin}` — других имён нет.
- `src/tick_nand.py` — в tick'е после distinct-matrix вызывает
  `update_neurochem(d=mean_d, weights=confidences)` на глобальной и локальной
  нейрохимии. Это замыкает контур: граф → нейрохимия → apply_to_bayes → граф.

### UI

Панель в header чата показывает 4 бара:
- **Стабильность** (serotonin) — фиолетовый
- **Напряжение** (norepinephrine) — оранжевый
- **Интерес** (dopamine) — зелёный
- **Усталость** (freeze.accumulator) — красный

Тултипы показывают технические имена (Серотонин/Норадреналин/Дофамин/Burnout)
и что именно измеряется.

## Что осталось открытым

- **Полное REM-переработка:** scouts с высоким |rpe| через Pump для замешивания
  эмоционально-насыщенных эпизодов (перенесено в Автономность)
- **Intrinsic pull в DMN:** `target = argmax(dopamine · novelty)` — сейчас
  случайный выбор пар в Scout
- **Circadian baseline drift:** нейромедиаторы могли бы иметь циркадный
  ритм (утро = выше dopamine, вечер = выше serotonin)

## Параметры

Жёстко прошиты в классе; при необходимости — переносятся в `settings.json`.

```python
Neurochem EMA decay:
  dopamine:       0.9    # быстрая реакция на новизну
  serotonin:      0.95   # медленная (стабильность копится)
  norepinephrine: 0.9    # быстрая (реакция на неопределённость)

ProtectiveFreeze:
  TAU_STABLE      = 0.6   # порог d за которым начинается conflict
  THETA_ACTIVE    = 0.15  # вход во freeze
  THETA_RECOVERY  = 0.08  # выход (гистерезис)
  DECAY           = 0.95  # ≈ 20 тиков до steady state

γ formula: γ = 2.0 + 3.0 · NE · (1 − S)
  min ≈ 2.0 (S=1, NE=0) — спокойный уверенный режим
  max ≈ 5.0 (S=0, NE=1) — возбуждённый ищущий
```

---

*Старая версия этого документа описывала 5 скаляров + γ как отдельное поле.
Упростили до 3 скаляров + derived γ в духе NeuroBrain-эскиза. Legacy имена
(`S`/`NE`/`DA_tonic`/`burnout_idx`) полностью удалены — single-path.*
