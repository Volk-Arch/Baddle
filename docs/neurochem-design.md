# Нейрохимический слой Baddle (v5d)

> Четыре динамических скаляра + один защитный режим, модулирующие
> `CognitiveState`. Не параллельный класс — поля и методы живут прямо в
> `CognitiveHorizon`/`CognitiveState` (v12 collapse pre-emptively done).

## Идея в одной фразе

Мышление не равно чистой логике. В реальном мозге баланс глобальных
нейромедиаторов модулирует скорость обучения, остроту внимания, тягу к
исследованию и готовность к сбросу. Baddle повторяет это минимальным
набором **сигналов**, каждый с чёткой ролью и формулой, входящей прямо
в Байесовский шаг ядра.

- **S (серотонин)** — цена обновления / пластичность
- **NE (норадреналин)** — фокус / Horizon↔DMN бюджет
- **DA (дофамин)** — reward prediction error / внутренний драйв
- **Burnout** — защитный режим при хроническом конфликте

## Почему именно эти четыре (и почему DA обязателен)

Каждый медиатор закрывает **концептуальную дыру** мышления:
- Без S система либо ригидная и не учится, либо постоянно дрейфует.
  S даёт *управляемую* пластичность.
- Без NE нет различения «пора фокусироваться» vs «можно блуждать».
  Фоновый DMN и активная работа смешиваются бесконтрольно.
- Без DA система может **думать, но не хочет**. Направление движения
  задаётся только извне. DA — это «что тянет» внутри, разница между
  машиной которая считает и агентом который идёт куда-то.
- Без Burnout система не умеет останавливать себя при сбое — она
  будет генерировать бред до победного, не умея заметить что
  закономерности больше нет.

## Формулы

### Серотонин (S ∈ [0.2, 1.0])

```
target_s =  0.85 (если user_feedback == "accepted")
         |  0.35 (если user_feedback == "rejected")
         |  max(0.2, 1 − d_self) (если d_self задан — мера самосогласованности)
         |  0.4 + 0.6·hrv_coherence (коупинг с телом)

S_{t+1} = λ_S · target_s + (1 − λ_S) · S_t
```

**Влияние на ядро:**
- `γ_eff = γ · S` — эффективная Bayesian-чувствительность
- `α_eff = α₀ · S` — learning rate для горизонта
- Входит в Bayes напрямую: `logit(post) = logit(prior) + γ·S·(1−2d)`

**Поведение:**
- S → 0.2 (низкий): ригидность, опора на prior, избегание ветвлений
- S → 1.0 (высокий): быстрое обучение, активный рефакторинг графа

### Норадреналин (NE ∈ [0, 1])

```
surprise_mag = max(0, |d − d_baseline|)                 (d_baseline = 0.4)
ne_d_ema = λ_NE · surprise_mag + (1 − λ_NE) · ne_d_ema  (скользящее окно)
target_ne = clamp(ne_d_ema · 2.0, 0, 1)
NE_{t+1} = λ_NE · target_ne + (1 − λ_NE) · NE_t

# Плюс внешние spike'и:
inject_ne(amount)          (user input → NE += 0.3..0.4)
decay в idle loop: NE → 0.3 (baseline)
```

**Влияние на ядро:**
- `T_eff = T₀ · (1 − κ·NE) + T_floor` — арузал обостряет выбор
- `budget_H = clamp(0.8·NE + 0.2, 0.2, 0.95)` — доля бюджета Horizon, остаток → DMN

**Поведение:**
- NE > 0.7: фокус, эксплуатация, DMN пауза
- NE < 0.3: диффузный режим, DMN активируется, ищет мосты

### Дофамин (DA: tonic + phasic)

```
rpe = Δconfidence − Δconfidence_predicted
DA_phasic_{t+1} = λ_DA_fast · rpe + (1 − λ_DA_fast) · DA_phasic_t
                 (быстрая компонента, секунды)
DA_tonic_{t+1} = λ_DA_slow · (DA_baseline + DA_phasic · 0.5) + (1 − λ_DA_slow) · DA_tonic_t
                 (медленная компонента, минуты-часы — «настроение»)
```

**Источники RPE:**
- Нода перешла `unverified → verified` быстрее ожидания → **+DA spike**
- Предсказание `verified` оказалось отвергнуто → **−DA spike**
- Пользователь ответил на `/graph/assist` вопрос → **+DA** (engagement = reward)
- Пользователь отверг синтез / переделывает → **−DA**
- Scout-мост квалифицировался `quality > 0.5` → **+DA** (инсайт-reward)
- HRV coherence высокая и стабильная → медленный drift DA_tonic вверх

**Влияние на ядро:**
- `β_policy_eff = β₀ · (0.5 + DA)` — усиление successful-фаз в policy weights
- `intrinsic_pull(node) = DA_tonic · novelty(node)` — смещает DMN-выбор в
  сторону областей где история RPE была положительной → **любопытство**
- Гейт recovery: выход из `PROTECTIVE_FREEZE` требует `DA_tonic > θ_DA_recovery`
  (анти-депрессивный механизм: нельзя вернуться к работе на голом отсутствии стресса)

**Поведение:**
- DA_tonic ↑: активность, закрепление успехов, exploration с предпочтениями
- DA_tonic ↓: ангедония; система на внешних стимулах, фоновых инсайтов нет

### Burnout (защитный режим)

```
conflict_signal = max(0, d − τ_stable)             (τ_stable = 0.6)
burnout_idx_{t+1} = λ_b · conflict_signal · (1 − S) + (1 − λ_b) · burnout_idx_t

if burnout_idx > θ_burnout (0.35):
    state = PROTECTIVE_FREEZE
    → apply_to_bayes возвращает prior без изменений (ΔlogW = 0)
    → γ минимальный, T максимальный (максимальное сглаживание)
    → агрессивная архивация конфликтных узлов

recovery:
    if burnout_idx < θ_recovery (0.2) AND DA_tonic > θ_DA_recovery (0.45):
        state = _prev_state  (возврат к предыдущему режиму)
```

**Поведение:** система не ломается, а **замораживает обновления**, продолжает
логировать события, запускает лёгкий DMN без обязательств. Восстановление —
только при снижении хронического конфликта **И** возврате тонической мотивации.

## HRV → Нейрохимия (тело как вход)

```
coherence → S           (спокойствие → выше пластичность)
coherence → DA_tonic    (когерентность как tonic DA baseline, slow drift)
stress    → NE          (стресс повышает арузал)
```

Всё это в `CognitiveState.update_from_hrv()`. Симулятор даёт те же сигналы
что реальный Polar H10 (будущий).

## Порядок вызовов в одном тике

```
1. sample: d = distinct(A, B)
2. compute RPE: rpe = Δconfidence − Δconfidence_predicted
3. update_neurochem(d, rpe, user_feedback, d_self): NE, S, DA, burnout EMA
4. mode check: if burnout_idx > θ_burnout → PROTECTIVE_FREEZE
5. apply params: γ_eff = γ·S, T_eff = T₀·(1−κ·NE)+T_floor, τ_{in/out}_eff = f(S)
6. bayes: prior → posterior через apply_to_bayes(prior, d)
7. policy update: β_eff в Horizon.update() (DA↑ → сильнее закрепляет)
8. Horizon/DMN: budget_H = f(NE); при DMN target = argmax(DA_tonic · novelty)
9. commit state_node: {action, snapshot с S/NE/DA/burnout, rpe, state_origin}
```

## `state_origin`: 1_rest vs 1_held

Мета-ярлык на каждом state_node:

```
if NE > 0.55 or burnout_idx > 0.2:  state_origin = "1_held"  (активен, напряжён)
else:                                state_origin = "1_rest"  (спокоен, готов сканировать)
```

Используется при meta-tick для понимания *в каком внутреннем режиме*
система была в прошлых похожих эпизодах. Идея из NAND-архитектуры:
1 ≠ 1, состояние помнит путь.

## Sync-first интерпретация (прайм-директива)

Вся нейрохимия работает в подчинении главной цели — синхронизации с
пользователем:

- **DA spike** не только при внутренней верификации, но **когда юзер
  подтверждает пользу** (действует на предложение, возвращается, доверяет)
- **NE follows user**: ввод → NE ↑ → Horizon берёт бюджет. Молчание → NE ↓ →
  DMN готовит почву для следующей встречи
- **S** реагирует на паттерн acceptance/rejection юзера — система
  подстраивается под **его** стиль, а не свой
- **Burnout** триггерит не от внутреннего конфликта абстрактно, а от
  хронического рассинхрона: много ошибок в понимании пользователя →
  защитное отступление, просит пересинхронизации через `/graph/assist`

## Константы (defaults, переопределяемы через `settings.json` позже)

```python
LAMBDA_NE = 0.3          # NE EMA
LAMBDA_S = 0.1           # S EMA (медленнее NE — пластичность не скачет)
LAMBDA_DA_FAST = 0.4     # DA_phasic
LAMBDA_DA_SLOW = 0.02    # DA_tonic (очень медленно — настроение)
LAMBDA_BURNOUT = 0.05    # burnout (дни чтоб накопить, не минуты)

D_BASELINE = 0.4         # baseline "удивления" для NE
TAU_STABLE = 0.6         # порог над которым d считается конфликтом
THETA_BURNOUT = 0.35     # вход в PROTECTIVE_FREEZE
THETA_RECOVERY = 0.2     # выход (вместе с DA condition)
THETA_DA_RECOVERY = 0.45 # минимальный DA_tonic для выхода из FREEZE
KAPPA_NE_TEMP = 0.8      # NE → T мультипликатор
T_FLOOR = 0.05
DA_TONIC_BASELINE = 0.5
S_BASELINE = 0.6
```

## Где живёт код

- `src/horizon.py`: класс `CognitiveState` (alias `CognitiveHorizon`), все поля,
  методы `update_neurochem`, `apply_to_bayes`, `inject_ne`, `update_from_hrv`,
  `effective_temperature`, `horizon_budget`
- `src/watchdog.py`: фоновый цикл — decay NE/DA_phasic в idle, NE-gated DMN
- `src/graph_logic.py`: `_bayesian_update_distinct(prior, d, state=...)` —
  делегирует в `state.apply_to_bayes()` если state передан
- `src/tick_nand.py`: emit `action: "ask"` при `sync_err > 0.6` или
  `NE < 0.35 + много unverified` (pause-on-question)
- `src/state_graph.py`: snapshot каждого тика содержит все четыре скаляра

## API для инспекции

```
GET /assist/state         → { state, neurochem: {S, NE, DA_tonic, DA_phasic, burnout_idx,
                              state_origin}, gamma, gamma_eff, t_effective,
                              horizon_budget, llm_disabled, hrv: {...} }

POST /assist/feedback     → body {feedback: "accepted"|"rejected"|"ignored"}
                           → применяет rpe + user_feedback к CognitiveState
                           → возвращает обновлённые neurochem values

POST /assist/camera       → body {enabled: true|false}
                           → toggle llm_disabled (Camera / sensory deprivation)
```

## Что остаётся открытым

- **Intrinsic pull в выборе DMN-пар** — `argmax(DA·novelty·relevance)` ещё не
  внедрён в `_find_distant_pair`. Сейчас рандом + furthest.
- **RPE-вычисление автоматически** — сейчас только manual через user_feedback
  endpoint. Нужно хранить `predicted_confidence_change` при каждом doubt/elaborate
  и сравнивать с фактическим.
- **Peer review нейроаналогий** — S, NE, DA, burnout как мы их реализовали
  не прошли формальную валидацию нейросаентистом. Формулы консистентны с
  Active Inference / RLPE литературой на уровне идей, но не строгой модели.

---

*Baddle не моделирует мозг буквально. Он берёт минимальный набор сигналов
с понятными ролями и встраивает их так, чтобы эмерджентно получилось
нечто похожее на мышление с настроением, мотивацией и защитой.*
