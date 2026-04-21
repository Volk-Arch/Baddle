"""baddle — graph Flask routes (Blueprint)."""

import random
from collections import defaultdict, deque
from datetime import datetime, timezone

import numpy as np

from flask import Blueprint, request, jsonify

from .prompts import _p
from .main import cosine_similarity, distinct, distinct_decision
from .graph_logic import (
    _graph, graph_lock, reset_graph,
    _auto_type_and_confidence, _auto_evidence_relation,
    _bayesian_update_distinct, _d_from_relation,
    _make_node, _ensure_node_fields, _get_texts, _add_node, _remove_node,
    _graph_generate, _clean_thought, _generate_thought,
    _ensure_embeddings, _compute_edges, _find_clusters, _remap_edges,
    _detect_traps, _compute_alpha_beta,
    sample_in_embedding_space,
    touch_node, touch_nodes, TOUCH_BOOST_DEFAULT,
)
from .hrv_manager import get_manager as get_hrv_manager

graph_bp = Blueprint("graph", __name__)


# ── Thinking-state декоратор: помечает cognitive_loop что идёт тяжёлая
# операция, чтобы UI конус показал соответствующую стадию (pump →
# dual cones, elaborate → pulse, и т.д.). Any error inside endpoint не
# оставляет thinking stuck — try/finally гарантирует clear.
import functools as _functools

def _with_thinking(kind: str):
    def deco(fn):
        @_functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                from .cognitive_loop import get_cognitive_loop
                get_cognitive_loop().set_thinking(kind)
            except Exception:
                get_cognitive_loop = None
            try:
                return fn(*args, **kwargs)
            finally:
                try:
                    from .cognitive_loop import get_cognitive_loop as _gcl
                    _gcl().clear_thinking()
                except Exception:
                    pass
        return wrapper
    return deco


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
@_with_thinking("think")
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
        _graph["edges"] = {"manual_links": [], "manual_unlinks": [], "directed": [],
                              "caused_by": [], "followed_by": []}
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
        # Store only mode_id — Horizon preset handles behavior (no primitive switches)
        from .modes import get_mode
        from .goals_store import add_goal
        from .workspace import get_workspace_manager
        mode_id = d.get("mode", "horizon")
        mode_cfg = get_mode(mode_id)
        nodes[new_idx]["mode"] = mode_id
        _graph["meta"]["mode"] = mode_id

        # Persistent goal lifecycle: регистрируем в goals_store
        try:
            ws_id = get_workspace_manager().active_id or "main"
        except Exception:
            ws_id = "main"
        try:
            goal_id = add_goal(
                text=text,
                mode=mode_id,
                workspace=ws_id,
                priority=d.get("priority"),
                deadline=d.get("deadline"),
                category=d.get("category"),
            )
            nodes[new_idx]["goal_id"] = goal_id  # связь node ↔ persistent record
        except Exception as e:
            print(f"[goal/add] goals_store persist failed: {e}")

        # Parse subgoals from multiline text for multi-goal modes
        subgoal_indices = []
        if mode_cfg.get("goals_count") == "2+":
            lines = [l.strip() for l in text.split('\n') if l.strip()]
            if len(lines) > 1:
                # First line = goal text, rest = subgoals
                nodes[new_idx]["text"] = lines[0]
                for sub_text in lines[1:]:
                    sub_idx = _add_node(sub_text, depth=0, topic="", node_type="hypothesis")
                    subgoal_indices.append(sub_idx)
                    directed.append([new_idx, sub_idx])  # goal → subgoal
                    pair = [min(new_idx, sub_idx), max(new_idx, sub_idx)]
                    if pair not in manual_links:
                        manual_links.append(pair)
                print(f"[add] goal '{lines[0][:30]}' with {len(subgoal_indices)} subgoals")
        nodes[new_idx]["subgoals"] = subgoal_indices

        # Link existing hypotheses to goal
        for i, n in enumerate(nodes):
            if i == new_idx or i in subgoal_indices:
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
    """Collapse a cluster: generate summary, optionally remove sources.

    Использует общий helper `_collapse_cluster_to_node` — тот же code path
    что и для chat execute_deep (batched) и DMN converge. Smart truncation,
    lineage, auto-link — всё в helper'е.
    """
    d = _p_data()
    indices = d.get("cluster", [])
    collapse_mode = d.get("collapse_mode", "short")
    custom_max_tokens = d.get("max_tokens")
    user_prompt = d.get("collapse_prompt", "").strip()
    no_merge = d.get("no_merge", False)
    collapse_override = d.get("collapse_override", "").strip()
    nodes = _graph["nodes"]

    if not indices or not nodes:
        return jsonify({"error": "no cluster to collapse"})

    # Generation Studio path — текст уже готов, создаём ноду напрямую
    # без LLM вызова. Это специальный случай, helper его не покрывает.
    if collapse_override:
        valid = [i for i in indices if 0 <= i < len(nodes)]
        if not valid:
            return jsonify({"error": "invalid cluster"})
        topic = _graph["meta"].get("topic", "")
        collapsed_topic = next((nodes[i].get("topic", "") for i in valid if nodes[i].get("topic")), topic)
        max_depth = max((nodes[i].get("depth", 0) for i in valid), default=0)
        avg_conf = round(sum(nodes[i].get("confidence", 0.5) for i in valid) / len(valid), 2)
        new_idx = _add_node(collapse_override[:1000], depth=max_depth + 1,
                             topic=collapsed_topic, confidence=avg_conf,
                             node_type="synthesis")
        nodes[new_idx]["full_text"] = collapse_override
        lineage = set(valid)
        for i in valid:
            lineage |= set(nodes[i].get("collapsed_from", []) or [])
        nodes[new_idx]["collapsed_from"] = sorted(lineage)
        directed = _graph["edges"].setdefault("directed", [])
        manual_links = _graph["edges"].setdefault("manual_links", [])
        for src in valid:
            directed.append([src, new_idx])
            pair = [min(src, new_idx), max(src, new_idx)]
            if pair not in manual_links:
                manual_links.append(pair)
        return _finalize(nodes, d["threshold"], d["sim_mode"], text=collapse_override)

    # LLM path через helper
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

    # Если юзер передал кастомный prompt — он перекрывает стандартный instruction
    final_instruction = user_prompt or instruction

    from .graph_logic import _collapse_cluster_to_node
    res = _collapse_cluster_to_node(
        indices=indices, lang=d["lang"],
        custom_system=system,
        instruction=final_instruction,   # helper сам соберёт prompt с truncation
        max_tokens=max_tokens,
        temp=d["temp"], top_k=d["top_k"],
        no_merge=no_merge,
    )
    if not res:
        return jsonify({"error": "collapse LLM failed"})

    return _finalize(nodes, d["threshold"], d["sim_mode"], text=res.get("text", ""))


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
@_with_thinking("elaborate")
def graph_expand():
    """Generate child ideas branching from a specific thought (same topic, new angles)."""
    d = _p_data()
    idx = int(d.get("index", -1))
    n = int(d.get("n", 3))
    maxtok_expand = int(d.get("maxtok_expand", 120))
    nodes = _graph["nodes"]

    if idx < 0 or idx >= len(nodes):
        return jsonify({"error": "invalid index"})

    # Hebbian: expand-source получает обращение (нода реально работает)
    touch_node(idx)

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
                d_val = _d_from_relation(rel, strength)
                nodes[idx]["confidence"] = _bayesian_update_distinct(old_conf, d_val)
                print(f"[auto-evidence] expand #{idx}→#{new_idx}: {rel} str={strength} d={d_val:.2f} conf {old_conf:.2f}→{nodes[idx]['confidence']:.2f}")
            else:
                print(f"[auto-evidence] expand #{idx}→#{new_idx}: {rel} str={strength} (conf unchanged at {nodes[idx]['confidence']:.2f})")

    return _finalize(nodes, d["threshold"], d["sim_mode"])


@graph_bp.route("/graph/elaborate", methods=["POST"])
@_with_thinking("elaborate")
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

    # Hebbian: источник elaborate-а получает сильное обращение (реальная работа)
    touch_node(idx)

    source = nodes[idx]["text"]
    topic = _graph["meta"].get("topic", "")
    new_thoughts = []
    parent_depth = nodes[idx]["depth"]
    parent_topic = nodes[idx]["topic"] or topic

    # Build context from goal node (if exists) for mode-aware elaboration
    goal_context = ""
    goal_nodes = [n for n in nodes if n.get("type") == "goal" and n.get("depth", 0) >= 0]
    if goal_nodes:
        from .modes import get_elaborate_hint
        hint = get_elaborate_hint(goal_nodes[0], d["lang"])
        if hint:
            goal_context = "\n" + hint

    system = _p(d["lang"], "think")

    attempts = 0
    while len(new_thoughts) < n and attempts < n * 3:
        attempts += 1
        user = f"{_p(d['lang'], 'topic')}: {topic}\n{_p(d['lang'], 'elaborate')}: {source}"
        if goal_context:
            user += goal_context
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
                d_val = _d_from_relation(rel, strength)
                nodes[idx]["confidence"] = _bayesian_update_distinct(old_conf, d_val)
                print(f"[auto-evidence] elaborate #{idx}→#{new_idx}: {rel} str={strength} d={d_val:.2f} conf {old_conf:.2f}→{nodes[idx]['confidence']:.2f}")
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
@_with_thinking("smartdc")
def graph_smartdc():
    """Smart DC: generate thesis, antithesis, neutral → centroid → synthesis."""
    d = _p_data()
    node_idx = int(d.get("index", -1))

    nodes = _graph["nodes"]
    if node_idx < 0 or node_idx >= len(nodes):
        return jsonify({"error": "invalid node index"})

    # Hebbian: нода проходит через диалектическую проверку — сильное обращение
    touch_node(node_idx)

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

    # Phase 1: Divergence — generate 3 poles (shared with execute_dispute)
    from .dialectic import generate_poles, synthesize
    context_block = ""
    if evidence_context:
        context_block = "\n\nExisting evidence:\n" + "\n".join(evidence_context[:5])

    poles_dict = generate_poles(
        statement, lang=d["lang"], temp=d["temp"], top_k=d["top_k"],
        seed=d["seed"], pole_tokens=200, context_block=context_block,
        return_entropy=True,
    )
    poles = [
        {"role": "dc_thesis", "text": poles_dict["thesis"]["text"], "entropy": poles_dict["thesis"]["entropy"]},
        {"role": "dc_antithesis", "text": poles_dict["antithesis"]["text"], "entropy": poles_dict["antithesis"]["entropy"]},
        {"role": "dc_neutral", "text": poles_dict["neutral"]["text"], "entropy": poles_dict["neutral"]["entropy"]},
    ]
    for p in poles:
        print(f"[smartdc] {p['role']}: {p['text'][:80]}...")

    # Phase 2: Convergence — synthesize (not concise — full tokens for essay-grade output)
    synthesis_text, synthesis_ent = synthesize(
        statement,
        poles[0]["text"], poles[1]["text"], poles[2]["text"],
        lang=d["lang"], temp=0.7, top_k=d["top_k"], max_tokens=300,
        concise=False, seed=d["seed"], return_entropy=True,
    )
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
@_with_thinking("pump")
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
    from .horizon import CognitiveState, create_horizon
    horizon_data = _graph.get("_horizon")
    if horizon_data:
        h = CognitiveState.from_dict(horizon_data)
    else:
        mode_id = _graph["meta"].get("mode", "horizon")
        h = create_horizon(mode_id)
    params = h.to_llm_params()
    print(f"[horizon manual] state={h.state} precision={h.precision:.2f} temp={params['temperature']:.2f} top_k={params['top_k']}")
    return jsonify(params)


# --------------- XOR Compare (LLM-as-judge) ---------------

@graph_bp.route("/graph/compare", methods=["POST"])
def graph_compare():
    """LLM-as-judge: compare verified options and pick the best."""
    from .prompts import _p
    d = _p_data()
    indices = d.get("indices", [])
    lang = d.get("lang", "ru")
    temp = float(d.get("temp", 0.5))
    top_k = int(d.get("top_k", 40))

    nodes = _graph["nodes"]
    options = []
    for idx in indices:
        if idx < len(nodes):
            n = nodes[idx]
            options.append({"idx": idx, "text": n.get("text", ""), "confidence": n.get("confidence", 0.5)})

    if len(options) < 2:
        return jsonify({"error": "need at least 2 options"})

    # Build comparison prompt
    options_text = "\n".join(f"{i+1}. {o['text']} (confidence: {o['confidence']:.0%})" for i, o in enumerate(options))
    if lang == "ru":
        system = "/no_think\nТы — судья. Сравни варианты и выбери лучший. Объясни почему в 2-3 предложениях. Ответь в формате:\nЛучший: [номер]\nПочему: [объяснение]"
    else:
        system = "/no_think\nYou are a judge. Compare options and pick the best. Explain in 2-3 sentences. Format:\nBest: [number]\nWhy: [explanation]"

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": options_text},
    ]
    result, _ = _graph_generate(messages, max_tokens=200, temp=temp, top_k=top_k)

    # Parse winner number
    winner_idx = None
    for line in result.split('\n'):
        line_lower = line.strip().lower()
        if line_lower.startswith('лучший:') or line_lower.startswith('best:'):
            try:
                num = int(''.join(c for c in line.split(':')[1] if c.isdigit()))
                if 1 <= num <= len(options):
                    winner_idx = options[num - 1]["idx"]
            except (ValueError, IndexError):
                pass

    return jsonify({
        "result": result,
        "winner_idx": winner_idx,
        "options": options,
    })


# --------------- Bayesian LLM helpers ---------------

@graph_bp.route("/graph/bayes-estimate-prior", methods=["POST"])
def graph_bayes_estimate_prior():
    """LLM estimates initial prior probability for a hypothesis."""
    d = _p_data()
    hypothesis = d.get("hypothesis", "")
    lang = d.get("lang", "ru")
    temp = float(d.get("temp", 0.3))
    top_k = int(d.get("top_k", 40))

    if not hypothesis:
        return jsonify({"error": "need hypothesis"})

    if lang == "ru":
        system = "/no_think\nОцени начальную вероятность гипотезы (0.01-0.99) без дополнительных данных, только на основе общих знаний.\nОтветь СТРОГО в формате:\nprior: число\nпочему: одно предложение"
    else:
        system = "/no_think\nEstimate initial probability of the hypothesis (0.01-0.99) based on general knowledge only.\nAnswer STRICTLY in format:\nprior: number\nwhy: one sentence"

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Гипотеза: {hypothesis}"},
    ]
    result, _ = _graph_generate(messages, max_tokens=80, temp=temp, top_k=top_k)

    prior = 0.5
    why = ""
    for line in result.split('\n'):
        line_s = line.strip().lower()
        if line_s.startswith('prior:'):
            try:
                prior = float(line_s.split(':')[1].strip())
                prior = max(0.01, min(0.99, prior))
            except ValueError:
                pass
        elif line_s.startswith('почему:') or line_s.startswith('why:'):
            why = line.split(':', 1)[1].strip()

    return jsonify({"prior": round(prior, 2), "why": why, "raw": result})


@graph_bp.route("/graph/bayes-estimate", methods=["POST"])
def graph_bayes_estimate():
    """LLM estimates relation and strength for an observation given a hypothesis."""
    d = _p_data()
    hypothesis = d.get("hypothesis", "")
    observation = d.get("observation", "")
    lang = d.get("lang", "ru")
    temp = float(d.get("temp", 0.3))
    top_k = int(d.get("top_k", 40))

    if not hypothesis or not observation:
        return jsonify({"error": "need hypothesis and observation"})

    if lang == "ru":
        system = "/no_think\nОцени: наблюдение подтверждает или опровергает гипотезу? Насколько сильно (0.1-0.99)?\nОтветь СТРОГО в формате:\nrelation: supports или contradicts\nstrength: число\nпочему: одно предложение"
    else:
        system = "/no_think\nEstimate: does the observation support or contradict the hypothesis? How strongly (0.1-0.99)?\nAnswer STRICTLY in format:\nrelation: supports or contradicts\nstrength: number\nwhy: one sentence"

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Гипотеза: {hypothesis}\nНаблюдение: {observation}"},
    ]
    result, _ = _graph_generate(messages, max_tokens=100, temp=temp, top_k=top_k)

    # Parse
    relation = "supports"
    strength = 0.7
    why = ""
    for line in result.split('\n'):
        line_s = line.strip().lower()
        if line_s.startswith('relation:'):
            val = line_s.split(':')[1].strip()
            if 'contradict' in val or 'опроверг' in val:
                relation = "contradicts"
        elif line_s.startswith('strength:'):
            try:
                strength = float(line_s.split(':')[1].strip())
                strength = max(0.1, min(0.99, strength))
            except ValueError:
                pass
        elif line_s.startswith('почему:') or line_s.startswith('why:'):
            why = line.split(':', 1)[1].strip()

    return jsonify({"relation": relation, "strength": round(strength, 2), "why": why, "raw": result})


@graph_bp.route("/graph/bayes-suggest", methods=["POST"])
def graph_bayes_suggest():
    """LLM suggests observations to look for given a hypothesis."""
    d = _p_data()
    hypothesis = d.get("hypothesis", "")
    existing = d.get("existing", [])
    lang = d.get("lang", "ru")
    temp = float(d.get("temp", 0.7))
    top_k = int(d.get("top_k", 40))

    if not hypothesis:
        return jsonify({"error": "need hypothesis"})

    existing_text = ""
    if existing:
        existing_text = "\nУже проверено:\n" + "\n".join(f"- {e}" for e in existing)

    if lang == "ru":
        system = "/no_think\nПредложи 3-5 наблюдений которые помогут проверить гипотезу. Для каждого укажи: что искать и подтвердит оно или опровергнет. Коротко, по одной строке."
    else:
        system = "/no_think\nSuggest 3-5 observations to test the hypothesis. For each: what to look for and whether it would support or contradict. One line each."

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Гипотеза: {hypothesis}{existing_text}"},
    ]
    result, _ = _graph_generate(messages, max_tokens=300, temp=temp, top_k=top_k)

    return jsonify({"suggestions": result})


# --------------- Horizon feedback ---------------

@graph_bp.route("/graph/horizon-feedback", methods=["POST"])
def graph_horizon_feedback():
    """Store feedback for CognitiveState. Applied on next tick."""
    data = request.get_json(force=True)
    _graph["_horizon_feedback"] = {
        "surprise": data.get("surprise"),
        "gradient": data.get("gradient"),
        "novelty": data.get("novelty"),
        "phase": data.get("phase"),
    }
    return jsonify({"ok": True})


# --------------- tick() — phase-based automatic thinking ---------------

@graph_bp.route("/workspace/list", methods=["GET"])
def workspace_list():
    """List all workspaces with active flag + node counts."""
    from .workspace import get_workspace_manager
    wm = get_workspace_manager()
    return jsonify({
        "workspaces": wm.list_workspaces(),
        "active": wm.active_id,
    })


@graph_bp.route("/workspace/create", methods=["POST"])
def workspace_create():
    """Create a new workspace (directory + meta entry)."""
    from .workspace import get_workspace_manager
    d = request.get_json(force=True) or {}
    ws_id = (d.get("id") or "").strip().lower().replace(" ", "_")
    title = d.get("title", ws_id)
    tags = d.get("tags", [])
    if not ws_id:
        return jsonify({"error": "id required"})
    try:
        info = get_workspace_manager().create(ws_id, title, tags)
        return jsonify({"ok": True, "workspace": info})
    except Exception as e:
        return jsonify({"error": str(e)})


@graph_bp.route("/workspace/switch", methods=["POST"])
def workspace_switch():
    """Switch active workspace. Flushes current, loads target graph into _graph.

    Cross-graph auto-seed: если target content-граф пустой И есть history
    за последние 7 дней по этому graph_id — подбросим 3 seed-ноды.
    """
    from .workspace import get_workspace_manager
    d = request.get_json(force=True) or {}
    ws_id = d.get("id", "").strip().lower()
    auto_seed = bool(d.get("auto_seed", True))
    if not ws_id:
        return jsonify({"error": "id required"})
    try:
        get_workspace_manager().switch(ws_id)
    except Exception as e:
        return jsonify({"error": str(e)})

    seeded = None
    if auto_seed and not _graph.get("nodes"):
        try:
            from .cross_graph import seed_from_history
            seeded = seed_from_history(days=7, limit=3, graph_id=ws_id)
        except Exception as e:
            print(f"[workspace/switch] auto-seed failed: {e}")

    return jsonify({"ok": True, "active": ws_id, "seeded": seeded})


@graph_bp.route("/workspace/save", methods=["POST"])
def workspace_save():
    """Flush current _graph to active workspace's graph.json."""
    from .workspace import get_workspace_manager
    get_workspace_manager().save_active()
    return jsonify({"ok": True})


@graph_bp.route("/workspace/delete", methods=["POST"])
def workspace_delete():
    from .workspace import get_workspace_manager
    d = request.get_json(force=True) or {}
    ws_id = d.get("id", "").strip().lower()
    try:
        get_workspace_manager().delete(ws_id)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)})


@graph_bp.route("/workspace/cross-edges", methods=["GET"])
def workspace_cross_edges():
    from .workspace import get_workspace_manager
    ws_filter = request.args.get("workspace")
    return jsonify({
        "edges": get_workspace_manager().list_cross_edges(ws_filter),
    })


@graph_bp.route("/workspace/find-cross", methods=["POST"])
def workspace_find_cross():
    """Scan other workspaces for distinct candidates. Saves matches as cross_edges."""
    from .workspace import get_workspace_manager
    d = request.get_json(force=True) or {}
    k = int(d.get("k", 5))
    tau = float(d.get("tau_in", 0.3))
    wm = get_workspace_manager()
    hits = wm.find_cross_candidates(k=k, tau_in=tau)
    saved = 0
    for h in hits:
        if wm.add_cross_edge(h["from_graph"], h["from_node"],
                             h["to_graph"], h["to_node"], h["d"]):
            saved += 1
    return jsonify({"hits": hits, "saved": saved})


@graph_bp.route("/workspace/seed-from-history", methods=["POST"])
def workspace_seed_from_history():
    """Continuity между сессиями: выводы из state_graph → seeds в текущем графе.

    Body (всё опционально):
      {
        "days": 7,            # окно истории
        "limit": 5,            # максимум seeds
        "graph_id": "main",    # фильтр по workspace (пусто = любой)
        "topic_hint": ""       # топик для новых нод
      }

    Создаёт `rendered=false` ноды с embedding'ами из state_embeddings.
    provenance: `node.seeded_from = <state_hash>`.
    """
    from .cross_graph import seed_from_history
    d = request.get_json(force=True) or {}
    result = seed_from_history(
        days=float(d.get("days", 7)),
        limit=int(d.get("limit", 5)),
        graph_id=d.get("graph_id"),
        topic_hint=(d.get("topic_hint") or "").strip(),
    )
    return jsonify(result)


@graph_bp.route("/workspace/meta", methods=["GET"])
def workspace_meta():
    """Meta-graph: derived view of graph-of-graphs."""
    from .workspace import get_workspace_manager
    return jsonify(get_workspace_manager().meta_graph())


@graph_bp.route("/graph/self", methods=["GET"])
def graph_self():
    """Read state graph — system's history-as-graph.

    Query params:
      limit: int (default 50)
      action: str (filter by action)
      user_initiated: "true"/"false" (filter)
      tail: "true" → last N instead of first N
    """
    from .state_graph import get_state_graph
    sg = get_state_graph()
    limit = int(request.args.get("limit", 50))
    action = request.args.get("action")
    ui_arg = request.args.get("user_initiated")
    want_tail = request.args.get("tail", "true").lower() == "true"

    def filt(entry):
        if action and entry.get("action") != action:
            return False
        if ui_arg is not None:
            want = ui_arg.lower() == "true"
            if bool(entry.get("user_initiated")) != want:
                return False
        return True

    entries = sg.read_all(filter_fn=filt)
    total = len(entries)
    if want_tail:
        entries = entries[-limit:]
    else:
        entries = entries[:limit]

    return jsonify({
        "entries": entries,
        "total": total,
        "returned": len(entries),
        "last_hash": sg._last_hash,
    })


@graph_bp.route("/graph/self/similar", methods=["POST"])
def graph_self_similar():
    """Episodic query: find k past state_nodes most similar to given text/state.

    Body: { "query": "text to embed", "k": 5 } OR { "embedding": [...], "k": 5 }
    """
    from .state_graph import get_state_graph
    from .api_backend import api_get_embedding
    d = request.get_json(force=True) or {}
    k = int(d.get("k", 5))
    query_emb = d.get("embedding")
    if not query_emb:
        query_text = d.get("query", "").strip()
        if not query_text:
            return jsonify({"error": "missing query or embedding"})
        try:
            query_emb = api_get_embedding(query_text)
        except Exception as e:
            return jsonify({"error": f"embedding failed: {e}"})
    sg = get_state_graph()
    results = sg.query_similar(query_emb, k=k)
    return jsonify({"results": results, "count": len(results)})


@graph_bp.route("/graph/brainstorm-seed", methods=["POST"])
def graph_brainstorm_seed():
    """Embedding-first brainstorm: topic → N пертурбированных векторов без LLM текста.

    Для каждого seed создаётся node с `rendered=false, text='💭'`, но с
    реальным embedding'ом. distinct/routing работают сразу. Текст
    рендерится лениво через POST /graph/render-node когда юзер откроет.

    Body: { "topic": "...", "n": 5, "sigma": 0.15, "novelty_threshold": 0.25 }
    """
    from .api_backend import api_get_embedding
    d = request.get_json(force=True) or {}
    topic = (d.get("topic") or "").strip()
    n = int(d.get("n", 5))
    sigma = float(d.get("sigma", 1.0))
    novelty = float(d.get("novelty_threshold", 0.2))
    if not topic:
        return jsonify({"error": "empty topic"})

    try:
        seed_emb = api_get_embedding(topic)
    except Exception as e:
        return jsonify({"error": f"seed embedding failed: {e}"})
    if not seed_emb:
        return jsonify({"error": "seed embedding returned empty"})

    existing = [nd.get("embedding") for nd in _graph["nodes"] if nd.get("embedding")]
    samples = sample_in_embedding_space(
        seed_emb, n=n, sigma=sigma,
        novelty_threshold=novelty,
        existing_embeddings=existing,
    )

    created = []
    for emb in samples:
        idx = _add_node(
            text="💭",
            depth=0,
            topic=topic,
            node_type="hypothesis",
            embedding=emb,
            rendered=False,
        )
        created.append(idx)

    _graph["meta"].setdefault("topic", topic)
    return jsonify({
        "created": created,
        "n_requested": n,
        "n_sampled": len(samples),
        "topic": topic,
    })


@graph_bp.route("/graph/render-node", methods=["POST"])
def graph_render_node():
    """Text-on-demand: разворачиваем unrendered-ноду в текст через LLM.

    Использует топик + тексты соседей (incoming directed) как контекст.
    Idempotent: если нода уже rendered, возвращает cached text.

    Body: { "index": N, "lang": "ru" }
    """
    d = request.get_json(force=True) or {}
    idx = int(d.get("index", -1))
    lang = d.get("lang", "ru")
    nodes = _graph["nodes"]
    if idx < 0 or idx >= len(nodes):
        return jsonify({"error": "invalid index"})

    node = nodes[idx]
    if node.get("rendered", True) and node.get("text") and node["text"] != "💭":
        return jsonify({"ok": True, "text": node["text"], "cached": True, "index": idx})

    topic = _graph["meta"].get("topic", "") or node.get("topic", "")
    neighbor_texts: list[str] = []
    for pair in _graph["edges"].get("directed", []):
        if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
            continue
        src, dst = pair
        if dst == idx and 0 <= src < len(nodes):
            t = nodes[src].get("text", "")
            if t and nodes[src].get("rendered", True):
                neighbor_texts.append(t[:80])

    if lang == "ru":
        system = ("/no_think\nРазверни seed-идею в одно короткое предложение "
                  "по теме. Без вступлений. Одно предложение.")
        user = f"Тема: {topic or '(не задана)'}"
        if neighbor_texts:
            user += "\nСоседние мысли:\n" + "\n".join(f"- {t}" for t in neighbor_texts[:3])
        user += "\n\nСформулируй новую идею одним предложением:"
    else:
        system = ("/no_think\nExpand the seed into one short sentence on topic. "
                  "No preamble. One sentence.")
        user = f"Topic: {topic or '(unset)'}"
        if neighbor_texts:
            user += "\nNeighbor thoughts:\n" + "\n".join(f"- {t}" for t in neighbor_texts[:3])
        user += "\n\nOne-sentence new idea:"

    try:
        text, _ = _graph_generate(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            max_tokens=80, temp=0.7, top_k=40,
        )
    except Exception as e:
        return jsonify({"error": f"generation failed: {e}"})

    rendered_text = _clean_thought(text or "", topic) or "[unable to render]"
    node["text"] = rendered_text
    node["rendered"] = True
    # Hebbian: рендер по клику — юзер реально посмотрел ноду, это обращение.
    touch_node(idx)
    # Embedding cache может стать stale: новый текст ≠ seed perturbation.
    # Оставляем старый embedding — он всё ещё описывает позицию ноды в
    # пространстве мыслей (rendered text подстроен под эту позицию).
    return jsonify({"ok": True, "text": rendered_text, "cached": False, "index": idx})


@graph_bp.route("/graph/consolidate", methods=["POST"])
def graph_consolidate():
    """Консолидация памяти — прунинг слабых нод + архив старого state_graph.

    Body (всё опционально):
      {
        "dry_run": false,
        "confidence_threshold": 0.3,  # content: ниже = кандидат к удалению
        "content_age_days": 30,       # content: давность last_accessed
        "state_retain_days": 14       # state_graph: новее этого — держим в main
      }

    Возвращает summary {content, state}.
    """
    from .consolidation import consolidate_all

    d = request.get_json(force=True) or {}
    result = consolidate_all(
        confidence_threshold=float(d.get("confidence_threshold", 0.3)),
        content_age_days=float(d.get("content_age_days", 30)),
        state_retain_days=float(d.get("state_retain_days", 14)),
        dry_run=bool(d.get("dry_run", False)),
    )
    return jsonify(result)


@graph_bp.route("/graph/synthesize", methods=["POST"])
def graph_synthesize():
    """Forced synthesis: top-N hypothesis/evidence/thought → LLM-синтез →
    synthesis-нода. Общий endpoint для graph-tab autorun и для юзер-вызова.

    Body: {n: 5, lang: "ru", max_tokens: 3000}
    """
    from .graph_logic import force_synthesize_top
    d = _p_data()
    n = int(d.get("n", 5))
    max_tokens = int(d.get("max_tokens", 3000))
    syn = force_synthesize_top(n=n, lang=d.get("lang", "ru"), max_tokens=max_tokens)
    if not syn:
        return jsonify({"error": "no_nodes_to_synthesize"})
    return jsonify({"ok": True, **syn})


@graph_bp.route("/graph/tick", methods=["POST"])
def graph_tick():
    """Foreground tick — ping в CognitiveLoop.

    Единый когнитивный контур (cognitive_loop.py) владеет и фоновой работой
    (Scout/DMN/NE decay), и foreground тиком. Юзер-инициированный тик
    разделяет timestamp с фоном, чтобы DMN не лез следующие 30 секунд
    (общий NE-бюджет).

    Mode config тюнит Horizon пресеты (τ_in/τ_out/γ/policy); логика сама
    эмерджентна из distinct-зон в tick_nand.
    """
    from .cognitive_loop import get_cognitive_loop

    d = _p_data()
    result = get_cognitive_loop().tick_foreground(
        threshold=d["threshold"],
        sim_mode=d["sim_mode"],
        stable_threshold=float(d.get("stable_threshold", 0.8)),
        force_collapse=d.get("force_collapse", False),
        max_meta=int(d.get("max_meta", 2)),
        min_hyp=int(d.get("min_hyp", 5)),
    )
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
    # Hebbian: к гипотезе прицепляют доказательство — сильное обращение.
    # Boost 0 чтобы не мешать следующему _bayesian_update_distinct —
    # последний сам выставит новое значение confidence.
    touch_node(hyp_idx, boost=0.0)
    # Auto-convert to hypothesis if adding evidence to a thought
    if hyp.get("type", "thought") == "thought":
        hyp["type"] = "hypothesis"
    prior = hyp["confidence"]

    # NAND Bayesian update via distinct distance (d derived from relation+strength)
    d_val = _d_from_relation(relation, strength)
    posterior = _bayesian_update_distinct(prior, d_val)

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

    # Hebbian: и источник, и цель навигации получают обращение. Цель — полный
    # boost (именно туда перешли), источник — слабее (мы уже были там).
    touch_node(to_idx)
    touch_node(from_idx, boost=TOUCH_BOOST_DEFAULT * 0.5)

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


@graph_bp.route("/graph/actions-timeline", methods=["GET"])
def graph_actions_timeline():
    """Вернуть action + outcome ноды в хронологическом порядке.

    Используется Lab UI для timeline-view: scroll через conversation
    (user_chat / baddle_reply) + proactive actions (sync_seeking /
    suggestions / bridges) + outcomes (delta_sync_error). Всё что
    происходит с системой относительно юзера — в одном списке.

    Query params:
      • `limit` (default 100, max 500) — ограничить count
      • `since_ts` — unix timestamp, возвращать только после
      • `kinds` — comma-separated action_kind filter (напр. "user_chat,baddle_reply")
      • `actor` — "user" | "baddle" | (omit для обоих)
      • `include_outcomes` — "1" (default) | "0"
    """
    from datetime import datetime as _dt
    try:
        limit = min(500, max(1, int(request.args.get("limit", "100"))))
    except Exception:
        limit = 100
    try:
        since_ts = float(request.args.get("since_ts", "0"))
    except Exception:
        since_ts = 0.0
    kinds_filter = set()
    kinds_raw = (request.args.get("kinds") or "").strip()
    if kinds_raw:
        kinds_filter = {k.strip() for k in kinds_raw.split(",") if k.strip()}
    actor_filter = (request.args.get("actor") or "").strip().lower()
    include_outcomes = (request.args.get("include_outcomes", "1") != "0")

    def _parse_ts(ts_iso) -> float:
        if not ts_iso:
            return 0.0
        try:
            return _dt.fromisoformat(str(ts_iso).replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0.0

    items = []
    nodes = _graph.get("nodes", [])
    for idx, n in enumerate(nodes):
        ntype = n.get("type")
        if ntype == "action":
            if actor_filter and n.get("actor") != actor_filter:
                continue
            if kinds_filter and n.get("action_kind") not in kinds_filter:
                continue
        elif ntype == "outcome":
            if not include_outcomes:
                continue
        else:
            continue

        ts = _parse_ts(n.get("created_at"))
        if since_ts and ts < since_ts:
            continue

        item = {
            "idx": idx,
            "type": ntype,
            "text": n.get("text", ""),
            "ts": ts,
            "created_at": n.get("created_at"),
        }
        if ntype == "action":
            item["actor"] = n.get("actor")
            item["action_kind"] = n.get("action_kind")
            item["closed"] = bool(n.get("closed"))
            item["outcome_idx"] = n.get("outcome_idx")
            ctx = n.get("context") or {}
            # Безопасно: берём только скалярные поля для UI
            item["time_of_day"] = ctx.get("time_of_day")
            item["sync_regime"] = ctx.get("sync_regime")
            item["sentiment"] = ctx.get("sentiment")  # для user_chat
        else:  # outcome
            item["linked_action_idx"] = n.get("linked_action_idx")
            item["delta_sync_error"] = n.get("delta_sync_error")
            item["user_reaction"] = n.get("user_reaction")
            item["latency_s"] = n.get("latency_s")
        items.append(item)

    # Сорт по ts возрастающий (chronological — старые сверху)
    items.sort(key=lambda x: x["ts"])
    # Применяем limit с конца (самые свежие)
    if len(items) > limit:
        items = items[-limit:]

    return jsonify({"items": items, "total_returned": len(items)})


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


# ═══ HRV integration ═══════════════════════════════════════════════════════

@graph_bp.route("/hrv/start", methods=["POST"])
def hrv_start():
    """Start HRV monitoring. mode: simulator (default) or polar."""
    d = request.get_json(force=True) if request.is_json else {}
    mode = d.get("mode", "simulator")
    kwargs = {}
    if "target_hr" in d:
        kwargs["target_hr"] = float(d["target_hr"])
    if "target_coherence" in d:
        kwargs["target_coherence"] = float(d["target_coherence"])
    mgr = get_hrv_manager()
    ok = mgr.start(mode=mode, **kwargs)
    return jsonify({"ok": ok, "status": mgr.get_status()})


@graph_bp.route("/hrv/stop", methods=["POST"])
def hrv_stop():
    mgr = get_hrv_manager()
    mgr.stop()
    return jsonify({"ok": True})


@graph_bp.route("/hrv/status", methods=["GET"])
def hrv_status():
    mgr = get_hrv_manager()
    return jsonify(mgr.get_status())


@graph_bp.route("/hrv/metrics", methods=["GET"])
def hrv_metrics():
    mgr = get_hrv_manager()
    metrics = mgr.get_metrics()
    state = mgr.get_baddle_state()

    # Push to UserState — HRV — сигнал тела пользователя, не системы.
    # Системная нейрохимия эволюционирует по собственным сигналам графа.
    from .user_state import get_user_state
    get_user_state().update_from_hrv(
        coherence=state.get("coherence"),
        rmssd=state.get("rmssd"),
        stress=state.get("stress"),
        activity=state.get("activity_magnitude"),
    )

    return jsonify({
        "metrics": metrics,
        "baddle_state": state,
    })


@graph_bp.route("/hrv/calibrate", methods=["POST"])
def hrv_calibrate():
    mgr = get_hrv_manager()
    baseline = mgr.calibrate()
    return jsonify({"ok": bool(baseline), "baseline": baseline})


@graph_bp.route("/hrv/simulate", methods=["POST"])
def hrv_simulate():
    """Adjust simulator params at runtime (for demo).

    Принимает: hr, coherence, activity (все опциональны).
    activity ∈ [0, 5] — magnitude движения (0=лежишь, 1=ходьба, 2+=бег).
    """
    d = request.get_json(force=True)
    mgr = get_hrv_manager()
    mgr.set_simulator_state(
        target_hr=d.get("hr"),
        target_coherence=d.get("coherence"),
        activity=d.get("activity"),
    )
    return jsonify({"ok": True, "status": mgr.get_status()})
