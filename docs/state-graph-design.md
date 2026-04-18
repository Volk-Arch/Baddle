# State-граф Baddle (v5e)

> Второй граф, живущий рядом с контент-графом. Append-only история
> собственной жизни системы. Каждый тик — одна нода (одна строка JSONL).
> Унифицирован с Git-аудитом из [nand-architecture.md](nand-architecture.md).

## Зачем нужен

Контент-граф отвечает на *что* думает система. State-граф отвечает на *как
и когда* она это делала. Без него:

- Нет self-model → система не может учиться на собственных паттернах
- Нет эпизодической памяти → каждая сессия начинается с нуля
- Нет Git-audit → детерминистский replay невозможен
- Невозможен meta-tick — тик не может читать свою историю и адаптироваться

## Структура узла

```json
{
  "hash": "a3f8c2d1abcd",
  "parent": "7b2e4f09dead",
  "timestamp": "2026-04-17T19:32:01+00:00",
  "action": "smartdc" | "elaborate" | "compare" | "pump" | "ask" | "stable" | ...,
  "phase": "generate" | "elaborate" | "doubt" | "merge" | "synthesize" | "dialogue",
  "user_initiated": true | false,
  "content_touched": [7, 12, 18],
  "state_snapshot": {
    "precision": 0.72,
    "state": "execution",
    "gamma": 2.4,
    "tau_in": 0.3, "tau_out": 0.7,
    "sync_error": 0.18,
    "neurochem": {
      "dopamine": 0.42, "serotonin": 0.6, "norepinephrine": 0.72,
      "burnout": 0.05, "freeze_active": false, "state_origin": "1_held"
    },
    "hrv": {"coherence": 0.68, "stress": 0.22, "rmssd": 54}
  },
  "state_origin": "1_held",
  "rpe": null,
  "user_feedback": null,
  "reason": "EMERGENT: #12 unverified — standard doubt",
  "graph_id": "main"
}
```

**Ключевые поля:**
- `hash` — sha1 prefix (12 chars) канонического JSON
- `parent` — hash предыдущей ноды (None для корня)
- `action` — один из NAND-эмерджентных действий
- `user_initiated` — был ли триггером юзер-ввод (NE spike)
- `content_touched` — индексы content-нод, на которые подействовали
- `state_snapshot` — полный `CognitiveState.get_metrics()` в момент тика
- `state_origin` — 1_rest (покой) / 1_held (напряжение), см. v8c
- `rpe` — reward prediction error (когда было measurable)
- `user_feedback` — accept/reject/ignore (когда юзер дал)

## Файлы на диске

```
state_graph.jsonl            # append-only log (default workspace)
state_embeddings.jsonl       # lazy-filled cache (hash → embedding)

# Для multi-workspace (v4):
graphs/{workspace_id}/state_graph.jsonl
graphs/{workspace_id}/state_embeddings.jsonl
```

JSONL = по одной JSON-строке на ноду. Append-only → запись быстрая,
файл растёт, никаких rewrites.

## Hash cheining

```python
canonical = json.dumps({
    "ts": timestamp,
    "parent": prev_hash,
    "action": action,
    "phase": phase,
    "content": sorted(content_touched),
    "reason": reason,
}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

hash_short = sha1(canonical.encode("utf-8")).hexdigest()[:12]
```

При рестарте StateGraph сканирует хвост файла чтобы восстановить последний
hash для parent chain.

## Git-аудит реализация

Из nand-architecture.md был описан Git-коммит каждого байесовского шага
с полной трассой. В v5e это **та же структура** — одна append-only запись,
не два параллельных лога:

- `hash` + `parent` = commit DAG
- `state_snapshot` = полная трасса параметров
- `content_touched` + `action` = что изменилось
- Можно откатить любой шаг (найти node в state_graph, применить diff до target)
- Автоматические ветки: CONFLICT → два parent'а (multi-parent support TBD)

## Эпизодическая память через distinct

```python
def query_similar(query_embedding, k=5, exclude_recent=3):
    """Возвращает k прошлых state_nodes наиболее похожих на query.
    Пропускает последние N (чтоб не попадали тривиальные 'now' matches)."""
```

Использует **тот же distinct() что и контент-граф**:
```
d = distinct(query_vec, past_state_vec)
```

Embedding state-ноды — конкатенация:
```
action:phase | state | state_origin | "S=X NE=Y DA=Z" | reason
```
Считается лениво при первом query через `api_get_embedding()`, кэшируется
в `state_embeddings.jsonl`.

**Use case:** «когда в последний раз система была в похожем состоянии —
что она делала и что сработало?» Это прямое применение NAND к
собственной истории = основа meta-tick.

## Meta-tick (открытое будущее)

State-граф позволяет тику читать **свой хвост** и адаптироваться:

```python
# псевдокод (ещё не реализовано)
tail = state_graph.tail(10)
if all(n["state_snapshot"]["state"] == "execution" for n in tail):
    if stddev([n["state_snapshot"]["sync_error"] for n in tail]) < 0.05:
        # 10 шагов в execution с неизменным sync_error → застряли
        emit_action("ask")  # pause-on-question
```

Сейчас `action: "ask"` триггерится по единичным условиям (sync_err > 0.6
или NE low + много unverified). Полный meta-tick читающий хвост — TODO.

## DMN прогулки по state-графу

Scout / DMN пампы сейчас гуляют по content-графу. Вторая форма — прогулка
по state-графу: находить похожие прошлые моменты и строить мосты между
ними. Это REM-аналог (v11) — переработка опыта, не только содержания.

Реализация: `query_similar()` уже работает. Нужно периодически в DMN-тике
выбирать случайный state_node, искать похожие, строить insight (когда
в обоих случаях было DMN и pump дал bridge → эти два случая обобщаются).

## API

```
GET /graph/self?limit=50&tail=true&action=smartdc&user_initiated=true
    → { entries: [...], total: N, returned: K, last_hash }

POST /graph/self/similar
  body { "query": "текст для embed", "k": 5 }
    OR { "embedding": [...], "k": 5 }
    → { results: [top-k state_nodes by distinct], count: K }
```

## Sync-first интерпретация

Паттерны юзера становятся эмерджентными свойствами state-графа:

```
cluster(user_feedback == "rejected" AND action == "compare")
  → этот юзер не любит long compare-карточки
  → при следующем сравнении → попробовать dispute (диалектика) вместо
```

Так S адаптируется к **конкретному носителю**. Без state-графа это было
бы хардкодом в настройках. Со state-графом — следствием наблюдения.

## Где живёт код

- `src/state_graph.py`: класс `StateGraph` со всеми методами
  (`append`, `read_all`, `tail`, `query_similar`, `ensure_embedding`)
- Singleton через `get_state_graph()`
- Hook в `src/tick_nand.py` `_emit()`: после каждого результата тика
  добавляет state_node с полным snapshot
- `src/workspace.py`: rebind StateGraph на target workspace при switch

## Что остаётся открытым

- **Полный meta-tick** — tick читает хвост state-графа и адаптирует
  следующее действие
- **Ветвление** (multi-parent) при CONFLICT — сейчас линейная цепочка
- **Консолидация** — прунинг «скучных» state-нод, сохранение поворотных
  точек (высокий RPE, смена mode). Без этого файл растёт линейно
- **DMN walks на state-графе** — второй режим Scout'а для REM-аналога

---

*State-граф — substrate эпизодической памяти. Не решает v11-вопрос
«может ли смысл возникнуть», но даёт его компоненты: continuity,
self-observation, эпизоды в контексте, не плоский лог.*

---

**Навигация:** [← HRV](hrv-design.md)  ·  [Индекс](README.md)  ·  [Следующее: Consolidation →](consolidation-design.md)
