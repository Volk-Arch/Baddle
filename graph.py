"""baddle — graph thinking mode (Blueprint)"""

import re
import numpy as np
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

def _ensure_lists(thoughts: list, topic: str = ""):
    """Ensure entropies/depths/topics lists are synced with thoughts length."""
    ent_list = _graph.setdefault("entropies", [])
    depth_list = _graph.setdefault("depths", [])
    topics_list = _graph.setdefault("topics", [])
    while len(ent_list) < len(thoughts):
        ent_list.append({"avg": 0.0, "unc": 0.0})
    while len(depth_list) < len(thoughts):
        depth_list.append(0)
    while len(topics_list) < len(thoughts):
        topics_list.append(topic)
    return ent_list, depth_list, topics_list

# ── module-level model reference (set by init_graph) ─────────────────────────
_llm = None


def init_graph(llm):
    """Call once after model is loaded to give graph mode access to the model."""
    global _llm
    _llm = llm


# ── state ────────────────────────────────────────────────────────────────────
def _fresh_graph():
    return {"thoughts": [], "topic": "", "manual_links": [], "manual_unlinks": [],
            "embeddings": [], "entropies": [], "depths": [], "topics": [],
            "directed_edges": [], "hub_nodes": set()}

_graph = _fresh_graph()


@graph_bp.route("/graph/reset", methods=["POST"])
def graph_reset():
    """Reset all graph state."""
    global _graph
    _graph = _fresh_graph()
    return jsonify({"ok": True})


# ── generation helpers ───────────────────────────────────────────────────────

def _graph_generate(messages: list[dict], max_tokens: int = 60, temp: float = 0.9, top_k: int = 40, seed: int = -1) -> tuple[str, dict]:
    """Generate text from chat messages using create_chat_completion. Returns (text, entropy_info)."""
    use_logprobs = getattr(_graph_generate, '_logprobs_ok', True)
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
    except (ValueError, RuntimeError):
        # logprobs not supported — disable permanently and retry
        _graph_generate._logprobs_ok = False
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
    # Remove <think>...</think> blocks
    low = raw.lower()
    if "</think>" in low:
        text = raw[low.index("</think>") + 8:]
    else:
        text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL | re.IGNORECASE)
        text = text if text.strip() else raw
    # Clean residual tags
    text = re.sub(r"<[^>]*>", "", text)
    text = text.strip()
    # Extract per-token entropy from logprobs
    logprobs_data = result["choices"][0].get("logprobs")
    avg_ent = 0.0
    unc = 0.0
    token_ents = []  # per-token: [{token, ent}, ...]
    if not logprobs_data:
        print(f"[graph_generate] logprobs missing (use_logprobs={use_logprobs}, _logprobs_ok={getattr(_graph_generate, '_logprobs_ok', True)})")
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


def _generate_thought(topic: str, existing: list[str], lang: str = "en", temp: float = 0.9, top_k: int = 40, seed: int = -1) -> tuple[str, float]:
    """Generate one short thought about the topic via chat. Returns (text, mean_entropy)."""
    system = _p(lang, "think")
    user = f"{_p(lang, 'topic')}: {topic}"
    if existing:
        user += f"\n{_p(lang, 'already')}:\n" + "\n".join(f"- {t}" for t in existing[-5:])
        user += f"\n{_p(lang, 'new_idea')}"
    else:
        user += f"\n{_p(lang, 'one_idea')}"

    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    text, ent = _graph_generate(messages, max_tokens=120, temp=temp, top_k=top_k, seed=seed)
    return _clean_thought(text, topic), ent


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
    depth_list = _graph.get("depths", [])
    manual_links = {(a, b) for a, b in _graph.get("manual_links", [])}
    manual_unlinks = {(a, b) for a, b in _graph.get("manual_unlinks", [])}
    edges = []
    for i in range(n):
        if i < len(depth_list) and depth_list[i] == -1:
            continue  # skip topic root nodes from similarity
        for j in range(i + 1, n):
            if j < len(depth_list) and depth_list[j] == -1:
                continue
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
        "entropies": _graph.get("entropies", []),
        "depths": _graph.get("depths", []),
        "topics": _graph.get("topics", []),
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
    lang = data.get("lang", "en")
    temp = float(data.get("temp", 0.9))
    top_k = int(data.get("top_k", 40))
    seed = int(data.get("seed", -1))
    existing = data.get("existing", [])

    if not topic:
        return jsonify({"error": "empty topic"})

    _graph["topic"] = topic
    if not existing:
        _graph["manual_links"] = []
        _graph["manual_unlinks"] = []
        _graph["embeddings"] = []
        _graph["entropies"] = []
        _graph["depths"] = []
        _graph["topics"] = []
        _graph["directed_edges"] = []
        _graph["hub_nodes"] = set()
    thoughts = list(existing)
    ent_list, depth_list, topics_list = _ensure_lists(thoughts, topic)

    # Add topic as root node (depth=-1) if not already present
    topic_idx = -1
    for i, t in enumerate(thoughts):
        if (depth_list[i] if i < len(depth_list) else 0) == -1 and t == topic:
            topic_idx = i
            break
    if topic_idx < 0:
        thoughts.append(topic)
        ent_list.append({"avg": 0.0, "unc": 0.0})
        depth_list.append(-1)
        topics_list.append(topic)
        topic_idx = len(thoughts) - 1

    new_thoughts = []
    directed = _graph.setdefault("directed_edges", [])
    manual_links = _graph.setdefault("manual_links", [])

    attempts = 0
    while len(new_thoughts) < n and attempts < n * 3:
        attempts += 1
        t, ent = _generate_thought(topic, new_thoughts, lang, temp, top_k, seed)
        if not t or len(t) < 10:
            continue
        if t.lower().strip("., ") in ("qwen3", "qwen", "llama", "gpt", "assistant"):
            continue
        if any(t.lower() == ex.lower() for ex in thoughts):
            continue
        thoughts.append(t)
        ent_list.append(ent)
        depth_list.append(0)
        topics_list.append(topic)
        new_idx = len(thoughts) - 1
        # Link topic root → new thought
        directed.append([topic_idx, new_idx])
        pair = [min(topic_idx, new_idx), max(topic_idx, new_idx)]
        if pair not in manual_links:
            manual_links.append(pair)
        new_thoughts.append(t)

    _graph["thoughts"] = thoughts
    edges = _compute_edges(thoughts, threshold, sim_mode)
    clusters = _find_clusters(len(thoughts), edges, threshold)

    return _graph_response(thoughts, edges, clusters)


@graph_bp.route("/graph/recalc", methods=["POST"])
def graph_recalc():
    """Recompute edges and clusters with a new threshold (no generation)."""
    data = request.get_json(force=True)
    threshold = float(data.get("threshold", 0.91))
    sim_mode = data.get("sim_mode", "embedding")
    thoughts = _graph["thoughts"]
    if not thoughts:
        return jsonify({"error": "no thoughts"})
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
    _graph.setdefault("entropies", []).append({"avg": 0.0, "unc": 0.0})
    _graph.setdefault("depths", []).append(0)
    _graph.setdefault("topics", []).append("")
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
    ent_list = _graph.get("entropies", [])
    if idx < len(ent_list):
        ent_list.pop(idx)
    depth_list = _graph.get("depths", [])
    if idx < len(depth_list):
        depth_list.pop(idx)
    topics_list = _graph.get("topics", [])
    if idx < len(topics_list):
        topics_list.pop(idx)
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
    lang = data.get("lang", "en")
    temp = float(data.get("temp", 0.7))
    top_k = int(data.get("top_k", 40))
    seed = int(data.get("seed", -1))
    collapse_mode = data.get("collapse_mode", "short")
    thoughts = _graph["thoughts"]
    topic = _graph["topic"]

    if not indices or not thoughts:
        return jsonify({"error": "no cluster to collapse"})

    cluster_texts = [thoughts[i] for i in indices if i < len(thoughts)]
    if collapse_mode == "long":
        system = _p(lang, "collapse_long")
        instruction = _p(lang, "write_long")
        max_tokens = 2000
    else:
        system = _p(lang, "collapse")
        instruction = _p(lang, "write_para")
        max_tokens = 800
    user = f"{_p(lang, 'topic')}: {topic}\n\n{_p(lang, 'ideas')}:\n"
    user += "\n".join(f"- {t}" for t in cluster_texts)
    user += f"\n\n{instruction}"

    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    text, ent = _graph_generate(messages, max_tokens=max_tokens, temp=temp, top_k=top_k, seed=seed)
    # If text was cut mid-sentence, try to continue
    if text and not text.rstrip().endswith(('.', '!', '?', '。', '»', '"')):
        messages_cont = list(messages) + [{"role": "assistant", "content": text}]
        cont, ent2 = _graph_generate(messages_cont, max_tokens=max_tokens // 2, temp=temp, top_k=top_k, seed=seed)
        if cont:
            text = text + cont
            # Merge entropy data
            if ent.get("tokens") and ent2.get("tokens"):
                ent["tokens"].extend(ent2["tokens"])
            ent["avg"] = (ent["avg"] + ent2["avg"]) / 2
            ent["unc"] = max(ent["unc"], ent2["unc"])

    valid_indices = [i for i in indices if i < len(thoughts)]
    ent_list = _graph.setdefault("entropies", [])
    depth_list = _graph.setdefault("depths", [])
    # Collapsed node inherits minimum depth from cluster (it replaces the group)
    min_depth = min((depth_list[i] for i in valid_indices if i < len(depth_list)), default=0)
    topics_list = _graph.setdefault("topics", [])
    collapsed_topic = next((topics_list[i] for i in valid_indices if i < len(topics_list) and topics_list[i]), topic)
    for i in sorted(valid_indices, reverse=True):
        thoughts.pop(i)
        if i < len(ent_list):
            ent_list.pop(i)
        if i < len(depth_list):
            depth_list.pop(i)
        if i < len(topics_list):
            topics_list.pop(i)
    _remap_manual_edges(valid_indices)
    thoughts.append(text)
    ent_list.append(ent)
    depth_list.append(min_depth)
    topics_list.append(collapsed_topic)
    new_idx = len(thoughts) - 1

    # Link collapsed node to its topic root (find topic node with depth=-1)
    directed = _graph.setdefault("directed_edges", [])
    manual_links = _graph.setdefault("manual_links", [])
    for i, t in enumerate(thoughts):
        if i < len(depth_list) and depth_list[i] == -1 and i < len(topics_list) and topics_list[i] == collapsed_topic:
            directed.append([i, new_idx])
            pair = [min(i, new_idx), max(i, new_idx)]
            if pair not in manual_links:
                manual_links.append(pair)
            break

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
    if "entropies" in data:
        _graph["entropies"] = data["entropies"]
    if "depths" in data:
        _graph["depths"] = data["depths"]
    if "topics" in data:
        _graph["topics"] = data["topics"]
    if "topic" in data:
        _graph["topic"] = data["topic"]
    # If saved edges/clusters provided, use them directly (undo restore)
    if "edges" in data and "clusters" in data:
        _graph["embeddings"] = []
        return _graph_response(thoughts, data["edges"], data["clusters"])
    # Otherwise recompute
    _graph["embeddings"] = []
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
    lang = data.get("lang", "en")
    temp = float(data.get("temp", 0.9))
    top_k = int(data.get("top_k", 40))
    seed = int(data.get("seed", -1))
    thoughts = _graph["thoughts"]

    if idx < 0 or idx >= len(thoughts):
        return jsonify({"error": "invalid index"})

    source = thoughts[idx]
    topic = _graph.get("topic", "")
    new_thoughts = []
    ent_list, depth_list, topics_list = _ensure_lists(thoughts, topic)
    parent_depth = depth_list[idx] if idx < len(depth_list) else 0
    parent_topic = topics_list[idx] if idx < len(topics_list) else topic

    system = _p(lang, "think")

    attempts = 0
    while len(new_thoughts) < n and attempts < n * 3:
        attempts += 1
        user = f"{_p(lang, 'topic')}: {topic}\n{_p(lang, 'source')}: {source}"
        if new_thoughts:
            user += f"\n{_p(lang, 'already_gen')}:\n" + "\n".join(f"- {t}" for t in new_thoughts)
        user += f"\n{_p(lang, 'branch')}"

        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        t, ent = _graph_generate(messages, max_tokens=120, temp=temp, top_k=top_k, seed=seed)
        t = _clean_thought(t, topic)

        if not t or len(t) < 10:
            continue
        if any(t.lower() == ex.lower() for ex in thoughts):
            continue
        thoughts.append(t)
        ent_list.append(ent)
        depth_list.append(parent_depth + 1)
        topics_list.append(parent_topic)
        new_thoughts.append(t)

    # Track directed edges for expand (parent → child)
    manual_links = _graph.setdefault("manual_links", [])
    directed = _graph.setdefault("directed_edges", [])
    base_idx = len(thoughts) - len(new_thoughts)
    for j in range(len(new_thoughts)):
        new_idx = base_idx + j
        pair = [min(idx, new_idx), max(idx, new_idx)]
        if pair not in manual_links:
            manual_links.append(pair)
        directed.append([idx, new_idx])

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
    lang = data.get("lang", "en")
    temp = float(data.get("temp", 0.9))
    top_k = int(data.get("top_k", 40))
    seed = int(data.get("seed", -1))
    direction = data.get("direction", "").strip()
    thoughts = _graph["thoughts"]

    if idx < 0 or idx >= len(thoughts):
        return jsonify({"error": "invalid index"})

    source = thoughts[idx]
    topic = _graph.get("topic", "")
    new_thoughts = []
    ent_list, depth_list, topics_list = _ensure_lists(thoughts, topic)
    parent_depth = depth_list[idx] if idx < len(depth_list) else 0
    parent_topic = topics_list[idx] if idx < len(topics_list) else topic

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
        t, ent = _graph_generate(messages, max_tokens=120, temp=temp, top_k=top_k, seed=seed)
        t = _clean_thought(t, topic)

        if not t or len(t) < 10:
            continue
        if any(t.lower() == ex.lower() for ex in thoughts):
            continue
        thoughts.append(t)
        ent_list.append(ent)
        depth_list.append(parent_depth + 1)
        topics_list.append(parent_topic)
        new_thoughts.append(t)

    # Force-link new thoughts to source and track directed edges
    manual_links = _graph.setdefault("manual_links", [])
    directed = _graph.setdefault("directed_edges", [])
    hubs = _graph.setdefault("hub_nodes", set())
    source_idx = idx
    hubs.add(source_idx)
    base_idx = len(thoughts) - len(new_thoughts)
    for j in range(len(new_thoughts)):
        new_idx = base_idx + j
        pair = [min(source_idx, new_idx), max(source_idx, new_idx)]
        if pair not in manual_links:
            manual_links.append(pair)
        directed.append([source_idx, new_idx])

    _graph["thoughts"] = thoughts
    edges = _compute_edges(thoughts, threshold, sim_mode)
    clusters = _find_clusters(len(thoughts), edges, threshold)
    return _graph_response(thoughts, edges, clusters)
