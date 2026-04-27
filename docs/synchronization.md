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

### 1. `sync_error_wave` per axis

Расширение [friston-loop](friston-loop.md):
```python
# в _rgk.project("freeze")
"sync_error_per_axis": {
    "dopamine":      abs(user.gain.value - system.gain.value),
    "serotonin":     abs(user.hyst.value - system.hyst.value),
    "norepinephrine": abs(user.aperture.value - system.aperture.value),
    "acetylcholine": abs(user.plasticity.value - system.plasticity.value),
    "gaba":          abs(user.damping.value - system.damping.value),
    "valence":       abs(user.valence - system_proxy.valence),
    "agency":        abs(user.agency - system_proxy.agency),
}
"sync_error_wave_max_axis": "norepinephrine"  # где наибольшее расхождение
```

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
