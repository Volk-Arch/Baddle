"""Baddle Watchdog — proactive background triggers.

Runs in background thread. Triggers:
  - Scout night: every N hours, run Pump between distant nodes
  - DMN continuous: on each tick, 1 Pump attempt if no active task
  - Alert generation: when conditions detected, add to alerts queue

Design: poll-based, non-blocking. UI polls /assist/alerts to see results.
"""
import threading
import time
import logging
import random
from typing import Optional, Callable, Tuple

from .graph_logic import _graph
from .hrv_manager import get_manager as get_hrv_manager

log = logging.getLogger(__name__)


def _find_distant_pair(nodes: list) -> Optional[Tuple[int, int]]:
    """Find two most distant hypothesis/thought nodes in the graph.

    Uses embedding cosine distance. Returns (idx_a, idx_b) or None.
    """
    from .main import cosine_similarity
    import numpy as np

    # Active hypothesis/thought nodes
    candidates = []
    for i, n in enumerate(nodes):
        if n.get("depth", 0) < 0:
            continue
        if n.get("type") not in ("hypothesis", "thought"):
            continue
        if not n.get("embedding"):
            continue
        candidates.append(i)

    if len(candidates) < 2:
        return None

    # Sample: randomly pick one node, find the furthest
    # (full O(n²) is slow — sampling is fine for DMN)
    pivot_idx = random.choice(candidates)
    pivot_emb = np.array(nodes[pivot_idx]["embedding"], dtype=np.float32)

    best_idx = None
    best_dist = -1.0
    for i in candidates:
        if i == pivot_idx:
            continue
        emb = np.array(nodes[i]["embedding"], dtype=np.float32)
        sim = cosine_similarity(pivot_emb, emb)
        dist = 1.0 - sim
        if dist > best_dist:
            best_dist = dist
            best_idx = i

    if best_idx is None:
        return None
    return (pivot_idx, best_idx)


class Watchdog:
    """Singleton background supervisor."""

    def __init__(self):
        self.is_running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_scout = 0.0        # last Scout Pump run
        self._last_dmn = 0.0           # last DMN cycle
        self._alerts_queue = []        # pending alerts
        self._lock = threading.Lock()
        self.scout_interval = 3 * 3600  # 3 hours
        self.dmn_interval = 600         # 10 min
        self.tick_interval = 60         # check every minute

    def start(self):
        if self.is_running:
            return
        self.is_running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="watchdog")
        self._thread.start()
        log.info("[watchdog] started")

    def stop(self):
        self.is_running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)

    def _loop(self):
        """Main watchdog loop — non-blocking checks every tick_interval."""
        while self.is_running and not self._stop_event.is_set():
            try:
                self._check_scout_night()
                self._check_dmn_continuous()
                self._check_hrv_alerts()
            except Exception as e:
                log.warning(f"[watchdog] error: {e}")
            self._stop_event.wait(self.tick_interval)

    # ── Scout night: Pump between distant nodes ────────────────────────

    def _check_scout_night(self):
        """Run Scout Pump between distant nodes every N hours."""
        now = time.time()
        if now - self._last_scout < self.scout_interval:
            return
        nodes = _graph.get("nodes", [])
        if len(nodes) < 5:
            return  # not enough material

        self._last_scout = now
        bridge = self._run_pump_bridge(max_iterations=2, save=True)
        if bridge:
            self._add_alert({
                "type": "scout_bridge",
                "severity": "info",
                "text": f"Scout нашёл мост: {bridge['text'][:80]}",
                "text_en": f"Scout found bridge: {bridge['text'][:80]}",
                "bridge": bridge,
            })
            log.info(f"[watchdog] scout bridge: {bridge['text'][:60]} q={bridge.get('quality', 0):.2f}")
        else:
            log.info(f"[watchdog] scout: no bridge found in {len(nodes)} nodes")

    # ── DMN continuous: background exploration ────────────────────────

    def _check_dmn_continuous(self):
        """Every dmn_interval, do a background Pump attempt if idle."""
        now = time.time()
        if now - self._last_dmn < self.dmn_interval:
            return
        nodes = _graph.get("nodes", [])
        if len(nodes) < 4:
            return
        self._last_dmn = now

        # DMN: faster, don't save automatically, just suggest
        bridge = self._run_pump_bridge(max_iterations=1, save=False)
        if bridge and bridge.get("quality", 0) > 0.5:
            self._add_alert({
                "type": "dmn_bridge",
                "severity": "info",
                "text": f"DMN-инсайт: {bridge['text'][:80]} (quality {bridge.get('quality', 0):.0%})",
                "text_en": f"DMN insight: {bridge['text'][:80]} (quality {bridge.get('quality', 0):.0%})",
                "bridge": bridge,
            }, dedupe=True)

    def _run_pump_bridge(self, max_iterations: int = 2, save: bool = False) -> Optional[dict]:
        """Actually call pump_logic and return the best bridge.

        save=True → persist bridge as a new node + link both sources.
        """
        from .graph_logic import _graph, _add_node, _ensure_embeddings
        from .pump_logic import pump

        nodes = _graph.get("nodes", [])
        if len(nodes) < 4:
            return None

        # Ensure embeddings exist for distance calc
        try:
            texts = [n.get("text", "") for n in nodes]
            _ensure_embeddings(texts)
        except Exception as e:
            log.warning(f"[watchdog] embeddings failed: {e}")
            return None

        pair = _find_distant_pair(nodes)
        if pair is None:
            return None

        idx_a, idx_b = pair
        log.info(f"[watchdog] Pump #{idx_a} ↔ #{idx_b}")

        try:
            result = pump(idx_a, idx_b, max_iterations=max_iterations, lang="ru")
        except Exception as e:
            log.warning(f"[watchdog] pump failed: {e}")
            return None

        if result.get("error"):
            log.info(f"[watchdog] pump error: {result['error']}")
            return None

        bridges = result.get("all_bridges", [])
        if not bridges:
            return None
        best = bridges[0]

        if save:
            try:
                new_idx = _add_node(
                    best["text"],
                    depth=0,
                    topic="",
                    node_type="hypothesis",
                    confidence=min(0.9, max(0.3, best.get("quality", 0.5))),
                )
                # Link to both sources via directed edges
                directed = _graph["edges"].setdefault("directed", [])
                directed.append([idx_a, new_idx])
                directed.append([idx_b, new_idx])
                manual_links = _graph["edges"].setdefault("manual_links", [])
                for other in (idx_a, idx_b):
                    pair_link = [min(new_idx, other), max(new_idx, other)]
                    if pair_link not in manual_links:
                        manual_links.append(pair_link)
                best["saved_idx"] = new_idx
                best["source_a"] = idx_a
                best["source_b"] = idx_b
            except Exception as e:
                log.warning(f"[watchdog] bridge save failed: {e}")

        return best

    # ── HRV alerts: coherence drops, stress spikes ─────────────────────

    def _check_hrv_alerts(self):
        mgr = get_hrv_manager()
        if not mgr.is_running:
            return
        state = mgr.get_baddle_state()
        coh = state.get("coherence")
        if coh is None:
            return
        # Rapid coherence drop → alert
        if coh < 0.25:
            self._add_alert({
                "type": "coherence_crit",
                "severity": "warning",
                "text": "Coherence очень низкая. Сделай паузу.",
                "text_en": "Coherence very low. Take a break.",
            }, dedupe=True)

    # ── Alerts queue ──────────────────────────────────────────────────

    def _add_alert(self, alert: dict, dedupe: bool = False):
        """Add alert to queue. If dedupe=True, skip if same type exists."""
        with self._lock:
            if dedupe:
                for a in self._alerts_queue:
                    if a.get("type") == alert.get("type"):
                        return
            alert["ts"] = time.time()
            self._alerts_queue.append(alert)
            # Keep only last 20
            if len(self._alerts_queue) > 20:
                self._alerts_queue = self._alerts_queue[-20:]

    def get_alerts(self, clear: bool = False) -> list:
        with self._lock:
            alerts = list(self._alerts_queue)
            if clear:
                self._alerts_queue.clear()
            return alerts

    def get_status(self) -> dict:
        return {
            "running": self.is_running,
            "alerts_pending": len(self._alerts_queue),
            "last_scout": self._last_scout,
            "last_dmn": self._last_dmn,
        }


# ── Singleton ─────────────────────────────────────────────────────────

_watchdog: Optional[Watchdog] = None


def get_watchdog() -> Watchdog:
    global _watchdog
    if _watchdog is None:
        _watchdog = Watchdog()
    return _watchdog
