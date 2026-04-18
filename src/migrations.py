"""Format validation + migration on startup.

Baddle держит несколько JSONL/JSON-streams. Когда меняется формат — нужно
убедиться что старые данные не ломают загрузку. Breaking changes запрещены,
все изменения = additions + backfill новых полей дефолтами.

Что проверяем/мигрируем на старте:

1. `user_state.json` — добавлены поля `last_sleep_duration_h`. Если старый
   dump без него — backfill'им None. User Dopamine/Serotonin/NE/burnout были
   всегда.
2. `graph.json` (per workspace) — ноды получили поля `activity_*` (id,
   category, ts_start, ts_end, done, duration_s), `goal_id`, `rendered`.
   Старые ноды без них — просто игнорируем (None ok для всех).
3. `state_graph.jsonl` — получил action `heartbeat` с state_snapshot. Старые
   записи других action-типов работают как раньше.
4. Новые файлы:
   - `activity.jsonl` — создаётся по мере трекинга
   - `plans.jsonl` — по мере планирования
   - `checkins.jsonl` — по мере check-in'ов
   - `patterns.jsonl` — пишется night_cycle'ом
5. `state_embeddings.jsonl` + `graph.json.embeddings` — формат не менялся,
   только добавлена persist-логика (workspace auto-load).

Идеология: **fail soft**. Мы не ломаем startup если что-то странное — просто
логируем warning + idеi дальше с безопасными дефолтами.
"""
from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_ROOT = Path(__file__).parent.parent


# ── Helpers ────────────────────────────────────────────────────────────

def _safe_json_load(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning(f"[migrations] {path.name}: unreadable ({e})")
        return None


def _safe_jsonl_scan(path: Path, limit: int = 100) -> tuple[int, int]:
    """Возвращает (total, broken). Для sanity-check."""
    if not path.exists():
        return (0, 0)
    total, broken = 0, 0
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                total += 1
                try:
                    json.loads(line)
                except json.JSONDecodeError:
                    broken += 1
                if total >= limit:
                    break
    except Exception as e:
        log.warning(f"[migrations] {path.name}: scan failed ({e})")
        return (0, -1)
    return (total, broken)


def _atomic_write_json(path: Path, data) -> bool:
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(path)
        return True
    except Exception as e:
        log.warning(f"[migrations] {path.name} write failed: {e}")
        return False


# ── Individual checks ─────────────────────────────────────────────────

def _check_user_state() -> dict:
    path = _ROOT / "user_state.json"
    st = _safe_json_load(path)
    if st is None:
        return {"file": "user_state.json", "status": "missing_or_unreadable"}
    changed = False
    # Check user_state_dump inside — backfill optional fields
    dump = st.get("user_state_dump")
    if isinstance(dump, dict):
        # last_sleep_duration_h не было → None ok (в from_dict)
        # Но убедимся что ключ существует с None если его нет
        if "last_sleep_duration_h" not in dump:
            dump["last_sleep_duration_h"] = None
            changed = True
    # last_briefing_ts (добавлен для persist briefing interval)
    if "last_briefing_ts" not in st:
        st["last_briefing_ts"] = 0.0
        changed = True
    if changed:
        _atomic_write_json(path, st)
    return {"file": "user_state.json",
            "status": "migrated" if changed else "ok",
            "has_dump": isinstance(dump, dict)}


def _check_user_profile() -> dict:
    path = _ROOT / "user_profile.json"
    p = _safe_json_load(path)
    if p is None:
        return {"file": "user_profile.json", "status": "missing"}
    # Гарантируем 5 категорий
    from .user_profile import CATEGORIES
    changed = False
    cats = p.setdefault("categories", {})
    for c in CATEGORIES:
        e = cats.setdefault(c, {})
        if "preferences" not in e:
            e["preferences"] = []; changed = True
        if "constraints" not in e:
            e["constraints"] = []; changed = True
    if "context" not in p:
        p["context"] = {}; changed = True
    if changed:
        _atomic_write_json(path, p)
    return {"file": "user_profile.json",
            "status": "migrated" if changed else "ok"}


def _check_workspace_graphs() -> list[dict]:
    """Каждый workspaces/*/graph.json должен парситься. Проверяем что
    _node_ поля не повреждены. Новые поля (activity_*, goal_id) просто
    отсутствуют у старых нод — ок.
    """
    results = []
    graphs_dir = _ROOT / "graphs"
    if not graphs_dir.exists():
        return results
    for ws_dir in graphs_dir.iterdir():
        if not ws_dir.is_dir():
            continue
        gf = ws_dir / "graph.json"
        if not gf.exists():
            continue
        data = _safe_json_load(gf)
        if data is None:
            results.append({"file": f"graphs/{ws_dir.name}/graph.json",
                            "status": "unreadable"})
            continue
        nodes = data.get("nodes") or []
        edges = data.get("edges") or {}
        embs = data.get("embeddings") or []
        # Проверим consistency embeddings.length ≈ nodes.length
        emb_mismatch = (len(embs) != len(nodes)) if embs else False
        # Count activity nodes
        activity_nodes = sum(1 for n in nodes if n.get("type") == "activity")
        results.append({
            "file": f"graphs/{ws_dir.name}/graph.json",
            "status": "ok",
            "nodes": len(nodes),
            "embeddings": len(embs),
            "emb_mismatch": emb_mismatch,
            "activity_nodes": activity_nodes,
            "directed_edges": len((edges or {}).get("directed") or []),
        })
    return results


def _check_jsonl_files() -> list[dict]:
    """Быстрый sanity-scan всех jsonl."""
    out = []
    files = [
        "goals.jsonl", "state_graph.jsonl", "state_embeddings.jsonl",
        "activity.jsonl", "plans.jsonl", "checkins.jsonl", "patterns.jsonl",
    ]
    for fn in files:
        path = _ROOT / fn
        total, broken = _safe_jsonl_scan(path, limit=1000)
        if total == 0 and not path.exists():
            status = "missing (ok — will be created on use)"
        elif broken == -1:
            status = "scan_failed"
        elif broken > 0:
            status = f"BROKEN: {broken}/{total} lines invalid"
        else:
            status = "ok"
        out.append({"file": fn, "total": total, "broken": broken,
                    "status": status})
    return out


def _check_state_graph_actions() -> dict:
    """Scan state_graph.jsonl — собрать distribution action-типов.
    Помогает убедиться что heartbeat/tick/assist/night_cycle все нормальные.
    """
    path = _ROOT / "state_graph.jsonl"
    if not path.exists():
        return {"file": "state_graph.jsonl", "status": "missing"}
    counts: dict[str, int] = {}
    total = 0
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                total += 1
                a = e.get("action", "?")
                counts[a] = counts.get(a, 0) + 1
    except Exception as e:
        return {"file": "state_graph.jsonl", "status": f"err: {e}"}
    return {"file": "state_graph.jsonl", "status": "ok",
            "total": total, "by_action": counts}


# ── Main ──────────────────────────────────────────────────────────────

def run_all(verbose: bool = True) -> dict:
    """Запустить все проверки + легкие миграции. Возвращает отчёт."""
    report = {
        "user_state": _check_user_state(),
        "user_profile": _check_user_profile(),
        "workspace_graphs": _check_workspace_graphs(),
        "jsonl_files": _check_jsonl_files(),
        "state_graph_actions": _check_state_graph_actions(),
    }
    if verbose:
        def _p(label, v):
            print(f"  {label}: {v}")
        print("[migrations] Format validation:")
        _p("user_state", report["user_state"]["status"])
        _p("user_profile", report["user_profile"]["status"])
        for w in report["workspace_graphs"]:
            extras = []
            if w.get("activity_nodes"):
                extras.append(f"{w['activity_nodes']} activity-нод")
            if w.get("emb_mismatch"):
                extras.append("⚠ embeddings len mismatch")
            tag = f" ({', '.join(extras)})" if extras else ""
            _p(w["file"], f"{w['status']}, {w.get('nodes', 0)} nodes{tag}")
        for j in report["jsonl_files"]:
            if j["status"].startswith("missing"):
                continue
            _p(j["file"], f"{j['status']} ({j['total']} lines)")
        sg = report["state_graph_actions"]
        if sg.get("by_action"):
            top = sorted(sg["by_action"].items(), key=lambda kv: -kv[1])[:5]
            _p("state_graph top actions",
               ", ".join(f"{a}:{c}" for a, c in top) + f" (всего {sg['total']})")
    return report
