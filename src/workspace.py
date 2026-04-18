"""Workspace — multi-graph support (v4).

Layout:
  graphs/
    main/
      graph.json            existing Baddle graph dump (nodes, edges, meta)
      state_graph.jsonl     state-graph for this workspace
      state_embeddings.jsonl
      meta.json             title, tags, created, last_active
    work/
      ...
    personal/
      ...
  workspaces/
    index.json              list of workspaces + cross_edges + active id

CognitiveState stays global (one neurochem per person — see prime directive).
user_state.json and settings.json are also global (not per workspace).

Cross-graph edges are discovered periodically by DMN walks that compare
node embeddings across different workspaces; stored in index.json as
  {"from_graph": str, "from_node": int, "to_graph": str, "to_node": int,
   "d": float, "ts": str}

Meta-graph is a derived view — not separate storage. Built from cross_edges:
each workspace = super-node, cross_edge density = weight.
"""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# ── Paths ───────────────────────────────────────────────────────────────────

_ROOT = Path(__file__).parent.parent
_GRAPHS_DIR = _ROOT / "graphs"
_WORKSPACES_DIR = _ROOT / "workspaces"
_INDEX_FILE = _WORKSPACES_DIR / "index.json"


# ── WorkspaceManager ────────────────────────────────────────────────────────

class WorkspaceManager:
    """Singleton manager of multi-graph workspaces.

    Active workspace is the one currently mapped into graph_logic._graph.
    On switch, current is flushed to disk; target is loaded.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.active_id: str = "main"
        self._index: dict = {
            "active_id": "main",
            "workspaces": {},
            "cross_edges": [],
        }
        self._loaded = False

    # ── Index load/save ─────────────────────────────────────────────────────

    def _ensure_dirs(self):
        _GRAPHS_DIR.mkdir(parents=True, exist_ok=True)
        _WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)

    def load_index(self):
        """Load workspace index or create default with 'main'."""
        if self._loaded:
            return
        self._ensure_dirs()
        if _INDEX_FILE.exists():
            try:
                self._index = json.loads(_INDEX_FILE.read_text(encoding="utf-8"))
            except Exception as e:
                log.warning(f"[workspace] index load failed: {e}")
                self._index = {"active_id": "main", "workspaces": {}, "cross_edges": []}
        # Ensure 'main' exists
        if "main" not in self._index.get("workspaces", {}):
            self._index.setdefault("workspaces", {})["main"] = {
                "id": "main",
                "title": "Main",
                "tags": [],
                "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "last_active": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        self.active_id = self._index.get("active_id", "main")
        self._loaded = True

    def save_index(self):
        self._ensure_dirs()
        with self._lock:
            _INDEX_FILE.write_text(
                json.dumps(self._index, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

    # ── Workspace paths ─────────────────────────────────────────────────────

    def workspace_dir(self, ws_id: str) -> Path:
        return _GRAPHS_DIR / ws_id

    def graph_file(self, ws_id: str) -> Path:
        return self.workspace_dir(ws_id) / "graph.json"

    def state_graph_dir(self, ws_id: str) -> Path:
        """Directory StateGraph uses as base_dir for its JSONL files."""
        return self.workspace_dir(ws_id)

    def meta_file(self, ws_id: str) -> Path:
        return self.workspace_dir(ws_id) / "meta.json"

    # ── CRUD ───────────────────────────────────────────────────────────────

    def list_workspaces(self) -> list[dict]:
        self.load_index()
        ws = self._index.get("workspaces", {})
        result = []
        for wid, info in ws.items():
            info = dict(info)
            info["active"] = (wid == self.active_id)
            # Count nodes if graph file exists
            gf = self.graph_file(wid)
            if gf.exists():
                try:
                    data = json.loads(gf.read_text(encoding="utf-8"))
                    info["node_count"] = len(data.get("nodes", []))
                except Exception:
                    info["node_count"] = 0
            else:
                info["node_count"] = 0
            result.append(info)
        return result

    def create(self, ws_id: str, title: str = "", tags: list = None) -> dict:
        """Create a new workspace (directory + meta). Doesn't switch."""
        self.load_index()
        ws_id = ws_id.strip().lower().replace(" ", "_")
        if not ws_id:
            raise ValueError("empty workspace id")
        if ws_id in self._index.get("workspaces", {}):
            raise ValueError(f"workspace '{ws_id}' already exists")
        self.workspace_dir(ws_id).mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        info = {
            "id": ws_id,
            "title": title or ws_id,
            "tags": tags or [],
            "created": now,
            "last_active": now,
        }
        self._index.setdefault("workspaces", {})[ws_id] = info
        self.meta_file(ws_id).write_text(
            json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        self.save_index()
        return info

    def _read_graph_file(self, ws_id: str) -> bool:
        """Читает graph.json нужного workspace'а в глобальный `_graph`.

        Возвращает True если файл существует и загружен, False если fresh
        workspace / файл сломан. Используется и при switch(), и на старте
        процесса (embeddings persist). Выделено из switch() чтобы один код
        на оба пути.
        """
        from .graph_logic import _graph, reset_graph
        target_gf = self.graph_file(ws_id)
        if not target_gf.exists():
            reset_graph()
            return False
        try:
            data = json.loads(target_gf.read_text(encoding="utf-8"))
            reset_graph()
            _graph["nodes"] = data.get("nodes", [])
            _graph["edges"] = data.get("edges", {
                "manual_links": [], "manual_unlinks": [], "directed": []
            })
            _graph["meta"] = data.get("meta", {"topic": "", "hub_nodes": set(), "mode": "horizon"})
            if isinstance(_graph["meta"].get("hub_nodes"), list):
                _graph["meta"]["hub_nodes"] = set(_graph["meta"]["hub_nodes"])
            _graph["embeddings"] = data.get("embeddings", [])
            _graph["tp_overrides"] = data.get("tp_overrides", {})
            if "_horizon" in data:
                _graph["_horizon"] = data["_horizon"]
            return True
        except Exception as e:
            log.warning(f"[workspace] failed to load {ws_id}: {e}")
            reset_graph()
            return False

    def load_active_graph(self):
        """Bootstrap-путь: подгрузить graph.json + state_graph активного
        workspace'а в runtime-state. Вызывается один раз при старте процесса
        (из ui.py), чтобы embeddings и ноды не терялись на рестарте.
        """
        from .state_graph import StateGraph, set_state_graph
        self.load_index()
        ws_id = self.active_id or "main"
        loaded = self._read_graph_file(ws_id)
        set_state_graph(StateGraph(base_dir=self.state_graph_dir(ws_id), graph_id=ws_id))
        if loaded:
            from .graph_logic import _graph
            n = len(_graph.get("nodes") or [])
            e = len(_graph.get("embeddings") or [])
            log.info(f"[workspace] restored '{ws_id}' from disk: {n} nodes, {e} embeddings")
        return loaded

    def switch(self, ws_id: str):
        """Switch active workspace. Flushes current _graph to disk, loads target.

        Also rebinds global StateGraph to target workspace.
        """
        from .graph_logic import _graph
        from .state_graph import StateGraph, set_state_graph

        self.load_index()
        if ws_id not in self._index.get("workspaces", {}):
            raise ValueError(f"workspace '{ws_id}' does not exist")

        # Flush current
        self._flush_active(_graph)

        # Load target (shared reader — embeddings persist without manual save)
        self._read_graph_file(ws_id)

        # Rebind state graph to target directory
        set_state_graph(StateGraph(base_dir=self.state_graph_dir(ws_id), graph_id=ws_id))

        # Update index
        self.active_id = ws_id
        self._index["active_id"] = ws_id
        self._index["workspaces"][ws_id]["last_active"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.save_index()
        log.info(f"[workspace] switched to {ws_id}")

    def _flush_active(self, graph_obj):
        """Save current _graph to the active workspace's graph.json."""
        try:
            self._ensure_dirs()
            self.workspace_dir(self.active_id).mkdir(parents=True, exist_ok=True)
            # hub_nodes is a set — coerce to list for JSON
            meta = dict(graph_obj.get("meta", {}))
            if isinstance(meta.get("hub_nodes"), set):
                meta["hub_nodes"] = sorted(meta["hub_nodes"])
            data = {
                "nodes": graph_obj.get("nodes", []),
                "edges": graph_obj.get("edges", {}),
                "meta": meta,
                "embeddings": graph_obj.get("embeddings", []),
                "tp_overrides": graph_obj.get("tp_overrides", {}),
            }
            if "_horizon" in graph_obj:
                data["_horizon"] = graph_obj["_horizon"]
            self.graph_file(self.active_id).write_text(
                json.dumps(data, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
        except Exception as e:
            log.warning(f"[workspace] flush {self.active_id} failed: {e}")

    def save_active(self):
        """Public save — call before shutdown or for periodic persistence."""
        from .graph_logic import _graph
        self._flush_active(_graph)
        self.save_index()

    def delete(self, ws_id: str):
        """Delete workspace. Can't delete active or 'main'."""
        self.load_index()
        if ws_id == self.active_id:
            raise ValueError("cannot delete active workspace")
        if ws_id == "main":
            raise ValueError("cannot delete 'main' workspace")
        if ws_id not in self._index.get("workspaces", {}):
            raise ValueError(f"workspace '{ws_id}' does not exist")
        import shutil
        shutil.rmtree(self.workspace_dir(ws_id), ignore_errors=True)
        del self._index["workspaces"][ws_id]
        # Drop cross_edges involving this workspace
        self._index["cross_edges"] = [
            e for e in self._index.get("cross_edges", [])
            if e.get("from_graph") != ws_id and e.get("to_graph") != ws_id
        ]
        self.save_index()

    # ── Cross-graph edges ───────────────────────────────────────────────────

    def add_cross_edge(self, from_graph: str, from_node: int,
                       to_graph: str, to_node: int, d: float):
        """Register a serendipity bridge between workspaces."""
        self.load_index()
        entry = {
            "from_graph": from_graph, "from_node": from_node,
            "to_graph": to_graph, "to_node": to_node,
            "d": round(d, 3),
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        edges = self._index.setdefault("cross_edges", [])
        # Dedupe on (from,to) pair direction-agnostic
        for e in edges:
            if ({e.get("from_graph"), e.get("to_graph")} == {from_graph, to_graph}
                    and {e.get("from_node"), e.get("to_node")} == {from_node, to_node}):
                return False
        edges.append(entry)
        # Cap size
        if len(edges) > 500:
            edges.sort(key=lambda x: x.get("ts", ""))
            self._index["cross_edges"] = edges[-500:]
        self.save_index()
        return True

    def list_cross_edges(self, workspace_id: Optional[str] = None) -> list[dict]:
        self.load_index()
        edges = self._index.get("cross_edges", [])
        if workspace_id:
            edges = [e for e in edges
                     if e.get("from_graph") == workspace_id
                     or e.get("to_graph") == workspace_id]
        return edges

    def find_serendipity_bridges(self, min_distinct: float = 0.05,
                                   max_distinct: float = 0.30,
                                   max_results: int = 3,
                                   register: bool = True) -> list[dict]:
        """Scan всех пар workspaces (не только active) для node-пар близких
        в embedding space но не связанных в графе. Serendipity = неочевидная
        но осмысленная связь между разными областями твоей жизни.

        Окно `min_distinct < d < max_distinct` отсекает:
        - near-duplicates (d < min_distinct = ~0.05) — это тривиальные копии
        - unrelated (d > max_distinct = ~0.30) — слишком далёкие для моста

        Если register=True, найденные мосты добавляются в cross_edges индекс
        (dedup встроен в add_cross_edge). Возвращает список dict'ов с from/to.

        Вызывается из DMN _check_dmn_cross_graph каждые 60 мин.
        """
        import numpy as np
        from .main import distinct

        self.load_index()
        ws_ids = list(self._index.get("workspaces", {}).keys())
        if len(ws_ids) < 2:
            return []

        # Cache nodes+embeddings per workspace, single read
        ws_cache: dict[str, tuple[list, list]] = {}
        for wid in ws_ids:
            gf = self.graph_file(wid)
            if not gf.exists():
                continue
            try:
                data = json.loads(gf.read_text(encoding="utf-8"))
                nodes_w = data.get("nodes", [])
                embs_w = data.get("embeddings", [])
                # v8b: fall back to node.embedding если parallel cache пустой
                if not embs_w and nodes_w:
                    embs_w = [n.get("embedding") for n in nodes_w]
                ws_cache[wid] = (nodes_w, embs_w)
            except Exception:
                continue
        if len(ws_cache) < 2:
            return []

        # Active workspace — исключаем его nodes (active уже в _graph RAM,
        # включать = дубли с find_cross_candidates). Но всё равно сканируем
        # все пары между disk-workspaces + active как специальный case.
        bridges: list[dict] = []
        # Перебираем неупорядоченные пары workspaces
        ids = sorted(ws_cache.keys())
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a_id, b_id = ids[i], ids[j]
                a_nodes, a_embs = ws_cache[a_id]
                b_nodes, b_embs = ws_cache[b_id]
                if not a_nodes or not b_nodes:
                    continue
                # Pre-vectorize with explicit index mapping (skip None)
                a_vecs = []
                for idx, emb in enumerate(a_embs):
                    if emb:
                        a_vecs.append((idx, np.array(emb, dtype=np.float32)))
                b_vecs = []
                for idx, emb in enumerate(b_embs):
                    if emb:
                        b_vecs.append((idx, np.array(emb, dtype=np.float32)))
                if not a_vecs or not b_vecs:
                    continue
                # Cap candidates per workspace (O(N·M) expensive): берём
                # до 30 нод каждой стороны — этого хватает для serendipity
                a_vecs = a_vecs[:30]
                b_vecs = b_vecs[:30]
                for a_idx, a_vec in a_vecs:
                    if a_vec.size == 0:
                        continue
                    for b_idx, b_vec in b_vecs:
                        if b_vec.size == 0:
                            continue
                        d = float(distinct(a_vec, b_vec))
                        if d <= min_distinct or d >= max_distinct:
                            continue
                        bridges.append({
                            "from_graph": a_id, "from_node": a_idx,
                            "to_graph": b_id,   "to_node":   b_idx,
                            "from_text": (a_nodes[a_idx].get("text", "") if a_idx < len(a_nodes) else "")[:80],
                            "to_text":   (b_nodes[b_idx].get("text", "") if b_idx < len(b_nodes) else "")[:80],
                            "d": round(d, 3),
                        })
        # Sort by distance (closest=first, but past the min threshold)
        bridges.sort(key=lambda x: x["d"])
        bridges = bridges[:max_results]

        # Register: persist into index as cross_edges (dedup внутри add_cross_edge)
        if register:
            for b in bridges:
                try:
                    self.add_cross_edge(
                        b["from_graph"], b["from_node"],
                        b["to_graph"], b["to_node"], b["d"],
                    )
                except Exception as e:
                    log.debug(f"[workspace] add_cross_edge failed: {e}")
        return bridges

    def find_cross_candidates(self, k: int = 5, tau_in: float = 0.3) -> list[dict]:
        """Scan random node pairs from OTHER workspaces, return those within τ_in.

        Expensive (reads all workspace files). Intended to be called from DMN tick.
        Only considers workspaces with embeddings cached on nodes (v8b).
        """
        import random
        import numpy as np
        from .main import distinct
        from .graph_logic import _graph

        self.load_index()
        others = [wid for wid in self._index.get("workspaces", {}) if wid != self.active_id]
        if not others:
            return []

        current_nodes = _graph.get("nodes", [])
        current_embs = _graph.get("embeddings", [])
        if len(current_nodes) < 2:
            return []

        # Randomly sample 3 anchors from current
        sample_n = min(3, len(current_nodes))
        anchors = random.sample(range(len(current_nodes)), sample_n)
        anchor_vecs = []
        for ai in anchors:
            emb = None
            if ai < len(current_embs) and current_embs[ai]:
                emb = current_embs[ai]
            elif current_nodes[ai].get("embedding"):
                emb = current_nodes[ai]["embedding"]
            if emb:
                anchor_vecs.append((ai, np.array(emb, dtype=np.float32)))
        if not anchor_vecs:
            return []

        hits = []
        for wid in others:
            gf = self.graph_file(wid)
            if not gf.exists():
                continue
            try:
                data = json.loads(gf.read_text(encoding="utf-8"))
                other_nodes = data.get("nodes", [])
                other_embs = data.get("embeddings", [])
            except Exception:
                continue
            # Random sample from other
            if not other_nodes:
                continue
            sample_other = random.sample(range(len(other_nodes)), min(5, len(other_nodes)))
            for oi in sample_other:
                other_emb = None
                if oi < len(other_embs) and other_embs[oi]:
                    other_emb = other_embs[oi]
                elif other_nodes[oi].get("embedding"):
                    other_emb = other_nodes[oi]["embedding"]
                if not other_emb:
                    continue
                o_vec = np.array(other_emb, dtype=np.float32)
                for ai, a_vec in anchor_vecs:
                    d = distinct(a_vec, o_vec)
                    if d < tau_in:
                        hits.append({
                            "from_graph": self.active_id, "from_node": ai,
                            "to_graph": wid, "to_node": oi,
                            "d": round(d, 3),
                            "from_text": current_nodes[ai].get("text", "")[:60],
                            "to_text": other_nodes[oi].get("text", "")[:60],
                        })
        hits.sort(key=lambda x: x["d"])
        return hits[:k]

    # ── Meta-graph (derived view) ───────────────────────────────────────────

    def meta_graph(self) -> dict:
        """Derived view: graph-of-graphs. Each workspace = node, cross-edge
        frequency = weight between workspace-nodes."""
        self.load_index()
        ws_list = list(self._index.get("workspaces", {}).values())
        edges = self._index.get("cross_edges", [])
        # Count edges per unordered (a,b)
        counts: dict[frozenset, int] = {}
        for e in edges:
            key = frozenset({e["from_graph"], e["to_graph"]})
            counts[key] = counts.get(key, 0) + 1
        meta_edges = []
        for key, count in counts.items():
            if len(key) != 2:
                continue
            a, b = list(key)
            meta_edges.append({"a": a, "b": b, "weight": count})
        return {
            "nodes": [{"id": w["id"], "title": w.get("title", w["id"]),
                       "tags": w.get("tags", [])} for w in ws_list],
            "edges": meta_edges,
            "active": self.active_id,
        }


# ── Singleton ───────────────────────────────────────────────────────────────

_manager: Optional[WorkspaceManager] = None


def get_workspace_manager() -> WorkspaceManager:
    global _manager
    if _manager is None:
        _manager = WorkspaceManager()
        _manager.load_index()
    return _manager
