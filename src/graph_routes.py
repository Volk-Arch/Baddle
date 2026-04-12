"""baddle — graph Flask routes (Blueprint)."""

import random
from collections import defaultdict, deque
from datetime import datetime, timezone

import numpy as np

from flask import Blueprint, request, jsonify

from .prompts import _p
from .main import cosine_similarity
from .graph_logic import (
    _graph, graph_lock, reset_graph,
    _auto_type_and_confidence, _auto_evidence_relation, _bayesian_update,
    _make_node, _ensure_node_fields, _get_texts, _add_node, _remove_node,
    _graph_generate, _clean_thought, _generate_thought,
    _ensure_embeddings, _compute_edges, _find_clusters, _remap_edges,
    _detect_traps, _compute_alpha_beta,
)

graph_bp = Blueprint("graph", __name__)


# ── helpers ──────────────────────────────────────────────────────────────────

def _p_data():
    """Extract common params from request JSON."""
    d = request.get_json(force=True)
    d.setdefault("threshold", 0.91)
    d.setdefault("sim_mode", "embedding")
    d.setdefault("lang", "en")
    d.setdefault("temp", 0.9)
    d.setdefault("top_k", 40)
    d.setdefault("seed", -1)
    d["threshold"] = float(d["threshold"])
    d["temp"] = float(d["temp"])
    d["top_k"] = int(d["top_k"])
    d["seed"] = int(d["seed"])
    return d


def _finalize(nodes, threshold, sim_mode, **extra):
    """Compute edges/clusters and return standard graph response."""
    edges = _compute_edges(nodes, threshold, sim_mode)
    clusters = _find_clusters(len(nodes), edges, threshold)
    traps = _detect_traps(nodes, edges)
    alpha_beta = _compute_alpha_beta(nodes)
    resp = {
        "nodes": nodes,
        "edges": edges,
        "clusters": clusters,
        "directed_edges": _graph["edges"].get("directed", []),
        "hub_nodes": list(_graph["meta"].get("hub_nodes", set())),
        "traps": traps,
        "alpha_beta": {str(k): v for k, v in alpha_beta.items()},
    }
    resp.update(extra)
    return jsonify(resp)


# ── routes ───────────────────────────────────────────────────────────────────

@graph_bp.route("/graph/reset", methods=["POST"])
def graph_reset():
    """Reset all graph state."""
    reset_graph()
    return jsonify({"ok": True})


@graph_bp.route("/graph/think", methods=["POST"])
def graph_think():
    """Generate N thoughts about a topic."""
    d = _p_data()
    topic = d.get("topic", "").strip()
    n = int(d.get("n", 6))
    maxtok_think = int(d.get("maxtok_think", 60))
    existing = d.get("existing", [])

    if not topic:
        return jsonify({"error": "empty topic"})

    _graph["meta"]["topic"] = topic
    if not existing:
        _graph["nodes"] = []
        _graph["edges"] = {"manual_links": [], "manual_unlinks": [], "directed": []}
        _graph["meta"]["hub_nodes"] = set()
        _graph["embeddings"] = []

    nodes = _graph["nodes"]

    # Restore nodes from existing data (sync from frontend)
    if existing:
        # Support both node dicts and legacy string lists
        if existing and isinstance(existing[0], str):
            _graph["nodes"] = [_make_node(i, t, topic=topic) for i, t in enumerate(existing)]
        else:
            _graph["nodes"] = list(existing)
        nodes = _graph["nodes"]
        _ensure_node_fields(nodes)
        goal_check = [n for n in nodes if n.get("type") == "goal"]
        print(f"[think] existing restored: {len(nodes)} nodes, goals: {len(goal_check)}, types: {[n.get('type','?') for n in nodes[:6]]}")

    # Brainstorm mode: if source_idx provided, link to that node instead of topic root
    source_idx = d.get("source_idx")
    if source_idx is not None:
        source_idx = int(source_idx)
        if source_idx < 0 or source_idx >= len(nodes):
            source_idx = None

    if source_idx is not None:
        topic_idx = source_idx
    else:
        # Add topic as root node (depth=-1) if not already present
        topic_idx = -1
        for i, nd in enumerate(nodes):
            if nd["depth"] == -1 and nd["text"] == topic:
                topic_idx = i
                break
        if topic_idx < 0:
            for i, nd in enumerate(nodes):
                if nd.get("type") == "goal" and nd["text"] == topic:
                    topic_idx = i
                    break
        if topic_idx < 0:
            topic_idx = _add_node(topic, depth=-1, topic=topic)

    new_thoughts = []
    directed = _graph["edges"]["directed"]
    manual_links = _graph["edges"]["manual_links"]
    novelty_threshold = float(d.get("novelty_threshold", 0.92))
    duplicates_skipped = 0

    attempts = 0
    while len(new_thoughts) < n and attempts < n * 3:
        attempts += 1
        t, ent = _generate_thought(topic, new_thoughts, d["lang"], d["temp"], d["top_k"], d["seed"], maxtok_think)
        if not t or len(t) < 10:
            continue
        if t.lower().strip("., ") in ("qwen3", "qwen", "llama", "gpt", "assistant"):
            continue
        if any(t.lower() == nd["text"].lower() for nd in nodes):
            duplicates_skipped += 1
            continue
        # Novelty check: reject if too similar to any existing node
        # Skip when few nodes (nothing to filter) — saves N embedding API calls
        if len(nodes) > 5:
            try:
                texts = _get_texts(nodes)
                _ensure_embeddings(texts)
                cache = _graph.get("embeddings", [])
                from .api_backend import api_get_embedding
                new_emb = api_get_embedding(t)
                if new_emb:
                    too_similar = False
                    for idx, emb in enumerate(cache):
                        if emb is not None:
                            sim = cosine_similarity(
                                np.array(new_emb, dtype=np.float32),
                                np.array(emb, dtype=np.float32))
                            if sim > novelty_threshold:
                                # Try rephrase before rejecting — idea may be new but wording similar
                                rephrase_messages = [
                                    {"role": "system", "content": "/no_think\nRephrase this idea in completely different words. Keep the core meaning. One sentence. Answer directly."},
                                    {"role": "user", "content": t},
                                ]
                                rephrased, _ = _graph_generate(rephrase_messages, max_tokens=60, temp=0.9)
                                rephrased = _clean_thought(rephrased, topic)
                                if rephrased and len(rephrased) > 10:
                                    re_emb = api_get_embedding(rephrased)
                                    if re_emb:
                                        re_sim = cosine_similarity(
                                            np.array(re_emb, dtype=np.float32),
                                            np.array(emb, dtype=np.float32))
                                        if re_sim <= novelty_threshold:
                                            print(f"[think] novelty rephrase saved: '{t[:30]}' → '{rephrased[:30]}' sim {sim:.2f}→{re_sim:.2f}")
                                            t = rephrased
                                            new_emb = re_emb
                                            too_similar = False
                                            break
                                print(f"[think] novelty reject: '{t[:40]}' sim={sim:.2f} with #{idx} '{nodes[idx]['text'][:40]}'")
                                too_similar = True
                                duplicates_skipped += 1
                                break
                    if too_similar:
                        continue
            except Exception as e:
                log.warning(f"[think] novelty check failed: {e}")
        auto_type, llm_conf = _auto_type_and_confidence(t)
        # Hypothesis = 0.5 (unverified, needs evidence/verify to change)
        # Fact = LLM confidence (already verified by knowledge)
        # Others = LLM confidence
        if auto_type == "hypothesis":
            conf = 0.5
        else:
            conf = llm_conf
        new_idx = _add_node(t, depth=0, topic=topic, entropy=ent, confidence=conf, node_type=auto_type)
        # Link topic root → new thought
        directed.append([topic_idx, new_idx])
        pair = [min(topic_idx, new_idx), max(topic_idx, new_idx)]
        if pair not in manual_links:
            manual_links.append(pair)
        # Also link goal → new thought (if goal exists)
        goal_nodes = [i for i, nd in enumerate(nodes) if nd.get("type") == "goal"]
        if goal_nodes:
            g_idx = goal_nodes[0]
            directed.append([g_idx, new_idx])
            gpair = [min(g_idx, new_idx), max(g_idx, new_idx)]
            if gpair not in manual_links:
                manual_links.append(gpair)
        new_thoughts.append(t)

    if duplicates_skipped > 0:
        print(f"[think] {len(new_thoughts)} new, {duplicates_skipped} novelty-rejected")
    return _finalize(nodes, d["threshold"], d["sim_mode"],
                     duplicates_skipped=duplicates_skipped, new_count=len(new_thoughts))


@graph_bp.route("/graph/recalc", methods=["POST"])
def graph_recalc():
    """Recompute edges and clusters with a new threshold (no generation)."""
    d = _p_data()
    nodes = _graph["nodes"]
    if not nodes:
        return jsonify({"error": "no thoughts"})
    return _finalize(nodes, d["threshold"], d["sim_mode"])


@graph_bp.route("/graph/add", methods=["POST"])
def graph_add():
    """Add a user-provided thought and recompute edges."""
    d = _p_data()
    text = d.get("text", "").strip()
    node_type = d.get("node_type", "auto")
    print(f"[add] text='{text[:40]}' node_type='{node_type}' raw={d.get('node_type')}")
    if not text:
        return jsonify({"error": "empty thought"})
    if node_type == "auto":
        node_type, auto_conf = _auto_type_and_confidence(text)
        new_idx = _add_node(text, depth=0, topic="", node_type=node_type, confidence=auto_conf)
    else:
        new_idx = _add_node(text, depth=0, topic="", node_type=node_type)

    nodes = _graph["nodes"]
    directed = _graph["edges"]["directed"]
    manual_links = _graph["edges"]["manual_links"]

    if node_type == "goal":
        # Store mode config on goal node and graph meta
        from .modes import get_mode
        mode_id = d.get("mode", "horizon")
        mode_cfg = get_mode(mode_id)
        nodes[new_idx]["mode"] = mode_id
        nodes[new_idx]["primitive"] = mode_cfg.get("primitive")
        nodes[new_idx]["strategy"] = mode_cfg.get("strategy")
        nodes[new_idx]["goal_type"] = mode_cfg.get("goal_type")
        _graph["meta"]["mode"] = mode_id
        for i, n in enumerate(nodes):
            if i == new_idx:
                continue
            if n.get("type") in ("hypothesis", "thought") and n.get("depth", 0) >= 0:
                directed.append([i, new_idx])
                pair = [min(i, new_idx), max(i, new_idx)]
                if pair not in manual_links:
                    manual_links.append(pair)

    return _finalize(nodes, d["threshold"], d["sim_mode"])


@graph_bp.route("/graph/remove", methods=["POST"])
def graph_remove():
    """Remove a thought by index and recompute edges."""
    d = _p_data()
    idx = int(d.get("index", -1))
    nodes = _graph["nodes"]
    if idx < 0 or idx >= len(nodes):
        return jsonify({"error": "invalid index"})
    _remove_node(idx)
    return _finalize(_graph["nodes"], d["threshold"], d["sim_mode"])


@graph_bp.route("/graph/confidence", methods=["POST"])
def graph_confidence():
    """Update confidence of a thought."""
    data = request.get_json(force=True)
    idx = int(data.get("index", -1))
    value = float(data.get("value", 0.5))
    value = max(0.0, min(1.0, value))
    nodes = _graph["nodes"]
    if idx < 0 or idx >= len(nodes):
        return jsonify({"error": "invalid index"})
    nodes[idx]["confidence"] = round(value, 2)
    return jsonify({"ok": True, "index": idx, "confidence": value})


@graph_bp.route("/graph/link", methods=["POST"])
def graph_link():
    """Manually add or remove an edge between two thoughts."""
    d = _p_data()
    a, b = int(d.get("a", -1)), int(d.get("b", -1))
    nodes = _graph["nodes"]
    if a < 0 or b < 0 or a >= len(nodes) or b >= len(nodes) or a == b:
        return jsonify({"error": "invalid indices"})
    pair = (min(a, b), max(a, b))
    manual_links = _graph["edges"]["manual_links"]
    manual_unlinks = _graph["edges"]["manual_unlinks"]
    edges_before = _compute_edges(nodes, d["threshold"], d["sim_mode"])
    has_edge = any(e["from"] == pair[0] and e["to"] == pair[1] for e in edges_before)
    if has_edge:
        if list(pair) in manual_links:
            manual_links.remove(list(pair))
        else:
            manual_unlinks.append(list(pair))
    else:
        if list(pair) in manual_unlinks:
            manual_unlinks.remove(list(pair))
        else:
            manual_links.append(list(pair))
    return _finalize(nodes, d["threshold"], d["sim_mode"])


@graph_bp.route("/graph/collapse", methods=["POST"])
def graph_collapse():
    """Collapse a cluster: generate summary, remove source nodes, add result."""
    d = _p_data()
    indices = d.get("cluster", [])
    collapse_mode = d.get("collapse_mode", "short")
    custom_max_tokens = d.get("max_tokens")
    user_prompt = d.get("collapse_prompt", "").strip()
    no_merge = d.get("no_merge", False)
    collapse_override = d.get("collapse_override", "").strip()
    nodes = _graph["nodes"]
    topic = _graph["meta"]["topic"]

    if not indices or not nodes:
        return jsonify({"error": "no cluster to collapse"})

    if collapse_override:
        # Text already generated via Generation Studio — skip generation
        text = collapse_override
        ent = {"avg": 0, "unc": 0, "tokens": []}
    else:
        cluster_texts = [nodes[i]["text"] for i in indices if i < len(nodes)]
        if collapse_mode == "long":
            system = _p(d["lang"], "collapse_long")
            instruction = _p(d["lang"], "write_long")
            max_tokens = 2000
        else:
            system = _p(d["lang"], "collapse")
            instruction = _p(d["lang"], "write_para")
            max_tokens = 800
        if custom_max_tokens:
            max_tokens = int(custom_max_tokens)
        # Smart truncation: fit texts into context window
        from .api_backend import _settings
        ctx_size = _settings.get("local_ctx", 8192)
        token_budget = ctx_size - 200 - max_tokens  # leave room for system + generation

        user = f"{_p(d['lang'], 'topic')}: {topic}\n\n{_p(d['lang'], 'ideas')}:\n"

        # Sort by confidence (highest first) so we keep the best if truncating
        indexed_texts = sorted(enumerate(cluster_texts), key=lambda x: -(nodes[indices[x[0]]].get("confidence", 0.5) if x[0] < len(indices) and indices[x[0]] < len(nodes) else 0))

        # Rough token count: len(text) / 3 for multilingual
        used_tokens = len(user) // 3
        selected_texts = []
        for orig_idx, t in indexed_texts:
            t_tokens = len(t) // 3 + 2  # +2 for "- " prefix
            if used_tokens + t_tokens > token_budget:
                continue  # skip, doesn't fit
            selected_texts.append(t)
            used_tokens += t_tokens

        if len(selected_texts) < len(cluster_texts):
            print(f"[collapse] context fit: {len(selected_texts)}/{len(cluster_texts)} texts ({used_tokens}/{token_budget} est. tokens)")

        user += "\n".join(f"- {t}" for t in selected_texts)
        if user_prompt:
            user += f"\n\n{user_prompt}"
        else:
            user += f"\n\n{instruction}"

        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        text, ent = _graph_generate(messages, max_tokens=max_tokens, temp=d["temp"], top_k=d["top_k"], seed=d["seed"])

    valid_indices = [i for i in indices if i < len(nodes)]

    if no_merge:
        # Keep originals, add collapsed as new node linked FROM source nodes
        collapsed_topic = next((nodes[i]["topic"] for i in valid_indices if nodes[i]["topic"]), topic)
        max_depth = max((nodes[i]["depth"] for i in valid_indices), default=0)
        collapsed_conf = sum(nodes[i]["confidence"] for i in valid_indices) / max(len(valid_indices), 1)
        new_idx = _add_node(text, depth=max_depth + 1, topic=collapsed_topic, entropy=ent, confidence=round(collapsed_conf, 2))
        # Store lineage — all sources this node was collapsed from (for ancestry checks)
        lineage = set(valid_indices)
        for i in valid_indices:
            lineage |= set(nodes[i].get("collapsed_from", []))
        nodes[new_idx]["collapsed_from"] = sorted(lineage)
        directed = _graph["edges"]["directed"]
        manual_links = _graph["edges"]["manual_links"]
        # Link each source → collapsed (traceable chain)
        for src_idx in valid_indices:
            directed.append([src_idx, new_idx])
            pair = [min(src_idx, new_idx), max(src_idx, new_idx)]
            if pair not in manual_links:
                manual_links.append(pair)
    else:
        # Normal mode: remove source nodes, add collapsed result
        min_depth = min((nodes[i]["depth"] for i in valid_indices), default=0)
        collapsed_topic = next((nodes[i]["topic"] for i in valid_indices if nodes[i]["topic"]), topic)
        # Average confidence of collapsed nodes
        collapsed_conf = sum(nodes[i]["confidence"] for i in valid_indices) / max(len(valid_indices), 1)
        # Collect lineage before removing nodes
        lineage = set(valid_indices)
        for i in valid_indices:
            lineage |= set(nodes[i].get("collapsed_from", []))
        for i in sorted(valid_indices, reverse=True):
            nodes.pop(i)
        # Reassign ids after removal
        for k, nd in enumerate(nodes):
            nd["id"] = k
        _remap_edges(valid_indices)
        new_idx = _add_node(text, depth=min_depth, topic=collapsed_topic,
                            entropy=ent, confidence=collapsed_conf)
        # Note: lineage indices are pre-removal, but that's OK for ancestry tracking
        nodes[new_idx]["collapsed_from"] = sorted(lineage)
        # Link collapsed node to topic root and goal
        directed = _graph["edges"]["directed"]
        manual_links = _graph["edges"]["manual_links"]
        for i, nd in enumerate(nodes):
            if nd["depth"] == -1 and nd["topic"] == collapsed_topic:
                directed.append([i, new_idx])
                pair = [min(i, new_idx), max(i, new_idx)]
                if pair not in manual_links:
                    manual_links.append(pair)
                break
        # Also link goal → collapsed (maintain goal connectivity)
        for i, nd in enumerate(nodes):
            if nd.get("type") == "goal":
                directed.append([i, new_idx])
                pair = [min(i, new_idx), max(i, new_idx)]
                if pair not in manual_links:
                    manual_links.append(pair)
                break

    return _finalize(nodes, d["threshold"], d["sim_mode"], text=text)


@graph_bp.route("/graph/sync", methods=["POST"])
def graph_sync():
    """Restore graph state (used by undo)."""
    d = _p_data()

    # Support both new node-object format and legacy parallel-array format
    if "nodes" in d:
        incoming_nodes = d["nodes"]
        if incoming_nodes and isinstance(incoming_nodes[0], str):
            # Legacy: list of strings
            topic = d.get("topic", _graph["meta"].get("topic", ""))
            incoming_nodes = [_make_node(i, t, topic=topic) for i, t in enumerate(incoming_nodes)]
        _graph["nodes"] = incoming_nodes
    elif "thoughts" in d:
        # Legacy parallel-array format
        thoughts = d["thoughts"]
        topic = d.get("topic", _graph["meta"].get("topic", ""))
        ents = d.get("entropies", [])
        depths = d.get("depths", [])
        topics = d.get("topics", [])
        confs = d.get("confidences", [])
        _graph["nodes"] = []
        for i, t in enumerate(thoughts):
            _graph["nodes"].append(_make_node(
                i, t,
                depth=depths[i] if i < len(depths) else 0,
                topic=topics[i] if i < len(topics) else topic,
                entropy=ents[i] if i < len(ents) else None,
                confidence=confs[i] if i < len(confs) else 0.5,
            ))
    _ensure_node_fields(_graph["nodes"])

    # Restore edges
    if "edges" in d and isinstance(d["edges"], dict) and "manual_links" in d["edges"]:
        # New format: edges is the edges dict
        _graph["edges"]["manual_links"] = d["edges"].get("manual_links", [])
        _graph["edges"]["manual_unlinks"] = d["edges"].get("manual_unlinks", [])
        _graph["edges"]["directed"] = d["edges"].get("directed", [])
    else:
        # Compat: directed_edges / manual_links at top level
        _graph["edges"]["manual_links"] = d.get("manual_links", [])
        _graph["edges"]["manual_unlinks"] = d.get("manual_unlinks", [])
        _graph["edges"]["directed"] = d.get("directed_edges", _graph["edges"].get("directed", []))

    _graph["meta"]["hub_nodes"] = set(d.get("hub_nodes", list(_graph["meta"].get("hub_nodes", set()))))
    if "topic" in d:
        _graph["meta"]["topic"] = d["topic"]

    # If saved computed edges/clusters provided, use them directly (undo restore)
    if "edges" in d and isinstance(d["edges"], list) and "clusters" in d:
        _graph["embeddings"] = []
        nodes = _graph["nodes"]
        traps = _detect_traps(nodes, d["edges"])
        alpha_beta = _compute_alpha_beta(nodes)
        return jsonify({
            "nodes": nodes,
            "edges": d["edges"],
            "clusters": d["clusters"],
            "directed_edges": _graph["edges"].get("directed", []),
            "hub_nodes": list(_graph["meta"].get("hub_nodes", set())),
            "traps": traps,
            "alpha_beta": {str(k): v for k, v in alpha_beta.items()},
        })

    # Otherwise recompute
    _graph["embeddings"] = []
    return _finalize(_graph["nodes"], d["threshold"], d["sim_mode"])


@graph_bp.route("/graph/expand", methods=["POST"])
def graph_expand():
    """Generate child ideas branching from a specific thought (same topic, new angles)."""
    d = _p_data()
    idx = int(d.get("index", -1))
    n = int(d.get("n", 3))
    maxtok_expand = int(d.get("maxtok_expand", 120))
    nodes = _graph["nodes"]

    if idx < 0 or idx >= len(nodes):
        return jsonify({"error": "invalid index"})

    source = nodes[idx]["text"]
    topic = _graph["meta"].get("topic", "")
    new_thoughts = []
    parent_depth = nodes[idx]["depth"]
    parent_topic = nodes[idx]["topic"] or topic

    system = _p(d["lang"], "think")

    attempts = 0
    while len(new_thoughts) < n and attempts < n * 3:
        attempts += 1
        user = f"{_p(d['lang'], 'topic')}: {topic}\n{_p(d['lang'], 'source')}: {source}"
        if new_thoughts:
            user += f"\n{_p(d['lang'], 'already_gen')}:\n" + "\n".join(f"- {t}" for t in new_thoughts)
        user += f"\n{_p(d['lang'], 'branch')}"

        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        t, ent = _graph_generate(messages, max_tokens=maxtok_expand, temp=d["temp"], top_k=d["top_k"], seed=d["seed"])
        t = _clean_thought(t, topic)

        if not t or len(t) < 10:
            continue
        if any(t.lower() == nd["text"].lower() for nd in nodes):
            continue
        avg_ent = ent.get("avg", 0.5) if ent else 0.5
        conf = round(max(0.2, min(0.9, 1.0 - avg_ent)), 2)
        _add_node(t, depth=parent_depth + 1, topic=parent_topic, entropy=ent, confidence=conf)
        new_thoughts.append(t)

    # Track directed edges for expand (parent → child) + auto-evidence
    manual_links = _graph["edges"]["manual_links"]
    directed = _graph["edges"]["directed"]
    parent_text = nodes[idx]["text"]
    base_idx = len(nodes) - len(new_thoughts)
    for j in range(len(new_thoughts)):
        new_idx = base_idx + j
        pair = [min(idx, new_idx), max(idx, new_idx)]
        if pair not in manual_links:
            manual_links.append(pair)
        directed.append([idx, new_idx])
        # Auto-evidence: child as evidence for parent hypothesis
        if nodes[idx].get("type") in ("hypothesis", "thought"):
            child_text = nodes[new_idx]["text"]
            rel, strength = _auto_evidence_relation(parent_text, child_text)
            nodes[new_idx]["evidence_relation"] = rel
            nodes[new_idx]["evidence_strength"] = strength
            nodes[new_idx]["evidence_target"] = idx
            nodes[new_idx]["type"] = "evidence"
            # Live Bayesian update if enabled
            from .api_backend import _settings
            if _settings.get("live_bayes"):
                old_conf = nodes[idx]["confidence"]
                p_e_h = strength if rel == "supports" else (1 - strength)
                p_e_nh = (1 - strength) if rel == "supports" else strength
                nodes[idx]["confidence"] = _bayesian_update(old_conf, p_e_h, p_e_nh)
                print(f"[auto-evidence] expand #{idx}→#{new_idx}: {rel} str={strength} conf {old_conf:.2f}→{nodes[idx]['confidence']:.2f}")
            else:
                print(f"[auto-evidence] expand #{idx}→#{new_idx}: {rel} str={strength} (conf unchanged at {nodes[idx]['confidence']:.2f})")

    return _finalize(nodes, d["threshold"], d["sim_mode"])


@graph_bp.route("/graph/elaborate", methods=["POST"])
def graph_elaborate():
    """Generate deeper ideas that elaborate on a specific thought (the source becomes a hub)."""
    d = _p_data()
    idx = int(d.get("index", -1))
    n = int(d.get("n", 3))
    direction = d.get("direction", "").strip()
    maxtok_elaborate = int(d.get("maxtok_elaborate", 120))
    nodes = _graph["nodes"]

    if idx < 0 or idx >= len(nodes):
        return jsonify({"error": "invalid index"})

    source = nodes[idx]["text"]
    topic = _graph["meta"].get("topic", "")
    new_thoughts = []
    parent_depth = nodes[idx]["depth"]
    parent_topic = nodes[idx]["topic"] or topic

    system = _p(d["lang"], "think")

    attempts = 0
    while len(new_thoughts) < n and attempts < n * 3:
        attempts += 1
        user = f"{_p(d['lang'], 'topic')}: {topic}\n{_p(d['lang'], 'elaborate')}: {source}"
        if direction:
            user += f"\n{_p(d['lang'], 'direction')}: {direction}"
        if new_thoughts:
            user += f"\n{_p(d['lang'], 'already_elab')}:\n" + "\n".join(f"- {t}" for t in new_thoughts)
        user += f"\n{_p(d['lang'], 'deeper')}"

        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        t, ent = _graph_generate(messages, max_tokens=maxtok_elaborate, temp=d["temp"], top_k=d["top_k"], seed=d["seed"])
        t = _clean_thought(t, topic)

        if not t or len(t) < 10:
            continue
        if any(t.lower() == nd["text"].lower() for nd in nodes):
            continue
        avg_ent = ent.get("avg", 0.5) if ent else 0.5
        conf = round(max(0.2, min(0.9, 1.0 - avg_ent)), 2)
        _add_node(t, depth=parent_depth + 1, topic=parent_topic, entropy=ent, confidence=conf)
        new_thoughts.append(t)

    # Force-link new thoughts to source and track directed edges
    manual_links = _graph["edges"]["manual_links"]
    directed = _graph["edges"]["directed"]
    hubs = _graph["meta"].setdefault("hub_nodes", set())
    source_idx = idx
    hubs.add(source_idx)
    parent_text = nodes[idx]["text"]
    base_idx = len(nodes) - len(new_thoughts)
    for j in range(len(new_thoughts)):
        new_idx = base_idx + j
        pair = [min(source_idx, new_idx), max(source_idx, new_idx)]
        if pair not in manual_links:
            manual_links.append(pair)
        directed.append([source_idx, new_idx])
        # Auto-evidence: elaborate child as evidence for parent
        if nodes[idx].get("type") in ("hypothesis", "thought"):
            child_text = nodes[new_idx]["text"]
            rel, strength = _auto_evidence_relation(parent_text, child_text)
            nodes[new_idx]["evidence_relation"] = rel
            nodes[new_idx]["evidence_strength"] = strength
            nodes[new_idx]["evidence_target"] = idx
            nodes[new_idx]["type"] = "evidence"
            # Live Bayesian update if enabled
            from .api_backend import _settings as _s2
            if _s2.get("live_bayes"):
                old_conf = nodes[idx]["confidence"]
                p_e_h = strength if rel == "supports" else (1 - strength)
                p_e_nh = (1 - strength) if rel == "supports" else strength
                nodes[idx]["confidence"] = _bayesian_update(old_conf, p_e_h, p_e_nh)
                print(f"[auto-evidence] elaborate #{idx}→#{new_idx}: {rel} str={strength} conf {old_conf:.2f}→{nodes[idx]['confidence']:.2f}")
            else:
                print(f"[auto-evidence] elaborate #{idx}→#{new_idx}: {rel} str={strength} (conf unchanged at {nodes[idx]['confidence']:.2f})")

    return _finalize(nodes, d["threshold"], d["sim_mode"])


# ── Generation Studio ────────────────────────────────────────────────────────

@graph_bp.route("/graph/studio/generate", methods=["POST"])
def graph_studio_generate():
    """Generate one variant for Generation Studio.

    Supports modes: rephrase, elaborate_preview, expand_preview, collapse_preview, freeform.
    Returns { text, entropy_info } without modifying graph state.
    """
    data = request.get_json(force=True)
    mode = data.get("mode", "rephrase")
    source_text = data.get("source_text", "")
    instruction = data.get("instruction", "")
    temp = float(data.get("temp", 0.9))
    top_k = int(data.get("top_k", 40))
    max_tokens = int(data.get("max_tokens", 1000))
    seed = int(data.get("seed", -1))
    lang = data.get("lang", "en")
    topic = _graph["meta"].get("topic", "")

    if mode == "rephrase":
        system = "You rephrase the given text. Keep the core meaning. Answer with ONLY the rephrased text, nothing else."
        user = f"Original: {source_text}"
        if instruction:
            user += f"\nInstruction: {instruction}"
        user += "\nRephrased:"

    elif mode == "elaborate_preview":
        system = _p(lang, "think")
        user = f"{_p(lang, 'topic')}: {topic}\n{_p(lang, 'elaborate')}: {source_text}"
        if instruction:
            user += f"\n{_p(lang, 'direction')}: {instruction}"
        user += f"\n{_p(lang, 'deeper')}"

    elif mode == "expand_preview":
        system = _p(lang, "think")
        user = f"{_p(lang, 'topic')}: {topic}\n{_p(lang, 'parent')}: {source_text}"
        if instruction:
            user += f"\nFocus: {instruction}"
        user += f"\n{_p(lang, 'new_idea')}"

    elif mode == "collapse_preview":
        system = _p(lang, "collapse")
        ideas = data.get("ideas", [])
        user = f"{_p(lang, 'topic')}: {topic}\n\n{_p(lang, 'ideas')}:\n"
        user += "\n".join(f"- {idea}" for idea in ideas)
        if instruction:
            user += f"\n{instruction}"

    else:  # freeform
        system = "You are a helpful assistant."
        user = instruction if instruction else source_text

    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    text, ent = _graph_generate(messages, max_tokens=max_tokens, temp=temp, top_k=top_k, seed=seed)
    text = _clean_thought(text, topic) if mode != "collapse_preview" else text

    # Clean <think> tags from collapse too
    if mode == "collapse_preview" and "<think>" in text:
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    return jsonify({"text": text, "entropy": ent})


@graph_bp.route("/graph/studio/apply-rephrase", methods=["POST"])
def graph_studio_apply_rephrase():
    """Apply a rephrase result: replace the text of a node, keep all links/positions."""
    d = _p_data()
    idx = int(d.get("index", -1))
    new_text = d.get("text", "").strip()

    nodes = _graph["nodes"]
    if idx < 0 or idx >= len(nodes):
        return jsonify({"error": "invalid index"})
    if not new_text:
        return jsonify({"error": "empty text"})

    nodes[idx]["text"] = new_text
    # Invalidate embedding cache for this node
    cache = _graph.get("embeddings", [])
    if idx < len(cache):
        cache[idx] = None

    return _finalize(nodes, d["threshold"], d["sim_mode"])


@graph_bp.route("/graph/studio/apply-child", methods=["POST"])
def graph_studio_apply_child():
    """Apply an elaborate/expand result: add as child node linked to parent with directed edge."""
    d = _p_data()
    parent_idx = int(d.get("index", -1))
    new_text = d.get("text", "").strip()
    child_type = d.get("type", "elaborate")  # "elaborate" or "expand"

    nodes = _graph["nodes"]
    topic = _graph["meta"].get("topic", "")
    if parent_idx < 0 or parent_idx >= len(nodes):
        return jsonify({"error": "invalid parent index"})
    if not new_text:
        return jsonify({"error": "empty text"})

    # Add the new thought
    parent_depth = nodes[parent_idx]["depth"]
    parent_topic = nodes[parent_idx]["topic"] or topic
    new_idx = _add_node(new_text, depth=parent_depth + 1, topic=parent_topic)

    # Create directed edge and manual link
    manual_links = _graph["edges"]["manual_links"]
    directed = _graph["edges"]["directed"]
    hubs = _graph["meta"].setdefault("hub_nodes", set())

    pair = [min(parent_idx, new_idx), max(parent_idx, new_idx)]
    if pair not in manual_links:
        manual_links.append(pair)
    directed.append([parent_idx, new_idx])

    if child_type == "elaborate":
        hubs.add(parent_idx)

    return _finalize(nodes, d["threshold"], d["sim_mode"])


# --------------- Smart DC (Dialectical Convergence) ---------------

@graph_bp.route("/graph/smartdc", methods=["POST"])
def graph_smartdc():
    """Smart DC: generate thesis, antithesis, neutral → centroid → synthesis."""
    d = _p_data()
    node_idx = int(d.get("index", -1))

    nodes = _graph["nodes"]
    if node_idx < 0 or node_idx >= len(nodes):
        return jsonify({"error": "invalid node index"})

    statement = nodes[node_idx]["text"]
    evidence_context = d.get("evidence_context", [])
    pump_context = d.get("pump_context")

    # If pump_context provided, override statement with bridge + A + B context
    if pump_context:
        statement = (
            f"Связь между двумя идеями:\n"
            f"A: {pump_context.get('node_a', '')}\n"
            f"B: {pump_context.get('node_b', '')}\n"
            f"Найденный мост: {pump_context.get('bridge', '')}"
        )

    # Phase 1: Divergence — generate 3 poles
    context_block = ""
    if evidence_context:
        context_block = "\n\nExisting evidence:\n" + "\n".join(evidence_context[:5])

    poles = []
    for role_key in ["dc_thesis", "dc_antithesis", "dc_neutral"]:
        messages = [
            {"role": "system", "content": _p(d["lang"], role_key)},
            {"role": "user", "content": f"{_p(d['lang'], 'dc_statement')}: {statement}{context_block}"},
        ]
        text, ent = _graph_generate(messages, max_tokens=200, temp=d["temp"], top_k=d["top_k"], seed=d["seed"])
        print(f"[smartdc] {role_key}: {text[:80]}...")
        poles.append({"role": role_key, "text": text, "entropy": ent})

    # Phase 2: Convergence — synthesize from 3 poles (BEFORE embeddings to keep KV cache clean)
    synthesis_messages = [
        {"role": "system", "content": _p(d["lang"], "dc_synthesis")},
        {"role": "user", "content":
            f"{_p(d['lang'], 'dc_statement')}: {statement}\n\n"
            f"{_p(d['lang'], 'dc_for')}:\n{poles[0]['text']}\n\n"
            f"{_p(d['lang'], 'dc_against')}:\n{poles[1]['text']}\n\n"
            f"{_p(d['lang'], 'dc_context')}:\n{poles[2]['text']}"
        },
    ]
    synthesis_text, synthesis_ent = _graph_generate(synthesis_messages, max_tokens=300, temp=0.7, top_k=d["top_k"], seed=d["seed"])
    print(f"[smartdc] synthesis: {synthesis_text[:80]}...")

    # Phase 3: Get embeddings for centroid confidence
    from .api_backend import api_get_embedding
    pole_embs = []
    syn_emb = None
    try:
        for p in poles:
            emb = api_get_embedding(p["text"])
            pole_embs.append(emb if emb and len(emb) > 0 else None)
        syn_emb_raw = api_get_embedding(synthesis_text)
        syn_emb = syn_emb_raw if syn_emb_raw and len(syn_emb_raw) > 0 else None
        pole_embs = [e for e in pole_embs if e is not None]
        print(f"[smartdc] embeddings: {len(pole_embs)}/3 poles + {'yes' if syn_emb else 'no'} synthesis")
    except Exception as e:
        print(f"[smartdc] embedding error: {e}")
        pole_embs = []
        syn_emb = None
    new_confidence = 0.5
    centroid_distance = -1

    # Per-pole similarity to statement (for bridge quality assessment)
    pole_confidences = {}
    statement_emb = None
    if pole_embs and len(pole_embs) >= 3:
        try:
            statement_emb = api_get_embedding(statement)
        except Exception:
            pass

    if pole_embs and syn_emb is not None:
        centroid = np.mean(pole_embs, axis=0)
        syn_arr = np.array(syn_emb)
        cent_arr = np.array(centroid)
        dot = np.dot(syn_arr, cent_arr)
        norm = np.linalg.norm(syn_arr) * np.linalg.norm(cent_arr)
        if norm > 0:
            centroid_distance = float(dot / norm)
            new_confidence = round(min(0.95, max(0.3, centroid_distance)), 2)

        # Per-pole confidence relative to statement
        if statement_emb:
            stmt_arr = np.array(statement_emb, dtype=np.float32)
            role_names = ["thesis", "antithesis", "neutral"]
            for i, emb in enumerate(pole_embs[:3]):
                sim = float(cosine_similarity(np.array(emb, dtype=np.float32), stmt_arr))
                pole_confidences[role_names[i]] = round(sim, 3)

            # Lean: thesis vs antithesis
            t_conf = pole_confidences.get("thesis", 0.5)
            a_conf = pole_confidences.get("antithesis", 0.5)
            lean = round(t_conf - a_conf, 3)  # positive = thesis wins, negative = antithesis wins
            pole_confidences["lean"] = lean
            # Tension: how close thesis and antithesis are (high = genuine debate)
            if len(pole_embs) >= 2:
                tension = float(cosine_similarity(
                    np.array(pole_embs[0], dtype=np.float32),
                    np.array(pole_embs[1], dtype=np.float32)))
                pole_confidences["tension"] = round(tension, 3)

        print(f"[smartdc] confidence from centroid: {new_confidence} (distance={centroid_distance:.3f})")
        if pole_confidences:
            print(f"[smartdc] poles: thesis={pole_confidences.get('thesis','?')} anti={pole_confidences.get('antithesis','?')} lean={pole_confidences.get('lean','?')} tension={pole_confidences.get('tension','?')}")
    else:
        syn_ent = synthesis_ent.get("avg", 1.0)
        pole_ents = [p["entropy"].get("avg", 1.0) for p in poles]
        avg_pole_ent = sum(pole_ents) / len(pole_ents)
        combined_ent = syn_ent * 0.7 + avg_pole_ent * 0.3
        new_confidence = round(max(0.3, min(0.95, 1.0 - combined_ent)), 2)
        print(f"[smartdc] confidence from entropy (fallback): {new_confidence}")

    return jsonify({
        "poles": [
            {"role": "thesis", "text": poles[0]["text"], "entropy": poles[0]["entropy"],
             "confidence": pole_confidences.get("thesis")},
            {"role": "antithesis", "text": poles[1]["text"], "entropy": poles[1]["entropy"],
             "confidence": pole_confidences.get("antithesis")},
            {"role": "neutral", "text": poles[2]["text"], "entropy": poles[2]["entropy"],
             "confidence": pole_confidences.get("neutral")},
        ],
        "synthesis": synthesis_text,
        "synthesis_entropy": synthesis_ent,
        "confidence": new_confidence,
        "centroid_distance": round(centroid_distance, 3),
        "pole_analysis": pole_confidences,
        "original_idx": node_idx,
    })


# --------------- Pump (Накачка) ---------------

@graph_bp.route("/graph/pump", methods=["POST"])
def graph_pump():
    """Find the hidden axis between two ideas via bilateral expansion."""
    from .pump_logic import pump

    d = _p_data()
    node_a = int(d.get("node_a", -1))
    node_b = int(d.get("node_b", -1))
    max_iter = int(d.get("max_iterations", 3))

    result = pump(node_a, node_b, max_iterations=max_iter,
                  lang=d["lang"], temp=d["temp"], top_k=d["top_k"])
    return jsonify(result)


# --------------- Horizon ---------------

@graph_bp.route("/graph/horizon-params")
def graph_horizon_params():
    """Get current Horizon LLM params for manual operations."""
    from .horizon import CognitiveHorizon, create_horizon
    horizon_data = _graph.get("_horizon")
    if horizon_data:
        h = CognitiveHorizon.from_dict(horizon_data)
    else:
        mode_id = _graph["meta"].get("mode", "horizon")
        h = create_horizon(mode_id)
    params = h.to_llm_params()
    print(f"[horizon manual] state={h.state} precision={h.precision:.2f} temp={params['temperature']:.2f} top_k={params['top_k']}")
    return jsonify(params)


# --------------- Horizon feedback ---------------

@graph_bp.route("/graph/horizon-feedback", methods=["POST"])
def graph_horizon_feedback():
    """Store feedback for CognitiveHorizon. Applied on next tick."""
    data = request.get_json(force=True)
    _graph["_horizon_feedback"] = {
        "surprise": data.get("surprise"),
        "gradient": data.get("gradient"),
        "novelty": data.get("novelty"),
        "phase": data.get("phase"),
    }
    return jsonify({"ok": True})


# --------------- tick() — phase-based automatic thinking ---------------

@graph_bp.route("/graph/tick", methods=["POST"])
def graph_tick():
    """Phase-based automatic thinking: EXPLORE → DEEPEN → VERIFY → META → SYNTHESIZE."""
    from .thinking import tick

    d = _p_data()
    stable_threshold = float(d.get("stable_threshold", 0.8))
    force_collapse = d.get("force_collapse", False)
    max_meta = int(d.get("max_meta", 2))
    min_hyp = int(d.get("min_hyp", 5))

    nodes = _graph["nodes"]
    edges = _compute_edges(nodes, d["threshold"], d["sim_mode"])

    result = tick(nodes, edges, _graph,
                  threshold=d["threshold"], stable_threshold=stable_threshold,
                  force_collapse=force_collapse,
                  max_meta=max_meta, min_hyp=min_hyp)
    return jsonify(result)


# --------------- Bayesian Evidence ---------------

@graph_bp.route("/graph/set-type", methods=["POST"])
def graph_set_type():
    """Set node type (thought, hypothesis, evidence, fact, question)."""
    data = request.get_json(force=True)
    idx = int(data.get("index", -1))
    node_type = data.get("type", "thought")
    nodes = _graph["nodes"]
    if idx < 0 or idx >= len(nodes):
        return jsonify({"error": "invalid index"})
    nodes[idx]["type"] = node_type
    return jsonify({"ok": True, "type": node_type})


@graph_bp.route("/graph/add-evidence", methods=["POST"])
def graph_add_evidence():
    """Add evidence node linked to a hypothesis, update hypothesis confidence via Bayes."""
    d = _p_data()
    hyp_idx = int(d.get("hypothesis", -1))
    evidence_text = d.get("text", "").strip()
    relation = d.get("relation", "supports")
    strength = float(d.get("strength", 0.7))

    nodes = _graph["nodes"]
    if hyp_idx < 0 or hyp_idx >= len(nodes):
        return jsonify({"error": "invalid hypothesis index"})
    if not evidence_text:
        return jsonify({"error": "empty evidence text"})

    hyp = nodes[hyp_idx]
    # Auto-convert to hypothesis if adding evidence to a thought
    if hyp.get("type", "thought") == "thought":
        hyp["type"] = "hypothesis"
    prior = hyp["confidence"]

    # Bayesian update: P(H|E) = P(E|H) * P(H) / P(E)
    if relation == "supports":
        p_e_h = strength       # likely to see this evidence if H true
        p_e_nh = 1 - strength  # unlikely if H false
    else:
        p_e_h = 1 - strength   # unlikely to see this evidence if H true
        p_e_nh = strength      # likely if H false

    posterior = _bayesian_update(prior, p_e_h, p_e_nh)

    # Update hypothesis confidence
    old_conf = hyp["confidence"]
    hyp["confidence"] = posterior

    # Add evidence node
    parent_topic = hyp.get("topic", "")
    parent_depth = hyp.get("depth", 0)
    ev_idx = _add_node(evidence_text, depth=parent_depth + 1, topic=parent_topic,
                       confidence=strength, node_type="evidence")
    # Store relation on evidence node for α/β computation
    nodes[ev_idx]["evidence_relation"] = relation
    nodes[ev_idx]["evidence_strength"] = strength
    nodes[ev_idx]["evidence_target"] = hyp_idx

    # Create directed edge hyp → evidence
    directed = _graph["edges"]["directed"]
    directed.append([hyp_idx, ev_idx])
    manual_links = _graph["edges"]["manual_links"]
    pair = [min(hyp_idx, ev_idx), max(hyp_idx, ev_idx)]
    if pair not in manual_links:
        manual_links.append(pair)

    print(f"[bayes] hyp #{hyp_idx} '{hyp['text'][:40]}': {old_conf:.2f} → {posterior:.3f} ({relation}, strength={strength})")

    return _finalize(nodes, d["threshold"], d["sim_mode"], bayes_update={
        "hypothesis": hyp_idx,
        "prior": old_conf,
        "posterior": posterior,
        "relation": relation,
        "evidence_idx": ev_idx,
    })


# --------------- Transition Prob: Navigate (Hebb) ---------------

@graph_bp.route("/graph/navigate", methods=["POST"])
def graph_navigate():
    """Hebb learning: strengthen transition_prob on edge when user navigates from→to."""
    data = request.get_json(force=True)
    from_idx = int(data.get("from", -1))
    to_idx = int(data.get("to", -1))
    lr = 0.05

    nodes = _graph["nodes"]
    if from_idx < 0 or to_idx < 0 or from_idx >= len(nodes) or to_idx >= len(nodes):
        return jsonify({"ok": False})
    if from_idx == to_idx:
        return jsonify({"ok": False})

    tp_overrides = _graph.setdefault("tp_overrides", {})
    key = f"{from_idx},{to_idx}"

    current = tp_overrides.get(key, 0.5)
    new_val = current + lr * (1.0 - current)
    tp_overrides[key] = round(new_val, 4)

    # Update last_accessed on the target node
    nodes[to_idx]["last_accessed"] = datetime.now(timezone.utc).isoformat()

    return jsonify({"ok": True, "tp": tp_overrides[key]})


# --------------- Random Walk ---------------

@graph_bp.route("/graph/walk", methods=["POST"])
def graph_walk():
    """Random Walk simulation from a start node."""
    data = request.get_json(force=True)
    start = int(data.get("start", 0))
    steps = int(data.get("steps", 5))
    runs = int(data.get("runs", 50))
    threshold = float(data.get("threshold", 0.91))
    sim_mode = data.get("sim_mode", "embedding")

    nodes = _graph["nodes"]
    if start < 0 or start >= len(nodes):
        return jsonify({"error": "invalid start"})

    # Use lower threshold for walk so it can navigate even when display edges are sparse
    walk_threshold = max(0.3, threshold - 0.3)
    edges = _compute_edges(nodes, walk_threshold, sim_mode)

    adj = defaultdict(list)
    for e in edges:
        adj[e["from"]].append((e["to"], e.get("tp", 0)))
        adj[e["to"]].append((e["from"], e.get("tp_rev", 0)))

    endpoint_counts = defaultdict(int)
    paths = []
    for _ in range(runs):
        current = start
        prev = -1
        path = [current]
        for _ in range(steps):
            neighbors = adj.get(current, [])
            if not neighbors:
                break
            # No-backtrack: filter out previous node if alternatives exist
            filtered = [(t, p) for t, p in neighbors if t != prev]
            if not filtered:
                filtered = neighbors  # stuck: allow backtrack
            targets, probs = zip(*filtered)
            total = sum(probs)
            if total == 0:
                next_node = random.choice(targets)
            else:
                r = random.random() * total
                cumulative = 0
                next_node = targets[-1]
                for t, p in zip(targets, probs):
                    cumulative += p
                    if r <= cumulative:
                        next_node = t
                        break
            prev = current
            current = next_node
            path.append(current)
        endpoint_counts[current] += 1
        paths.append(path)

    top_endpoints = sorted(endpoint_counts.items(), key=lambda x: -x[1])[:3]
    best_endpoint = top_endpoints[0][0] if top_endpoints else start
    best_path = next((p for p in paths if p[-1] == best_endpoint), [start])

    result_endpoints = []
    for idx, count in top_endpoints:
        result_endpoints.append({
            "idx": idx,
            "count": count,
            "pct": round(count / runs * 100),
            "text": nodes[idx]["text"][:80] if idx < len(nodes) else ""
        })

    return jsonify({
        "path": best_path,
        "endpoints": result_endpoints,
        "runs": runs,
        "steps": steps
    })


# --------------- Save / Load / AutoSave ---------------

import json as _json
from pathlib import Path as _Path

def _graphs_dir():
    d = _Path(__file__).parent.parent / "graphs"
    d.mkdir(exist_ok=True)
    return d


def _slugify(text: str) -> str:
    """Simple slug from topic text (ASCII + cyrillic safe)."""
    import re as _re
    s = text.strip().lower()[:60]
    s = _re.sub(r'[^\w\s-]', '', s)
    s = _re.sub(r'[\s_]+', '_', s).strip('_')
    return s or "untitled"


@graph_bp.route("/graph/list", methods=["GET"])
def graph_list():
    """List saved graphs."""
    graphs = []
    for f in sorted(_graphs_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = _json.loads(f.read_text(encoding="utf-8"))
            nodes = data.get("nodes", data.get("thoughts", []))
            graphs.append({
                "name": f.stem,
                "topic": data.get("topic", ""),
                "nodes_count": len(nodes),
                "modified": f.stat().st_mtime,
            })
        except Exception:
            pass
    return jsonify({"graphs": graphs})


@graph_bp.route("/graph/save", methods=["POST"])
def graph_save():
    """Save current graph to server."""
    data = request.get_json(force=True)
    name = data.get("name", "").strip()

    # Build save payload from frontend data + backend state
    topic = data.get("topic", _graph["meta"].get("topic", ""))
    if not name:
        name = _slugify(topic) if topic else "untitled"

    # Sanitize name
    name = name.replace("/", "_").replace("\\", "_").replace("..", "_")

    save_data = {
        "topic": topic,
        "nodes": data.get("nodes", _graph["nodes"]),
        "edges": data.get("edges", []),
        "clusters": data.get("clusters", []),
        "positions": data.get("positions", []),
        "collapsed": data.get("collapsed", []),
        "hubs": data.get("hubs", []),
        "directed": data.get("directed", _graph["edges"].get("directed", [])),
        "manual_links": data.get("manual_links", _graph["edges"].get("manual_links", [])),
        "manual_unlinks": data.get("manual_unlinks", _graph["edges"].get("manual_unlinks", [])),
        "threshold": data.get("threshold", 0.91),
        "sim_mode": data.get("sim_mode", "embedding"),
    }

    path = _graphs_dir() / f"{name}.json"
    path.write_text(_json.dumps(save_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify({"ok": True, "name": name, "path": str(path)})


@graph_bp.route("/graph/load", methods=["POST"])
def graph_load():
    """Load a saved graph from server."""
    data = request.get_json(force=True)
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "no name specified"})

    path = _graphs_dir() / f"{name}.json"
    if not path.is_file():
        return jsonify({"error": f"graph '{name}' not found"})

    try:
        save_data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return jsonify({"error": f"failed to read: {e}"})

    return jsonify({"ok": True, "data": save_data})


@graph_bp.route("/graph/delete", methods=["POST"])
def graph_delete():
    """Delete a saved graph."""
    data = request.get_json(force=True)
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "no name specified"})

    path = _graphs_dir() / f"{name}.json"
    if path.is_file():
        path.unlink()
        return jsonify({"ok": True})
    return jsonify({"error": f"graph '{name}' not found"})
