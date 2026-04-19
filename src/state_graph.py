"""StateGraph — second graph that remembers what the system did and when.

History-as-graph. Each tick → one state_node (one JSONL line). Parent = previous
moment. Unified with the Git-audit from docs/nand-architecture.md — same structure.

state_node structure:
{
  "hash":          sha1 prefix (12 chars) of canonical content,
  "parent":        prior hash (None for root),
  "timestamp":     ISO-8601 UTC,
  "action":        "smartdc" | "elaborate" | "compare" | "pump" | "stable" | "ask" | ...,
  "phase":         "generate" | "elaborate" | "doubt" | "merge" | "synthesize" | ...,
  "user_initiated": bool,           # was there a user event triggering this?
  "content_touched": [int, ...],     # content-graph node indices
  "state_snapshot": {...},           # CognitiveState.to_dict() at this moment
  "state_origin":  "1_rest" | "1_held",
  "rpe":           float | None,
  "user_feedback": "accepted" | "rejected" | "ignored" | None,
  "reason":        str (from tick result),
  "graph_id":      str (workspace id, "main" by default)
}

Embeddings are computed lazily on query, cached in state_embeddings.jsonl.
Supports episodic query: query_similar(embedding, k=5) → k most-similar moments.

File layout:
  state_graph.jsonl            — append-only log (human readable)
  state_embeddings.jsonl       — lazily-filled cache (hash → embedding)

For v4 multi-graph, layout will become:
  graphs/{id}/state_graph.jsonl
  graphs/{id}/state_embeddings.jsonl
"""

import json
import hashlib
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# ── Defaults ────────────────────────────────────────────────────────────────

# Legacy (pre multi-workspace): state_graph.jsonl жил в корне. Теперь в
# `data/` (если base_dir=None) для workspace=main fallback. Новые воркспейсы
# передают свой base_dir из WorkspaceManager → graphs/<ws>/.
from .paths import DATA_DIR as _DEFAULT_DIR
_STATE_GRAPH_FILE = "state_graph.jsonl"
_STATE_EMBEDDINGS_FILE = "state_embeddings.jsonl"


# ── StateGraph ──────────────────────────────────────────────────────────────

class StateGraph:
    """Append-only log of system's own history. Thread-safe append.

    Lightweight — nothing stays in memory except the last hash (for parent chaining)
    and optional embedding cache. The log file is the source of truth.
    """

    def __init__(self, base_dir: Optional[Path] = None, graph_id: str = "main"):
        self.base_dir = Path(base_dir) if base_dir else _DEFAULT_DIR
        self.graph_id = graph_id
        self.path = self.base_dir / _STATE_GRAPH_FILE
        self.emb_path = self.base_dir / _STATE_EMBEDDINGS_FILE
        self._lock = threading.Lock()
        self._last_hash: Optional[str] = self._recover_last_hash()
        self._emb_cache: dict[str, list[float]] = {}  # hash → embedding (lazy loaded)

    def _recover_last_hash(self) -> Optional[str]:
        """Scan the file tail to recover the last state_node hash (for parent chain)."""
        if not self.path.exists():
            return None
        try:
            last_line = None
            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        last_line = line
            if not last_line:
                return None
            entry = json.loads(last_line)
            return entry.get("hash")
        except Exception as e:
            log.warning(f"[state_graph] could not recover last hash: {e}")
            return None

    # ── Append ──────────────────────────────────────────────────────────────

    def append(self,
               action: str,
               phase: str = "",
               user_initiated: bool = False,
               content_touched: list[int] = None,
               state_snapshot: dict = None,
               rpe: Optional[float] = None,
               user_feedback: Optional[str] = None,
               reason: str = "",
               state_origin: str = "1_rest") -> str:
        """Append a state_node. Returns its hash."""
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        content_touched = content_touched or []

        # Canonical content for hashing: stable keys and sorted
        canonical = json.dumps({
            "ts": ts,
            "parent": self._last_hash,
            "action": action,
            "phase": phase,
            "content": sorted(content_touched),
            "reason": reason,
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        hash_full = hashlib.sha1(canonical.encode("utf-8")).hexdigest()
        hash_short = hash_full[:12]

        entry = {
            "hash": hash_short,
            "parent": self._last_hash,
            "timestamp": ts,
            "action": action,
            "phase": phase,
            "user_initiated": bool(user_initiated),
            "content_touched": list(content_touched),
            "state_snapshot": state_snapshot or {},
            "state_origin": state_origin,
            "rpe": rpe,
            "user_feedback": user_feedback,
            "reason": reason,
            "graph_id": self.graph_id,
        }

        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._last_hash = hash_short

        return hash_short

    # ── Read / Stream ───────────────────────────────────────────────────────

    def read_all(self, limit: Optional[int] = None,
                 filter_fn=None) -> list[dict]:
        """Stream-read entries, return list. Filter: callable(entry) → bool."""
        if not self.path.exists():
            return []
        out = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if filter_fn and not filter_fn(entry):
                    continue
                out.append(entry)
                if limit and len(out) >= limit:
                    break
        return out

    def tail(self, n: int = 20) -> list[dict]:
        """Last n entries (useful for meta-tick)."""
        all_entries = self.read_all()
        return all_entries[-n:] if all_entries else []

    def count(self) -> int:
        if not self.path.exists():
            return 0
        with self.path.open("r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())

    # ── Embeddings (lazy, cached) ───────────────────────────────────────────

    def _compute_embedding_text(self, entry: dict) -> str:
        """Build compact text representation of a state_node for embedding."""
        action = entry.get("action", "")
        phase = entry.get("phase", "")
        reason = entry.get("reason", "")[:100]
        origin = entry.get("state_origin", "")
        snap = entry.get("state_snapshot") or {}
        state = snap.get("state", "")
        neuro = snap.get("neurochem", {}) or {}
        bits = []
        bits.append(f"{action}:{phase}")
        if state:
            bits.append(state)
        if origin:
            bits.append(origin)
        if neuro:
            def _fmt(v):
                return f"{v:.2f}" if isinstance(v, (int, float)) else "?"
            bits.append(
                f"S={_fmt(neuro.get('serotonin'))} "
                f"NE={_fmt(neuro.get('norepinephrine'))} "
                f"DA={_fmt(neuro.get('dopamine'))}"
            )
        if reason:
            bits.append(reason)
        return " | ".join(bits)

    def _load_embedding_cache(self):
        """One-time lazy load of embeddings file."""
        if self._emb_cache:
            return
        if not self.emb_path.exists():
            return
        with self.emb_path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                    h = e.get("hash")
                    v = e.get("embedding")
                    if h and v:
                        self._emb_cache[h] = v
                except json.JSONDecodeError:
                    continue

    def _save_embedding(self, hash_short: str, embedding: list[float]):
        """Append embedding to cache file."""
        with self._lock:
            with self.emb_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"hash": hash_short, "embedding": embedding}) + "\n")
        self._emb_cache[hash_short] = embedding

    def ensure_embedding(self, entry: dict) -> Optional[list[float]]:
        """Compute (and cache) embedding for a state_node. Returns None on failure."""
        self._load_embedding_cache()
        h = entry.get("hash")
        if not h:
            return None
        if h in self._emb_cache:
            return self._emb_cache[h]
        # Compute
        from .api_backend import api_get_embedding
        text = self._compute_embedding_text(entry)
        if not text:
            return None
        try:
            emb = api_get_embedding(text)
            if emb:
                self._save_embedding(h, emb)
                return emb
        except Exception as e:
            log.warning(f"[state_graph] embedding failed for {h}: {e}")
        return None

    # ── Markov transitions: P(next_action | prev_action) ────────────────────

    def action_transitions(self, tail_n: int = 200,
                            exclude_actions: tuple = ("heartbeat", "none", "")) -> dict:
        """Bigram probabilities над action-sequence из последних tail_n entries.

        Возвращает:
            {
              "transitions": {prev_action: {next_action: prob, ...}},
              "counts":      {prev_action: {next_action: count, ...}},
              "totals":      {prev_action: total_out},
              "n":           сколько transitions учтено
            }

        Используется meta_tick'ом для детекции «policy drift» — когда
        текущий bigram сильно отклоняется от исторической вероятности.
        Также tick_nand может использовать `most_likely_next()` для
        предиктивного policy_nudge.

        heartbeat/none отфильтрованы по умолчанию — это техническая
        активность, не cognitive actions.
        """
        entries = self.read_all()
        if len(entries) < 2:
            return {"transitions": {}, "counts": {}, "totals": {}, "n": 0}
        # Берём хвост и фильтруем
        rel = [e for e in entries[-tail_n:]
                if e.get("action", "") not in exclude_actions]
        if len(rel) < 2:
            return {"transitions": {}, "counts": {}, "totals": {}, "n": 0}
        counts: dict[str, dict[str, int]] = {}
        for a, b in zip(rel[:-1], rel[1:]):
            prev = a.get("action", "")
            nxt = b.get("action", "")
            if not prev or not nxt:
                continue
            counts.setdefault(prev, {})[nxt] = counts.setdefault(prev, {}).get(nxt, 0) + 1
        totals = {k: sum(v.values()) for k, v in counts.items()}
        transitions = {}
        for prev, nxts in counts.items():
            tot = totals.get(prev, 0) or 1
            transitions[prev] = {n: round(c / tot, 3) for n, c in nxts.items()}
        return {
            "transitions": transitions,
            "counts": counts,
            "totals": totals,
            "n": len(rel) - 1,
        }

    def most_likely_next(self, prev_action: str, tail_n: int = 200) -> Optional[tuple]:
        """Самая вероятная next-action после prev_action (None если нет данных).

        Returns (action, probability) или None.
        """
        tr = self.action_transitions(tail_n=tail_n)
        row = tr["transitions"].get(prev_action)
        if not row:
            return None
        nxt = max(row, key=row.get)
        return (nxt, row[nxt])

    # ── Episodic query ──────────────────────────────────────────────────────

    def query_similar(self, query_embedding: list[float],
                      k: int = 5,
                      exclude_recent: int = 3) -> list[dict]:
        """Find k most-similar past state_nodes via distinct distance.

        Skips the most recent `exclude_recent` nodes (to avoid trivial "now" matches).
        Only considers nodes with cached embeddings (doesn't force compute for all —
        that would be expensive). Call `ensure_embedding` to pre-populate.
        """
        import numpy as np
        from .main import distinct

        self._load_embedding_cache()
        if not self._emb_cache:
            return []

        all_entries = self.read_all()
        if len(all_entries) <= exclude_recent:
            return []
        candidates = all_entries[:-exclude_recent] if exclude_recent > 0 else all_entries

        query_vec = np.array(query_embedding, dtype=np.float32)
        if query_vec.size == 0:
            return []

        scored = []
        for entry in candidates:
            h = entry.get("hash")
            emb = self._emb_cache.get(h)
            if not emb:
                continue
            v = np.array(emb, dtype=np.float32)
            if v.size == 0:
                continue
            d = distinct(query_vec, v)
            scored.append((d, entry))

        scored.sort(key=lambda x: x[0])
        return [e for _, e in scored[:k]]


# ── Global singleton ────────────────────────────────────────────────────────

_global_state_graph: Optional[StateGraph] = None


def get_state_graph() -> StateGraph:
    """Global default StateGraph (workspace 'main'). Per-workspace graphs come in v4."""
    global _global_state_graph
    if _global_state_graph is None:
        _global_state_graph = StateGraph()
    return _global_state_graph


def set_state_graph(sg: StateGraph):
    """Replace global (for tests)."""
    global _global_state_graph
    _global_state_graph = sg
