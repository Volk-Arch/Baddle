# Thinking Operations — атомные операции над графом

Четыре операции образуют механику мышления Baddle. Каждая отвечает на
свой вопрос, но все работают через общие примитивы: embedding similarity,
`distinct()`, centroid-анализ, rephrase pattern.

| Операция | Вопрос |
|---|---|
| **[Smart DC](#smart-dc)** | Верна ли эта мысль? |
| **[Pump](#pump)** | Что связывает две далёкие идеи? |
| **[Novelty check](#novelty-check)** | Эта мысль новая? |
| **[Embedding-first brainstorm](#embedding-first-brainstorm)** | Можно ли думать без текста? |

Все четыре опираются на фундаментальный примитив `distinct(a, b)` из
[nand-architecture.md](nand-architecture.md) — различие между идеями
это не свойство, а отношение.

---

## Smart DC

**Идея.** Модель не может честно оценить свою же мысль — она склонна
соглашаться. Smart DC заставляет посмотреть с трёх сторон принудительно:
сильнейший аргумент **ЗА**, сильнейший **ПРОТИВ**, нейтральный **контекст**.
Три разных промпта — три разных «персоны». Результат — не «верно/неверно»,
а карта уверенности с метриками.

**Как работает.** Сначала дивергенция: три параллельных запроса генерируют
тезис (адвокат), антитезис (критик), нейтральный контекст (аналитик).
Каждый — один абзац до 100 слов. Затем конвергенция: четвёртый запрос
получает все три и синтезирует связный итог балансируя перспективы.

**Метрики через embedding.** Confidence считается через centroid трёх
полюсов: чем ближе синтез к центру равновесия — тем он сбалансированнее.
Плюс per-pole анализ даёт **lean** (куда склоняется утверждение) и
**tension** (глубина vs тривиальность спора). Балансированный spor с
низким tension — самый интересный случай: несогласие настоящее, а не
поверхностное.

Если embeddings недоступны — fallback на entropy логпробов.

**Куда идёт confidence.** В ноду графа, в Bayes update через
[Neurochem](neurochem-design.md), в heatmap UI, и обратно в
[Horizon](horizon-design.md) как `surprise = 1 − confidence` —
корректирует precision для следующего шага.

---

## Pump

**Идея.** Smart DC проверяет одну мысль. Pump берёт **две несвязанные
идеи** и ищет скрытые оси на которых обе — точки. Не поиск существующих
связей (это делает Walk), а **генерация** нового абстрактного измерения.

Пример: «иммунная система» + «банкротства» → «адаптивность vs жёсткость»,
«каскадный отказ», «ложные сигналы». Три оси — три инсайта из одной пары.

**Как работает.** Каждая идея итеративно «накачивается» контекстом —
ассоциациями, аспектами, следствиями. Облака растут. Затем LLM-as-abstractor
получает оба облака с инструкцией найти несколько **разных** скрытых
измерений, и с памятью «не повторять уже найденные». Каждый мост
верифицируется через Smart DC — получает те же метрики (lean, tension,
quality). Результаты ранжируются по quality, накапливаются через итерации,
дедуплицируются по тексту.

**Место в архитектуре.** Pump — **поворот** конуса
([cone-design.md § 5. Поворот](cone-design.md)). Не расширение
(Brainstorm) и не сужение (SmartDC), а смена оси. Два конуса от двух
идей → поиск общего пространства → новое направление, невидимое
изнутри каждого конуса.

**Где используется.** Ручной Pump через UI, и автономно в Scout / DMN
каждые 10 минут — см. [dmn-scout-design.md](dmn-scout-design.md).

---

## Novelty check

**Проблема.** LLM склонны повторяться — особенно маленькие модели (8B).
Без фильтра граф за 10 шагов автозапуска заполняется парафразами одной
мысли.

**Алгоритм.** Каждая новая мысль сравнивается с существующими нодами
через cosine similarity по embedding'ам. Если similarity выше порога —
мысль **не отбрасывается сразу**. LLM получает задачу переформулировать
её, сохраняя смысл. Новый embedding сравнивается заново. Если
similarity теперь ниже порога — принимается с новой формулировкой.
Если всё ещё выше — отклоняется.

Логика: модель часто генерирует **новую идею похожими словами**.
Rephrase меняет слова сохраняя суть. Если суть правда новая — embedding
сместится; если дубль — останется близко.

**Адаптивный порог.** Управляется `CognitiveState.precision`. В
EXPLORATION порог мягче — пропускаем больше; в EXECUTION — строже,
только действительно новое. Один параметр precision управляет
temperature, top_k и novelty одновременно.

При малом графе (< 5 нод) фильтр пропускается — на старте нужно набрать
массу.

---

## Embedding-first brainstorm

**Мотивация.** Стандартный brainstorm: LLM генерирует N текстов, каждый
embedд'ится и сравнивается. Из N обычно 60–80% дубликатов → столько же
LLM-токенов уходят впустую.

**Переворот порядка.** Сначала один embedding seed'а, потом N
perturbations в vector space (miliseconds, cheap numpy), novelty-filter
геометрически. Выжившие — ноды с embedding'ом но без текста. Текст
генерируется **только когда юзер открыл ноду**. Экономия на N−1
embed-вызовах + пропорциональная render_rate (типично юзер смотрит 20%
из 5 идей).

**Unrendered ноды** полноценные для всех graph-операций (tick, distinct,
state_graph) — только UI показывает их значком 💭 и ждёт клика. По клику
lazy expand: короткий LLM-запрос разворачивает направление в одно
предложение на тему, используя контекст из соседних нод. Embedding не
обновляется — оригинальный perturbed vector описывал позицию, новый
text подстраивается под неё.

**Что это даёт.** (1) Чистота distinct-routing — embedding не привязан
к lexical формулировке. (2) Отсутствие ghost-нод — novelty-reject до
любого LLM-call. (3) Text-on-demand как самостоятельная фича — любую
ноду можно пометить `rendered=False`, distinct/routing продолжают
работать, текст дорендерится при необходимости.

---

## Общие паттерны

Четыре операции используют один набор механик:

- **Embedding similarity (cosine)** — базис всего: novelty vs existing,
  Pump bridge coverage, Smart DC centroid/lean/tension, perturbation
  validation.
- **`distinct(a, b)`** — NAND primitive. Три зоны: CONFIRM (candidate
  merge), EXPLORE (pump/elaborate), CONFLICT (smartdc/doubt).
- **Rephrase-before-reject** — не только в novelty. Дешёвый LLM call
  часто спасает полезный signal прежде чем отказаться.
- **Centroid + distance** — Smart DC мерит `distance(synthesis,
  centroid_of_poles)`. Pump использует при оценке моста. Общий паттерн:
  `confidence = 1 − distance(target, centroid_of_members)`.

---

## Где в коде

- `src/graph_routes.py` — endpoints `/graph/smartdc`, `/graph/pump`,
  `/graph/think`, `/graph/brainstorm-seed`, `/graph/render-node`
- `src/pump_logic.py` — `pump()`, `_expand_cloud()`, `_find_bridges()`,
  `_verify_bridge()`, `_compute_bridge_quality()`
- `src/graph_logic.py` — `sample_in_embedding_space()`,
  `_ensure_embeddings()`, novelty check в `/graph/think` path
- `src/prompts.py` — промпты `dc_thesis` / `dc_antithesis` / `dc_neutral`
  / `dc_synthesis`
- `src/horizon.py::to_llm_params()` — адаптивный `novelty_threshold`
- `static/js/graph.js` — UI для Pump и unrendered-нод

---

**Навигация:** [← Ontology](ontology.md) · [Индекс](README.md) · [DMN / Scout →](dmn-scout-design.md)
