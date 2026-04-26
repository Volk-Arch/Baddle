"""baddle — helpers for the emergent tick (NAND architecture).

Classification + similarity + target-picking utilities. The actual tick loop
lives in tick_nand.py (distinct-zone routing). No primitive switches.
"""

import random
import logging
from collections import defaultdict, deque

log = logging.getLogger(__name__)


# ── Classification ───────────────────────────────────────────────────────────

def classify_nodes(nodes, edges, graph, stable_threshold=0.8):
    """Classify active nodes into categories for tick decision-making."""
    active_nodes = [(i, n) for i, n in enumerate(nodes) if n.get("depth", 0) >= 0]

    goals = [(i, n) for i, n in active_nodes if n.get("type") == "goal"]
    goal_idx = goals[0][0] if goals else None
    goal_text = nodes[goal_idx]["text"][:60] if goal_idx is not None else ""

    hypotheses = [(i, n) for i, n in active_nodes
                  if n.get("type") in ("hypothesis", "thought")]

    # Count directed children per node
    directed_children = {}
    for a, b in graph["edges"].get("directed", []):
        directed_children[a] = directed_children.get(a, 0) + 1

    # Bare = needs elaboration: no evidence, not yet verified, not a synthesis
    bare = [h for h in hypotheses
            if directed_children.get(h[0], 0) == 0
            and h[1].get("confidence", 0.5) < stable_threshold
            and not h[1].get("collapsed_from")]

    unverified = [h for h in hypotheses if h[1].get("confidence", 0.5) < stable_threshold]
    verified = [h for h in hypotheses if h[1].get("confidence", 0.5) >= stable_threshold]

    return {
        "active_nodes": active_nodes,
        "goals": goals,
        "goal_idx": goal_idx,
        "goal_text": goal_text,
        "hypotheses": hypotheses,
        "bare": bare,
        "unverified": unverified,
        "verified": verified,
    }


# ── Merge: find similar to collapse ─────────────────────────────────────────

MAX_MERGE_BATCH = 4  # merge at most 4 at a time — preserves diversity


def _filter_lineage(indices, nodes):
    """Find the largest group of nodes with no shared lineage.
    Greedy: add nodes one by one, skip if conflicts with existing group."""
    lineages = {}
    for i in indices:
        lineage = set(nodes[i].get("collapsed_from", []))
        stack = list(lineage)
        while stack:
            s = stack.pop()
            if s < len(nodes):
                parents = set(nodes[s].get("collapsed_from", []))
                new = parents - lineage
                lineage |= new
                stack.extend(new)
        lineages[i] = lineage

    # Greedy grouping: take each node if it doesn't overlap with group
    group = []
    group_lineage = set()  # union of all lineages + indices in group
    for i in indices:
        # Check: i not in any existing member's lineage, and no existing member in i's lineage
        if i in group_lineage:
            continue
        conflict = False
        for j in group:
            if j in lineages[i] or i in lineages[j]:
                conflict = True
                break
        if not conflict:
            group.append(i)
            group_lineage.add(i)
            group_lineage |= lineages[i]
    return group


# ── Pick distant pair (for Pump in Scout) ────────────────────────────────────

def _pick_distant_pair(candidates, edges):
    """Pick two nodes with lowest similarity (most distant). For Pump in Scout mode."""
    if len(candidates) < 2:
        return None

    # Build edge weight lookup
    weights = {}
    for e in edges:
        key = (min(e["from"], e["to"]), max(e["from"], e["to"]))
        weights[key] = e.get("weight", 0)

    best_pair = None
    best_sim = 1.0
    idxs = [i for i, _ in candidates]

    for a in range(len(idxs)):
        for b in range(a + 1, len(idxs)):
            key = (min(idxs[a], idxs[b]), max(idxs[a], idxs[b]))
            sim = weights.get(key, 0)
            if sim < best_sim:
                best_sim = sim
                best_pair = (idxs[a], idxs[b])

    return best_pair


# ── Pick target ──────────────────────────────────────────────────────────────

def _pick_target(candidates, goal_idx, edges):
    """Pick best candidate: closest to goal, with occasional random for diversity."""
    if not candidates:
        return None

    count = getattr(_pick_target, '_count', 0)
    _pick_target._count = count + 1
    if count % 3 == 2 and len(candidates) > 1:
        return random.choice(candidates)

    if goal_idx is None:
        return min(candidates, key=lambda x: x[1].get("confidence", 0.5))

    adj = defaultdict(set)
    for e in edges:
        adj[e["from"]].add(e["to"])
        adj[e["to"]].add(e["from"])

    def bfs_dist(start):
        if start == goal_idx:
            return 0
        visited = {start}
        queue = deque([(start, 0)])
        while queue:
            cur, d = queue.popleft()
            for nb in adj.get(cur, []):
                if nb == goal_idx:
                    return d + 1
                if nb not in visited:
                    visited.add(nb)
                    queue.append((nb, d + 1))
        return 999

    return min(candidates, key=lambda x: (bfs_dist(x[0]), x[1].get("confidence", 0.5)))


# ── Force Collapse ───────────────────────────────────────────────────────────

def _tick_force_collapse(active_nodes, stable_threshold=0.8):
    """Force-collapse: batch verified into groups of 5."""
    node_map = {i: n for i, n in active_nodes}
    collapsable = [i for i, n in active_nodes
                   if n.get("type") != "goal"
                   and n.get("confidence", 0.5) >= stable_threshold]
    collapsable.sort(key=lambda i: -node_map[i].get("confidence", 0.5))

    if len(collapsable) > 5:
        return {
            "action": "collapse", "target": collapsable[:5], "phase": "merge",
            "reason": f"FORCE MERGE: batch of 5 from {len(collapsable)}.",
            "text": "batch collapse",
        }
    elif len(collapsable) >= 2:
        return {
            "action": "collapse", "target": collapsable, "phase": "merge",
            "reason": f"FINAL MERGE: {len(collapsable)} remaining.",
            "text": "final batch",
        }

    avg = sum(n.get("confidence", 0.5) for _, n in active_nodes) / max(len(active_nodes), 1)
    return {
        "action": "stable", "phase": "synthesize",
        "reason": f"SYNTHESIZE: {len(active_nodes)} nodes, avg {avg:.0%}.",
    }
