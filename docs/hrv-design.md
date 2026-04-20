# HRV — тело как вход в мышление

## Идея

`CognitiveState` управляет «конусом мышления» через ошибку предсказания
(чисто информационный сигнал). Но тело — такой же источник параметров.
Вариабельность сердечного ритма (HRV) отражает баланс симпатика/
парасимпатики, когерентность дыхания и сердца, уровень восстановления.
Это физиологический сигнал, и он напрямую маппится на параметры конуса.

Два режима работы системы:

| Режим | Что управляет конусом |
|-------|----------------------|
| **Автономный** | Информационный контур: prediction error → precision → params |
| **Резонансный** | Физиологический контур: HRV → θ/φ → params |

Переключение плавное, не бинарное. Типичный путь: автономный в обычной
работе, резонансный при заторе — когда граф уперся в тупик и нужна смена
состояния, а не новая порция текста.

---

## Граница ответственности (важно)

HRV **не модулирует внутреннюю нейрохимию системы напрямую** (с
появлением Симбиоза). HRV — сигнал **тела пользователя**, он обновляет:
- `UserState.serotonin` — через coherence (связность ритмов)
- `UserState.norepinephrine` — через стресс
- `UserState.activity_magnitude` — через акселерометр (отдельный канал)

`SystemState` (нейрохимия системы) эволюционирует по собственной
динамике графа. Детали разделения → [symbiosis-design.md](symbiosis-design.md).

Движение комбинируется с когерентностью и даёт 4-зонную классификацию
(`UserState.activity_zone`) — recovery / stress_rest / healthy_load /
overload. Детали → [user-model-design.md](user-model-design.md) секция 3.

---

## Источники данных — разные устройства, разная семантика

Текущий `hrv_manager.py` построен под **Polar-like модель**: непрерывный
поток RR-интервалов плюс акселерометр. Это не универсально. Реальность:

| Источник | RR | Частота | Accel | HR | Sleep |
|----------|-----|---------|-------|-----|-------|
| **Симулятор** (сейчас) | эмулируется каждый beat | ~1/s | скаляр-слайдер | derived | нет |
| **Polar H10** | каждый beat | ~1/s | 50 Гц X/Y/Z | derived | нет |
| **Apple Watch** | редкие samples | раз в минуты | отдельный поток 50 Гц | 5с-stream | да (HealthKit) |
| **Oura / Garmin** | сырой RR часто недоступен | — | разные | continuous | да (детально) |
| **Phone IMU** | — | — | 50–100 Гц | — | — |

**Следствие:** один `hrv_manager` с `rr_buffer + activity_magnitude`
хорошо моделирует Polar и симулятор. Для Apple Watch / Oura нужен
отдельный адаптер, который конвертирует редкие RR + непрерывный HR в те
же сигналы что ожидает `hrv_to_baddle_state`:

- `coherence` из разреженных RR → альтернативная формула (HR variability
  из HR-timeseries, не RR-to-RR)
- `activity_magnitude` — из Watch motion & workouts API
- `sleep quality` (новое) → кормит `UserState.long_reserve` overnight recovery

Это отдельная задача в TODO (экосистема / интеграции, deferred). Она не
ломает текущую архитектуру — добавляется как ещё один режим
`hrv_manager.start(mode="apple_watch")`.

---

## Push vs Pull синхронизация

До внедрения зонной классификации `UserState.hrv_*` обновлялся **только**
при явном вызове `GET /hrv/metrics` (pull). Если UI не поллит — UserState
устаревает, zone-alerts и sync_regime считаются на старой когерентности.

**Сейчас:** `CognitiveLoop._check_hrv_push()` каждые 15 секунд пушит свежий
baddle_state из hrv_manager в UserState. Это гарантирует что
`activity_zone`, `named_state`, `sync_regime` согласованы с реальным
телом ±15 секунд, независимо от активности UI.

---

## Параметры конуса

Конус мышления — метафора с тремя параметрами плюс одна метрика качества.

| Параметр | Физический аналог | Эффект |
|----------|------------------|--------|
| **θ (ширина/угол раствора)** | HRV-когерентность, альфа-тета активность | Широкий → креативность, неопределённость. Узкий → фокус, уверенность |
| **φ (ориентация оси)** | Вектор внимания, смена контекста | Поворот → доступ к другому подпространству без изменения глубины |
| **ω (высота)** | Горизонт предсказания | Удлинение → стратегическое мышление. Короткий → реакция на «здесь и сейчас» |
| **Качество поверхности** | Когерентность ритмов | Гладкое → стабильная траектория. Шероховатое → когнитивное трение |

Адаптация идёт так:
- θ подстраивается к уверенности: растёт когда хочется исследовать,
  сужается когда надо решать. (`θ_{t+1} = θ_t − α·∇_θ(уверенность) + β·ε(шум)`)
- φ поворачивается на новизну и контекст.
  (`φ_{t+1} = φ_t + γ·∇_φ(новизна) + δ·∇_φ(контекст)`)
- Байесовский постериор накатывается сверху: `Posterior = BayesUpdate(Prior(θ, φ), Data)`

---

## Маппинг HRV → θ

Физиологическая калибровка: когерентность задаёт базовый θ, RMSSD
(парасимпатический тонус) при низких значениях принудительно сужает.

| Coherence | θ базовый | Смысл |
|-----------|-----------|-------|
| > 0.7 | 0.8 (широкий) | Система стабильна, можно исследовать |
| 0.4 – 0.7 | 0.5 (средний) | Нормальная работа |
| < 0.4 | 0.2 (узкий) | Нестабильно, нужен фокус |

Если RMSSD < 20 мс — умножаем θ на 0.7 (принудительное сужение, тело
просит экономии). Финальный θ ограничиваем в [0.1, 0.9].

---

## Маппинг HRV → φ (разрешение на поворот)

φ — ориентация, резкий поворот = смена контекста. Решение «можно
поворачивать или нет» принимаем по балансу симпатика/парасимпатика и
тренду пульса.

| Условие | Разрешён поворот? | Ограничение |
|---------|-------------------|-------------|
| LF/HF > 3.0 (симпатика доминирует) | нет | — |
| HR растёт > 5 bpm / окно | нет | — |
| θ > 0.6 и 0.5 < LF/HF < 2.0 | да | Δφ ≤ 0.3 (крупные повороты) |
| Остальное | да | Δφ ≤ 0.1 (мелкие повороты) |

Тело «на взводе» (симпатика или резкий пульс) — ничего не крутим,
углубляем текущее. Тело спокойное и θ достаточно широк — разрешаем
большие повороты для выхода в новую область.

---

## Пять состояний (расширение четырёх из Horizon)

| Состояние | θ | φ | Байесовский маркер | Поведение |
|-----------|---|---|-------------------|-----------|
| **STABILIZE** | сужается→0 | фиксируется | Q-factor ↑, энтропия ↓ | Калибровка, сброс шума |
| **FOCUSED** | узкий | стабилен | P(H) > 0.8, низкая энтропия | Готовность к действию, блокировка ветвления |
| **EXPLORE** | широкий | свободен | 0.4 < P < 0.8, рост дисперсии | Активный скан, пометка мостов |
| **SHIFT** | средний | Δφ > порога | Смена контекста, пересчёт приоров | Перестройка связей |
| **CONFLICT** | любой | любой | Несовместимые приоры | Остановка, запрос разрешения |

### Переходы с гистерезисом

Переход срабатывает не при первом пересечении порога, а по взвешенному
счёту за окно в N тиков — чтобы не было дребезга:

```
FOCUSED → EXPLORE:  confidence < 0.7
EXPLORE → FOCUSED:  confidence > 0.85   (разрыв 0.15 — гистерезис)

score = w₁·Δconfidence + w₂·entropy_rate + w₃·user_input + w₄·timer_drift
Переход: score > threshold И удерживается N тиков
```

### HRV-условия переходов

| Состояние | Вход | Выход |
|-----------|------|-------|
| STABILIZE | coherence < 0.3 ИЛИ rmssd < 15 | coherence > 0.5 в течение 60 сек |
| FOCUSED | 0.4 < coherence < 0.7 И lf_hf < 2.5 | coherence > 0.7 ИЛИ coherence < 0.3 |
| EXPLORE | coherence > 0.7 И rmssd > 30 | coherence < 0.5 ИЛИ lf_hf > 3.0 |
| SHIFT | Ручной ИЛИ RSA amplitude ↑ 50% / 30с | phase_lock стабилен 90 сек |
| CONFLICT | coherence ↓ 40% / 30с ИЛИ HR ↑ 15 bpm | coherence > 0.4 И HR стабилен 60 сек |

---

## Метрики с Polar H10

Что считаем из RR-потока и на каких окнах:

| Метрика | Формула | Окно | Зачем |
|---------|---------|------|-------|
| HR | `60000 / mean(RR)` | 10 с | Общий уровень активации |
| RMSSD | `sqrt(mean((RR[i+1] − RR[i])²))` | 60 с | Парасимпатический тонус |
| SDNN | `std(RR)` | 60 с | Общая вариабельность |
| HRV-coherence | peak / total power в окне 0.04–0.26 Гц | 60 с | **Ключевая**: синхронизация дыхания↔сердца |
| LF/HF | `power(0.04–0.15) / power(0.15–0.4)` | 120 с | Баланс симпатика/парасимпатика |
| RSA | Амплитуда модуляции HR на частоте дыхания | 60 с | Качество связи дыхание↔сердце |

---

## Резонансные метки

Дополнительные индикаторы помимо байесовских весов — нужны чтобы
отличать «творческий резонанс» от «когнитивного расфокуса», а не только
«уверен или нет».

- **Q-factor** — отношение сигнал/шум в текущем срезе графа. Высокий
  → узкий стабильный пик, низкий → широкий поиск
- **Phase lock** — синхронизация тезисов. Высокая → готовность
  к коммиту, система собрала мнение
- **Harmonic bleed** — расширение θ захватывает смежные домены.
  Система помечает их как потенциальные мосты, а не шум

---

## Тело как многоуровневый резонатор

Разные системы тела осциллируют на разных частотах. Когда они входят
в фазовую синхронизацию, тело работает как высокодобротный осциллятор —
чувствительность к тонким паттернам растёт.

| Уровень | Частота | Влияние на конус |
|---------|---------|------------------|
| Нейроны (бета/гамма) | 14–100 Гц | Сужение θ, фиксация φ — аналитический режим |
| Сердце (HRV) | ~1 Гц (модуляция 0.1–0.4 Гц) | Стабилизация θ, плавность поворотов φ |
| Дыхание | 0.2–0.3 Гц | Расширение θ, замедление внутри конуса |
| Спинномозговая жидкость | 0.05–0.1 Гц | Глобальный поворот φ, доступ к «тихим» веткам |

---

## Медитация как ручная настройка конуса

Разные медитативные практики работают с разными параметрами конуса.
Это не метафора — это прямое управление θ/φ через дыхание и внимание:

| Практика | Механизм | Эффект на конус |
|----------|----------|-----------------|
| Наблюдение дыхания | Синхронизация дыхание→HRV | Плавное расширение θ |
| Сканирование тела | Рост когерентности микровибраций | Обнаружение искажений φ |
| Открытый мониторинг | Снижение Q, расширение полосы | Максимум θ, креативность |
| Фокус на объекте | Повышение Q, одна частота | Минимум θ, максимум ω |
| Пауза между мыслями | Условия для стоячей волны | Сброс φ, перепозиционирование |

---

## Архитектура интеграции

```
┌──────────────┐      ┌─────────────────┐      ┌──────────────┐
│  Polar H10   │─────▶│  Baddle Bridge  │─────▶│  UserState   │
│  (BLE)       │ RR   │  (HRV parser)   │ D/S/ │  user.D/S/NE │
└──────────────┘      └─────────────────┘  NE  └──────────────┘
                             │
                             ▼
                      ┌─────────────────┐
                      │  Metrics Store  │
                      │  (time-series)  │
                      └─────────────────┘
```

---

## Два режима работы — автономный vs резонансный

В автономном режиме Baddle проходит граф, считает апдейты, мерджит.
Быстро, воспроизводимо. В резонансном — когда граф зашёл в тупик
(конфликт долго не разрешается) или нужны инсайты — тело меняет θ/φ,
система переключается со «решать» на «сканировать».

### Переключение

```yaml
mode:
  type: "autonomous" | "resonant" | "hybrid"
  transition_guards:
    auto_to_resonant:
      - hrv_coherence < 0.35
      - conflict_duration > 120s
    resonant_to_auto:
      - hrv_coherence > 0.75
      - phase_lock_stable > 90s
```

### Что меняется в резонансном

| Автономный | Резонансный |
|------------|-------------|
| `argmax(confidence)` | `sample(distribution, temperature=θ)` |
| `shortest_path` | `resonant_walk` (гармонические связи) |
| `hard_merge` | `standing_wave_buffer` — удержание противоречий до синхронизации |

---

## Калибровка

10-минутная сессия для определения персональных порогов:
1. Спокойное дыхание → baseline coherence, RMSSD, LF/HF
2. Пороги: `mean ± 0.5·std`
3. Сохраняется в конфиг, обновляется со временем

---

## Шаги реализации

1. **MVP.** Polar H10 → RR-интервалы → консоль через `bleak` BLE.
2. **Alpha.** Расчёт θ из HRV, ручное переключение состояний.
3. **Beta.** Автоматические переходы + UI-визуализация конуса.
4. **Production.** Адаптивная калибровка + интеграция с tick cycle.

---

## Sensor stream (multi-source polymorphism)

Изначально `HRVManager` поддерживал один источник одновременно (simulator
или polar). Другие устройства (Apple Watch, Oura, manual check-in) не было
куда подключить, данные не персистились. В 2026-04-20 добавлен
**полиморфный sensor stream** — единый поток замеров от любого источника.

### Архитектура

```
                 ┌────────────────────────────────────────────────┐
                 │  SensorStream (singleton, thread-safe)         │
                 │  rolling window + append-only jsonl            │
                 └───────────────▲──────────────────▲─────────────┘
                                 │                  │
  ┌──────────────────────────────┘                  └────────────────┐
  │                                                                  │
  ▼                                                                  ▼
push_rr(source, rr)                          push_subjective(energy, focus, …)
push_hrv_snapshot(source, rmssd, coherence)   push_activity(source, magnitude)
  │                                                                  ▲
  │                                                                  │
  ▼                                                                  │
┌─────────────────┐ ┌──────────────┐ ┌──────────────┐  ┌──────────────┐
│ HRVManager      │ │ PolarH10     │ │ AppleWatch   │  │ Checkin form │
│ (simulator)     │ │ (stub)       │ │ (stub)       │  │ (working)    │
└─────────────────┘ └──────────────┘ └──────────────┘  └──────────────┘
```

### `SensorReading`

Один замер одного датчика в один момент ([src/sensor_stream.py](../src/sensor_stream.py)):

```python
@dataclass
class SensorReading:
    ts: float                    # unix timestamp
    source: str                  # polar_h10 | apple_watch | oura | garmin | manual | simulator
    kind: str                    # rr | hrv_snapshot | activity | subjective
    metrics: dict[str, float]    # зависит от kind (см. ниже)
    confidence: float = 1.0      # 0..1, насколько надёжна выборка
```

**Kinds и ожидаемые metrics:**

| kind | Поля в `metrics` | Пример источника |
|------|------------------|-----------------|
| `rr` | `rr_ms` | Polar H10 (каждый beat), simulator |
| `hrv_snapshot` | `rmssd`, `coherence`, `heart_rate`, `lf_hf_ratio`, `stress` | Polar (каждые 15с), Apple Watch, Oura утром |
| `activity` | `magnitude` (0…5: 0=покой, 1=ходьба, 2+=бег) | Акселерометр Polar, слайдер симулятора |
| `subjective` | `energy`, `focus`, `stress`, `surprise`, `valence` (всё в [0,1] или [-1,1]) | Manual checkin |

**Confidence guidelines:**
- `polar_h10`, `simulator` hrv_snapshot → 1.0 (sensor high-freq)
- `apple_watch` sparse HR → 0.8 (агрегат из редких samples)
- `oura` morning snapshot → 0.9 (снимок свежий, но один на сутки)
- `manual` checkin → 0.7 (самоотчёт)

### `SensorStream`

**Persist:** `data/sensor_readings.jsonl` append-only. RR с high-freq
источников downsample'ятся (каждый 10-й пишется на диск — иначе файл
раздуется до гигабайтов за день).

**In-memory:** rolling window последних 2000 readings для быстрого query
без чтения файла.

**Query API:**

```python
stream.recent(kinds=['hrv_snapshot'], sources=['polar_h10'], since_seconds=300)
# → list[SensorReading]

stream.latest_hrv_aggregate(window_s=180)
# → {rmssd, coherence, heart_rate, stress, _sources, _sample_count, _window_s}
# weighted avg: weight = confidence × exp(-age/τ), τ = window_s/2

stream.recent_activity(window_s=60)  # latest magnitude
stream.active_sources(stale_after_s=60)  # кто пушил недавно
```

Weighted-average **ключевое для multi-source**: когда одновременно есть
Polar (high-freq, high-confidence) и Apple Watch (sparse, low-confidence)
+ последний manual check-in 10 минут назад — агрегат отдаёт взвешенную
смесь, приоритет у свежего и надёжного.

### Адаптеры

[src/sensor_adapters.py](../src/sensor_adapters.py) — skeleton-классы:

| Адаптер | Статус | Что сделать |
|---------|--------|-------------|
| `simulator` (в `hrv_manager.py`) | ✅ работает | — |
| `manual` (через `/checkin`) | ✅ работает | — |
| `PolarH10Adapter` | ⏳ stub | `pip install bleak bleakheart`, async BLE loop, MAC address UUID |
| `AppleWatchAdapter` | ⏳ stub | HealthKit XML export parser, или iOS shortcut → локальный HTTP |
| `OuraAdapter` | ⏳ stub | Oura REST v2 API, personal access token, polling daily-readiness |
| `GarminAdapter` | ⏳ stub | `garminconnect` pip, HR-stream poll |

Все адаптеры push'ат в тот же `SensorStream` — consumer (UserState)
не знает про источник, читает агрегат.

### Endpoints

- `GET /sensor/readings?kind=&source=&since=` — последние readings в окне
- `GET /sensor/aggregate?window=180` — weighted HRV-aggregate + latest activity

См. [src/assistant.py](../src/assistant.py) раздел «Sensor stream».

### Что осталось

Мигация сделана минимально — **новая инфраструктура работает параллельно
со старой**, чтобы ничего не сломать. Полный переход требует ещё:

1. **`UserState.update_from_hrv` → читает из stream.** Сейчас его
   всё ещё дёргает `CognitiveLoop._check_hrv_push` через `hrv_manager.
   get_baddle_state()`. Надо переписать на `stream.latest_hrv_aggregate()`
   и `stream.recent_activity()`. ~15 call-sites в cognitive_loop/assistant.
   Без этого stream отдаёт данные, но `UserState.serotonin/ne` всё равно
   питается через manager. Не срочно пока есть один real источник.

2. **Реальный `PolarH10Adapter`.** Нужен физический девайс + bleak-зависимости.
   Логика: `bleak.BleakClient` → `bleakheart.HeartRate.listen()` →
   `push_rr(SOURCE_POLAR, ms)` на каждый beat.
   В параллель — `bleakheart.Accelerometer.listen()` → `push_activity`.
   Каждые 15с — агрегат RMSSD/coherence через `calculate_hrv_metrics` →
   `push_hrv_snapshot`. Это 100-150 строк, но требует тестового устройства.

3. **Apple Watch import.** HealthKit export XML → пробегаемся по
   `<Record type="HKQuantityTypeIdentifierHeartRate">` → batch push
   `hrv_snapshot`. Это one-shot импорт истории, не continuous stream.
   Для continuous — нужен iOS shortcut или HKObserver на своём Mac.

4. **Oura / Garmin adapter'ы.** REST polling раз в N минут (energy budget
   запрос должен учитывать rate limit'ы). У Oura есть webhook V2 — можно
   подписаться на события.

5. **Calibration store в stream.** Сейчас `HRVManager._baseline` хранится
   в manager'е. Правильнее — `data/sensor_baselines.json` per-source:
   `{polar_h10: {rmssd_mean, rmssd_std, coherence_mean, ...}}`. Normalization
   HRV-метрик должна делаться относительно baseline каждого источника,
   потому что абсолютные значения различаются (chest-strap vs optical).

6. **Conflict resolution.** Когда Polar и Oura одновременно активны и
   дают разные stress — weighted aggregate разрулит, но **при расхождении
   > threshold** стоит log'ировать. Это диагностика датчиков (Polar мог
   отвалиться, Oura устарел).

### Как добавить свой источник

1. Если он push'ит в real-time (Polar-like) — используй паттерн
   `PolarH10Adapter`: async thread + `push_rr` / `push_hrv_snapshot`.
2. Если source sparse (Apple, Oura) — просто push'и `hrv_snapshot` когда
   доступен новый sample, с правильным `confidence`.
3. Если source manual (форма, voice, другое приложение) — push через
   `push_subjective` с `source='manual_XXX'`.
4. Не забудь добавить source-константу в `sensor_stream.py`.
5. Если нужны специфические метрики (Garmin body battery, Oura readiness)
   — добавь новый `kind` и расширь `latest_hrv_aggregate` если хочешь
   включить в weighted avg.

---

**Навигация:** [← User Model](user-model-design.md)  ·  [Индекс](README.md)  ·  [Следующее: State graph →](state-graph-design.md)
