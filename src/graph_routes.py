"""baddle — graph Flask routes (Blueprint)."""

import re
import random
import logging
from collections import defaultdict, deque
from datetime import datetime, timezone

import numpy as np

from flask import Blueprint, request, jsonify

from .prompts import _p
from .main import cosine_similarity, get_embedding
from .graph_logic import (
    _graph, _llm, init_graph, reset_graph,
    _auto_type_and_confidence, _auto_evidence_relation, _bayesian_update,
    _make_node, _ensure_node_fields, _get_texts, _add_node, _remove_node,
    _graph_generate, _clean_thought, _generate_thought, _api_available,
    _ensure_embeddings, _compute_edges, _find_clusters, _remap_edges,
    _detect_traps, _compute_alpha_beta,
)

log = logging.getLogger(__name__)

graph_bp = Blueprint("graph", __name__)


def _graph_response(nodes, edges, clusters, **extra):
    """Build standard graph response with node objects."""
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
    if _llm is None and not _api_available():
        return jsonify({"error": "requires in-process model or API"})
    data = request.get_json(force=True)
    topic = data.get("topic", "").strip()
    n = int(data.get("n", 6))
    threshold = float(data.get("threshold", 0.91))
    sim_mode = data.get("sim_mode", "embedding")
    lang = data.get("lang", "en")
    temp = float(data.get("temp", 0.9))
    top_k = int(data.get("top_k", 40))
    seed = int(data.get("seed", -1))
    maxtok_think = int(data.get("maxtok_think", 60))
    existing = data.get("existing", [])

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

    # Add topic as root node (depth=-1) if not already present
    # First check if a goal node matches the topic text — use it instead
    topic_idx = -1
    for i, nd in enumerate(nodes):
        if nd["depth"] == -1 and nd["text"] == topic:
            topic_idx = i
            break
    if topic_idx < 0:
        # Check if goal node has same text — don't duplicate it
        for i, nd in enumerate(nodes):
            if nd.get("type") == "goal" and nd["text"] == topic:
                topic_idx = i
                break
    if topic_idx < 0:
        topic_idx = _add_node(topic, depth=-1, topic=topic)

    new_thoughts = []
    directed = _graph["edges"]["directed"]
    manual_links = _graph["edges"]["manual_links"]

    attempts = 0
    while len(new_thoughts) < n and attempts < n * 3:
        attempts += 1
        t, ent = _generate_thought(topic, new_thoughts, lang, temp, top_k, seed, maxtok_think)
        if not t or len(t) < 10:
            continue
        if t.lower().strip("., ") in ("qwen3", "qwen", "llama", "gpt", "assistant"):
            continue
        if any(t.lower() == nd["text"].lower() for nd in nodes):
            continue
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

    edges = _compute_edges(nodes, threshold, sim_mode)
    clusters = _find_clusters(len(nodes), edges, threshold)

    return _graph_response(nodes, edges, clusters)


@graph_bp.route("/graph/recalc", methods=["POST"])
def graph_recalc():
    """Recompute edges and clusters with a new threshold (no generation)."""
    data = request.get_json(force=True)
    threshold = float(data.get("threshold", 0.91))
    sim_mode = data.get("sim_mode", "embedding")
    nodes = _graph["nodes"]
    if not nodes:
        return jsonify({"error": "no thoughts"})
    edges = _compute_edges(nodes, threshold, sim_mode)
    clusters = _find_clusters(len(nodes), edges, threshold)
    return _graph_response(nodes, edges, clusters)


@graph_bp.route("/graph/add", methods=["POST"])
def graph_add():
    """Add a user-provided thought and recompute edges."""
    if _llm is None and not _api_available():
        return jsonify({"error": "requires in-process model or API"})
    data = request.get_json(force=True)
    text = data.get("text", "").strip()
    threshold = float(data.get("threshold", 0.91))
    sim_mode = data.get("sim_mode", "embedding")
    node_type = data.get("node_type", "auto")
    if not text:
        return jsonify({"error": "empty thought"})
    # Determine type and confidence
    if node_type == "auto":
        node_type, auto_conf = _auto_type_and_confidence(text)
        new_idx = _add_node(text, depth=0, topic="", node_type=node_type, confidence=auto_conf)
    else:
        new_idx = _add_node(text, depth=0, topic="", node_type=node_type)

    nodes = _graph["nodes"]
    directed = _graph["edges"]["directed"]
    manual_links = _graph["edges"]["manual_links"]

    # Goal: connect all existing hypothesis/thought nodes → goal (they work toward it)
    if node_type == "goal":
        for i, n in enumerate(nodes):
            if i == new_idx:
                continue
            if n.get("type") in ("hypothesis", "thought") and n.get("depth", 0) >= 0:
                directed.append([i, new_idx])
                pair = [min(i, new_idx), max(i, new_idx)]
                if pair not in manual_links:
                    manual_links.append(pair)

    edges = _compute_edges(nodes, threshold, sim_mode)
    clusters = _find_clusters(len(nodes), edges, threshold)
    return _graph_response(nodes, edges, clusters)


@graph_bp.route("/graph/remove", methods=["POST"])
def graph_remove():
    """Remove a thought by index and recompute edges."""
    data = request.get_json(force=True)
    idx = int(data.get("index", -1))
    threshold = float(data.get("threshold", 0.91))
    sim_mode = data.get("sim_mode", "embedding")
    nodes = _graph["nodes"]
    if idx < 0 or idx >= len(nodes):
        return jsonify({"error": "invalid index"})
    _remove_node(idx)
    nodes = _graph["nodes"]
    edges = _compute_edges(nodes, threshold, sim_mode)
    clusters = _find_clusters(len(nodes), edges, threshold)
    return _graph_response(nodes, edges, clusters)


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
    data = request.get_json(force=True)
    a = int(data.get("a", -1))
    b = int(data.get("b", -1))
    threshold = float(data.get("threshold", 0.91))
    sim_mode = data.get("sim_mode", "embedding")
    nodes = _graph["nodes"]
    if a < 0 or b < 0 or a >= len(nodes) or b >= len(nodes) or a == b:
        return jsonify({"error": "invalid indices"})
    pair = (min(a, b), max(a, b))
    manual_links = _graph["edges"]["manual_links"]
    manual_unlinks = _graph["edges"]["manual_unlinks"]
    edges_before = _compute_edges(nodes, threshold, sim_mode)
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
    edges = _compute_edges(nodes, threshold, sim_mode)
    clusters = _find_clusters(len(nodes), edges, threshold)
    return _graph_response(nodes, edges, clusters)


@graph_bp.route("/graph/collapse", methods=["POST"])
def graph_collapse():
    """Collapse a cluster: generate summary, remove source nodes, add result."""
    if _llm is None and not _api_available():
        # Check if collapse_override is provided (from Generation Studio)
        data_peek = request.get_json(silent=True) or {}
        if not data_peek.get("collapse_override"):
            return jsonify({"error": "requires in-process model or API"})
    data = request.get_json(force=True)
    indices = data.get("cluster", [])
    threshold = float(data.get("threshold", 0.91))
    sim_mode = data.get("sim_mode", "embedding")
    lang = data.get("lang", "en")
    temp = float(data.get("temp", 0.7))
    top_k = int(data.get("top_k", 40))
    seed = int(data.get("seed", -1))
    collapse_mode = data.get("collapse_mode", "short")
    custom_max_tokens = data.get("max_tokens")
    user_prompt = data.get("collapse_prompt", "").strip()
    no_merge = data.get("no_merge", False)
    collapse_override = data.get("collapse_override", "").strip()
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
            system = _p(lang, "collapse_long")
            instruction = _p(lang, "write_long")
            max_tokens = 2000
        else:
            system = _p(lang, "collapse")
            instruction = _p(lang, "write_para")
            max_tokens = 800
        if custom_max_tokens:
            max_tokens = int(custom_max_tokens)
        # Smart truncation: fit texts into context window
        from .api_backend import _settings
        ctx_size = _settings.get("local_ctx", 4096)
        token_budget = ctx_size - 200 - max_tokens  # leave room for system + generation

        user = f"{_p(lang, 'topic')}: {topic}\n\n{_p(lang, 'ideas')}:\n"

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
        text, ent = _graph_generate(messages, max_tokens=max_tokens, temp=temp, top_k=top_k, seed=seed)
        # If text was cut mid-sentence, try to continue
        if text and not text.rstrip().endswith(('.', '!', '?', '。', '»', '"')):
            messages_cont = list(messages) + [{"role": "assistant", "content": text}]
            cont, ent2 = _graph_generate(messages_cont, max_tokens=max_tokens // 2, temp=temp, top_k=top_k, seed=seed)
            if cont:
                text = text + cont
                if ent.get("tokens") and ent2.get("tokens"):
                    ent["tokens"].extend(ent2["tokens"])
                ent["avg"] = (ent["avg"] + ent2["avg"]) / 2
                ent["unc"] = max(ent["unc"], ent2["unc"])

    valid_indices = [i for i in indices if i < len(nodes)]

    if no_merge:
        # Keep originals, add collapsed as new node linked FROM source nodes
        collapsed_topic = next((nodes[i]["topic"] for i in valid_indices if nodes[i]["topic"]), topic)
        max_depth = max((nodes[i]["depth"] for i in valid_indices), default=0)
        collapsed_conf = sum(nodes[i]["confidence"] for i in valid_indices) / max(len(valid_indices), 1)
        new_idx = _add_node(text, depth=max_depth + 1, topic=collapsed_topic, entropy=ent, confidence=round(collapsed_conf, 2))
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
        for i in sorted(valid_indices, reverse=True):
            nodes.pop(i)
        # Reassign ids after removal
        for k, nd in enumerate(nodes):
            nd["id"] = k
        _remap_edges(valid_indices)
        new_idx = _add_node(text, depth=min_depth, topic=collapsed_topic,
                            entropy=ent, confidence=collapsed_conf)
        # Link collapsed node to its topic root
        directed = _graph["edges"]["directed"]
        manual_links = _graph["edges"]["manual_links"]
        for i, nd in enumerate(nodes):
            if nd["depth"] == -1 and nd["topic"] == collapsed_topic:
                directed.append([i, new_idx])
                pair = [min(i, new_idx), max(i, new_idx)]
                if pair not in manual_links:
                    manual_links.append(pair)
                break

    edges = _compute_edges(nodes, threshold, sim_mode)
    clusters = _find_clusters(len(nodes), edges, threshold)

    return _graph_response(nodes, edges, clusters, text=text)


@graph_bp.route("/graph/sync", methods=["POST"])
def graph_sync():
    """Restore graph state (used by undo)."""
    data = request.get_json(force=True)
    threshold = float(data.get("threshold", 0.91))
    sim_mode = data.get("sim_mode", "embedding")

    # Support both new node-object format and legacy parallel-array format
    if "nodes" in data:
        incoming_nodes = data["nodes"]
        if incoming_nodes and isinstance(incoming_nodes[0], str):
            # Legacy: list of strings
            topic = data.get("topic", _graph["meta"].get("topic", ""))
            incoming_nodes = [_make_node(i, t, topic=topic) for i, t in enumerate(incoming_nodes)]
        _graph["nodes"] = incoming_nodes
    elif "thoughts" in data:
        # Legacy parallel-array format
        thoughts = data["thoughts"]
        topic = data.get("topic", _graph["meta"].get("topic", ""))
        ents = data.get("entropies", [])
        depths = data.get("depths", [])
        topics = data.get("topics", [])
        confs = data.get("confidences", [])
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
    if "edges" in data and isinstance(data["edges"], dict) and "manual_links" in data["edges"]:
        # New format: edges is the edges dict
        _graph["edges"]["manual_links"] = data["edges"].get("manual_links", [])
        _graph["edges"]["manual_unlinks"] = data["edges"].get("manual_unlinks", [])
        _graph["edges"]["directed"] = data["edges"].get("directed", [])
    else:
        # Compat: directed_edges / manual_links at top level
        _graph["edges"]["manual_links"] = data.get("manual_links", [])
        _graph["edges"]["manual_unlinks"] = data.get("manual_unlinks", [])
        _graph["edges"]["directed"] = data.get("directed_edges", _graph["edges"].get("directed", []))

    _graph["meta"]["hub_nodes"] = set(data.get("hub_nodes", list(_graph["meta"].get("hub_nodes", set()))))
    if "topic" in data:
        _graph["meta"]["topic"] = data["topic"]

    # If saved computed edges/clusters provided, use them directly (undo restore)
    if "edges" in data and isinstance(data["edges"], list) and "clusters" in data:
        _graph["embeddings"] = []
        return _graph_response(_graph["nodes"], data["edges"], data["clusters"])

    # Otherwise recompute
    _graph["embeddings"] = []
    nodes = _graph["nodes"]
    edges = _compute_edges(nodes, threshold, sim_mode)
    clusters = _find_clusters(len(nodes), edges, threshold)
    return _graph_response(nodes, edges, clusters)


@graph_bp.route("/graph/expand", methods=["POST"])
def graph_expand():
    """Generate child ideas branching from a specific thought (same topic, new angles)."""
    if _llm is None and not _api_available():
        return jsonify({"error": "requires in-process model or API"})
    data = request.get_json(force=True)
    idx = int(data.get("index", -1))
    n = int(data.get("n", 3))
    threshold = float(data.get("threshold", 0.91))
    sim_mode = data.get("sim_mode", "embedding")
    lang = data.get("lang", "en")
    temp = float(data.get("temp", 0.9))
    top_k = int(data.get("top_k", 40))
    seed = int(data.get("seed", -1))
    maxtok_expand = int(data.get("maxtok_expand", 120))
    nodes = _graph["nodes"]

    if idx < 0 or idx >= len(nodes):
        return jsonify({"error": "invalid index"})

    source = nodes[idx]["text"]
    topic = _graph["meta"].get("topic", "")
    new_thoughts = []
    parent_depth = nodes[idx]["depth"]
    parent_topic = nodes[idx]["topic"] or topic

    system = _p(lang, "think")

    attempts = 0
    while len(new_thoughts) < n and attempts < n * 3:
        attempts += 1
        user = f"{_p(lang, 'topic')}: {topic}\n{_p(lang, 'source')}: {source}"
        if new_thoughts:
            user += f"\n{_p(lang, 'already_gen')}:\n" + "\n".join(f"- {t}" for t in new_thoughts)
        user += f"\n{_p(lang, 'branch')}"

        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        t, ent = _graph_generate(messages, max_tokens=maxtok_expand, temp=temp, top_k=top_k, seed=seed)
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
            print(f"[auto-evidence] expand #{idx}→#{new_idx}: {rel} str={strength} (conf unchanged at {nodes[idx]['confidence']:.2f})")

    edges = _compute_edges(nodes, threshold, sim_mode)
    clusters = _find_clusters(len(nodes), edges, threshold)
    return _graph_response(nodes, edges, clusters)


@graph_bp.route("/graph/elaborate", methods=["POST"])
def graph_elaborate():
    """Generate deeper ideas that elaborate on a specific thought (the source becomes a hub)."""
    if _llm is None and not _api_available():
        return jsonify({"error": "requires in-process model or API"})
    data = request.get_json(force=True)
    idx = int(data.get("index", -1))
    n = int(data.get("n", 3))
    threshold = float(data.get("threshold", 0.91))
    sim_mode = data.get("sim_mode", "embedding")
    lang = data.get("lang", "en")
    temp = float(data.get("temp", 0.9))
    top_k = int(data.get("top_k", 40))
    seed = int(data.get("seed", -1))
    direction = data.get("direction", "").strip()
    maxtok_elaborate = int(data.get("maxtok_elaborate", 120))
    nodes = _graph["nodes"]

    if idx < 0 or idx >= len(nodes):
        return jsonify({"error": "invalid index"})

    source = nodes[idx]["text"]
    topic = _graph["meta"].get("topic", "")
    new_thoughts = []
    parent_depth = nodes[idx]["depth"]
    parent_topic = nodes[idx]["topic"] or topic

    system = _p(lang, "think")

    attempts = 0
    while len(new_thoughts) < n and attempts < n * 3:
        attempts += 1
        user = f"{_p(lang, 'topic')}: {topic}\n{_p(lang, 'elaborate')}: {source}"
        if direction:
            user += f"\n{_p(lang, 'direction')}: {direction}"
        if new_thoughts:
            user += f"\n{_p(lang, 'already_elab')}:\n" + "\n".join(f"- {t}" for t in new_thoughts)
        user += f"\n{_p(lang, 'deeper')}"

        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        t, ent = _graph_generate(messages, max_tokens=maxtok_elaborate, temp=temp, top_k=top_k, seed=seed)
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
            print(f"[auto-evidence] elaborate #{idx}→#{new_idx}: {rel} str={strength} (conf unchanged at {nodes[idx]['confidence']:.2f})")

    edges = _compute_edges(nodes, threshold, sim_mode)
    clusters = _find_clusters(len(nodes), edges, threshold)
    return _graph_response(nodes, edges, clusters)


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
    data = request.get_json(force=True)
    idx = int(data.get("index", -1))
    new_text = data.get("text", "").strip()
    threshold = float(data.get("threshold", 0.91))
    sim_mode = data.get("sim_mode", "embedding")

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

    edges = _compute_edges(nodes, threshold, sim_mode)
    clusters = _find_clusters(len(nodes), edges, threshold)
    return _graph_response(nodes, edges, clusters)


@graph_bp.route("/graph/studio/apply-child", methods=["POST"])
def graph_studio_apply_child():
    """Apply an elaborate/expand result: add as child node linked to parent with directed edge."""
    data = request.get_json(force=True)
    parent_idx = int(data.get("index", -1))
    new_text = data.get("text", "").strip()
    child_type = data.get("type", "elaborate")  # "elaborate" or "expand"
    threshold = float(data.get("threshold", 0.91))
    sim_mode = data.get("sim_mode", "embedding")

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

    edges = _compute_edges(nodes, threshold, sim_mode)
    clusters = _find_clusters(len(nodes), edges, threshold)
    return _graph_response(nodes, edges, clusters)


# --------------- Smart DC (Dialectical Convergence) ---------------

@graph_bp.route("/graph/smartdc", methods=["POST"])
def graph_smartdc():
    """Smart DC: generate thesis, antithesis, neutral → centroid → synthesis."""
    data = request.get_json(force=True)
    node_idx = int(data.get("index", -1))
    lang = data.get("lang", "en")
    temp = float(data.get("temp", 0.9))
    top_k = int(data.get("top_k", 40))
    seed = int(data.get("seed", -1))
    threshold = float(data.get("threshold", 0.91))
    sim_mode = data.get("sim_mode", "embedding")

    nodes = _graph["nodes"]
    if node_idx < 0 or node_idx >= len(nodes):
        return jsonify({"error": "invalid node index"})

    statement = nodes[node_idx]["text"]

    # Phase 1: Divergence — generate 3 poles
    poles = []
    for role_key in ["dc_thesis", "dc_antithesis", "dc_neutral"]:
        messages = [
            {"role": "system", "content": _p(lang, role_key)},
            {"role": "user", "content": f"{_p(lang, 'dc_statement')}: {statement}"},
        ]
        text, ent = _graph_generate(messages, max_tokens=200, temp=temp, top_k=top_k, seed=seed)
        print(f"[smartdc] {role_key}: {text[:80]}...")
        poles.append({"role": role_key, "text": text, "entropy": ent})

    # Phase 2: Convergence — synthesize from 3 poles (BEFORE embeddings to keep KV cache clean)
    synthesis_messages = [
        {"role": "system", "content": _p(lang, "dc_synthesis")},
        {"role": "user", "content":
            f"{_p(lang, 'dc_statement')}: {statement}\n\n"
            f"{_p(lang, 'dc_for')}:\n{poles[0]['text']}\n\n"
            f"{_p(lang, 'dc_against')}:\n{poles[1]['text']}\n\n"
            f"{_p(lang, 'dc_context')}:\n{poles[2]['text']}"
        },
    ]
    synthesis_text, synthesis_ent = _graph_generate(synthesis_messages, max_tokens=300, temp=0.7, top_k=top_k, seed=seed)
    print(f"[smartdc] synthesis: {synthesis_text[:80]}...")

    # Phase 3: Try embeddings for centroid confidence, fallback to entropy
    from .api_backend import use_api_for, api_get_embedding
    pole_embs = []
    syn_emb = None
    if use_api_for("embeddings"):
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

    if pole_embs and syn_emb is not None:
        centroid = np.mean(pole_embs, axis=0)
        syn_arr = np.array(syn_emb)
        cent_arr = np.array(centroid)
        dot = np.dot(syn_arr, cent_arr)
        norm = np.linalg.norm(syn_arr) * np.linalg.norm(cent_arr)
        if norm > 0:
            centroid_distance = float(dot / norm)
            new_confidence = round(min(0.95, max(0.3, centroid_distance)), 2)
        print(f"[smartdc] confidence from centroid: {new_confidence} (distance={centroid_distance:.3f})")
    else:
        # Fallback: entropy-based confidence
        syn_ent = synthesis_ent.get("avg", 1.0)
        pole_ents = [p["entropy"].get("avg", 1.0) for p in poles]
        avg_pole_ent = sum(pole_ents) / len(pole_ents)
        combined_ent = syn_ent * 0.7 + avg_pole_ent * 0.3
        new_confidence = round(max(0.3, min(0.95, 1.0 - combined_ent)), 2)
        print(f"[smartdc] confidence from entropy (fallback): {new_confidence}")

    return jsonify({
        "poles": [
            {"role": "thesis", "text": poles[0]["text"], "entropy": poles[0]["entropy"]},
            {"role": "antithesis", "text": poles[1]["text"], "entropy": poles[1]["entropy"]},
            {"role": "neutral", "text": poles[2]["text"], "entropy": poles[2]["entropy"]},
        ],
        "synthesis": synthesis_text,
        "synthesis_entropy": synthesis_ent,
        "confidence": new_confidence,
        "centroid_distance": round(centroid_distance, 3),
        "original_idx": node_idx,
    })


# --------------- tick() — phase-based automatic thinking ---------------

@graph_bp.route("/graph/tick", methods=["POST"])
def graph_tick():
    """Phase-based automatic thinking: EXPLORE → DEEPEN → VERIFY → META → SYNTHESIZE."""
    data = request.get_json(force=True)
    threshold = float(data.get("threshold", 0.91))
    sim_mode = data.get("sim_mode", "embedding")
    stable_threshold = float(data.get("stable_threshold", 0.8))
    run_mode = data.get("run_mode", "deep")  # "fast" or "deep"
    force_collapse = data.get("force_collapse", False)

    nodes = _graph["nodes"]
    if not nodes:
        return jsonify({"action": "none", "reason": "Graph is empty.", "phase": "none"})

    edges = _compute_edges(nodes, threshold, sim_mode)
    active_nodes = [(i, n) for i, n in enumerate(nodes) if n.get("depth", 0) >= 0]
    if not active_nodes:
        return jsonify({"action": "none", "reason": "No active nodes.", "phase": "none"})

    # Classify nodes
    goals = [(i, n) for i, n in active_nodes if n.get("type") == "goal"]
    goal_idx = goals[0][0] if goals else None
    goal_text = nodes[goal_idx]["text"][:60] if goal_idx is not None else ""
    if not goals:
        print(f"[tick] WARNING: no goal node found. Types: {[n.get('type','?') for _,n in active_nodes[:5]]}")

    hypotheses = [(i, n) for i, n in active_nodes
                  if n.get("type") in ("hypothesis", "thought")]
    questions = [(i, n) for i, n in active_nodes if n.get("type") == "question"]

    directed_children = {}
    for a, b in _graph["edges"].get("directed", []):
        directed_children[a] = directed_children.get(a, 0) + 1

    no_evidence = [h for h in hypotheses if directed_children.get(h[0], 0) == 0]
    unverified = [h for h in hypotheses if h[1].get("confidence", 0.5) < stable_threshold]
    weak = [h for h in hypotheses if h[1].get("confidence", 0.5) <= 0.5]
    verified = [h for h in hypotheses if h[1].get("confidence", 0.5) >= stable_threshold]

    # ── FORCE COLLAPSE (after step limit reached) — collapse in batches of 5 ──
    if force_collapse:
        collapsable = [i for i, n in active_nodes if n.get("type") not in ("evidence", "goal")]
        if len(collapsable) > 5:
            # Take first 5 to collapse
            batch = collapsable[:5]
            return jsonify({
                "action": "collapse",
                "target": batch,
                "phase": "collapse",
                "reason": f"COLLAPSE PHASE: batch of 5 from {len(collapsable)} remaining.",
                "text": "batch collapse",
            })
        elif len(collapsable) >= 2:
            # Last batch
            return jsonify({
                "action": "collapse",
                "target": collapsable,
                "phase": "collapse",
                "reason": f"FINAL COLLAPSE: {len(collapsable)} remaining.",
                "text": "final batch",
            })
        # Already collapsed enough → stable
        avg_conf = sum(n.get("confidence", 0.5) for _, n in active_nodes) / len(active_nodes)
        return jsonify({
            "action": "stable",
            "phase": "synthesize",
            "reason": f"SYNTHESIZE: {len(active_nodes)} nodes, avg {avg_conf:.0%}.",
        })

    # ── Helper: pick best node toward goal (BFS shortest path + exploration) ──
    def _pick_toward_goal(candidates):
        """Pick candidate closest to goal by BFS. Exploration if stuck."""
        if not candidates:
            return None
        if goal_idx is None:
            pick = min(candidates, key=lambda x: x[1].get("confidence", 0.5))
            return pick, -1, False

        adj_h = defaultdict(set)
        for e in edges:
            adj_h[e["from"]].add(e["to"]); adj_h[e["to"]].add(e["from"])

        def bfs_dist(start, target):
            if start == target: return 0
            visited = {start}; queue = deque([(start, 0)])
            while queue:
                cur, d = queue.popleft()
                for nb in adj_h.get(cur, []):
                    if nb == target: return d + 1
                    if nb not in visited: visited.add(nb); queue.append((nb, d + 1))
            return 999

        distances = {ci: bfs_dist(ci, goal_idx) for ci, _ in candidates}
        sorted_c = sorted(candidates, key=lambda x: (distances.get(x[0], 999), x[1].get("confidence", 0.5)))

        traps = set(_detect_traps(nodes, edges))
        safe = [c for c in sorted_c if c[0] not in traps] or sorted_c

        tried = _graph.get("_tick_tried", set())
        if safe[0][0] in tried and len(safe) > 1:
            remaining = [c for c in safe if c[0] not in tried]
            pick = remaining[0] if remaining else random.choice(safe)
            expl = True
        else:
            pick = safe[0]
            expl = False
        tried.add(pick[0])
        _graph["_tick_tried"] = tried
        return pick, distances.get(pick[0], 999), expl

    # ══════════ FAST MODE — priority-based, converges when possible ══════════
    if run_mode == "fast":
        # 1. Too few hypotheses → Think
        if len(hypotheses) < 3:
            return jsonify({"action": "think_toward", "target": goal_idx or 0, "phase": "fast",
                            "reason": f"FAST: {len(hypotheses)} hypotheses, need more.", "text": goal_text})
        # 2. Weak → Verify (BFS + exploration + traps)
        if weak:
            result = _pick_toward_goal(weak)
            if result:
                t, dist, expl = result
                tag = " [exploration]" if expl else ""
                return jsonify({"action": "smartdc", "target": t[0], "phase": "fast",
                                "reason": f"FAST: #{t[0]} conf={t[1]['confidence']:.0%}, dist={dist}. Verify.{tag}", "text": t[1]["text"][:80]})
        # 3. No evidence → Elaborate (BFS + exploration + traps)
        if no_evidence:
            result = _pick_toward_goal(no_evidence)
            if result:
                t, dist, expl = result
                tag = " [exploration]" if expl else ""
                return jsonify({"action": "elaborate", "target": t[0], "phase": "fast",
                                "reason": f"FAST: #{t[0]} no evidence, dist={dist}. Elaborate.{tag}", "text": t[1]["text"][:80]})
        # 4. Rephrase — if 2+ children but still weak (max 1 per node)
        rephrased = _graph.get("_rephrased", set())
        needs_rephrase = [h for h in unverified
                          if directed_children.get(h[0], 0) >= 2
                          and h[1].get("confidence", 0.5) <= 0.5
                          and h[0] not in rephrased]
        if needs_rephrase:
            t = needs_rephrase[0]
            rephrased.add(t[0]); _graph["_rephrased"] = rephrased
            return jsonify({"action": "rephrase", "target": t[0], "phase": "fast",
                            "reason": f"FAST: #{t[0]} {directed_children[t[0]]} children, conf={t[1]['confidence']:.0%}. Rephrase.", "text": t[1]["text"][:80]})
        # 5. Ask (max 1)
        asked = _graph.get("_asked_nodes", set())
        total_q = sum(1 for n in nodes if n.get("type") == "question")
        if total_q < 1 and unverified:
            need_q = [h for h in unverified if h[0] not in asked]
            if need_q:
                t = need_q[0]
                asked.add(t[0]); _graph["_asked_nodes"] = asked
                return jsonify({"action": "ask", "target": t[0], "phase": "fast",
                                "reason": f"FAST: probing #{t[0]}.", "text": t[1]["text"][:80]})
        # 6. Unverified → Verify (BFS + exploration + traps)
        if unverified:
            result = _pick_toward_goal(unverified)
            if result:
                t, dist, expl = result
                tag = " [exploration]" if expl else ""
                return jsonify({"action": "smartdc", "target": t[0], "phase": "fast",
                                "reason": f"FAST: #{t[0]} conf={t[1]['confidence']:.0%}, dist={dist}. Verify.{tag}", "text": t[1]["text"][:80]})
        # 7. Isolated → Expand
        connected = set()
        for e in edges:
            connected.add(e["from"]); connected.add(e["to"])
        isolated = [(i, n) for i, n in active_nodes
                    if i not in connected and n.get("type") not in ("evidence", "goal")]
        if isolated:
            t = isolated[0]
            return jsonify({"action": "expand", "target": t[0], "phase": "fast",
                            "reason": f"FAST: #{t[0]} isolated. Expand.", "text": t[1]["text"][:80]})
        # 8. Collapse verified nodes (cluster-based or all verified if ≥5)
        clusters = _find_clusters(len(nodes), edges, threshold)
        for cl in clusters:
            real = [i for i in cl if nodes[i].get("depth", 0) >= 0 and nodes[i].get("type") not in ("evidence", "goal")]
            if len(real) >= 5:
                avg_c = sum(nodes[i].get("confidence", 0.5) for i in real) / len(real)
                if avg_c >= stable_threshold - 0.1:
                    return jsonify({"action": "collapse", "target": real, "phase": "fast",
                                    "reason": f"FAST: cluster {len(real)} nodes, avg {avg_c:.0%}. Collapse.",
                                    "text": ", ".join(nodes[i]["text"][:25] for i in real[:3]) + "..."})
        # 8b. No clusters but many verified → collapse all verified
        if len(verified) >= 5:
            v_ids = [i for i, _ in verified]
            return jsonify({"action": "collapse", "target": v_ids, "phase": "fast",
                            "reason": f"FAST: {len(verified)} verified, no clusters. Collapse verified.",
                            "text": ", ".join(nodes[i]["text"][:25] for i in v_ids[:3]) + "..."})
        # 9. META
        meta_done = _graph.get("_meta_done", False)
        if not meta_done and len(verified) >= 3:
            _graph["_meta_done"] = True
            return jsonify({"action": "think_toward", "target": goal_idx or 0, "phase": "fast",
                            "reason": f"FAST META: {len(verified)} verified. What did I miss?", "text": goal_text})
        # 10. Stable
        avg = sum(n.get("confidence", 0.5) for _, n in active_nodes) / len(active_nodes)
        return jsonify({"action": "stable", "phase": "fast",
                        "reason": f"FAST DONE: {len(active_nodes)} nodes, avg {avg:.0%}."})

    # ══════════ DEEP MODE — phase-based, thorough investigation ══════════

    # ── PHASE 1: EXPLORE — need mass (< 5 hypotheses) ──
    if len(hypotheses) < 5:
        return jsonify({
            "action": "think_toward",
            "target": goal_idx or 0,
            "phase": "explore",
            "reason": f"EXPLORE: {len(hypotheses)} hypotheses, need more ideas. (goal: {goal_text})",
            "text": goal_text,
        })

    # ── PHASE 2: DEEPEN — add evidence to bare hypotheses ──
    if no_evidence:
        result = _pick_toward_goal(no_evidence)
        if result:
            target, dist, expl = result
            tag = " [exploration]" if expl else ""
            return jsonify({
                "action": "elaborate",
                "target": target[0],
                "phase": "deepen",
                "reason": f"DEEPEN: #{target[0]} no evidence ({len(no_evidence)} bare, dist={dist}){tag}",
                "text": target[1]["text"][:80],
            })

    # ── PHASE 2b: REPHRASE — if 2+ children but still weak (max 1 per node) ──
    rephrased = _graph.get("_rephrased", set())
    needs_rephrase = [h for h in unverified
                      if directed_children.get(h[0], 0) >= 2
                      and h[1].get("confidence", 0.5) <= 0.5
                      and h[0] not in rephrased]
    if needs_rephrase:
        result = _pick_toward_goal(needs_rephrase)
        if result:
            target, dist, expl = result
            rephrased.add(target[0]); _graph["_rephrased"] = rephrased
            tag = " [exploration]" if expl else ""
            return jsonify({
                "action": "rephrase",
                "target": target[0],
                "phase": "deepen",
                "reason": f"DEEPEN: #{target[0]} {directed_children[target[0]]} children, conf={target[1]['confidence']:.0%}. Rephrase. (dist={dist}){tag}",
                "text": target[1]["text"][:80],
            })

    # ── PHASE 3: VERIFY — Smart DC on unverified ──
    if unverified:
        result = _pick_toward_goal(unverified)
        if result:
            target, dist, expl = result
            tag = " [exploration]" if expl else ""
            return jsonify({
                "action": "smartdc",
                "target": target[0],
                "phase": "verify",
                "reason": f"VERIFY: #{target[0]} conf={target[1]['confidence']:.0%} ({len(unverified)} unverified, dist={dist}){tag}",
                "text": target[1]["text"][:80],
            })

    # ── PHASE 4: META — "what did I miss?" ──
    meta_done = _graph.get("_meta_done", False)
    if not meta_done and len(verified) >= 5:
        _graph["_meta_done"] = True
        return jsonify({
            "action": "think_toward",
            "target": goal_idx or 0,
            "phase": "meta",
            "reason": f"META: {len(verified)} verified. What angles did I miss?",
            "text": goal_text,
        })

    # ── PHASE 3b: COLLAPSE — verified clusters or all verified ──
    clusters = _find_clusters(len(nodes), edges, threshold)
    for cl in clusters:
        real_nodes = [i for i in cl if nodes[i].get("depth", 0) >= 0
                      and nodes[i].get("type") not in ("evidence", "goal")]
        verified_in_cl = [i for i in real_nodes if nodes[i].get("confidence", 0.5) >= stable_threshold]
        if len(verified_in_cl) >= 5:
            return jsonify({
                "action": "collapse",
                "target": real_nodes,
                "phase": "collapse",
                "reason": f"COLLAPSE: cluster of {len(real_nodes)} nodes ({len(verified_in_cl)} verified).",
                "text": ", ".join(nodes[i]["text"][:25] for i in real_nodes[:3]) + "...",
            })
    # No clusters but many verified → collapse all verified
    if len(verified) >= 5:
        v_ids = [i for i, _ in verified]
        return jsonify({
            "action": "collapse",
            "target": v_ids,
            "phase": "collapse",
            "reason": f"COLLAPSE: {len(verified)} verified, no clusters. Synthesize.",
            "text": ", ".join(nodes[i]["text"][:25] for i in v_ids[:3]) + "...",
        })

    # ── PHASE 4a: EXPAND — isolated nodes ──
    connected = set()
    for e in edges:
        connected.add(e["from"]); connected.add(e["to"])
    isolated = [(i, n) for i, n in active_nodes
                if i not in connected and n.get("type") not in ("evidence", "goal")]
    if isolated:
        t = isolated[0]
        return jsonify({
            "action": "expand",
            "target": t[0],
            "phase": "deepen",
            "reason": f"DEEPEN: #{t[0]} isolated. Expand to connect.",
            "text": t[1]["text"][:80],
        })

    # ── PHASE 4b: ASK — probing questions (max 3, only once per node) ──
    asked_nodes = _graph.get("_asked_nodes", set())
    if len(questions) < 3:
        need_q = [h for h in hypotheses if h[0] not in asked_nodes and h[1].get("confidence", 0.5) < stable_threshold]
        if need_q:
            target = need_q[0]
            asked_nodes.add(target[0])
            _graph["_asked_nodes"] = asked_nodes
            return jsonify({
                "action": "ask",
                "target": target[0],
                "phase": "ask",
                "reason": f"ASK: probing #{target[0]} ({len(questions)}/3 questions, {len(asked_nodes)} asked)",
                "text": target[1]["text"][:80],
            })

    # ── PHASE 5: SYNTHESIZE ──
    avg_conf = sum(n.get("confidence", 0.5) for _, n in active_nodes) / len(active_nodes)
    return jsonify({
        "action": "stable",
        "phase": "synthesize",
        "reason": f"SYNTHESIZE: {len(active_nodes)} nodes, {len(verified)} verified, avg {avg_conf:.0%}. Ready for final summary.",
    })


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
    data = request.get_json(force=True)
    hyp_idx = int(data.get("hypothesis", -1))
    evidence_text = data.get("text", "").strip()
    relation = data.get("relation", "supports")  # "supports" or "contradicts"
    strength = float(data.get("strength", 0.7))  # P(E|H) — how likely is this evidence if hypothesis is true
    threshold = float(data.get("threshold", 0.91))
    sim_mode = data.get("sim_mode", "embedding")

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

    edges = _compute_edges(nodes, threshold, sim_mode)
    clusters = _find_clusters(len(nodes), edges, threshold)

    print(f"[bayes] hyp #{hyp_idx} '{hyp['text'][:40]}': {old_conf:.2f} → {posterior:.3f} ({relation}, strength={strength})")

    return _graph_response(nodes, edges, clusters, bayes_update={
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
