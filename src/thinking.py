"""baddle — autonomous thinking engine (tick).

Pure logic, no Flask dependency. Returns dicts with action/target/phase/reason/text.

The cycle (phase-based, like human thinking):
  GENERATE   — batch of diverse ideas (novelty-checked on API side)
  MERGE      — collapse similar before wasting work on duplicates
  ELABORATE  — deepen unique ideas with evidence
  DOUBT      — Smart DC on elaborated but unverified
  GENERATE+  — all verified? look for gaps (META with context)
  SYNTHESIZE — nothing new → stable → final summary

Generate, merge similar, deepen unique, doubt each, repeat.
"""

import random
import logging
from collections import defaultdict, deque

from .graph_logic import _find_clusters

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


def _find_similar_group(candidates, nodes, edges, threshold, min_size=2):
    """Find a group of similar nodes to merge.
    Returns list of indices (capped at MAX_MERGE_BATCH) or None."""
    if len(candidates) < min_size:
        return None

    c_set = {i for i, _ in candidates}

    # Semantic clusters
    clusters = _find_clusters(len(nodes), edges, threshold)
    for c in clusters:
        group = [i for i in c if i in c_set
                 and nodes[i].get("type") not in ("evidence", "goal")]
        fresh = _filter_lineage(group, nodes)
        if len(fresh) >= min_size:
            return fresh[:MAX_MERGE_BATCH]

    # Topic groups
    by_topic = defaultdict(list)
    for i, n in candidates:
        by_topic[n.get("topic", "") or ""].append(i)
    for ids in sorted(by_topic.values(), key=len, reverse=True):
        fresh = _filter_lineage(ids, nodes)
        if len(fresh) >= min_size:
            return fresh[:MAX_MERGE_BATCH]

    return None


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


# ── Tick ─────────────────────────────────────────────────────────────────────

def tick(nodes, edges, graph, threshold=0.91, stable_threshold=0.8,
         force_collapse=False, max_meta=2, min_hyp=5, **kwargs):
    """Phase-based tick: generate → merge → elaborate → doubt → repeat.

    Each phase runs to completion before the next begins.
    Phase transitions happen when there's nothing left to do in current phase.
    """
    if not nodes:
        return {"action": "none", "reason": "Graph is empty.", "phase": "none"}

    cl = classify_nodes(nodes, edges, graph, stable_threshold)
    if not cl["active_nodes"]:
        return {"action": "none", "reason": "No active nodes.", "phase": "none"}

    goal_idx = cl["goal_idx"]
    goal_text = cl["goal_text"]

    # Read mode from goal node (default: horizon = current research cycle)
    mode_id = "horizon"
    if goal_idx is not None:
        mode_id = nodes[goal_idx].get("mode", "horizon")
    hypotheses = cl["hypotheses"]
    bare = cl["bare"]
    unverified = cl["unverified"]
    verified = cl["verified"]

    if force_collapse:
        return _tick_force_collapse(cl["active_nodes"], stable_threshold)

    # ── PHASE 1: GENERATE ──
    # Only generate if we haven't built mass yet. Once we did, merge may reduce
    # count — that's good, don't refill. META handles "need more" later.
    generated = graph.get("_generated", False)
    if not generated and len(hypotheses) < min_hyp:
        return {
            "action": "think_toward", "target": goal_idx or 0, "phase": "generate",
            "reason": f"GENERATE: {len(hypotheses)}/{min_hyp} ideas. Need more.",
            "text": goal_text,
        }
    if len(hypotheses) >= min_hyp:
        graph["_generated"] = True

    # ── PHASE 2: MERGE similar ──
    # Before elaborating or doubting, reduce duplicates
    merge = _find_similar_group(hypotheses, nodes, edges, threshold)
    if merge:
        return {
            "action": "collapse", "target": merge, "phase": "merge",
            "reason": f"MERGE: {len(merge)} similar. Combine before deepening.",
            "text": ", ".join(nodes[i]["text"][:25] for i in merge[:3]) + "...",
        }

    # ── PHASE 3: ELABORATE bare ──
    # Deepen ideas that have no evidence yet
    if bare:
        target = _pick_target(bare, goal_idx, edges)
        if target:
            return {
                "action": "elaborate", "target": target[0], "phase": "elaborate",
                "reason": f"ELABORATE: #{target[0]} needs evidence ({len(bare)} bare).",
                "text": target[1]["text"][:80],
            }

    # ── PHASE 4: DOUBT unverified ──
    if unverified:
        target = _pick_target(unverified, goal_idx, edges)
        if target:
            return {
                "action": "smartdc", "target": target[0], "phase": "doubt",
                "reason": f"DOUBT: #{target[0]} conf={target[1]['confidence']:.0%} ({len(unverified)} unverified).",
                "text": target[1]["text"][:80],
            }

    # ── PHASE 5: GENERATE+ (META) ──
    # All verified. Look for gaps.
    meta_count = graph.get("_meta_count", 0)
    if meta_count < max_meta and len(verified) >= 3:
        graph["_meta_count"] = meta_count + 1
        verified_texts = [n["text"][:80] for _, n in verified]
        return {
            "action": "think_toward", "target": goal_idx or 0, "phase": "generate",
            "reason": f"GENERATE+: {len(verified)} verified. What's missing?",
            "text": goal_text,
            "verified_texts": verified_texts,
        }

    # ── PHASE 6: SYNTHESIZE ──
    avg = sum(n.get("confidence", 0.5) for _, n in cl["active_nodes"]) / max(len(cl["active_nodes"]), 1)
    return {
        "action": "stable", "phase": "synthesize",
        "reason": f"SYNTHESIZE: {len(hypotheses)} ideas, {len(verified)} verified, avg {avg:.0%}. Ready.",
    }


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
