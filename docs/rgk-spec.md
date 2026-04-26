# РГК — Резонансно-Генерирующий Контур: спецификация физической модели Baddle

> Зафиксировано 2026-04-25 после Phase A+B+C (consolidation phase done) — поверх 6 правил simplification-plan'а появилась **единая физическая модель**. РГК = унифицированный язык от химии до UI: волновая физика + 5-axis нейрохимия + порог с гистерезисом + R/C режимы.
>
> Документ — **стратегический** для следующей фазы рефакторинга. Цель: коллапсировать ~10000 строк Baddle в выражение одной модели (~2200 строк). Phase A/B/C — это слои проекций; РГК — субстрат под ними.

---

## 1. Контекст

После Phase A+B+C проект собрал 6 правил (Signal, EMA, граф, distinct, PE, резонатор), но это всё ещё **6 отдельных абстракций**, не один субстрат. Из-за этого:

- 30+ полей состояния в UserState/Neurochem/ProtectiveFreeze/CognitiveState
- 13 детекторов, каждый со своим compute_urgency
- Sync_regime (FLOW/REST/PROTECT/CONFESS) и capacity_zone (green/yellow/red) — **производные**, но реализованы как параллельные слои
- 7 fallback-блоков (HRV missing, LLM offline, etc.) — symptom of модели не покрывающей все случаи

**Гипотеза автора (2026-04-25):** Baddle описывается **одной физической моделью** — РГК. Все остальное — проекции одного состояния. Если модель полная — коллапс к ~200 строкам ядра возможен.

---

## 2. Аксиомы РГК

1. **Поле едино.** Информация и энергия распространяются как волны в одном поле.
2. **По умолчанию резонанс.** Система стремится к резонансу с доминирующей частотой поля.
3. **Критический порог** возмущения превращает резонанс в деструктивный.
4. **Контрволна как восстановление.** Стабильность через генерацию волны со сдвигом фазы 180°.
5. **Гистерезис обязателен.** Переход R↔C требует разных порогов (включения / выключения), иначе паразитные колебания.

Доп.: 6 правил simplification-plan'а — **проекции** этих аксиом:
- Signal/Detectors = детектор амплитуды + порог + проекция C-режима в каналы (text/tone/silence)
- EMA Registry = ткань резонатора с инерцией
- Граф/distinct = носитель состояний и их близости
- PE = условие переключения R/C
- Резонатор Rule = это и есть РГК
- Контрволна Rule (не давить, инвертировать) = аксиома 4

---

## 3. Математическая модель

### 3.1. Базовое уравнение волны

`S(t) = A · sin(ω·t + φ)`

### 3.2. Условие резонанса (Mode R)

`|φ_sys − φ_in| < θ_res` — система следует за входом с задержкой τ.
Выход: `out(t) = S(t)` (буфер/усиление).

### 3.3. Условие контрволны (Mode C)

При `|S(t) − Baseline| > Thr_high`:
`C(t) = −k · S(t − τ_gen)`, k ≈ 1.

Результат: `R(t) = S(t) + C(t) = S(t) · (1 − k)`. При k → 1 → полное гашение.

### 3.4. Гистерезис

```
Thr_high(t) = Thr_base + α · ∫|S(t) − Baseline|·dt
Thr_low(t)  = Thr_high(t) − ΔHyst
Mode C активен пока S(t) > Thr_high
Возврат в R когда S(t) < Thr_low
```

### 3.5. Балансовая формула (новое — закрывает модель)

```
Баланс ≈ (Gain × Aperture × Plasticity) / (Hysteresis × Damping) ≈ 1.0
```

- **> 1.5** → гиперрезонанс (срыв в шум, мания, аддикция)
- **< 0.5** → гипостабильность (застревание, апатия, изоляция)
- **≈ 1.0** → оптимальный резонанс

Это **observable** (один скаляр), которого сейчас в Baddle нет. Является diagnostic для долгосрочного здоровья системы.

---

## 4. Нейрохимия как параметры резонатора

**Ключевой инсайт:** нейромедиаторы — **не сигналы**, а параметры настройки ткани. Сигнал = волна (Glutamate/GABA как носители excitation/inhibition).

| Вещество | Параметр РГК | Что делает | Поведенческий эквивалент |
|---|---|---|---|
| **DA** | Gain + Plasticity_rate | Усиливает амплитуду успешных мод. Снижает порог novelty/reward. | Мотивация, обучение, «хочу ещё» |
| **5-HT** | Hysteresis_width + Damping | Расширяет гистерезис. Гасит шум. Стабилизирует несущую. | Терпение, импульс-контроль |
| **NE** | Aperture_narrowing + SNR_boost | Сужает конус. Повышает Q-фактор. Подавляет фон. | Тревога/фокус, туннельное внимание |
| **ACh** | Phase_shift_ease + Learning_mode | Снижает стоимость перестройки ткани. Захват новых частот. | Внимание, нейропластичность |
| **GABA** | Local_inhibition + Waveform_sharpness | Стенки стоячей волны. Гасит боковые лепестки. | Торможение, чёткие границы |
| **Glutamate** | Base_amplitude + Propagation | Сырая энергия колебаний. | Возбуждение, передача сигнала |

**Пары-регуляторы → режимы РГК:**

| Пара | Физика | Режим |
|---|---|---|
| DA↑/5-HT↓ | Высокий gain + узкий hyst | 🔴 Гиперрезонанс (мания) |
| 5-HT↑/DA↓ | Широкий hyst + низкий gain | 🔵 Стабильный резонанс (рутина) |
| NE↑/ACh↓ | Узкая aperture + жёсткая ткань | 🟠 Тактический захват (стресс) |
| ACh↑/NE↓ | Текучая ткань + широкая aperture | 🟡 Режим обучения (творчество) |
| GABA↑/Glu↓ | Жёсткие стенки + низкая энергия | ⚫ Изоляция (седация) |

---

## 5. Карта состояний РГК

| Состояние | Профиль | Физика | Поведение |
|---|---|---|---|
| 🔵 ПОТОК | DA↑, ACh↑, NE↗, 5-HT↗ | Gain↑, Plasticity↑ | Фокус + гибкость |
| 🟢 УСТОЙЧИВОСТЬ | 5-HT↑, GABA↑ | Hysteresis↑, Damping↑ | Спокойствие, терпение |
| 🟠 ФОКУС/ТРЕВОГА | NE↑↑, 5-HT↓, GABA↓ | Aperture↓↓, Q↑↑ | Туннельное, реактивное |
| 🟡 ИССЛЕДОВАНИЕ | ACh↑, DA↗, NE↓ | Plasticity↑↑, Damping↓ | Любопытство, аналогии |
| 🔴 ПЕРЕГРУЗ/ПАНИКА | NE↑↑↑, GABA↓↓ | Порог пробит, автоколебания | Хаос, скачки |
| ⚫ ЗАСТОЙ/АПАТИЯ | DA↓↓, Glu↓ | Gain↓↓, аттракторы цементированы | Вязкость, повторение |
| ⚪ ВЫГОРАНИЕ | DA↓, NE↑(хрон), ACh↓ | Ткань высохла, плотность=0 | Цинизм, автоматизм |
| ✨ ИНСАЙТ | ACh↑↑, DA(пик), Theta-Gamma | Внезапное снижение вязкости | Новый аттрактор |

---

## 6. Маппинг к Baddle: что есть, что нет

### Уже работает (~70% РГК)

| РГК | Baddle |
|---|---|
| Gain (DA) | `Neurochem.dopamine` EMA ✓ |
| Hysteresis (5-HT) | `serotonin` EMA + `ProtectiveFreeze.THETA_ACTIVE/RECOVERY` ✓ |
| Aperture (NE) | `norepinephrine` EMA + γ-формула ✓ (UI-aperture для depth engine — Tier 2) |
| Threshold + hysteresis | `ProtectiveFreeze.THETA_ACTIVE=0.15` / `THETA_RECOVERY=0.08` ✓ |
| Perturbation | `sync_error = ‖user − system‖` или `imbalance_pressure` ✓ |
| Mode switching | `sync_regime` FLOW/REST/PROTECT/CONFESS (близко но 4 режима, не R/C) |

### Расхождения

**1. sync_regime 4 режима vs R/C bit.**
Похоже что 4 режима — проекция R/C × (user_state, system_state) на 2D плоскость. РГК говорит «один битовый switch», sync_regime — производное. Упрощение возможно: `sync_regime = project_regime(R_or_C, user_level, system_level)`.

**2. Counter-wave размазана.**
РГК: один C-режим + проекция выбирает канал (text/tone/silence/regime). Сейчас 13 детекторов независимо решают «должен ли я вернуть юзера». Объединимо как `if user_mode == "C": emit_counter(channel_selector(state))`.

**3. 3-axis vs 5-axis.**
ACh + GABA отсутствуют. Без них балансовая формула не замыкается. Сейчас `γ = 2 + 3·NE·(1−S)` — частная пара. Полная `(DA·NE·ACh)/(5HT·GABA)` — другой уровень diagnostic.

### Что РГК даёт нового

- **`balance()` как один скаляр** — diagnostic «насколько в резонансе». Сейчас нет.
- **R/C как primary state**, остальное проекции. Сейчас — много state объектов с derived sync_regime/capacity_zone.
- **Симметрия химия → физика → режим → протокол** — один словарь от метрик до UI-кнопок.
- **Aperture как явный UI-параметр** (Tier 2 spec) — один slider вместо 3 несвязанных knob'ов.

---

## 7. Концепция коллапса

### Целевая архитектура

```python
# src/rgk.py  ~200 строк
class РГК:
    """One resonator-generator circuit. State + dynamics + projections."""

    # 2 связанных резонатора (mirror каскада)
    user_chem: dict = {gain, hyst, aperture, plasticity, damping}
    system_chem: dict = {...}
    user_amp: ndarray   # N-D wave state (3-5 axis)
    system_amp: ndarray
    user_mode: Literal["R", "C"]
    system_mode: Literal["R", "C"]

    def step(self, obs, dt):
        pe = distinct(obs, self.predict_user())
        self.user_amp += pe * gradient(self.user_chem) * dt
        self.user_amp -= damping(self.user_chem) * dt
        self.user_mode = toggle_with_hysteresis(pe, self.user_mode, threshold)
        # symmetric for system
        # coupling: sync_error = ||user_amp - system_amp||

    def balance(self) -> float:
        """≈ 1.0 = резонанс. Trend = долгосрочное здоровье."""
        c = self.user_chem
        return (c.gain * c.aperture * c.plasticity) / (c.hyst * c.damping)

    def project(self, domain: str):
        """Все 13 детекторов + capacity + regime + UI → projections."""
        if domain == "signal":  ...
        elif domain == "capacity_zone":  ...
        elif domain == "sync_regime":  ...
        elif domain == "ui":  ...
```

### Размер

Текущий код:
- `src/user_state.py`: 1300 строк
- `src/neurochem.py`: 400 строк
- `src/horizon.py`: 600 строк
- `src/signals.py` + `src/detectors.py`: 1200 строк
- `src/cognitive_loop.py`: 2500 строк

≈ **6150 строк state + dynamics**, всё это в РГК сворачивается в **~200 строк ядра + 5 проекторов по ~50 строк**.

После коллапса:
- РГК-ядро: ~200 строк
- Проекции (signal/capacity/regime/ui/heartbeat): ~250 строк
- IO (HRV/LLM/HTTP/persistence): ~500 строк (нельзя сжать)
- DMN/REM heavy work (pump_bridge, REM emotional): ~1000 строк (реальные алгоритмы)
- UI/CSS/JS: ~500 строк (DOM manipulation forced)

**Итого ~2450 строк vs ~10000 текущих — 4x reduction (теоретический target).**

> **Реализация:** Phase D сделан 2026-04-25, реалистичный target пересмотрен до 1.5-1.8× reduction (adapter overhead). Что реально в коде — [docs/neurochem-design.md § 5-axis](../docs/neurochem-design.md) и [docs/world-model.md § Mapping](../docs/world-model.md). Опциональный line-count cleanup — [TODO.md § 🧹 Cleanup](TODO.md).

---

## 8. Открытые вопросы / риски

### Q1. Что если модель неполная?

В docs описаны эпизодическая память, action_memory, рефлексия после night_cycle. РГК это не покрывает явно — это либо проекции state-trajectory (история), либо отдельные подсистемы. Перед коллапсом — прогон РГК через эталонный event-sequence Phase A identity. Если semantic-identity не получается — модель неполна.

### Q2. Heavy work (DMN/REM/LLM) куда?

РГК не описывает «как генерировать новые мысли» (pump между нодами, REM emotional clustering, LLM execute_deep). Это IO/computation, не state. Останется отдельно — РГК даёт **trigger** (когда запустить DMN), но не алгоритм самого DMN.

### Q3. 5-axis ACh + GABA — добавить сейчас или отложить?

simplification-plan §12 говорит «не добавлять пока γ-формула работает». Но РГК показывает что **без ACh+GABA балансовая формула не замыкается**. Решение: добавить как часть РГК-коллапса, не отдельно. Если диагностика покажет что 3-axis достаточно — упростим обратно.

### Q4. Backward-compat для UI?

Phase C уже сменила response shape (energy → capacity). Следующая смена (capacity → balance + mode) сломает UI ещё раз. Допустимо — single-user проект. Не пытаться compat-shim делать.

### Q5. Identity тесты vs новая модель?

Phase A identity (10 тестов) фиксирует bit-identical EMA values. РГК изменит формулы — bit-identity не сохранится. Identity → property-based: «при возмущении X mode меняется в C через N тиков». Меньше unit, больше physical invariants.

---

## 9. Реализация

Реализовано 2026-04-25: `src/rgk.py` (Resonator + 2 связанных + balance + проекторы); UserState / Neurochem / ProtectiveFreeze работают как facades поверх `_rgk`. 5-axis ACh+GABA с feeders v1 — ограничения описаны в [docs/neurochem-design.md § 5-axis](../docs/neurochem-design.md). Mapping наших полей к внешним психологическим словарям — [docs/world-model.md § Mapping](../docs/world-model.md).

История миграции — `memory/project_session_20260425_phase_d.md`.

---

## 10. Связанные docs

- **РГК v1.0** — исходная спецификация (диалог 2026-04-24, локально у автора). Этот документ — её адаптация под Baddle. Дополнительные слои из v1.0 (counter-wave generation, prompt-routing по химии, экстренные протоколы, 8-region map) → Tier 2 в [TODO.md](TODO.md).
- [docs/neurochem-design.md § 5-axis](../docs/neurochem-design.md) — реализация (Phase D done): feeders v1 для ACh/GABA с лимитами.
- [docs/world-model.md § Mapping](../docs/world-model.md) — таблица «внешние словари ↔ наши поля ↔ код».
- [TODO.md § 🧹 Cleanup](TODO.md) — опциональный line-count cleanup (Phase E-I).
- [simplification-plan.md](simplification-plan.md) §4 — 6 правил, которые проекции РГК аксиом
- [docs/resonance-model.md](../docs/resonance-model.md) — единый словарь
- [docs/friston-loop.md](../docs/friston-loop.md) — PE как условие переключения R/C
- [docs/cone-design.md](../docs/cone-design.md) — апертурный предел (NE-параметр)
- [docs/world-model.md](../docs/world-model.md) — каскад зеркал (двойной резонатор)
- [docs/neurochem-design.md](../docs/neurochem-design.md) — нейромедиаторы как параметры
- [resonance-code-changes.md](resonance-code-changes.md) — `aperture` (Tier 2, часть РГК)

---

## 11. Main takeaway

**До РГК:** Phase A+B+C это инкрементальная конвергенция от 21 cascade к 6 правилам. Каждая фаза снижала bespoke на 30-40%, но 6 правил остались отдельными абстракциями.

**С РГК:** 6 правил — это **проекции одной модели**. Если коллапсировать к ядру — проект не 10k строк, а ~2.5k. Новая фича = добавить axis или меняющий проектор, не подсистему.

**Цена:** одна сессия дизайна + одна-две сессии имплементации (новая ветка). Identity не bit-preserved, нужны property-based тесты. Возможно вскроется что модель неполна — тогда переоценка.

**Когда делать:** когда у автора будет контекст на 2-4 часа подряд. Не инкрементально (то что мы делали в Phase A/B/C). Один большой коллапс в ветке, mergeable когда identity-проверка пройдёт.

**Что сохраняется:** simplification-plan §11 main takeaway — «новая фича = декларация, не подсистема». РГК это финализирует: декларация = новый проектор или новый параметр в chem. Каркас один.
