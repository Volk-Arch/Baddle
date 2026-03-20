"""baddle — graph thinking mode (Blueprint)"""

import re
import numpy as np
from flask import Blueprint, request, jsonify

from main import _sample, _get_logits, _entropy, format_chat, get_embedding, cosine_similarity

graph_bp = Blueprint("graph", __name__)

# ── module-level model reference (set by init_graph) ─────────────────────────
_llm = None


def init_graph(llm):
    """Call once after model is loaded to give graph mode access to the model."""
    global _llm
    _llm = llm


# ── state ────────────────────────────────────────────────────────────────────
_graph = {"thoughts": [], "topic": "", "manual_links": [], "manual_unlinks": [], "embeddings": []}


# ── generation helpers ───────────────────────────────────────────────────────

def _graph_generate(messages: list[dict], max_tokens: int = 60, temp: float = 0.9) -> str:
    """Generate text from chat messages for graph mode."""
    prompt_str = format_chat(_llm, messages)
    _llm.reset()
    tokens = _llm.tokenize(prompt_str.encode())
    _llm.eval(tokens)

    raw = ""
    eos = _llm.token_eos()
    think_done = False
    for _ in range(max_tokens):
        tok = _sample(_llm, temp)
        _llm.eval([tok])
        if tok == eos:
            break
        piece = _llm.detokenize([tok]).decode("utf-8", errors="replace")
        raw += piece
        if not think_done and "</think>" in raw.lower():
            think_done = True
        if think_done and re.search(r"<\|", raw[raw.lower().rfind("</think>") + 8:]):
            break
    low = raw.lower()
    if "</think>" in low:
        text = raw[low.index("</think>") + 8:]
    else:
        text = raw
    text = re.sub(r"<[^>]*>", "", text)
    return text.strip()


def _generate_thought(topic: str, existing: list[str]) -> str:
    """Generate one short thought about the topic via chat."""
    system = "/no_think\nYou generate ONE short idea (1 sentence, max 15 words). No numbering, no bullets, just the idea. Answer in the same language as the topic. Answer directly."
    user = f"Topic: {topic}"
    if existing:
        user += "\nAlready suggested:\n" + "\n".join(f"- {t}" for t in existing[-5:])
        user += "\nGenerate a NEW different idea."
    else:
        user += "\nGenerate one idea."

    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    text = _graph_generate(messages, max_tokens=120)
    text = re.split(r"\s*(?:Human|User|Assistant)\s*:", text, flags=re.IGNORECASE)[0]
    text = text.split("\n")[0].strip()
    for prefix in ["- ", "* ", "1. ", "1) "]:
        if text.startswith(prefix):
            text = text[len(prefix):]
    text = re.sub(r"\s*(user|assistant|system)\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\d+[.)]\s*", "", text)
    return text.strip()


# ── similarity & clustering ──────────────────────────────────────────────────

def _ensure_embeddings(thoughts: list[str]):
    """Compute and cache embeddings for all thoughts."""
    cache = _graph.setdefault("embeddings", [])
    while len(cache) < len(thoughts):
        idx = len(cache)
        emb = get_embedding(_llm, thoughts[idx])
        cache.append(emb.tolist() if len(emb) > 0 else None)
    while len(cache) > len(thoughts):
        cache.pop()


def _jaccard(i: int, j: int, thoughts: list[str]) -> float:
    """Jaccard similarity on token sets."""
    toks_i = set(_llm.tokenize(thoughts[i].encode(), add_bos=False))
    toks_j = set(_llm.tokenize(thoughts[j].encode(), add_bos=False))
    if not toks_i or not toks_j:
        return 0.0
    return len(toks_i & toks_j) / len(toks_i | toks_j)


def _embedding_sim(i: int, j: int, thoughts: list[str]) -> float:
    """Cosine similarity on embeddings."""
    cache = _graph.get("embeddings", [])
    emb_i = cache[i] if i < len(cache) else None
    emb_j = cache[j] if j < len(cache) else None
    if emb_i is not None and emb_j is not None:
        return cosine_similarity(np.array(emb_i, dtype=np.float32),
                                 np.array(emb_j, dtype=np.float32))
    return _jaccard(i, j, thoughts)  # fallback


def _compute_edges(thoughts: list[str], threshold: float, sim_mode: str = "embedding") -> list[dict]:
    """Compute similarity edges between thoughts.

    sim_mode: "embedding" (cosine on model embeddings) or "jaccard" (token overlap).
    """
    if sim_mode == "embedding":
        _ensure_embeddings(thoughts)
        sim_fn = _embedding_sim
    else:
        sim_fn = _jaccard

    n = len(thoughts)
    manual_links = {(a, b) for a, b in _graph.get("manual_links", [])}
    manual_unlinks = {(a, b) for a, b in _graph.get("manual_unlinks", [])}
    edges = []
    for i in range(n):
        for j in range(i + 1, n):
            pair = (i, j)
            if pair in manual_unlinks:
                continue
            sim = sim_fn(i, j, thoughts)
            manual = pair in manual_links
            if sim >= threshold or manual:
                edges.append({
                    "from": i, "to": j,
                    "weight": round(sim, 3),
                    "manual": manual and sim < threshold,
                })
    return edges


def _find_clusters(n: int, edges: list[dict], threshold: float) -> list[list[int]]:
    """Find connected components as clusters."""
    adj = {i: set() for i in range(n)}
    for e in edges:
        adj[e["from"]].add(e["to"])
        adj[e["to"]].add(e["from"])
    visited = set()
    clusters = []
    for i in range(n):
        if i in visited:
            continue
        cluster = []
        queue = [i]
        while queue:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            cluster.append(node)
            queue.extend(adj[node] - visited)
        if len(cluster) >= 2:
            clusters.append(sorted(cluster))
    return clusters


def _remap_manual_edges(removed_indices: list[int]):
    """Remap manual link/unlink indices and embedding cache after nodes are removed."""
    removed = set(removed_indices)
    def remap(idx):
        return idx - sum(1 for r in removed if r < idx)
    for key in ("manual_links", "manual_unlinks"):
        old = _graph.get(key, [])
        new = []
        for pair in old:
            a, b = pair
            if a in removed or b in removed:
                continue
            a2, b2 = remap(a), remap(b)
            new.append([min(a2, b2), max(a2, b2)])
        _graph[key] = new
    # Remap directed edges (ordered: [from, to])
    old_dir = _graph.get("directed_edges", [])
    new_dir = []
    for pair in old_dir:
        a, b = pair
        if a in removed or b in removed:
            continue
        new_dir.append([remap(a), remap(b)])
    _graph["directed_edges"] = new_dir
    # Remap hub nodes
    old_hubs = _graph.get("hub_nodes", set())
    _graph["hub_nodes"] = {remap(h) for h in old_hubs if h not in removed}
    cache = _graph.get("embeddings", [])
    for i in sorted(removed, reverse=True):
        if i < len(cache):
            cache.pop(i)


def _graph_response(thoughts, edges, clusters, **extra):
    """Build standard graph response with directed edges and hub nodes."""
    resp = {
        "thoughts": thoughts, "edges": edges, "clusters": clusters,
        "directed_edges": _graph.get("directed_edges", []),
        "hub_nodes": list(_graph.get("hub_nodes", set())),
    }
    resp.update(extra)
    return jsonify(resp)


# ── routes ───────────────────────────────────────────────────────────────────

@graph_bp.route("/graph/think", methods=["POST"])
def graph_think():
    """Generate N thoughts about a topic."""
    if _llm is None:
        return jsonify({"error": "Graph mode requires in-process model"})
    data = request.get_json(force=True)
    topic = data.get("topic", "").strip()
    n = int(data.get("n", 6))
    threshold = float(data.get("threshold", 0.91))
    sim_mode = data.get("sim_mode", "embedding")
    existing = data.get("existing", [])

    if not topic:
        return jsonify({"error": "empty topic"})

    _graph["topic"] = topic
    if not existing:
        _graph["manual_links"] = []
        _graph["manual_unlinks"] = []
        _graph["embeddings"] = []
        _graph["directed_edges"] = []
        _graph["hub_nodes"] = set()
    thoughts = list(existing)
    new_thoughts = []  # only thoughts generated in THIS call (for dedup prompt)

    attempts = 0
    while len(new_thoughts) < n and attempts < n * 3:
        attempts += 1
        t = _generate_thought(topic, new_thoughts)
        if not t or len(t) < 10:
            continue
        if t.lower().strip("., ") in ("qwen3", "qwen", "llama", "gpt", "assistant"):
            continue
        if any(t.lower() == ex.lower() for ex in thoughts):
            continue
        thoughts.append(t)
        new_thoughts.append(t)

    _graph["thoughts"] = thoughts
    edges = _compute_edges(thoughts, threshold, sim_mode)
    clusters = _find_clusters(len(thoughts), edges, threshold)

    return _graph_response(thoughts, edges, clusters)


@graph_bp.route("/graph/add", methods=["POST"])
def graph_add():
    """Add a user-provided thought and recompute edges."""
    data = request.get_json(force=True)
    text = data.get("text", "").strip()
    threshold = float(data.get("threshold", 0.91))
    sim_mode = data.get("sim_mode", "embedding")
    if not text:
        return jsonify({"error": "empty thought"})
    _graph["thoughts"].append(text)
    thoughts = _graph["thoughts"]
    edges = _compute_edges(thoughts, threshold, sim_mode)
    clusters = _find_clusters(len(thoughts), edges, threshold)
    return _graph_response(thoughts, edges, clusters)


@graph_bp.route("/graph/remove", methods=["POST"])
def graph_remove():
    """Remove a thought by index and recompute edges."""
    data = request.get_json(force=True)
    idx = int(data.get("index", -1))
    threshold = float(data.get("threshold", 0.91))
    sim_mode = data.get("sim_mode", "embedding")
    thoughts = _graph["thoughts"]
    if idx < 0 or idx >= len(thoughts):
        return jsonify({"error": "invalid index"})
    thoughts.pop(idx)
    _remap_manual_edges([idx])
    edges = _compute_edges(thoughts, threshold, sim_mode)
    clusters = _find_clusters(len(thoughts), edges, threshold)
    return _graph_response(thoughts, edges, clusters)


@graph_bp.route("/graph/link", methods=["POST"])
def graph_link():
    """Manually add or remove an edge between two thoughts."""
    data = request.get_json(force=True)
    a = int(data.get("a", -1))
    b = int(data.get("b", -1))
    threshold = float(data.get("threshold", 0.91))
    sim_mode = data.get("sim_mode", "embedding")
    thoughts = _graph["thoughts"]
    if a < 0 or b < 0 or a >= len(thoughts) or b >= len(thoughts) or a == b:
        return jsonify({"error": "invalid indices"})
    pair = (min(a, b), max(a, b))
    manual_links = _graph.setdefault("manual_links", [])
    manual_unlinks = _graph.setdefault("manual_unlinks", [])
    edges_before = _compute_edges(thoughts, threshold, sim_mode)
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
    edges = _compute_edges(thoughts, threshold, sim_mode)
    clusters = _find_clusters(len(thoughts), edges, threshold)
    return _graph_response(thoughts, edges, clusters)


@graph_bp.route("/graph/collapse", methods=["POST"])
def graph_collapse():
    """Collapse a cluster: generate summary, remove source nodes, add result."""
    if _llm is None:
        return jsonify({"error": "requires in-process model"})
    data = request.get_json(force=True)
    indices = data.get("cluster", [])
    threshold = float(data.get("threshold", 0.91))
    sim_mode = data.get("sim_mode", "embedding")
    thoughts = _graph["thoughts"]
    topic = _graph["topic"]

    if not indices or not thoughts:
        return jsonify({"error": "no cluster to collapse"})

    cluster_texts = [thoughts[i] for i in indices if i < len(thoughts)]
    system = "/no_think\nYou combine ideas into a coherent paragraph. Write naturally, do not list the ideas separately. Answer in the same language as the topic. Answer directly."
    user = f"Topic: {topic}\n\nIdeas to combine:\n"
    user += "\n".join(f"- {t}" for t in cluster_texts)
    user += "\n\nWrite one coherent paragraph that connects these ideas."

    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    text = _graph_generate(messages, max_tokens=400, temp=0.7)

    valid_indices = [i for i in indices if i < len(thoughts)]
    for i in sorted(valid_indices, reverse=True):
        thoughts.pop(i)
    _remap_manual_edges(valid_indices)
    thoughts.append(text)
    _graph["thoughts"] = thoughts

    edges = _compute_edges(thoughts, threshold, sim_mode)
    clusters = _find_clusters(len(thoughts), edges, threshold)

    return _graph_response(thoughts, edges, clusters, text=text)


@graph_bp.route("/graph/sync", methods=["POST"])
def graph_sync():
    """Restore graph state (used by undo)."""
    data = request.get_json(force=True)
    thoughts = data.get("thoughts", [])
    threshold = float(data.get("threshold", 0.91))
    sim_mode = data.get("sim_mode", "embedding")
    _graph["thoughts"] = thoughts
    _graph["manual_links"] = data.get("manual_links", [])
    _graph["manual_unlinks"] = data.get("manual_unlinks", [])
    _graph["directed_edges"] = data.get("directed_edges", _graph.get("directed_edges", []))
    _graph["hub_nodes"] = set(data.get("hub_nodes", _graph.get("hub_nodes", set())))
    if "topic" in data:
        _graph["topic"] = data["topic"]
    _graph["embeddings"] = []  # will be recomputed
    edges = _compute_edges(thoughts, threshold, sim_mode)
    clusters = _find_clusters(len(thoughts), edges, threshold)
    return _graph_response(thoughts, edges, clusters)


@graph_bp.route("/graph/expand", methods=["POST"])
def graph_expand():
    """Generate child ideas branching from a specific thought (same topic, new angles)."""
    if _llm is None:
        return jsonify({"error": "requires in-process model"})
    data = request.get_json(force=True)
    idx = int(data.get("index", -1))
    n = int(data.get("n", 3))
    threshold = float(data.get("threshold", 0.91))
    sim_mode = data.get("sim_mode", "embedding")
    thoughts = _graph["thoughts"]

    if idx < 0 or idx >= len(thoughts):
        return jsonify({"error": "invalid index"})

    source = thoughts[idx]
    topic = _graph.get("topic", "")
    new_thoughts = []

    system = "/no_think\nYou generate ONE short idea (1 sentence, max 15 words). No numbering, no bullets, just the idea. Answer in the same language as the source text. Answer directly."

    attempts = 0
    while len(new_thoughts) < n and attempts < n * 3:
        attempts += 1
        user = f"Topic: {topic}\nSource idea: {source}"
        if new_thoughts:
            user += "\nAlready generated:\n" + "\n".join(f"- {t}" for t in new_thoughts)
        user += "\nGenerate a NEW related idea that branches from the source idea. A different angle on the same subject."

        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        t = _graph_generate(messages, max_tokens=120)
        t = re.split(r"\s*(?:Human|User|Assistant)\s*:", t, flags=re.IGNORECASE)[0]
        t = t.split("\n")[0].strip()
        for prefix in ["- ", "* ", "1. ", "1) "]:
            if t.startswith(prefix):
                t = t[len(prefix):]
        t = re.sub(r"^\d+[.)]\s*", "", t).strip()

        if not t or len(t) < 10:
            continue
        if any(t.lower() == ex.lower() for ex in thoughts):
            continue
        thoughts.append(t)
        new_thoughts.append(t)

    _graph["thoughts"] = thoughts
    edges = _compute_edges(thoughts, threshold, sim_mode)
    clusters = _find_clusters(len(thoughts), edges, threshold)
    return _graph_response(thoughts, edges, clusters)


@graph_bp.route("/graph/elaborate", methods=["POST"])
def graph_elaborate():
    """Generate deeper ideas that elaborate on a specific thought (the source becomes a hub)."""
    if _llm is None:
        return jsonify({"error": "requires in-process model"})
    data = request.get_json(force=True)
    idx = int(data.get("index", -1))
    n = int(data.get("n", 3))
    threshold = float(data.get("threshold", 0.91))
    sim_mode = data.get("sim_mode", "embedding")
    direction = data.get("direction", "").strip()
    thoughts = _graph["thoughts"]

    if idx < 0 or idx >= len(thoughts):
        return jsonify({"error": "invalid index"})

    source = thoughts[idx]
    topic = _graph.get("topic", "")
    new_thoughts = []

    system = "/no_think\nYou generate ONE short idea (1 sentence, max 15 words). No numbering, no bullets, just the idea. Answer in the same language as the source text. Answer directly."

    attempts = 0
    while len(new_thoughts) < n and attempts < n * 3:
        attempts += 1
        user = f"Topic: {topic}\nIdea to elaborate: {source}"
        if direction:
            user += f"\nDirection: {direction}"
        if new_thoughts:
            user += "\nAlready elaborated:\n" + "\n".join(f"- {t}" for t in new_thoughts)
        user += "\nGo DEEPER into this specific idea. Unpack a detail, consequence, or mechanism. Not a new angle — dig into THIS idea."

        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        t = _graph_generate(messages, max_tokens=120)
        t = re.split(r"\s*(?:Human|User|Assistant)\s*:", t, flags=re.IGNORECASE)[0]
        t = t.split("\n")[0].strip()
        for prefix in ["- ", "* ", "1. ", "1) "]:
            if t.startswith(prefix):
                t = t[len(prefix):]
        t = re.sub(r"^\d+[.)]\s*", "", t).strip()

        if not t or len(t) < 10:
            continue
        if any(t.lower() == ex.lower() for ex in thoughts):
            continue
        thoughts.append(t)
        new_thoughts.append(t)

    # Force-link new thoughts to source and track directed edges
    manual_links = _graph.setdefault("manual_links", [])
    directed = _graph.setdefault("directed_edges", [])
    hubs = _graph.setdefault("hub_nodes", set())
    source_idx = idx
    hubs.add(source_idx)
    for t in new_thoughts:
        new_idx = thoughts.index(t)
        pair = [min(source_idx, new_idx), max(source_idx, new_idx)]
        if pair not in manual_links:
            manual_links.append(pair)
        directed.append([source_idx, new_idx])

    _graph["thoughts"] = thoughts
    edges = _compute_edges(thoughts, threshold, sim_mode)
    clusters = _find_clusters(len(thoughts), edges, threshold)
    return _graph_response(thoughts, edges, clusters)
