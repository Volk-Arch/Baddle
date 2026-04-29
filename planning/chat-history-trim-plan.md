# chat_history.py trim plan (deferred)

> **Status (2026-04-29):** decided **not** to trim в текущей сессии.
> chat_history.py acknowledged как UI persistence layer, complementary
> с workspace primitive (`src/memory/workspace.py`). Этот файл — план
> на случай если позже захотим унифицировать.

## Текущее positioning

| Слой              | Содержимое                          | Цель                          |
|-------------------|-------------------------------------|-------------------------------|
| **workspace**     | actions (action_kind taxonomy)      | cognition: cross-processing, outcome tracking |
| **chat_history**  | UI entries (msg/card formatting)    | UI: reload chat panel, dedup AM briefings |

**Дубликат content для msg entries:**
- user_chat: записывается в обоих (workspace.record_committed + chat_history.append_entry)
- baddle_reply: workspace через /assist response; chat_history через client _chatStorePush
- chat_event_*: workspace + chat_history через _push_event_to_chat helper
- Cards: только в chat_history (UI artifacts от JS)

**Особые features chat_history:**
- `_migrate_intro_into_card` — data healing для legacy bug (intro msg + card → merge intro в card.intro)
- `_dedup_morning` — убирает дубли morning briefing'ов в одну UTC-дату
- `_MAX_ENTRIES = 200` — trim к последним 200 при append

## Trim plan (если решим)

**Цель:** один source of truth — workspace. chat_history.jsonl deleted. UI читает через graph queries.

### Step 1: workspace.list_chat_entries() helper

Новый helper в `src/memory/workspace.py`:

```python
CHAT_ACTION_KINDS = frozenset({
    "user_chat", "baddle_reply", "brief_morning", "brief_weekly",
    "ui_card",  # новый — cards from JS
    # chat_event_* через prefix match
})

def list_chat_entries(limit: int = 200) -> list[dict]:
    """UI persistence path: graph query → reformat to chat_history entry shape.

    Returns entries in chronological order:
      {"kind":"msg", "role":"user"|"assistant", "content":"...", "meta":{}}
      {"kind":"card", "card":{...}}
    """
    with graph_lock:
        nodes = [
            n for n in _graph["nodes"]
            if n.get("type") == "action"
            and n.get("scope") == "graph"
            and (n.get("action_kind") in CHAT_ACTION_KINDS
                  or (n.get("action_kind") or "").startswith("chat_event_"))
        ]
    nodes.sort(key=lambda n: float(n.get("committed_at") or 0))
    nodes = nodes[-limit:]

    out = []
    for n in nodes:
        kind = n.get("action_kind") or ""
        if kind == "ui_card":
            out.append({"kind": "card", "card": n.get("card") or {}})
        elif kind in ("user_chat",):
            out.append({"kind": "msg", "role": "user",
                         "content": n.get("text") or "", "meta": {}})
        elif kind in ("baddle_reply", "brief_morning", "brief_weekly"):
            out.append({"kind": "msg", "role": "assistant",
                         "content": n.get("text") or "",
                         "meta": {"mode_name": _mode_name_for(kind)}})
        elif kind.startswith("chat_event_"):
            out.append({"kind": "msg", "role": "assistant",
                         "content": n.get("text") or "",
                         "meta": {"mode_name": kind.replace("chat_event_", "").replace("_", " ")}})
    return out
```

### Step 2: ui_card action_kind для cards from JS

JS pushes cards через `_chatStorePush({kind:'card', card:{type:'morning_briefing', ...}})`.
Server-side `/assist/chat/append` route detects card-shape entry → `workspace.record_committed(action_kind='ui_card', extras={'card': card_dict})`.

**Subtype tracking** через `extras.card.type` — UI handlers могут filter (например morning_briefing).

### Step 3: Update routes

**src/io/routes/chat.py /assist/chat/append:**
```python
@assistant_bp.route("/assist/chat/append", methods=["POST"])
def assist_chat_append():
    entry = request.get_json(force=True, silent=True)
    if not isinstance(entry, dict):
        return jsonify({"error": "entry must be object"}), 400
    try:
        from ...memory import workspace
        kind = entry.get("kind", "")
        if kind == "msg":
            role = (entry.get("role") or "").lower()
            text = str(entry.get("text") or entry.get("content") or "")[:500]
            if role == "user" and text:
                # existing user_chat path (sentiment + record_committed)
                ...
            elif role == "assistant":
                # mostly already в workspace через /assist response — skip duplicate
                pass
        elif kind == "card":
            card = entry.get("card") or {}
            workspace.record_committed(
                actor="baddle", action_kind="ui_card",
                text=card.get("title") or card.get("type") or "",
                urgency=0.3, accumulate=False, ttl_seconds=24*3600,
                extras={"card": card},
            )
        return jsonify({"ok": True})
    except Exception as e:
        log.warning(f"[/assist/chat/append] failed: {e}")
        return jsonify({"error": str(e)}), 500
```

**src/io/routes/chat.py /assist/chat/history:**
```python
@assistant_bp.route("/assist/chat/history", methods=["GET"])
def assist_chat_history():
    from ...memory import workspace
    return jsonify({"entries": workspace.list_chat_entries(limit=200)})
```

**src/io/routes/chat.py /assist/chat/clear:**
```python
@assistant_bp.route("/assist/chat/clear", methods=["POST"])
def assist_chat_clear():
    from ...memory import workspace
    n = workspace.archive_chat_entries()  # mark scope='archived' для chat actions
    return jsonify({"ok": True, "removed": n})
```

### Step 4: Update _push_event_to_chat в state.py

Убрать chat_history.append_entry call. Только record_action — попадёт в workspace, появится в graph query.

### Step 5: Lost features

- **_migrate_intro_into_card** — теряем (data healing для одного legacy bug 2026-Q1; bug fixed, старые ноды могут быть stale но not blocking)
- **_dedup_morning** — теряем; чтобы сохранить, нужно add filter в `list_chat_entries`: для action_kind='brief_morning' оставлять только last per UTC date

### Step 6: Delete chat_history.py + paths.CHAT_HISTORY_FILE

```python
# src/paths.py — remove CHAT_HISTORY_FILE
# Delete src/chat_history.py
# Update imports in src/io/routes/chat.py + src/io/state.py + src/process/cognitive_loop.py
```

### Step 7: Tests

- Unit tests для `list_chat_entries`: msg/card mapping, ordering, limit, dedup_morning recreation
- Integration test: /assist response → /assist/chat/history shows msg
- Test card push: /assist/chat/append с card → /assist/chat/history shows card

## Estimated cost

- Implementation: ~1.5ч (steps 1-6)
- Test coverage: ~30-45 мин
- UI smoke test (manual): ~15 мин
- Total: **~2-2.5ч** один commit или 2 commits

## Когда стоит делать

Триггеры:
- Если chat_history.jsonl будет существенно расходиться с workspace state (sync issues observed in production)
- Если новый source events запросит entry в chat_history но не в workspace (или наоборот)
- Если требуется cross-processing над chat msgs (sync_seeking detector хочет видеть повторяющиеся user msg)
- Если просто хочется уменьшить файлы — это weak triggers

## Риски

- UI reload performance: graph query (~few ms) vs jsonl read (~ms). Pre-W14, chat_history было оптимизированно для fast UI. После trim — slower.
- Data loss при migration: existing chat_history.jsonl не migration'ится в graph. Если важна continuity — нужен миграционный script.
- Losing data healing logic — если возникнут новые legacy bugs, нет _migrate_*_into_*.

## Альтернатива (если не trim)

Документировать legitimate parallel в chat_history.py module docstring (done 2026-04-29).
