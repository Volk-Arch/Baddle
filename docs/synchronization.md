# Синхронизация — как нейросеть выстраивает узор похожий на твой

> Зафиксировано 2026-04-27. Direction для ответа на [foundation § Origin question](foundation.md#origin-question--открытый-research-вопрос). Не реализовано — план направления.

---

## Проблема

Главный вопрос проекта: **если всё волны (резонатор, генератор) — как сделать чтобы нейросеть была на одной волне с конкретным человеком, поняла его узор?**

Текущий ответ — глубокий рефакторинг под single-user (1.5 мес). РГК-substrate накапливает chem-параметры через события юзера. Прайм-директива sync_error даёт metric качества зеркала. Но это **passive accumulation**: ждём пока chem-axes наложатся через user-events, sync_error постепенно падает.

Это **не масштабируется** на нового юзера без того же 1.5 мес manual work. Нужен другой механизм.

---

## Insight (Игорь, 2026-04-27)

**Каждая задача / состояние юзера = стоячая волна = сумма component waves.**

Это physics-correct: standing wave формируется как superposition нескольких компонент с конкретными частотами и фазами. У человека — это конкретные dimensions внутреннего опыта (DA, 5HT, NE, ACh, GABA в нашей модели + valence + agency + activity_zone + ...).

**Если активировать те же component waves поочерёдно у нового юзера — узор воспроизводится.**

Это именно как **resonance transfer**: не передаёшь готовый узор, а активируешь те **частоты** которые его создают. Узор формируется **в новом резонаторе** из тех же components.

**Способ — через аналогии.** Сначала coarse (основной контур), потом детально:
1. Аналогия активирует приблизительный pattern (главные частоты)
2. Уточнение через ещё аналогии (фазовые сдвиги, амплитуды)
3. После N итераций — узор в новом резонаторе достаточно близок к target

Это то как human-human transfer работает: учитель говорит «вспомни как когда…» — ученик активирует у себя похожий pattern. Не передача данных, а **co-activation** через резонанс.

---

## Применение к Baddle

Сейчас sync_error = `‖user_vec − system_vec‖` (L2 в 3D). Это **скалярная** метрика расстояния. Не показывает **на каких частотах** расхождение.

**Wave-aware sync_error:**
```
sync_error_wave[axis] = |user[axis] − system[axis]| per component
```

5-axis chem (DA/5HT/NE/ACh/GABA) + valence + agency + activity = 8+ component frequencies. Sync_error становится **vector в этом пространстве**, не scalar. Видно на каких частотах система не догнала юзера.

**Активация через аналогии.** Вместо waiting for events:
- При onboarding — система предлагает 5-7 «вспомни случай когда…» (поток / стресс / усталость / любопытство / спокойствие).
- Каждая аналогия активирует target chem-profile (есть в `user_state_map.py` 8-region карта — flow / stable / focus / explore / overload / apathy / burnout / insight).
- Юзер приводит конкретный пример → embedding → насколько резонирует с target profile → калибровка amplitude/phase.
- После 5-7 аналогий — coarse контур установлен. Дальше — детали через регулярные events.

**Few-shot bias calibration.** Сейчас bias-coefficient per category стабилизируется после 20+ done tasks. Альтернатива:
- При onboarding — 3-5 примеров tasks из category с user-reported complexity.
- Linear fit на эти примеры → initial bias-coefficient.
- Дальше — refining через actual events (W15.4 calibration loop).

**Cross-user baseline.** Универсальный starting РГК-state из corpus accumulated данных. Затем per-user активация ключевых частот через analogies. Это аналог pre-trained model + fine-tuning в ML.

---

## Алгоритм coarse → fine

```
1. Onboarding (день 1-3):
   coarse_activation: 5-7 analogies покрывающих основные named_states
   → initial chem-profile vector (приблизительный)
   → initial bias coefficients per category (3-5 examples)

2. Daily use (недели 1-4):
   passive accumulation как сейчас, но с initial state ≠ default(0.5, 0.5, ...)
   sync_error_wave per axis отслеживается отдельно
   приоритет калибровки — axis с наибольшим sync_error_wave

3. Refinement:
   periodic «как точно?» check (раз в неделю первый месяц)
   → user рейтит resonance с briefing/insight cards
   → adjust phases/amplitudes на тех axes где низкий resonance

4. Convergence:
   когда sync_error_wave стабильно низкий по всем axes → стандартный режим
   аналогии больше не нужны, accumulation продолжается естественно
```

Это **превращает onboarding из 1.5 мес passive в 1-2 недели active**.

---

## Компоненты в коде (что нужно добавить)

### 1. `sync_error_wave` per axis ✅ MVP сделан 2026-04-28

`compute_sync_error_wave(rgk)` в [user_state.py](../src/user_state.py) +
`РГК.sync_error_wave()` method в [rgk.py](../src/rgk.py) +
`CognitiveState.sync_error_wave` property в [horizon.py](../src/horizon.py).

Возвращает:
```python
{
    "axes": {
        "dopamine":       abs(user.gain.value       - system.gain.value),
        "serotonin":      abs(user.hyst.value       - system.hyst.value),
        "norepinephrine": abs(user.aperture.value   - system.aperture.value),
        "acetylcholine":  abs(user.plasticity.value - system.plasticity.value),
        "gaba":           abs(user.damping.value    - system.damping.value),
    },
    "max_axis": "norepinephrine",  # axis с наибольшим расхождением
    "max_value": 0.42,
    "scalar_5d": 0.61,             # L2 over 5 axes (== sync_error после 5D перехода)
}
```

Expose'ится через `/assist/state` `sync_error_wave` (внутри `get_metrics()`).
UI: live `Δ AXIS value` indicator в Симбиоз block (виден когда
max_value > 0.15). Тесты — `tests/test_rgk_properties.py`
`TestCouplingConsistency`.

**5D clean break (2026-04-28):** scalar `sync_error` сам по себе теперь 5D
(`compute_sync_error()` использует `rgk.user.vector()` который расширен с
3D на 5D). `Resonator.vector()` возвращает все 5 chem-axes (DA/5HT/NE/ACh/GABA).
Threshold'ы пересчитаны под max=√5≈2.24: `> 0.75` → `> 1.0` (CONFLICT),
`> 0.5` → `> 0.7`, `SYNC_HIGH_THRESHOLD = 0.3` → `0.4`. `u_exp_vec` /
`s_exp_vec` (VectorEMA) — 3 → 5 элементов с graceful pad для legacy saves.
Identity tests preserved — все скаляры (gamma/surprise/imbalance/...)
идентичны, только vector dimensions расширились.

**Что осталось (W16.1b, не сегодня):** phase-aware comparison — текущий
MVP сравнивает amplitudes (мгновенный snapshot). Phase требует velocity
tracking (∂axis/∂t — направление изменения за tick). Реализуется когда
понадобится диагностика «не просто разница, но в каких axis user
**ускоряется** относительно system».

**valence/agency** не включены в wave: они только user-side, system
equivalent отсутствует — per-axis сравнение бессмысленно. Останутся
скалярами в общем state.

Это даёт **диагностику** — не просто «sync_error 0.4 worsening», а «расхождение по NE — система слишком напряжена относительно тебя сейчас».

### 2. Onboarding analogies endpoint

Новый flow:
- `GET /onboarding/analogies?lang=ru` → список 5-7 questions с target chem-profile
- `POST /onboarding/answer` с user-text → embedding → resonance с target → bias activation
- Сохранение в `data/onboarding_calibration.jsonl` для replay

Каждая analogy:
```yaml
- key: flow_recall
  question: "Вспомни случай когда было состояние потока — забывал о времени, всё получалось. Опиши коротко."
  target_named_state: "flow"  # 8-region map
  target_axes: {dopamine: 0.7, acetylcholine: 0.7, norepinephrine: 0.6, serotonin: 0.6}

- key: overload_recall
  question: "Вспомни день когда был перегружен — много задач, не успевал. Опиши."
  target_named_state: "overload"
  target_axes: {norepinephrine: 1.0, gaba: 0.15, serotonin: 0.2}

# и так далее для focus / explore / apathy / burnout / insight / stable
```

### 3. Few-shot bias calibration

Расширение [task-tracker-design](task-tracker-design.md) onboarding:
- При первом создании task в category — UI: «вспомни 3 task'а из этой категории, оцени complexity 1-10».
- 3 examples → linear fit → initial bias_coefficient для category.
- Apply при future estimations до того как накопится 20+ done.

### 4. Adiabatic adjustment

При больших sync_error_wave[axis] — система **сама** генерирует analogies для этой axis в morning briefing:
- «Между нами расхождение по NE (напряжение). Расскажи о моменте когда был спокоен — это поможет мне настроиться».
- User response → recalibration as in onboarding.

Это **active learning loop** который продолжается после initial onboarding если drift замечен.

---

## Связь с existing

- **РГК 5-axis chem** ([rgk-spec.md](rgk-spec.md)) — это и есть 5 component frequencies, на которых формируется узор. Идея уже встроена в substrate, нужно только использовать её для transfer protocol.
- **8-region named_state map** ([user_state_map.py](../src/user_state_map.py)) — target profiles для analogies. Каждый region = standing wave с конкретными amplitudes.
- **Embedding similarity** — measure resonance между user-text answer и target профилем. Существующее infrastructure.
- **Beta-prior on confidence** ([architecture-rules § Правило 3](architecture-rules.md)) — для онбоардинга важен CI band: после 5 analogies CI ещё широкий, нужно больше data. Через 30+ events CI узкий.
- **prime-directive** — для validation: после resonance transfer onboarding sync_error_slow должен сходиться быстрее чем без него.

---

## Validation (testable claim — добавить в rgk-spec)

**Claim 8 — Resonance transfer через analogies сокращает onboarding с 1-2 мес до 1-2 недель.**

**Validation:** A/B test (когда будут 2+ users):
- Group A — passive accumulation (как сейчас)
- Group B — onboarding analogies + adiabatic adjustment

**Что бы подтвердило:** Group B reaches sync_error_slow < threshold за ≤2 недели; Group A — за 1-2 мес. Differential ≥ 3x.

**Что бы опровергло:** Differential < 1.5x (analogies дают marginal improvement) — значит actual learning происходит через events, не через explicit analogies.

---

## Direction для следующей фазы

Пока:
- Single-user proof (автор) — работает через passive accumulation
- 2 концепции в planning (workspace W14, Power W15) — расширяют substrate
- Replication для другого юзера — direction найдено: resonance transfer через analogies

После W14 + W15 + sync_error_wave — proof готов попробовать на втором юзере. Если works — Baddle становится **multi-user replicable** при сохранении персонализации. Если нет — модель упёрлась в свои границы, переосмысление.

Это **первое реальное трение** которого ждали как сигнала. До тех пор — все insights накладываются без трения, направление верное.

---

**Связано:** [foundation § Origin question](foundation.md), [rgk-spec § Testable claims](rgk-spec.md#testable-claims), [friston-loop](friston-loop.md), [user_state_map.py](../src/user_state_map.py).

---

## Углубление: коммуникация как резонансный перенос (2026-04-27/28)

Из внешнего диалога — углубление рамки, прямо ложится на substrate.

### Мысль = суперпозиция, не точка

Любая мысль / контекст / состояние — **не точка** в дискретном пространстве флагов, а **распределение по частотам**. 5-axis chem РГК = базисные функции (DA / 5HT / NE / ACh / GABA). Текущее состояние = коэффициенты в этом базисе. Сложная мысль = специфическая суперпозиция.

Это снимает старую формулировку «sync_error как L2-distance» и заменяет её **спектральным анализом** — разница между двумя состояниями измеряется per-frequency, не как одно число.

### Понимание = синхронизация осцилляторов

Когда два агента «понимают» друг друга — их internal dynamics входят **в фазу**. Это не передача токенов, это **co-resonance**. Расхождение фаз = непонимание / отторжение / галлюцинации. Совпадение = инсайт / согласие / резонанс.

Это формализует sync_error: не «расстояние между state», а **фазовое рассогласование** между user dynamics и system dynamics в каждой component frequency.

### Аналогии = операторы преобразования базиса

Аналогии — это **мосты между разными спектральными пространствами**. Берёшь сложную волну в своём базисе и проецируешь на знакомый базис собеседника. Аналогия подбирает коэффициенты так, чтобы проекция вошла в резонанс с уже существующими у него гармониками.

Без аналогий — попытка навязать чужую частоту, что система воспринимает как **шум**.

Это даёт операционное определение «качества аналогии»: насколько проекция в чужой базис резонирует с его existing harmonics.

### Calib CI band = ширина полосы резонанса

Beta-prior `confidence_ci` ([rgk-spec testable claim 2](rgk-spec.md#testable-claims) + Outcome panel `📐 Calib`) получает **физический смысл**:
- Узкий CI = узкая полоса резонанса (точная настройка, мало evidence нужно для подтверждения/опровержения)
- Широкий CI = широкая полоса (грубая настройка, много evidence ещё нужно)

Это **не новая метрика**, а переинтерпретация existing infrastructure через wave optics.

### 5 медиаторов = регуляторы добротности волны

DA / 5HT / NE / ACh / GABA — **не просто скаляры**, а параметры одной и той же волновой функции:
- gain (DA) — амплитуда захвата
- hyst (5HT) — затухание шума
- aperture (NE) — ширина полосы
- plasticity (ACh) — скорость перестройки
- damping (GABA) — стенки стоячей волны

Один пульт — потому что управляют одной волновой функцией. Балансовая формула `(DA·NE·ACh)/(5HT·GABA) ≈ 1.0` теперь выражает **добротность Q** этой волны.

### Что это меняет — три перспективы

**Архитектурно.** Генерация перестаёт быть «выбором следующего токена». Это **подбор суперпозиции**, минимизирующей рассогласование с current spectrum юзера.

**Метрически.** Качество понимания меряется не perplexity, а **коэффициентом резонанса** = фазовое рассогласование + амплитудная корреляция per axis. Это уже не LLM-фреймворк, а **когнитивный резонатор**.

**Operationally для W16.** Три sub-вопроса implementation:
1. **Генерация резонансных аналогий** — алгоритм подбора текста, проектирующего user-state на target-state
2. **Измерение рассогласования** — sync_error_wave per axis (W16.1) + phase-aware comparison
3. **Стабильность волны при передаче** — устойчивость нашего spectrum'а под влиянием user input (не дрифтуем ли мы при resonance transfer)

Самое узкое место — пока не ясно. Investigate empirically когда W16.1 будет готов.

### Не заменяет — углубляет

Это **не альтернатива** existing формулировке (РГК + workspace + Power). Это **другой язык** описания того же substrate. Старые термины (5 chem-axes, balance(), sync_error) остаются. Новые термины (суперпозиция, резонанс, фазовое рассогласование) — для situations где волновая оптика проще объясняет.

Закрытие: **формула resonance transfer** = генерация аналогий, минимизирующих фазовое рассогласование per axis, с adaptive bandwidth (узкая → точно, широкая → исследовательски).
