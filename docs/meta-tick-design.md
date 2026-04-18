# Meta-tick — tick второго порядка через state-граф

> Tick первого порядка смотрит на мгновенный граф и решает что делать.
> Meta-tick смотрит на **собственную историю** и замечает паттерны,
> которые не видны в одном кадре: «я застрял», «юзер меня не принимает»,
> «я сам переоцениваю свои ответы».
>
> Это замыкает петлю: tick пишет state_graph → state_graph читается в
> следующем tick'е → поведение адаптируется → цикл.

## Зачем

Без meta-tick система реагирует только на **текущее** состояние графа.
Если она 10 тиков подряд сидит в EXECUTION с неизменным sync_error —
значит она не сдвигает понимание, но сама этого не замечает. Tick всё
равно эмитит action каждый раз, каждый раз с тем же результатом.

Meta-tick добавляет второй порядок: смотрим на паттерн **в истории**,
а не только в моменте. Если видим зависание — выходим либо через
clarifying question (`ask`), либо через принудительный `compare`,
либо через толчок policy.

## Паттерны

### stuck_execution
9 из 10 последних tick'ов в состоянии `EXECUTION`, `sync_error` изменился
< 0.05. Система в узком фокусе, но ничего не узнаёт.
→ **ask** (спросить юзера, он/она скорее всего уточнит что именно хочется).

### high_rejection
3 из 5 последних entries имеют `user_feedback == "rejected"`. Юзер
активно не принимает ответы → пересинхрон нужен.
→ **ask** + `policy_nudge {doubt: +0.1, generate: −0.05, elaborate: −0.05}`.

### rpe_negative_streak
6 из 10 последних `recent_rpe < −0.05` в state_snapshot.neurochem.
Система системно ожидает больше чем получает — overprediction.
→ **stabilize** (force `INTEGRATION`) + `policy_nudge {merge: +0.1,
generate: −0.1}`. Идея: хватит генерировать новое, пора консолидировать
что есть.

### action_monotony
5 одинаковых `action` подряд (кроме `stable`/`none`). Routing зацикливается
на одной фазе — выбиваем через `compare`.
→ **compare** + `policy_nudge {doubt: +0.1, merge: −0.05, generate: −0.05}`.

### normal
Ничего не сработало. Продолжаем обычный routing.

## Приоритет

Паттерны проверяются в порядке: `rejection → stuck → rpe_streak → monotony`.
Первый сработавший побеждает. Обосновано:
1. Rejection — сигнал от юзера, перекрывает всё
2. Stuck с плоским sync — максимальный desync, ASK
3. RPE streak — системная проблема, требует stabilize
4. Monotony — локальная проблема routing'а, самая слабая

## Применение

`tick_nand.py` вызывает `analyze_tail(tail)` после обычной ASK-проверки:

```python
if recommend == "ask":    return _emit({"action": "ask", ...})
if recommend == "compare": return _emit({"action": "compare", ...})
if recommend == "stabilize": horizon.state = INTEGRATION
if meta.get("policy_nudge"): apply_policy_nudge(horizon, nudge)
```

`apply_policy_nudge` делает `weights[phase] += delta` с `max(0.05, ...)`
floor и нормализацией суммы к 1.0. Эффект — следующий tick через
`horizon.select_phase(available)` выберет другую фазу.

## Что не реализовано

- **Длинные окна.** 20 последних tick'ов ≈ минуты работы. Паттерны на
  уровне дней (например «по понедельникам я всегда эмичу ask») потребуют
  запросов по архиву state_graph + per-day индексы.
- **Combined patterns.** Сейчас первый паттерн выигрывает. Бывает что
  stuck + high rejection одновременно — сейчас просто один сработает.
- **Learning rate.** policy nudge хардкод ±0.1. Могло бы скейлиться
  силой паттерна (3 rejection vs 5 rejection = разная уверенность).
- **Meta-meta.** Сколько раз мы уже эмитили META-ask по одной причине?
  Нет счётчика, может зациклиться. Надо добавить cooldown на повторный
  META-ask одного паттерна.

## Файлы

- [src/meta_tick.py](../src/meta_tick.py) — `analyze_tail`, `apply_policy_nudge`
- [src/tick_nand.py](../src/tick_nand.py) — интеграция после ASK CHECK
- [src/state_graph.py](../src/state_graph.py) — источник данных (`tail(20)`)
