"""Baddle CognitiveLoop — continuous background cognition.

Formerly "Watchdog"; after v5a/v12 collapse the loop owns both:
  - Scout/DMN (pump between distant nodes, bridge discovery)
  - HRV alerts
  - Continuous neurochem dynamics: NE decays, DA_phasic decays, S drifts, burnout updates
  - NE-driven budget: low NE → DMN runs more often; high NE → DMN paused

Still called `Watchdog` class for backward compat (existing /watchdog/* endpoints).

Design: poll-based, non-blocking. UI polls /assist/alerts to see results.
NE spike is injected by /assist and /graph/assist on user input.
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
        """Main cognitive loop — NE-driven budget, continuous neurochem, Scout/DMN.

        Each iteration:
          1. Read global CognitiveState (NE, DA, S, burnout, HRV)
          2. Update neurochem homeostasis (phasic DA decay, NE baseline pull)
          3. If NE < 0.5 → DMN-tick eligible (pump between distant nodes)
          4. If NE very low → Scout-tick eligible (long-interval pump with save)
          5. Check HRV alerts
          6. Sleep tick_interval (scaled by NE: high NE → quick checks, low NE → relaxed)
        """
        from .horizon import get_global_state, PROTECTIVE_FREEZE
        while self.is_running and not self._stop_event.is_set():
            try:
                state = get_global_state()

                # 1. Continuous neurochem homeostasis (time-based decay)
                # DA_phasic naturally decays toward 0; NE pulled toward baseline
                state.DA_phasic *= 0.92   # ~8% decay per cognitive-loop tick
                # NE decays toward 0.3 baseline slowly when no input
                ne_decay = 0.05
                state.NE = state.NE * (1 - ne_decay) + 0.3 * ne_decay
                # Tonic DA slow drift
                from .horizon import DA_TONIC_BASELINE
                state.DA_tonic = state.DA_tonic * 0.998 + DA_TONIC_BASELINE * 0.002

                # 2. NE-driven DMN gating
                # High NE (user active) → skip DMN; Low NE → DMN more eager
                if state.state != PROTECTIVE_FREEZE and state.NE < 0.55:
                    # Under load boost DMN: shorter effective interval
                    self._check_dmn_continuous()
                    self._check_scout_night()

                # 3. HRV alerts always checked
                self._check_hrv_alerts()
            except Exception as e:
                log.warning(f"[cognitive_loop] error: {e}")

            # Interval scales with NE: active user → shorter ticks (we're in sync),
            # quiet → relaxed
            try:
                state = get_global_state()
                scaled = self.tick_interval * max(0.5, 1.2 - state.NE)
            except Exception:
                scaled = self.tick_interval
            self._stop_event.wait(scaled)

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

        # Feed DMN result back into neurochem: good bridges → DA spike (curiosity reward)
        try:
            from .horizon import get_global_state
            cs = get_global_state()
            quality = best.get("quality", 0.0)
            rpe = (quality - 0.5) * 0.6  # scale: q=1 → +0.3, q=0 → -0.3
            # Approximate d as 1 - quality for state-update purposes
            cs.update_neurochem(d=(1.0 - quality), rpe=rpe)
        except Exception as e:
            log.debug(f"[cognitive_loop] neurochem feedback failed: {e}")

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
