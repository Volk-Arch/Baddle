"""baddle — graph thinking mode (Blueprint)"""

import re
import logging
import numpy as np

log = logging.getLogger(__name__)
from flask import Blueprint, request, jsonify

from main import _sample, _get_logits, _entropy, format_chat, get_embedding, cosine_similarity

graph_bp = Blueprint("graph", __name__)

# ── language-aware system prompts ────────────────────────────────────────────
_PROMPTS = {
    "en": {
        "think":       "/no_think\nYou generate ONE short idea (1 sentence, max 15 words). No numbering, no bullets, just the idea. Answer directly.",
        "collapse":    "/no_think\nYou combine ideas into a coherent paragraph. Write naturally, do not list the ideas separately. Answer directly.",
        "deeper":      "Go DEEPER into this specific idea. Unpack a detail, consequence, or mechanism. Not a new angle — dig into THIS idea.",
        "branch":      "Generate a NEW related idea that branches from the source idea. A different angle on the same subject.",
        "new_idea":    "Generate a NEW different idea.",
        "one_idea":    "Generate one idea.",
        "topic":       "Topic",
        "already":     "Already suggested",
        "ideas":       "Ideas to combine",
        "write_para":  "Write one coherent paragraph that connects these ideas.",
        "collapse_long": "/no_think\nYou write a detailed essay combining the given ideas. Develop each idea, show connections between them, add reasoning and examples. Write naturally as flowing text, not a list. Answer directly.",
        "write_long":  "Write a detailed, multi-paragraph text that develops and connects these ideas.",
        "source":      "Source idea",
        "elaborate":   "Idea to elaborate",
        "direction":   "Direction",
        "already_gen": "Already generated",
        "already_elab":"Already elaborated",
    },
    "ru": {
        "think":       "/no_think\nТы генерируешь ОДНУ короткую идею (1 предложение, максимум 15 слов). Без нумерации, без списков, только идея. Отвечай сразу.",
        "collapse":    "/no_think\nОбъедини идеи в связный абзац. Пиши естественно, не перечисляй идеи отдельно. Отвечай сразу.",
        "deeper":      "Углубись В ЭТУ конкретную идею. Раскрой деталь, следствие или механизм. Не новый ракурс — копай ВГЛУБЬ.",
        "branch":      "Сгенерируй НОВУЮ связанную идею, ответвлённую от исходной. Другой ракурс на ту же тему.",
        "new_idea":    "Сгенерируй НОВУЮ, другую идею.",
        "one_idea":    "Сгенерируй одну идею.",
        "topic":       "Тема",
        "already":     "Уже предложено",
        "ideas":       "Идеи для объединения",
        "write_para":  "Напиши один связный абзац, объединяющий эти идеи.",
        "collapse_long": "/no_think\nНапиши развёрнутое эссе, объединяющее данные идеи. Раскрой каждую идею, покажи связи между ними, добавь рассуждения и примеры. Пиши связным текстом, не списком. Отвечай сразу.",
        "write_long":  "Напиши развёрнутый текст из нескольких абзацев, раскрывающий и связывающий эти идеи.",
        "source":      "Исходная идея",
        "elaborate":   "Идея для углубления",
        "direction":   "Направление",
        "already_gen": "Уже сгенерировано",
        "already_elab":"Уже углублено",
    },
}

def _p(lang: str, key: str) -> str:
    return _PROMPTS.get(lang, _PROMPTS["en"]).get(key, _PROMPTS["en"][key])


# ── node helpers ─────────────────────────────────────────────────────────────

def _make_node(id: int, text: str, depth: int = 0, topic: str = "",
               entropy: dict | None = None, confidence: float = 0.5) -> dict:
    """Create a node dict with all required fields."""
    return {
        "id": id,
        "text": text,
        "entropy": entropy or {"avg": 0.0, "unc": 0.0},
        "depth": depth,
        "topic": topic,
        "confidence": round(confidence, 2),
    }


def _ensure_node_fields(nodes: list[dict]):
    """Ensure each node dict has all required fields."""
    for i, node in enumerate(nodes):
        node.setdefault("id", i)
        node.setdefault("text", "")
        node.setdefault("entropy", {"avg": 0.0, "unc": 0.0})
        node.setdefault("depth", 0)
        node.setdefault("topic", "")
        node.setdefault("confidence", 0.5)


def _get_texts(nodes: list[dict] | None = None) -> list[str]:
    """Return list of texts from nodes (for similarity, prompts)."""
    if nodes is None:
        nodes = _graph["nodes"]
    return [n["text"] for n in nodes]


def _add_node(text: str, depth: int = 0, topic: str = "",
              entropy: dict | None = None, confidence: float = 0.5) -> int:
    """Create node with next id, append to graph, return new index."""
    nodes = _graph["nodes"]
    new_id = len(nodes)
    nodes.append(_make_node(new_id, text, depth, topic, entropy, confidence))
    return new_id


def _remove_node(idx: int):
    """Remove node at idx, remap all edge indices and embeddings."""
    nodes = _graph["nodes"]
    if idx < 0 or idx >= len(nodes):
        return
    nodes.pop(idx)
    # Reassign sequential ids
    for i, node in enumerate(nodes):
        node["id"] = i
    # Remap edges and embeddings
    _remap_edges([idx])


# ── module-level model reference (set by init_graph) ─────────────────────────
_llm = None


def init_graph(llm):
    """Call once after model is loaded to give graph mode access to the model."""
    global _llm
    _llm = llm


# ── state ────────────────────────────────────────────────────────────────────
def _fresh_graph():
    return {
        "nodes": [],
        "edges": {
            "manual_links": [],
            "manual_unlinks": [],
            "directed": [],
        },
        "meta": {
            "topic": "",
            "hub_nodes": set(),
        },
        "embeddings": [],  # cache, not persisted
    }

_graph = _fresh_graph()


@graph_bp.route("/graph/reset", methods=["POST"])
def graph_reset():
    """Reset all graph state."""
    global _graph
    _graph = _fresh_graph()
    return jsonify({"ok": True})


# ── generation helpers ───────────────────────────────────────────────────────

def _api_available() -> bool:
    """Check if API mode is configured for graph generation."""
    from api_backend import use_api_for
    return use_api_for("graph")


def _graph_generate(messages: list[dict], max_tokens: int = 60, temp: float = 0.9, top_k: int = 40, seed: int = -1) -> tuple[str, dict]:
    """Generate text from chat messages. Uses API or local model based on settings.
    Returns (text, entropy_info)."""
    from api_backend import use_api_for

    if use_api_for("graph"):
        return _graph_generate_api(messages, max_tokens, temp, top_k)
    return _graph_generate_local(messages, max_tokens, temp, top_k, seed)


def _graph_generate_api(messages: list[dict], max_tokens: int, temp: float, top_k: int) -> tuple[str, dict]:
    """Generate via OpenAI-compatible API."""
    from api_backend import api_chat_completion

    text, avg_ent, unc_pct, token_ents_raw, token_texts = api_chat_completion(
        messages, max_tokens=max_tokens, temperature=temp, top_k=top_k,
    )
    # Clean thinking blocks
    text = _clean_thinking(text)
    # Build token entropy info
    token_ents = []
    for i, e in enumerate(token_ents_raw):
        tok = token_texts[i] if i < len(token_texts) else ""
        token_ents.append({"token": tok, "ent": round(float(e), 3)})
    return text, {"avg": round(float(avg_ent), 3), "unc": round(float(unc_pct), 3), "tokens": token_ents}


def _graph_generate_local(messages: list[dict], max_tokens: int, temp: float, top_k: int, seed: int) -> tuple[str, dict]:
    """Generate via local llama.cpp model."""
    use_logprobs = getattr(_graph_generate_local, '_logprobs_ok', True)
    # Reset KV cache before each generation to avoid stale state
    _llm.reset()
    try:
        kwargs = dict(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temp,
            top_k=top_k,
            top_p=0.95,
            repeat_penalty=1.1,
        )
        if seed >= 0:
            kwargs["seed"] = seed
        if use_logprobs:
            kwargs["logprobs"] = True
            kwargs["top_logprobs"] = 1
        result = _llm.create_chat_completion(**kwargs)
    except (ValueError, RuntimeError) as e:
        log.warning(f"[graph_generate_local] logprobs failed: {e}, retrying without")
        _graph_generate_local._logprobs_ok = False
        _llm.reset()
        result = _llm.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temp,
            top_k=top_k,
            top_p=0.95,
            repeat_penalty=1.1,
        )
    raw = result["choices"][0]["message"]["content"] or ""
    text = _clean_thinking(raw)
    # Extract per-token entropy from logprobs
    logprobs_data = result["choices"][0].get("logprobs")
    avg_ent = 0.0
    unc = 0.0
    token_ents = []
    if logprobs_data and logprobs_data.get("content"):
        for lp in logprobs_data["content"]:
            if "logprob" in lp:
                e = -lp["logprob"]
                token_ents.append({"token": lp.get("token", ""), "ent": round(float(e), 3)})
        if token_ents:
            lps = [t["ent"] for t in token_ents]
            avg_ent = sum(lps) / len(lps)
            unc = sum(1 for lp in lps if lp > 2.0) / len(lps)
    return text, {"avg": round(float(avg_ent), 3), "unc": round(float(unc), 3), "tokens": token_ents}


def _clean_thinking(raw: str) -> str:
    """Remove <think>...</think> blocks and residual tags from generated text."""
    low = raw.lower()
    if "</think>" in low:
        text = raw[low.index("</think>") + 8:]
    else:
        text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL | re.IGNORECASE)
        text = text if text.strip() else raw
    text = re.sub(r"<[^>]*>", "", text)
    return text.strip()


def _clean_thought(text: str, topic: str) -> str:
    """Clean generated thought text — remove thinking, pick best line."""
    text = re.split(r"\s*(?:Human|User|Assistant)\s*:", text, flags=re.IGNORECASE)[0]
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    # If topic has cyrillic, prefer cyrillic lines
    has_cyr = bool(re.search(r"[а-яА-ЯёЁ]", topic))
    if has_cyr:
        cyr_lines = [l for l in lines if re.search(r"[а-яА-ЯёЁ]", l)]
        if cyr_lines:
            lines = cyr_lines
    best = lines[-1] if lines else ""
    for prefix in ["- ", "* ", "1. ", "1) "]:
        if best.startswith(prefix):
            best = best[len(prefix):]
    best = re.sub(r"^\d+[.)]\s*", "", best)
    best = re.sub(r"^(Topic|Тема)\s*:.*?[.!?]\s*", "", best, flags=re.IGNORECASE)
    return best.strip()


def _generate_thought(topic: str, existing: list[str], lang: str = "en", temp: float = 0.9, top_k: int = 40, seed: int = -1, max_tokens: int = 60) -> tuple[str, float]:
    """Generate one short thought about the topic via chat. Returns (text, mean_entropy)."""
    system = _p(lang, "think")
    user = f"{_p(lang, 'topic')}: {topic}"
    if existing:
        user += f"\n{_p(lang, 'already')}:\n" + "\n".join(f"- {t}" for t in existing[-5:])
        user += f"\n{_p(lang, 'new_idea')}"
    else:
        user += f"\n{_p(lang, 'one_idea')}"

    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    text, ent = _graph_generate(messages, max_tokens=max_tokens, temp=temp, top_k=top_k, seed=seed)
    return _clean_thought(text, topic), ent


# ── similarity & clustering ──────────────────────────────────────────────────

def _ensure_embeddings(texts: list[str]):
    """Compute and cache embeddings for all texts."""
    from api_backend import use_api_for, api_get_embedding

    cache = _graph.setdefault("embeddings", [])
    while len(cache) < len(texts):
        idx = len(cache)
        if use_api_for("embeddings"):
            emb = api_get_embedding(texts[idx])
            cache.append(emb if emb else None)
        else:
            emb = get_embedding(_llm, texts[idx])
            cache.append(emb.tolist() if len(emb) > 0 else None)
    while len(cache) > len(texts):
        cache.pop()


def _jaccard(i: int, j: int, texts: list[str]) -> float:
    """Jaccard similarity on token sets."""
    if _llm is None:
        # Fallback: word-level jaccard when no local model
        toks_i = set(texts[i].lower().split())
        toks_j = set(texts[j].lower().split())
    else:
        toks_i = set(_llm.tokenize(texts[i].encode(), add_bos=False))
        toks_j = set(_llm.tokenize(texts[j].encode(), add_bos=False))
    if not toks_i or not toks_j:
        return 0.0
    return len(toks_i & toks_j) / len(toks_i | toks_j)


def _embedding_sim(i: int, j: int, texts: list[str]) -> float:
    """Cosine similarity on embeddings."""
    cache = _graph.get("embeddings", [])
    emb_i = cache[i] if i < len(cache) else None
    emb_j = cache[j] if j < len(cache) else None
    if emb_i is not None and emb_j is not None:
        return cosine_similarity(np.array(emb_i, dtype=np.float32),
                                 np.array(emb_j, dtype=np.float32))
    return _jaccard(i, j, texts)  # fallback


def _compute_edges(nodes: list[dict], threshold: float, sim_mode: str = "embedding") -> list[dict]:
    """Compute similarity edges between nodes.

    sim_mode: "embedding" (cosine on model embeddings), "jaccard" (token overlap), or "off" (no edges).
    """
    texts = _get_texts(nodes)
    if sim_mode == "off":
        return []
    if sim_mode == "embedding":
        try:
            _ensure_embeddings(texts)
            sim_fn = _embedding_sim
        except Exception as e:
            print(f"[graph] Embedding failed, falling back to Jaccard: {e}")
            sim_fn = _jaccard
    else:
        sim_fn = _jaccard

    n = len(nodes)
    manual_links = {(a, b) for a, b in _graph["edges"].get("manual_links", [])}
    manual_unlinks = {(a, b) for a, b in _graph["edges"].get("manual_unlinks", [])}
    edges = []
    for i in range(n):
        if nodes[i]["depth"] == -1:
            continue  # skip topic root nodes from similarity
        for j in range(i + 1, n):
            if nodes[j]["depth"] == -1:
                continue
            pair = (i, j)
            if pair in manual_unlinks:
                continue
            sim = sim_fn(i, j, texts)
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


def _remap_edges(removed_indices: list[int]):
    """Remap all edge indices and embedding cache after nodes are removed."""
    removed = set(removed_indices)
    def remap(idx):
        return idx - sum(1 for r in removed if r < idx)

    edges_dict = _graph["edges"]
    for key in ("manual_links", "manual_unlinks"):
        old = edges_dict.get(key, [])
        new = []
        for pair in old:
            a, b = pair
            if a in removed or b in removed:
                continue
            a2, b2 = remap(a), remap(b)
            new.append([min(a2, b2), max(a2, b2)])
        edges_dict[key] = new

    # Remap directed edges (ordered: [from, to])
    old_dir = edges_dict.get("directed", [])
    new_dir = []
    for pair in old_dir:
        a, b = pair
        if a in removed or b in removed:
            continue
        new_dir.append([remap(a), remap(b)])
    edges_dict["directed"] = new_dir

    # Remap hub nodes
    meta = _graph["meta"]
    old_hubs = meta.get("hub_nodes", set())
    meta["hub_nodes"] = {remap(h) for h in old_hubs if h not in removed}

    # Remap embedding cache
    cache = _graph.get("embeddings", [])
    for i in sorted(removed, reverse=True):
        if i < len(cache):
            cache.pop(i)


def _graph_response(nodes, edges, clusters, **extra):
    """Build standard graph response with node objects."""
    resp = {
        "nodes": nodes,
        "edges": edges,
        "clusters": clusters,
        "directed_edges": _graph["edges"].get("directed", []),
        "hub_nodes": list(_graph["meta"].get("hub_nodes", set())),
    }
    resp.update(extra)
    return jsonify(resp)


# ── routes ───────────────────────────────────────────────────────────────────

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

    # Add topic as root node (depth=-1) if not already present
    topic_idx = -1
    for i, nd in enumerate(nodes):
        if nd["depth"] == -1 and nd["text"] == topic:
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
        new_idx = _add_node(t, depth=0, topic=topic, entropy=ent)
        # Link topic root → new thought
        directed.append([topic_idx, new_idx])
        pair = [min(topic_idx, new_idx), max(topic_idx, new_idx)]
        if pair not in manual_links:
            manual_links.append(pair)
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
    data = request.get_json(force=True)
    text = data.get("text", "").strip()
    threshold = float(data.get("threshold", 0.91))
    sim_mode = data.get("sim_mode", "embedding")
    if not text:
        return jsonify({"error": "empty thought"})
    _add_node(text, depth=0, topic="")
    nodes = _graph["nodes"]
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
        user = f"{_p(lang, 'topic')}: {topic}\n\n{_p(lang, 'ideas')}:\n"
        user += "\n".join(f"- {t}" for t in cluster_texts)
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
        # Test mode: add collapsed text as new node but keep originals
        collapsed_topic = next((nodes[i]["topic"] for i in valid_indices if nodes[i]["topic"]), topic)
        min_depth = min((nodes[i]["depth"] for i in valid_indices), default=0)
        new_idx = _add_node(text, depth=min_depth, topic=collapsed_topic, entropy=ent)
        # Link to topic root
        directed = _graph["edges"]["directed"]
        manual_links = _graph["edges"]["manual_links"]
        for i, nd in enumerate(nodes):
            if nd["depth"] == -1 and nd["topic"] == collapsed_topic:
                directed.append([i, new_idx])
                pair = [min(i, new_idx), max(i, new_idx)]
                if pair not in manual_links:
                    manual_links.append(pair)
                break
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
        _add_node(t, depth=parent_depth + 1, topic=parent_topic, entropy=ent)
        new_thoughts.append(t)

    # Track directed edges for expand (parent → child)
    manual_links = _graph["edges"]["manual_links"]
    directed = _graph["edges"]["directed"]
    base_idx = len(nodes) - len(new_thoughts)
    for j in range(len(new_thoughts)):
        new_idx = base_idx + j
        pair = [min(idx, new_idx), max(idx, new_idx)]
        if pair not in manual_links:
            manual_links.append(pair)
        directed.append([idx, new_idx])

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
        _add_node(t, depth=parent_depth + 1, topic=parent_topic, entropy=ent)
        new_thoughts.append(t)

    # Force-link new thoughts to source and track directed edges
    manual_links = _graph["edges"]["manual_links"]
    directed = _graph["edges"]["directed"]
    hubs = _graph["meta"].setdefault("hub_nodes", set())
    source_idx = idx
    hubs.add(source_idx)
    base_idx = len(nodes) - len(new_thoughts)
    for j in range(len(new_thoughts)):
        new_idx = base_idx + j
        pair = [min(source_idx, new_idx), max(source_idx, new_idx)]
        if pair not in manual_links:
            manual_links.append(pair)
        directed.append([source_idx, new_idx])

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
        import re
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
