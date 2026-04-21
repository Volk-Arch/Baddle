# Симбиоз — два state-вектора

> Прайм-директива: `sync_error = ‖user − system‖` — L2 в 3D пространстве
> нейрохимии (dopamine / serotonin / norepinephrine). Не декларация,
> а вычисляемая метрика.
>
> Полный предиктивный контур (surprise, PE каналы, imbalance_pressure) —
> в **[friston-loop.md](friston-loop.md)**. Этот doc описывает симметрию
> двух state-векторов как таковую.

---

## Идея

Две симметричные структуры: `Neurochem` (система) и `UserState`
(пользователь) одинаковой формы. Sync-метрика — L2-норма разности.

```
UserState:   [dopamine, serotonin, norepinephrine]   ← ты
SystemState: [dopamine, serotonin, norepinephrine]   ← Baddle

sync_error = ‖user_vec − system_vec‖   # max ≈ √3 ≈ 1.732
```

Burnout / agency / valence / surprise остаются как **отдельные поля у
обеих сторон** — но не входят в sync_error vector (разные физические
явления, смешивать в одной оси L2 давало шум).

До 2026-04-23 вектор был 4D с burnout. Теперь 3D.

---

## Откуда берутся сигналы

**System side** (`Neurochem`) питается **динамикой графа** — см.
[neurochem-design.md](neurochem-design.md).

**User side** (`UserState`) питается **наблюдаемыми сигналами юзера:**

| Канал | → | UserState скаляр |
|---|---|---|
| HRV coherence | → | serotonin |
| HRV stress / RMSSD | → | norepinephrine |
| User engagement (любое сообщение) | → | dopamine (мягко +0.007) |
| Chat sentiment (light LLM) | → | valence |
| 👍 feedback | → | dopamine +0.9, valence +0.7 |
| 👎 feedback | → | dopamine +0.2, burnout +0.05, valence −0.7 |
| decisions_today × cost | → | burnout |
| completed / planned | → | agency |

Все EMA — одна строка, как в Neurochem. Максимально простой контракт.

---

## Режимы симбиоза

Из `(sync_error, user_level, system_level)` derives один из четырёх
режимов:

| Режим | Условие | Поведение |
|---|---|---|
| **FLOW** | sync < 0.3, оба `mean(D,S) > 0.55` | Полный объём, сложные задачи |
| **REST** | sync < 0.3, оба < 0.35 | Предлагает паузу |
| **PROTECT** | sync ≥ 0.3, user low + system high | Берёт на себя: короче ответы |
| **CONFESS** | sync ≥ 0.3, user high + system low | «Дай мне время подумать» |
| *fallback* | любые промежуточные | FLOW |

Advice-слой ([/assist/alerts](../src/assistant.py)) выводит подсказки из
regime, не из жёстких порогов на каждое поле. Это **третий контур
замкнутости** (диалог) — раньше был «задаю вопрос», теперь адаптивное
поведение на основе взаимного состояния.

---

## Жизненный цикл сигнала

```
Пользователь пишет сообщение
  ↓
/assist обработчик:
  • cs.inject_ne(0.4)                     system: NE spike
  • user.register_input()                  user: last_input_ts
  • user.update_from_energy(decisions)     user: burnout
  ↓
classify_intent_llm получает state_hint (текущую системную химию)
  ↓
execute_via_zones(mode) → карточка
  ↓
Пользователь жмёт 👍/👎
  • cs.update_neurochem(d=0.2 | 0.8)      system: feedback в химию
  • user.update_from_feedback(kind)        user: feedback в user-химию
  ↓
UI polling (3s):
  sync_error = ‖user_vec − system_vec‖
  sync_regime = derived
  → две симметричные панели + sync-индикатор
```

---

## UI

Header таба — две симметричные панели вокруг sync-индикатора:

```
┌─────────────────────────────────────────────────────────┐
│ ТЫ     │ Интерес │ Стабильность │ Напряжение │ Усталость │
│                                                           │
│               ⚡ FLOW  sync 78%                           │
│                                                           │
│ BADDLE │ Интерес │ Стабильность │ Напряжение │ Усталость │
└─────────────────────────────────────────────────────────┘
```

Одинаковый цветовой код обеих сторон + симметричная вёрстка =
визуально видно **где расходимся и куда тянет**. Цвет пилюли режима:
FLOW зелёный, REST серый, PROTECT оранжевый, CONFESS малиновый.

---

## Где в коде

- `src/user_state.py` — `UserState`, `compute_sync_error`,
  `compute_sync_regime`, пороги `SYNC_HIGH_THRESHOLD` и друзья
- `src/neurochem.py` — `Neurochem` + `ProtectiveFreeze`
- `src/horizon.py` — `CognitiveState` (держит оба), derived property
  `sync_error` и `sync_regime`
- `src/assistant.py` — `/assist/state` endpoint (возвращает обе стороны
  + sync), `/assist/alerts` (regime-based advice)

---

**Навигация:** [← Neurochem](neurochem-design.md) · [Индекс](README.md) · [Следующее: User Model →](user-model-design.md)
