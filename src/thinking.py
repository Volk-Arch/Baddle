"""baddle — autonomous thinking engine (tick).

Pure logic, no Flask dependency. Returns dicts with action/target/phase/reason/text.
"""

import random
import logging
from collections import defaultdict, deque

from .graph_logic import _detect_traps, _find_clusters

log = logging.getLogger(__name__)


def classify_nodes(nodes, edges, graph, stable_threshold=0.8):
    """Classify active nodes into categories for tick decision-making.

    Returns dict with: active_nodes, goals, goal_idx, goal_text,
    hypotheses, questions, directed_children, no_evidence, unverified, weak, verified.
    """
    active_nodes = [(i, n) for i, n in enumerate(nodes) if n.get("depth", 0) >= 0]

    goals = [(i, n) for i, n in active_nodes if n.get("type") == "goal"]
    goal_idx = goals[0][0] if goals else None
    goal_text = nodes[goal_idx]["text"][:60] if goal_idx is not None else ""
    if not goals:
        print(f"[tick] WARNING: no goal node found. Types: {[n.get('type','?') for _,n in active_nodes[:5]]}")

    hypotheses = [(i, n) for i, n in active_nodes
                  if n.get("type") in ("hypothesis", "thought")]
    questions = [(i, n) for i, n in active_nodes if n.get("type") == "question"]

    directed_children = {}
    for a, b in graph["edges"].get("directed", []):
        directed_children[a] = directed_children.get(a, 0) + 1

    no_evidence = [h for h in hypotheses if directed_children.get(h[0], 0) == 0]
    unverified = [h for h in hypotheses if h[1].get("confidence", 0.5) < stable_threshold]
    weak = [h for h in hypotheses if h[1].get("confidence", 0.5) <= 0.5]
    verified = [h for h in hypotheses if h[1].get("confidence", 0.5) >= stable_threshold]

    return {
        "active_nodes": active_nodes,
        "goals": goals,
        "goal_idx": goal_idx,
        "goal_text": goal_text,
        "hypotheses": hypotheses,
        "questions": questions,
        "directed_children": directed_children,
        "no_evidence": no_evidence,
        "unverified": unverified,
        "weak": weak,
        "verified": verified,
    }


def pick_toward_goal(candidates, goal_idx, nodes, edges, graph):
    """Pick candidate closest to goal by BFS. Exploration if stuck.

    Returns (node_tuple, distance, is_exploration) or None.
    """
    if not candidates:
        return None
    if goal_idx is None:
        pick = min(candidates, key=lambda x: x[1].get("confidence", 0.5))
        return pick, -1, False

    adj_h = defaultdict(set)
    for e in edges:
        adj_h[e["from"]].add(e["to"])
        adj_h[e["to"]].add(e["from"])

    def bfs_dist(start, target):
        if start == target:
            return 0
        visited = {start}
        queue = deque([(start, 0)])
        while queue:
            cur, d = queue.popleft()
            for nb in adj_h.get(cur, []):
                if nb == target:
                    return d + 1
                if nb not in visited:
                    visited.add(nb)
                    queue.append((nb, d + 1))
        return 999

    distances = {ci: bfs_dist(ci, goal_idx) for ci, _ in candidates}
    sorted_c = sorted(candidates, key=lambda x: (distances.get(x[0], 999), x[1].get("confidence", 0.5)))

    traps = set(_detect_traps(nodes, edges))
    safe = [c for c in sorted_c if c[0] not in traps] or sorted_c

    tried = graph.get("_tick_tried", set())
    if safe[0][0] in tried and len(safe) > 1:
        remaining = [c for c in safe if c[0] not in tried]
        pick = remaining[0] if remaining else random.choice(safe)
        expl = True
    else:
        pick = safe[0]
        expl = False
    tried.add(pick[0])
    graph["_tick_tried"] = tried
    return pick, distances.get(pick[0], 999), expl


def tick_force_collapse(active_nodes):
    """Handle force-collapse phase: collapse in batches of 5.

    Returns action dict.
    """
    collapsable = [i for i, n in active_nodes if n.get("type") not in ("evidence", "goal")]
    if len(collapsable) > 5:
        batch = collapsable[:5]
        return {
            "action": "collapse",
            "target": batch,
            "phase": "collapse",
            "reason": f"COLLAPSE PHASE: batch of 5 from {len(collapsable)} remaining.",
            "text": "batch collapse",
        }
    elif len(collapsable) >= 2:
        return {
            "action": "collapse",
            "target": collapsable,
            "phase": "collapse",
            "reason": f"FINAL COLLAPSE: {len(collapsable)} remaining.",
            "text": "final batch",
        }
    avg_conf = sum(n.get("confidence", 0.5) for _, n in active_nodes) / len(active_nodes)
    return {
        "action": "stable",
        "phase": "synthesize",
        "reason": f"SYNTHESIZE: {len(active_nodes)} nodes, avg {avg_conf:.0%}.",
    }


def tick_fast(cl, nodes, edges, graph, stable_threshold, threshold):
    """FAST MODE — priority-based, converges when possible.

    cl: classified nodes dict from classify_nodes().
    Returns action dict.
    """
    goal_idx = cl["goal_idx"]
    goal_text = cl["goal_text"]
    hypotheses = cl["hypotheses"]
    weak = cl["weak"]
    no_evidence = cl["no_evidence"]
    unverified = cl["unverified"]
    verified = cl["verified"]
    directed_children = cl["directed_children"]
    active_nodes = cl["active_nodes"]

    # 1. Too few hypotheses → Think
    if len(hypotheses) < 3:
        return {"action": "think_toward", "target": goal_idx or 0, "phase": "fast",
                "reason": f"FAST: {len(hypotheses)} hypotheses, need more.", "text": goal_text}

    # 2. Weak → Verify
    if weak:
        result = pick_toward_goal(weak, goal_idx, nodes, edges, graph)
        if result:
            t, dist, expl = result
            tag = " [exploration]" if expl else ""
            return {"action": "smartdc", "target": t[0], "phase": "fast",
                    "reason": f"FAST: #{t[0]} conf={t[1]['confidence']:.0%}, dist={dist}. Verify.{tag}", "text": t[1]["text"][:80]}

    # 3. No evidence → Elaborate
    if no_evidence:
        result = pick_toward_goal(no_evidence, goal_idx, nodes, edges, graph)
        if result:
            t, dist, expl = result
            tag = " [exploration]" if expl else ""
            return {"action": "elaborate", "target": t[0], "phase": "fast",
                    "reason": f"FAST: #{t[0]} no evidence, dist={dist}. Elaborate.{tag}", "text": t[1]["text"][:80]}

    # 4. Rephrase — if 2+ children but still weak (max 1 per node)
    rephrased = graph.get("_rephrased", set())
    needs_rephrase = [h for h in unverified
                      if directed_children.get(h[0], 0) >= 2
                      and h[1].get("confidence", 0.5) <= 0.5
                      and h[0] not in rephrased]
    if needs_rephrase:
        t = needs_rephrase[0]
        rephrased.add(t[0]); graph["_rephrased"] = rephrased
        return {"action": "rephrase", "target": t[0], "phase": "fast",
                "reason": f"FAST: #{t[0]} {directed_children[t[0]]} children, conf={t[1]['confidence']:.0%}. Rephrase.", "text": t[1]["text"][:80]}

    # 5. Ask (max 1)
    asked = graph.get("_asked_nodes", set())
    total_q = sum(1 for n in nodes if n.get("type") == "question")
    if total_q < 1 and unverified:
        need_q = [h for h in unverified if h[0] not in asked]
        if need_q:
            t = need_q[0]
            asked.add(t[0]); graph["_asked_nodes"] = asked
            return {"action": "ask", "target": t[0], "phase": "fast",
                    "reason": f"FAST: probing #{t[0]}.", "text": t[1]["text"][:80]}

    # 6. Unverified → Verify
    if unverified:
        result = pick_toward_goal(unverified, goal_idx, nodes, edges, graph)
        if result:
            t, dist, expl = result
            tag = " [exploration]" if expl else ""
            return {"action": "smartdc", "target": t[0], "phase": "fast",
                    "reason": f"FAST: #{t[0]} conf={t[1]['confidence']:.0%}, dist={dist}. Verify.{tag}", "text": t[1]["text"][:80]}

    # 7. Isolated → Expand
    connected = set()
    for e in edges:
        connected.add(e["from"]); connected.add(e["to"])
    isolated = [(i, n) for i, n in active_nodes
                if i not in connected and n.get("type") not in ("evidence", "goal")]
    if isolated:
        t = isolated[0]
        return {"action": "expand", "target": t[0], "phase": "fast",
                "reason": f"FAST: #{t[0]} isolated. Expand.", "text": t[1]["text"][:80]}

    # 8. Collapse verified nodes (cluster-based or all verified if ≥5)
    clusters = _find_clusters(len(nodes), edges, threshold)
    for c in clusters:
        real = [i for i in c if nodes[i].get("depth", 0) >= 0 and nodes[i].get("type") not in ("evidence", "goal")]
        if len(real) >= 5:
            avg_c = sum(nodes[i].get("confidence", 0.5) for i in real) / len(real)
            if avg_c >= stable_threshold - 0.1:
                return {"action": "collapse", "target": real, "phase": "fast",
                        "reason": f"FAST: cluster {len(real)} nodes, avg {avg_c:.0%}. Collapse.",
                        "text": ", ".join(nodes[i]["text"][:25] for i in real[:3]) + "..."}
    # 8b. No clusters but many verified
    if len(verified) >= 5:
        v_ids = [i for i, _ in verified]
        return {"action": "collapse", "target": v_ids, "phase": "fast",
                "reason": f"FAST: {len(verified)} verified, no clusters. Collapse verified.",
                "text": ", ".join(nodes[i]["text"][:25] for i in v_ids[:3]) + "..."}

    # 9. META
    meta_done = graph.get("_meta_done", False)
    if not meta_done and len(verified) >= 3:
        graph["_meta_done"] = True
        return {"action": "think_toward", "target": goal_idx or 0, "phase": "fast",
                "reason": f"FAST META: {len(verified)} verified. What did I miss?", "text": goal_text}

    # 10. Stable
    avg = sum(n.get("confidence", 0.5) for _, n in active_nodes) / len(active_nodes)
    return {"action": "stable", "phase": "fast",
            "reason": f"FAST DONE: {len(active_nodes)} nodes, avg {avg:.0%}."}


def tick_deep(cl, nodes, edges, graph, stable_threshold, threshold):
    """DEEP MODE — phase-based, thorough investigation.

    cl: classified nodes dict from classify_nodes().
    Returns action dict.
    """
    goal_idx = cl["goal_idx"]
    goal_text = cl["goal_text"]
    hypotheses = cl["hypotheses"]
    questions = cl["questions"]
    no_evidence = cl["no_evidence"]
    unverified = cl["unverified"]
    verified = cl["verified"]
    directed_children = cl["directed_children"]
    active_nodes = cl["active_nodes"]

    # ── PHASE 1: EXPLORE — need mass (< 5 hypotheses) ──
    if len(hypotheses) < 5:
        return {
            "action": "think_toward",
            "target": goal_idx or 0,
            "phase": "explore",
            "reason": f"EXPLORE: {len(hypotheses)} hypotheses, need more ideas. (goal: {goal_text})",
            "text": goal_text,
        }

    # ── PHASE 2: DEEPEN — add evidence to bare hypotheses ──
    if no_evidence:
        result = pick_toward_goal(no_evidence, goal_idx, nodes, edges, graph)
        if result:
            target, dist, expl = result
            tag = " [exploration]" if expl else ""
            return {
                "action": "elaborate",
                "target": target[0],
                "phase": "deepen",
                "reason": f"DEEPEN: #{target[0]} no evidence ({len(no_evidence)} bare, dist={dist}){tag}",
                "text": target[1]["text"][:80],
            }

    # ── PHASE 2b: REPHRASE — if 2+ children but still weak (max 1 per node) ──
    rephrased = graph.get("_rephrased", set())
    needs_rephrase = [h for h in unverified
                      if directed_children.get(h[0], 0) >= 2
                      and h[1].get("confidence", 0.5) <= 0.5
                      and h[0] not in rephrased]
    if needs_rephrase:
        result = pick_toward_goal(needs_rephrase, goal_idx, nodes, edges, graph)
        if result:
            target, dist, expl = result
            rephrased.add(target[0]); graph["_rephrased"] = rephrased
            tag = " [exploration]" if expl else ""
            return {
                "action": "rephrase",
                "target": target[0],
                "phase": "deepen",
                "reason": f"DEEPEN: #{target[0]} {directed_children[target[0]]} children, conf={target[1]['confidence']:.0%}. Rephrase. (dist={dist}){tag}",
                "text": target[1]["text"][:80],
            }

    # ── PHASE 3: VERIFY — Smart DC on unverified ──
    if unverified:
        result = pick_toward_goal(unverified, goal_idx, nodes, edges, graph)
        if result:
            target, dist, expl = result
            tag = " [exploration]" if expl else ""
            return {
                "action": "smartdc",
                "target": target[0],
                "phase": "verify",
                "reason": f"VERIFY: #{target[0]} conf={target[1]['confidence']:.0%} ({len(unverified)} unverified, dist={dist}){tag}",
                "text": target[1]["text"][:80],
            }

    # ── PHASE 4: META — "what did I miss?" ──
    meta_done = graph.get("_meta_done", False)
    if not meta_done and len(verified) >= 5:
        graph["_meta_done"] = True
        return {
            "action": "think_toward",
            "target": goal_idx or 0,
            "phase": "meta",
            "reason": f"META: {len(verified)} verified. What angles did I miss?",
            "text": goal_text,
        }

    # ── PHASE 3b: COLLAPSE — verified clusters or all verified ──
    clusters = _find_clusters(len(nodes), edges, threshold)
    for c in clusters:
        real_nodes = [i for i in c if nodes[i].get("depth", 0) >= 0
                      and nodes[i].get("type") not in ("evidence", "goal")]
        verified_in_cl = [i for i in real_nodes if nodes[i].get("confidence", 0.5) >= stable_threshold]
        if len(verified_in_cl) >= 5:
            return {
                "action": "collapse",
                "target": real_nodes,
                "phase": "collapse",
                "reason": f"COLLAPSE: cluster of {len(real_nodes)} nodes ({len(verified_in_cl)} verified).",
                "text": ", ".join(nodes[i]["text"][:25] for i in real_nodes[:3]) + "...",
            }
    if len(verified) >= 5:
        v_ids = [i for i, _ in verified]
        return {
            "action": "collapse",
            "target": v_ids,
            "phase": "collapse",
            "reason": f"COLLAPSE: {len(verified)} verified, no clusters. Synthesize.",
            "text": ", ".join(nodes[i]["text"][:25] for i in v_ids[:3]) + "...",
        }

    # ── PHASE 4a: EXPAND — isolated nodes ──
    connected = set()
    for e in edges:
        connected.add(e["from"]); connected.add(e["to"])
    isolated = [(i, n) for i, n in active_nodes
                if i not in connected and n.get("type") not in ("evidence", "goal")]
    if isolated:
        t = isolated[0]
        return {
            "action": "expand",
            "target": t[0],
            "phase": "deepen",
            "reason": f"DEEPEN: #{t[0]} isolated. Expand to connect.",
            "text": t[1]["text"][:80],
        }

    # ── PHASE 4b: ASK — probing questions (max 3, only once per node) ──
    asked_nodes = graph.get("_asked_nodes", set())
    if len(questions) < 3:
        need_q = [h for h in hypotheses if h[0] not in asked_nodes and h[1].get("confidence", 0.5) < stable_threshold]
        if need_q:
            target = need_q[0]
            asked_nodes.add(target[0])
            graph["_asked_nodes"] = asked_nodes
            return {
                "action": "ask",
                "target": target[0],
                "phase": "ask",
                "reason": f"ASK: probing #{target[0]} ({len(questions)}/3 questions, {len(asked_nodes)} asked)",
                "text": target[1]["text"][:80],
            }

    # ── PHASE 5: SYNTHESIZE ──
    avg_conf = sum(n.get("confidence", 0.5) for _, n in active_nodes) / len(active_nodes)
    return {
        "action": "stable",
        "phase": "synthesize",
        "reason": f"SYNTHESIZE: {len(active_nodes)} nodes, {len(verified)} verified, avg {avg_conf:.0%}. Ready for final summary.",
    }


def tick(nodes, edges, graph, threshold=0.91, stable_threshold=0.8,
         run_mode="deep", force_collapse=False):
    """Main tick entry point. Pure logic, returns action dict.

    Args:
        nodes: list of node dicts
        edges: precomputed edge list
        graph: graph state dict (for _tick_tried, _rephrased, _asked_nodes, _meta_done, edges.directed)
        threshold: similarity threshold
        stable_threshold: confidence threshold for "verified"
        run_mode: "fast" or "deep"
        force_collapse: if True, collapse remaining nodes in batches
    """
    if not nodes:
        return {"action": "none", "reason": "Graph is empty.", "phase": "none"}

    cl = classify_nodes(nodes, edges, graph, stable_threshold)
    if not cl["active_nodes"]:
        return {"action": "none", "reason": "No active nodes.", "phase": "none"}

    if force_collapse:
        return tick_force_collapse(cl["active_nodes"])

    if run_mode == "fast":
        return tick_fast(cl, nodes, edges, graph, stable_threshold, threshold)

    return tick_deep(cl, nodes, edges, graph, stable_threshold, threshold)
