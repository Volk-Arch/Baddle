# Decisions log

> Спорные решения, принятые по ходу работы. Не TODO (что делать), не docs (как работает), не strategy (зачем). Это **что выбрали и почему**, чтобы в будущем не переоткрывать одни и те же дилеммы.
>
> Формат: `D-N | дата | контекст | выбрали | альтернативы | статус | rationale`. Статусы: `accepted` (живёт), `provisional` (живёт пока, ждём данных), `deferred` (отложено), `rejected` (рассмотрено и отказались).

---

## D-1 | 2026-04-25 | Adapter pattern в Phase D

**Контекст:** Phase D collapse — Resonator с 5-axis chem в `src/rgk.py`. UserState/Neurochem/ProtectiveFreeze — что с ними делать?

**Выбрали:** Adapter pattern — UserState/Neurochem/PF остаются как **facade-классы**, делегируют `_rgk.*` внутри. Public API сохранён (33 properties + 14 methods + to_dict/from_dict).

**Альтернативы:**
- Replace — удалить facades, callers через `get_global_rgk()` напрямую. Дёшево по lines (~−1000), дорого по риску (~50 callers, breaks UI/tests).
- Adapter в стиле property delegation (выбрано).

**Статус:** `accepted`. Цена — +500 строк boilerplate (33 properties getter/setter pairs). Это сознательная плата за zero-risk миграцию.

**Дальнейший путь:** если когда-нибудь захочется реальный line-count cleanup — Phase I в [cleanup-plan.md](cleanup-plan.md) убирает facades. Сейчас не делается.

---

## D-2 | 2026-04-25 | balance() corridor `[0.3, 1.5]`

**Контекст:** [rgk-spec.md §3.5](rgk-spec.md) даёт балансовую формулу `(DA·NE·ACh)/(5HT·GABA) ≈ 1.0`. Какие границы зелёного/жёлтого/красного для UI?

**Выбрали:** Корридор `[0.3, 1.5]` зелёный, `[0.3, 0.5] ∪ [1.5, 2.0]` жёлтый, иначе красный — **теоретический** из rgk-spec, валидируется через 2 мес use данных prime_directive.

**Альтернативы:**
- Жёстче `[0.5, 1.5]` зелёный
- Шире `[0.2, 2.0]` зелёный
- Per-user калибровка с самого начала

**Статус:** `provisional`. Если 95%+ случаев в узком окне `[0.4, 0.7]` — формула слишком плоская, нужны жёстче feeders. См. [TODO.md § Calibration](TODO.md).

---

## D-3 | 2026-04-25 | ACh/GABA feeders как proxy v1

**Контекст:** 5-axis в коде требует источников сигнала для ACh (plasticity) и GABA (damping). У Baddle нет прямых сенсоров этих свойств.

**Выбрали:** Proxy v1: System ACh = `nodes_created_within(3600)/10` + DMN bridge_quality; System GABA = `freeze.active` boolean; User ACh = surprise-triggered; User GABA = `1 − focus_residue`.

**Альтернативы:**
- Не реализовывать ACh/GABA, оставить 3-axis. **Отвергнуто** — нарушает physical model, balance() формула не замыкается.
- embedding_scattering для GABA. **Deferred** — expensive embedding ops в hot path.
- Continuous distinct(msg, recent) для User ACh. **Deferred** — пока surprise-triggered достаточно.
- Breathing detection для User GABA. **Deferred** — opt-in, ждём breathing-mode фичи.

**Статус:** `provisional`. Ограничения честно описаны в [docs/neurochem-design.md § Источники сигнала](../docs/neurochem-design.md). Калибровка через 2 мес.

---

## D-5 | 2026-04-25 | Per-class РГК vs singleton

**Контекст:** Phase D создал `РГК` объект. Где он живёт?

**Выбрали:** Каждый из UserState/Neurochem/ProtectiveFreeze создаёт **свой** `_rgk = РГК()`. Три независимых РГК в системе.

**Альтернативы:**
- Singleton `get_global_rgk()` — все 3 класса используют один РГК. Семантически правильно (каскад зеркал = ОДНА пара резонаторов). Но требует test isolation logic.
- Параметр `rgk=None` в __init__ — production использует global, тесты создают свой.

**Статус:** `deferred`. Per-class работает для тестов и текущей кодовой базы — каскад зеркал реализован через top-level `compute_sync_error(user, neuro, freeze)`. Singleton нужен **prerequisite** для Phase I (см. [cleanup-plan.md](cleanup-plan.md)) и для cross-class feeders которые требуют shared state read.

**Trade-off:** `Neurochem._rgk.user` и `UserState._rgk.system` — пустые/дефолтные, не используются. Каждый класс владеет полным РГК но активно использует только relevant половину. ~200 строк wasted memory, не critical.

---

## D-6 | 2026-04-25 | Удаление workspace tests как orphan

**Контекст:** `tests/test_workspace_scoping.py` 5 failures — `route()/add_goal() got unexpected keyword 'workspace'`. Pre-existing, не моё.

**Выбрали:** Удалить файл. Workspace подсистема была удалена в commit 636c047 (refactor 2026-04-23, single-user simplification) — `workspace.py`, `workspaces_modal.html`, `cross-graph-design.md` все удалены. Тесты — orphan'ы deleted feature.

**Альтернативы:**
- Восстановить workspace feature и поддержать тесты. **Отвергнуто** — feature deliberately removed.
- Skip + пометить `@pytest.mark.skip`. **Отвергнуто** — фичи нет, тестировать нечего.

**Статус:** `accepted`. Файл удалён, CI clean.

---

## D-7 | 2026-04-25 | Identity test sentinel вместо bit-identity на любых данных

**Контекст:** Phase A identity-тесты фиксировали bit-identical EMA values. Phase D меняет порядок операций в adapter pattern — bit-identity на любых данных недостижима.

**Выбрали:** **Semantic identity** на fixed Phase A event sequence остаётся как **sentinel** (TOL 1e-5). Дополнительно — 6 property-based тестов на random seeds (mode hysteresis, balance corridor, coupling, chem bounds, PE consistency).

**Альтернативы:**
- Bit-identity на всех путях. **Отвергнуто** — недостижимо после adapter.
- Только property-based, удалить identity. **Отвергнуто** — sentinel ловит регрессии в самых критичных путях.

**Статус:** `accepted`. 10 identity + 150 property + 1 skipped (counter-wave Tier 2). См. [tests/test_metric_identity.py](../tests/test_metric_identity.py) и [tests/test_rgk_properties.py](../tests/test_rgk_properties.py).

---

## D-8 | 2026-04-25 | Counter-wave generation отложен (R/C bit без actual output)

**Контекст:** РГК v1.0 §6.1 даёт simulation `step(obs, dt)` с buffer для Mode C output `−k·buffer[delay-1]`. Реализовать в Phase D?

**Выбрали:** **Не реализовывать.** R/C bit с гистерезисом на месте (`Resonator.update_mode(perturbation)`), но `update_mode()` не вызывается автоматически. `step(obs, dt)` с buffer — opt-in для будущего.

**Альтернативы:** Полная реализация actual counter-wave generation для будущей audio/sensor processing.

**Статус:** `deferred`. Baddle нет real-time signal processing — R/C bit нужен только как signal к prompt-роутеру (Tier 2 фича). См. [TODO.md § Tier 2](TODO.md) — Counter-wave actual generation.

---

## D-9 | 2026-04-25 | 8-region РГК-карта не делается, оставлен 10-region named_state

**Контекст:** РГК v1.0 §«Карта состояний» даёт 8 регионов (🔵Поток / 🟢Устойчивость / 🟠Фокус / 🟡Исследование / 🔴Перегруз / ⚫Застой / ⚪Выгорание / ✨Инсайт). Сейчас существует 10-region Voronoi `named_state` в (T, A) пространстве (`user_state_map.py`).

**Выбрали:** Оставить existing 10-region named_state. Переименование — Tier 2 (~50 строк изменений + UI).

**Альтернативы:** Переименовать прямо сейчас в 8-region.

**Статус:** `deferred`. Не блокирует ничего; переименование когда будет appetite для UI работы.

---

## D-10 | 2026-04-25 | Documentation структура: docs/ описывает реальность, planning/ — намерения

**Контекст:** Документация смешивала «как работает» и «что делалось» (rgk-architecture.md был с tables-as-API в чужом стиле; rgk-migration-plan.md был полностью про процесс).

**Выбрали:**
- `docs/` — как код работает **сейчас**, narrative-стиль, без temporal language («после Phase D», «расширено», «с 2026-04-25»).
- `planning/TODO.md` — что осталось делать.
- `planning/simplification-plan.md` — strategy: 6 правил, дисциплина.
- `planning/rgk-spec.md` — теоретическая модель.
- `planning/cleanup-plan.md` — детальный E-I roadmap.
- `planning/decisions.md` (этот файл) — спорные решения сессии.
- `planning/breathing-mode.md`, `resonance-code-changes.md`, `resonance-prompt-preset.md` — Tier 2 design specs.
- `memory/project_session_*.md` — снапшоты сессий для retention.

**Удалено:** `docs/rgk-architecture.md` (контент перетёк в `neurochem-design.md` + `world-model.md` mapping table), `planning/rgk-migration-plan.md` (Phase D done, контент в memory snapshot).

**Статус:** `accepted` после feedback Игоря 2026-04-25.

---

## D-11 | 2026-04-25 | Intake chat-export (диалог 22-24.04 ↔ внешний LLM)

**Контекст:** Игорь приложил `chat-export.cleaned.json` — 139-message диалог 22-24 апреля 2026 со внешним LLM, родивший РГК v1.0 spec. Просил использовать для улучшения планов и docs.

**Выбрали:** Прочитать через explore-агента (экономия контекста), извлечь gaps между обсуждённым и текущим TODO/decisions, **точечно** интегрировать новое (не переписывать docs).

**Что нашли:**
- Большая часть концептуальных идей (РГК, конус, 5-axis, balance, каскад зеркал, гистерезис, контр-волна) — **уже** реализована или в TODO.
- 2 расширения существующего: OQ #3 (память как ключ) → углублено формулировкой «протокол воспроизведения волны» + фазовый сдвиг при reconstruction.
- 2 новых Tier 2 пункта: **Snapshot-якорь узора** при перерыве + **Cone-viz controls** (геометрия конуса как UI инструмент, не индикатор).

**Альтернативы:**
- Переписать docs полностью под язык диалога. **Отвергнуто** — docs уже в narrative-стиле, диалог — research notes, не финальные docs.
- Создать `docs/cone-design.md` с новыми идеями. **Отвергнуто** — `cone-design.md` уже существует, расширять при реализации фичей.

**Статус:** `accepted`. Полный отчёт explore-агента не сохранён (одноразовая экстракция); ключевые gaps — в TODO § Tier 2 «Snapshot-якорь» и «Cone-viz controls».

**Урок про процесс:** intake внешнего контекста (диалог с другим LLM, статья, чужой код) → лучше через explore-агент с structured prompt, чем самому читать. Экономит контекст основной сессии в 5-10×.

---

## Что ещё может попасть сюда

Когда снова возникнет ситуация «сделали выбор, который мог бы быть и другим» — записывать сюда. Поля те же. Закрытые вопросы (например, OQ #1-#6 в TODO) тоже могут переехать сюда когда решены.
