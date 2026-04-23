"""Graph store — persistence для единственного графа Baddle.

Заменил собой WorkspaceManager из предыдущей multi-graph архитектуры. Логика
та же — atomic read/write `graph.json` в `graphs/main/` — но без переключения
контекстов. Также держит `StateGraph` singleton привязанным к той же папке.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_ROOT = Path(__file__).parent.parent
_GRAPHS_DIR = _ROOT / "graphs"
GRAPH_DIR = _GRAPHS_DIR / "main"
GRAPH_FILE = GRAPH_DIR / "graph.json"
META_FILE = GRAPH_DIR / "meta.json"


def ensure_dirs() -> None:
    GRAPH_DIR.mkdir(parents=True, exist_ok=True)


def load_graph() -> bool:
    """Подгружает `graph.json` в runtime `_graph`. Возвращает True если файл
    существует и загружен, False — если fresh install / файл сломан."""
    from .graph_logic import _graph, reset_graph

    ensure_dirs()
    if not GRAPH_FILE.exists():
        reset_graph()
        return False
    try:
        data = json.loads(GRAPH_FILE.read_text(encoding="utf-8"))
        reset_graph()
        _graph["nodes"] = data.get("nodes", [])
        _graph["edges"] = data.get("edges", {
            "manual_links": [], "manual_unlinks": [], "directed": []
        })
        # Legacy migration: Action Memory edge-types
        _graph["edges"].setdefault("caused_by", [])
        _graph["edges"].setdefault("followed_by", [])
        _graph["meta"] = data.get("meta", {"topic": "", "hub_nodes": set(), "mode": "horizon"})
        if isinstance(_graph["meta"].get("hub_nodes"), list):
            _graph["meta"]["hub_nodes"] = set(_graph["meta"]["hub_nodes"])
        _graph["embeddings"] = data.get("embeddings", [])
        _graph["tp_overrides"] = data.get("tp_overrides", {})
        if "_horizon" in data:
            _graph["_horizon"] = data["_horizon"]
        return True
    except Exception as e:
        log.warning(f"[graph_store] load failed: {e}")
        reset_graph()
        return False


def save_graph() -> None:
    """Пишет текущий `_graph` в `graph.json`. Atomic replace."""
    from .graph_logic import _graph

    try:
        ensure_dirs()
        meta = dict(_graph.get("meta", {}))
        if isinstance(meta.get("hub_nodes"), set):
            meta["hub_nodes"] = sorted(meta["hub_nodes"])
        data = {
            "nodes": _graph.get("nodes", []),
            "edges": _graph.get("edges", {}),
            "meta": meta,
            "embeddings": _graph.get("embeddings", []),
            "tp_overrides": _graph.get("tp_overrides", {}),
        }
        if "_horizon" in _graph:
            data["_horizon"] = _graph["_horizon"]
        GRAPH_FILE.write_text(
            json.dumps(data, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception as e:
        log.warning(f"[graph_store] save failed: {e}")


def bootstrap() -> bool:
    """Bootstrap-путь для ui.py: подгрузить графа в runtime, привязать
    StateGraph singleton. Вызывается один раз при старте процесса."""
    from .state_graph import StateGraph, set_state_graph

    ensure_dirs()
    loaded = load_graph()
    set_state_graph(StateGraph(base_dir=GRAPH_DIR))
    _touch_meta()
    if loaded:
        from .graph_logic import _graph
        n = len(_graph.get("nodes") or [])
        e = len(_graph.get("embeddings") or [])
        log.info(f"[graph_store] restored from disk: {n} nodes, {e} embeddings")
    return loaded


def _touch_meta() -> None:
    """Обновляет `last_active` timestamp в `meta.json`."""
    try:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if META_FILE.exists():
            info = json.loads(META_FILE.read_text(encoding="utf-8"))
            info["last_active"] = now
        else:
            info = {
                "id": "main",
                "title": "Main",
                "created": now,
                "last_active": now,
            }
        META_FILE.write_text(
            json.dumps(info, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        log.debug(f"[graph_store] meta touch failed: {e}")
