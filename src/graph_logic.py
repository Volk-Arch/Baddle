"""baddle — graph thinking logic (nodes, edges, Bayes, similarity, generation)."""

import re
import random
import logging
import threading
from collections import defaultdict, deque
from datetime import datetime, timezone

import numpy as np

from .main import _sample, _get_logits, _entropy, format_chat, get_embedding, cosine_similarity
from .prompts import _p

log = logging.getLogger(__name__)


# ── auto-type & auto-evidence ────────────────────────────────────────────────

def _auto_type_and_confidence(text: str) -> tuple[str, float]:
    """Automatically determine node type and initial confidence using LLM.
    Returns (type, confidence). Confidence 0.0-1.0."""
    t = text.strip()

    # Try LLM classification (fast, max_tokens=10)
    try:
        messages = [
            {"role": "system", "content": "/no_think\nClassify this text and rate confidence 0-100.\n"
             "Types: hypothesis (unverified claim, 30-60), fact (well-known truth, 80-99), "
             "question (always 50), evidence (how strong, 40-90), goal (desired state, 50-70), action (completed action, 80-95).\n"
             "Reply ONLY: type confidence\nExample: hypothesis 40"},
            {"role": "user", "content": t[:200]},
        ]
        result, _ = _graph_generate(messages, max_tokens=10, temp=0.1, top_k=1)
        parts = result.strip().lower().split()
        print(f"[auto_type] raw='{result.strip()}' parts={parts}")
        valid_types = ("hypothesis", "fact", "question", "evidence", "goal", "action")
        if len(parts) >= 2 and parts[0] in valid_types:
            try:
                raw_conf = float(parts[1])
                # LLM may answer on 0-10 scale or 0-100 scale
                if raw_conf <= 10:
                    conf = raw_conf / 10.0  # 0-10 → 0.0-1.0
                else:
                    conf = raw_conf / 100.0  # 0-100 → 0.0-1.0
            except ValueError:
                conf = 0.5
            conf = max(0.1, min(0.99, conf))
            return (parts[0], round(conf, 2))
        elif len(parts) >= 1 and parts[0] in valid_types:
            defaults = {"hypothesis": 0.5, "fact": 0.9, "question": 0.5, "evidence": 0.7, "goal": 0.6, "action": 0.9}
            return (parts[0], defaults[parts[0]])
    except Exception as e:
        log.warning(f"[auto_type] LLM classification failed: {e}")

    # Regex fallback
    log.info(f"[auto_type] fallback to regex for: '{t[:60]}...'")
    if t.endswith('?'):
        return ("question", 0.5)
    q_words = ('почему', 'зачем', 'как ', 'что ', 'какой', 'какая', 'какие',
               'why', 'how', 'what', 'which', 'when', 'where', 'is ', 'are ', 'can ', 'does ')
    if any(t.lower().startswith(w) for w in q_words):
        return ("question", 0.5)
    log.warning(f"[auto_type] defaulting to hypothesis/0.5 for: '{t[:60]}...'")
    return ("hypothesis", 0.5)



def _auto_evidence_relation(parent_text: str, child_text: str) -> tuple[str, float]:
    """Determine if child supports or contradicts parent using LLM.
    Returns (relation, strength 0.0-1.0)."""
    try:
        messages = [
            {"role": "system", "content": "/no_think\nDoes the evidence support or contradict the hypothesis?\n"
             "Reply ONLY: supports strength OR contradicts strength\n"
             "strength is 0-100 (how strong the evidence is)\n"
             "Example: supports 75"},
            {"role": "user", "content": f"Hypothesis: {parent_text[:150]}\nEvidence: {child_text[:150]}"},
        ]
        result, _ = _graph_generate(messages, max_tokens=10, temp=0.1, top_k=1)
        parts = result.strip().lower().split()
        if len(parts) >= 2 and parts[0] in ("supports", "contradicts"):
            strength = int(parts[1]) / 100.0
            return (parts[0], round(max(0.1, min(0.95, strength)), 2))
        elif len(parts) >= 1 and parts[0] in ("supports", "contradicts"):
            return (parts[0], 0.7)
    except Exception as e:
        log.warning(f"[auto_evidence] LLM relation check failed: {e}")

    # Regex fallback
    log.info(f"[auto_evidence] fallback to regex")
    neg_patterns = (r'\bне\b', r'\bнет\b', r'\bоднако\b', r'\bно\b', r'\bnot\b', r'\bhowever\b', r'\bbut\b')
    child_lower = child_text.lower()
    if any(re.search(p, child_lower) for p in neg_patterns):
        return ("contradicts", 0.6)
    return ("supports", 0.7)


def _bayesian_update(prior: float, p_e_h: float, p_e_nh: float) -> float:
    """Compute Bayesian posterior: P(H|E) = P(E|H)*P(H) / P(E)."""
    p_e = p_e_h * prior + p_e_nh * (1 - prior)
    if p_e > 0:
        return round(min(0.99, max(0.01, (p_e_h * prior) / p_e)), 3)
    return prior


# ── node helpers ─────────────────────────────────────────────────────────────

def _make_node(node_id: int, text: str, depth: int = 0, topic: str = "",
               entropy: dict | None = None, confidence: float = 0.5,
               node_type: str = "thought") -> dict:
    """Create a node dict with all required fields."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": node_id,
        "text": text,
        "entropy": entropy or {"avg": 0.0, "unc": 0.0},
        "depth": depth,
        "topic": topic,
        "confidence": round(confidence, 2),
        "type": node_type,  # "thought", "hypothesis", "evidence", "fact", "question"
        "created_at": now,
        "last_accessed": now,
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
        node.setdefault("type", "thought")
        node.setdefault("created_at", None)
        node.setdefault("last_accessed", None)


def _get_texts(nodes: list[dict] | None = None) -> list[str]:
    """Return list of texts from nodes (for similarity, prompts)."""
    if nodes is None:
        nodes = _graph["nodes"]
    return [n["text"] for n in nodes]


def _add_node(text: str, depth: int = 0, topic: str = "",
              entropy: dict | None = None, confidence: float = 0.5,
              node_type: str = "thought") -> int:
    """Create node with next id, append to graph, return new index."""
    with graph_lock:
        nodes = _graph["nodes"]
        new_id = len(nodes)
        nodes.append(_make_node(new_id, text, depth, topic, entropy, confidence, node_type))
        _graph.pop("_tick_tried", None)
        return new_id


def _remove_node(idx: int):
    """Remove node at idx, remap all edge indices and embeddings."""
    with graph_lock:
        nodes = _graph["nodes"]
        if idx < 0 or idx >= len(nodes):
            return
        nodes.pop(idx)
        for i, node in enumerate(nodes):
            node["id"] = i
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
        "tp_overrides": {},  # "from,to" -> learned transition_prob
    }

_graph = _fresh_graph()
graph_lock = threading.Lock()


def reset_graph():
    """Reset all graph state (clears in-place to preserve references)."""
    with graph_lock:
        fresh = _fresh_graph()
        _graph.clear()
        _graph.update(fresh)


# ── generation helpers ───────────────────────────────────────────────────────

def _api_available() -> bool:
    """Check if API mode is configured for graph generation."""
    from .api_backend import use_api_for
    return use_api_for("graph")


def _graph_generate(messages: list[dict], max_tokens: int = 60, temp: float = 0.9, top_k: int = 40, seed: int = -1) -> tuple[str, dict]:
    """Generate text from chat messages. Uses API or local model based on settings.
    Returns (text, entropy_info)."""
    from .api_backend import use_api_for

    if use_api_for("graph"):
        return _graph_generate_api(messages, max_tokens, temp, top_k)
    return _graph_generate_local(messages, max_tokens, temp, top_k, seed)


def _graph_generate_api(messages: list[dict], max_tokens: int, temp: float, top_k: int) -> tuple[str, dict]:
    """Generate via OpenAI-compatible API."""
    from .api_backend import api_chat_completion

    try:
        text, avg_ent, unc_pct, token_ents_raw, token_texts = api_chat_completion(
            messages, max_tokens=max_tokens, temperature=temp, top_k=top_k,
        )
    except (KeyError, IndexError, TypeError) as e:
        log.error(f"[graph_generate_api] Failed to parse API response: {e}")
        return "", {"avg": 0.0, "unc": 0.0, "tokens": []}
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
    from .api_backend import use_api_for, api_get_embedding

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
                # Determine edge relation
                rel = "similarity"
                ni, nj = nodes[i], nodes[j]
                # Check if one is evidence for the other
                if ni.get("type") == "evidence" and ni.get("evidence_target") == j:
                    rel = ni.get("evidence_relation", "supports")
                elif nj.get("type") == "evidence" and nj.get("evidence_target") == i:
                    rel = nj.get("evidence_relation", "supports")
                edges.append({
                    "from": i, "to": j,
                    "weight": round(sim, 3),
                    "manual": manual and sim < threshold,
                    "relation": rel,
                })
    # --- Temporal links: connect nodes created within 5 minutes ---
    TEMPORAL_WINDOW = 300  # seconds
    edge_set = {(e["from"], e["to"]) for e in edges}
    for i in range(n):
        if nodes[i]["depth"] == -1:
            continue
        t_i = nodes[i].get("created_at")
        if not t_i:
            continue
        try:
            dt_i = datetime.fromisoformat(t_i)
        except (ValueError, TypeError):
            continue
        for j in range(i + 1, n):
            if nodes[j]["depth"] == -1:
                continue
            if (i, j) in edge_set:
                continue  # already connected
            t_j = nodes[j].get("created_at")
            if not t_j:
                continue
            try:
                dt_j = datetime.fromisoformat(t_j)
            except (ValueError, TypeError):
                continue
            diff = abs((dt_i - dt_j).total_seconds())
            if diff <= TEMPORAL_WINDOW:
                # Temporal weight: closer in time = stronger (0.3 to 0.6)
                tw = round(0.6 - (diff / TEMPORAL_WINDOW) * 0.3, 3)
                edges.append({
                    "from": i, "to": j,
                    "weight": tw,
                    "manual": False,
                    "temporal": True,
                    "relation": "temporal",
                })
                edge_set.add((i, j))

    # --- Compute transition_prob (tp) ---
    # For each node, normalize outgoing weights to sum=1.0
    # Directed edges get a bonus multiplier
    directed = set()
    for a, b in _graph["edges"].get("directed", []):
        directed.add((a, b))

    # Collect outgoing weights per node (both directions since similarity is undirected)
    out_weights = defaultdict(list)  # node -> [(edge_idx, target, raw_weight)]
    for idx, e in enumerate(edges):
        w = e["weight"]
        bonus_ab = 1.5 if (e["from"], e["to"]) in directed else 1.0
        bonus_ba = 1.5 if (e["to"], e["from"]) in directed else 1.0
        out_weights[e["from"]].append((idx, e["to"], w * bonus_ab))
        out_weights[e["to"]].append((idx, e["from"], w * bonus_ba))

    # Apply any learned tp overrides
    tp_overrides = _graph.get("tp_overrides", {})

    # Normalize per-node and store tp as from→to value
    # Each edge stores tp for from→to direction
    tp_forward = {}  # (from, to) -> prob
    for node, outs in out_weights.items():
        total = sum(w for _, _, w in outs)
        if total > 0:
            for edge_idx, target, w in outs:
                key = f"{node},{target}"
                if key in tp_overrides:
                    tp_forward[(node, target)] = tp_overrides[key]
                else:
                    tp_forward[(node, target)] = round(w / total, 3)

    # Re-normalize after overrides
    for node, outs in out_weights.items():
        targets = [(t, tp_forward.get((node, t), 0)) for _, t, _ in outs]
        total = sum(p for _, p in targets)
        if total > 0 and abs(total - 1.0) > 0.01:
            for t, p in targets:
                tp_forward[(node, t)] = round(p / total, 3)

    # Set tp on each edge (from→to direction)
    for e in edges:
        e["tp"] = tp_forward.get((e["from"], e["to"]), 0)
        e["tp_rev"] = tp_forward.get((e["to"], e["from"]), 0)

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
        queue = deque([i])
        while queue:
            node = queue.popleft()
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

    # Remap tp_overrides
    old_tp = _graph.get("tp_overrides", {})
    new_tp = {}
    for key, val in old_tp.items():
        parts = key.split(",")
        a, b = int(parts[0]), int(parts[1])
        if a in removed or b in removed:
            continue
        new_tp[f"{remap(a)},{remap(b)}"] = val
    _graph["tp_overrides"] = new_tp

    # Remap embedding cache
    cache = _graph.get("embeddings", [])
    for i in sorted(removed, reverse=True):
        if i < len(cache):
            cache.pop(i)


def _detect_traps(nodes, edges):
    """Detect trap nodes: high incoming tp, low outgoing tp."""
    incoming = defaultdict(float)
    outgoing = defaultdict(float)
    for e in edges:
        outgoing[e["from"]] += e.get("tp", 0)
        incoming[e["to"]] += e.get("tp", 0)
        outgoing[e["to"]] += e.get("tp_rev", 0)
        incoming[e["from"]] += e.get("tp_rev", 0)

    traps = []
    for i in range(len(nodes)):
        if nodes[i]["depth"] == -1:
            continue
        inc = incoming.get(i, 0)
        out = outgoing.get(i, 0)
        if inc > 0 and out < inc * 0.3:  # incoming >> outgoing
            traps.append(i)
    return traps


def _compute_alpha_beta(nodes):
    """Compute α (supports count) and β (contradicts count) for hypothesis nodes."""
    ab = {}  # hyp_idx -> {alpha, beta, evidence_ids}
    for i, node in enumerate(nodes):
        if node.get("type") == "evidence":
            target = node.get("evidence_target")
            if target is not None and target < len(nodes):
                if target not in ab:
                    ab[target] = {"alpha": 0, "beta": 0, "evidence": []}
                rel = node.get("evidence_relation", "supports")
                strength = node.get("evidence_strength", 0.7)
                if rel == "supports":
                    ab[target]["alpha"] += strength
                else:
                    ab[target]["beta"] += strength
                ab[target]["evidence"].append({"idx": i, "relation": rel, "strength": strength})
    return ab
