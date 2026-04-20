"""Pump (Накачка) — finding the hidden axis between two ideas.

Takes two unrelated nodes, expands each into a context cloud,
then asks LLM to abstract the hidden parameter connecting both.

Not embedding similarity — generative abstraction:
"immune system" + "corporate bankruptcy" → "adaptability vs rigidity under threat"

The bridge C doesn't exist in either cloud. LLM generates it by abstracting
over both contexts.
"""

import logging
import numpy as np

from .graph_logic import (
    _graph, _graph_generate, _clean_thought,
    _ensure_embeddings, _get_texts, _add_node,
    cosine_similarity, touch_node,
)
from .prompts import _p

log = logging.getLogger(__name__)


def pump(node_a_idx: int, node_b_idx: int, max_iterations: int = 3,
         lang: str = "ru", temp: float = 0.7, top_k: int = 40) -> dict:
    """Find the hidden axis between two ideas.

    Returns:
        {
            "bridge": str,          # the hidden parameter text
            "confidence": float,    # how well it relates to both sides
            "sim_to_a": float,
            "sim_to_b": float,
            "cloud_a": [str],       # expanded context of A
            "cloud_b": [str],       # expanded context of B
            "iterations": int,
        }
        or {"error": str} on failure.
    """
    nodes = _graph["nodes"]
    if node_a_idx < 0 or node_a_idx >= len(nodes):
        return {"error": "invalid node_a"}
    if node_b_idx < 0 or node_b_idx >= len(nodes):
        return {"error": "invalid node_b"}

    # Hebbian: обе ноды участвуют в поиске моста — это сильное обращение
    # независимо от того успешен ли итог. Мост найден → further uses уже
    # пойдут через reference в scout-card / elaborate, это даст ещё boost.
    touch_node(node_a_idx)
    touch_node(node_b_idx)

    text_a = nodes[node_a_idx]["text"]
    text_b = nodes[node_b_idx]["text"]

    cloud_a = [text_a]
    cloud_b = [text_b]

    # Collect existing neighbors as initial context
    for n in nodes:
        if n.get("evidence_target") == node_a_idx or n.get("id") in _get_children(node_a_idx):
            cloud_a.append(n["text"])
        if n.get("evidence_target") == node_b_idx or n.get("id") in _get_children(node_b_idx):
            cloud_b.append(n["text"])

    best_confidence = 0
    all_bridges = []          # accumulate across ALL iterations
    seen_bridges = set()      # dedup by normalized text

    for iteration in range(max_iterations):
        # 1. Expand clouds (always, including first iteration)
        new_a = _expand_cloud(text_a, cloud_a, lang, temp, top_k, n=5)
        new_b = _expand_cloud(text_b, cloud_b, lang, temp, top_k, n=5)
        cloud_a.extend(new_a)
        cloud_b.extend(new_b)
        print(f"[pump] iter={iteration+1} clouds: A={len(cloud_a)} (+{len(new_a)}), B={len(cloud_b)} (+{len(new_b)})")

        # 2. Ask LLM for hidden parameters (multiple)
        existing = [b["text"] for b in all_bridges]
        bridge_texts = _find_bridges(text_a, cloud_a, text_b, cloud_b, lang, temp, top_k, existing)
        if not bridge_texts:
            continue

        # 3. Verify each bridge via SmartDC with A+B context
        for bt in bridge_texts:
            # Dedup: skip if we already have a very similar bridge
            bt_norm = bt.strip().lower()
            if bt_norm in seen_bridges:
                continue
            seen_bridges.add(bt_norm)

            # Embedding-based similarity
            emb_conf, sim_a, sim_b = _evaluate_bridge(bt, cloud_a, cloud_b)

            # SmartDC verification with pump context
            dc_result = _verify_bridge(bt, text_a, text_b, lang, temp, top_k)

            # Bridge quality from pole analysis
            quality = _compute_bridge_quality(dc_result)

            print(f"[pump] iter={iteration+1} bridge='{bt[:40]}' emb={emb_conf:.2f} quality={quality:.2f} lean={dc_result.get('lean', '?')}")

            poles = dc_result.get("poles", [])
            bridge_data = {
                "text": bt,
                "emb_confidence": round(emb_conf, 3),
                "sim_to_a": round(sim_a, 3),
                "sim_to_b": round(sim_b, 3),
                "quality": round(quality, 3),
                "synthesis": dc_result.get("synthesis", ""),
                "thesis": poles[0] if len(poles) > 0 else "",
                "antithesis": poles[1] if len(poles) > 1 else "",
                "neutral": poles[2] if len(poles) > 2 else "",
                "lean": dc_result.get("lean"),
                "tension": dc_result.get("tension"),
                "dc_confidence": dc_result.get("confidence"),
            }
            all_bridges.append(bridge_data)

            if quality > best_confidence:
                best_confidence = quality

        # Sort accumulated bridges by quality
        all_bridges.sort(key=lambda b: -b["quality"])

        # Stop only if we have enough good bridges
        good_count = sum(1 for b in all_bridges if b["quality"] > 0.4)
        if good_count >= 3 or (iteration >= 1 and best_confidence > 0.5):
            break

    if not all_bridges:
        return {
            "error": "bridge not found",
            "cloud_a": cloud_a[:5],
            "cloud_b": cloud_b[:5],
            "iterations": max_iterations,
        }

    return {
        "bridge": all_bridges[0]["text"],
        "confidence": round(best_confidence, 3),
        "all_bridges": all_bridges,
        "text_a": text_a[:120],        # для UI scout-card: A → B layout
        "text_b": text_b[:120],
        "cloud_a": cloud_a[:10],
        "cloud_b": cloud_b[:10],
        "iterations": iteration + 1,
    }


def _get_children(node_idx: int) -> set:
    """Get direct children of a node from directed edges."""
    children = set()
    for a, b in _graph["edges"].get("directed", []):
        if a == node_idx:
            children.add(b)
    return children


def _expand_cloud(source_text: str, existing: list[str], lang: str,
                  temp: float, top_k: int, n: int = 3) -> list[str]:
    """Generate N associations/aspects from source, avoiding existing."""
    system = _p(lang, "think")
    user = f"{_p(lang, 'topic')}: {source_text}"
    if existing:
        user += f"\n{_p(lang, 'already')}:\n" + "\n".join(f"- {t}" for t in existing[-5:])
        user += f"\n{_p(lang, 'new_idea')}"

    new_texts = []
    for _ in range(n):
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        text, _ = _graph_generate(messages, max_tokens=60, temp=temp, top_k=top_k)
        text = _clean_thought(text, source_text)
        if text and len(text) > 10:
            new_texts.append(text)
            # Add to "already" for next generation
            user = f"{_p(lang, 'topic')}: {source_text}"
            user += f"\n{_p(lang, 'already')}:\n" + "\n".join(f"- {t}" for t in (existing + new_texts)[-5:])
            user += f"\n{_p(lang, 'new_idea')}"

    return new_texts


_BRIDGE_PROMPT_RU = """/no_think
У меня две группы понятий, связанные с двумя разными идеями.

Идея A: {text_a}
Контекст A:
{cloud_a}

Идея B: {text_b}
Контекст B:
{cloud_b}
{existing_section}
Найди 3 РАЗНЫХ скрытых параметра, оси или измерения, которые связывают эти две идеи.
Не пересказывай контексты. Каждая ось — на которой обе идеи оказываются точками.
Ответь списком из 3 коротких формулировок, каждая на новой строке. Без нумерации."""

_BRIDGE_PROMPT_EN = """/no_think
I have two groups of concepts related to two different ideas.

Idea A: {text_a}
Context A:
{cloud_a}

Idea B: {text_b}
Context B:
{cloud_b}
{existing_section}
Find 3 DIFFERENT hidden parameters, axes, or dimensions connecting these two ideas.
Don't retell the contexts. Each axis — one on which both ideas are points.
Answer with a list of 3 short phrases, each on a new line. No numbering."""


def _find_bridges(text_a: str, cloud_a: list[str], text_b: str, cloud_b: list[str],
                  lang: str, temp: float, top_k: int,
                  existing_bridges: list[str] | None = None) -> list[str]:
    """Ask LLM to find hidden axes between two idea clouds. Returns list of bridges."""
    prompt_template = _BRIDGE_PROMPT_RU if lang == "ru" else _BRIDGE_PROMPT_EN

    cloud_a_str = "\n".join(f"- {t}" for t in cloud_a[:8])
    cloud_b_str = "\n".join(f"- {t}" for t in cloud_b[:8])

    existing_section = ""
    if existing_bridges:
        if lang == "ru":
            existing_section = "\nУже найденные оси (НЕ повторяй их, ищи ДРУГИЕ):\n" + "\n".join(f"- {b}" for b in existing_bridges) + "\n"
        else:
            existing_section = "\nAlready found axes (do NOT repeat, find DIFFERENT ones):\n" + "\n".join(f"- {b}" for b in existing_bridges) + "\n"

    prompt = prompt_template.format(
        text_a=text_a, cloud_a=cloud_a_str,
        text_b=text_b, cloud_b=cloud_b_str,
        existing_section=existing_section,
    )

    messages = [{"role": "user", "content": prompt}]
    text, _ = _graph_generate(messages, max_tokens=300, temp=temp, top_k=top_k)

    # Parse lines
    bridges = []
    for line in text.strip().split("\n"):
        line = line.strip().strip("-").strip("*").strip('"').strip("'").strip()
        # Remove numbering
        import re
        line = re.sub(r"^\d+[.)]\s*", "", line)
        if len(line) > 5:
            bridges.append(line)
    return bridges[:5]  # max 5


def _verify_bridge(bridge_text: str, text_a: str, text_b: str,
                   lang: str, temp: float, top_k: int) -> dict:
    """Run SmartDC on bridge with A+B context. Returns pole analysis."""
    from .prompts import _p

    statement = f"Связь: A='{text_a}' и B='{text_b}'. Мост: {bridge_text}"

    # Generate 3 poles
    poles = []
    for role_key in ["dc_thesis", "dc_antithesis", "dc_neutral"]:
        messages = [
            {"role": "system", "content": _p(lang, role_key)},
            {"role": "user", "content": f"{_p(lang, 'dc_statement')}: {statement}"},
        ]
        text, _ = _graph_generate(messages, max_tokens=150, temp=temp, top_k=top_k)
        poles.append(text)

    # Synthesis
    concise = " Максимум 3-4 предложения." if lang == "ru" else " Maximum 3-4 sentences."
    synthesis_messages = [
        {"role": "system", "content": _p(lang, "dc_synthesis") + concise},
        {"role": "user", "content":
            f"{_p(lang, 'dc_statement')}: {statement}\n\n"
            f"{_p(lang, 'dc_for')}:\n{poles[0]}\n\n"
            f"{_p(lang, 'dc_against')}:\n{poles[1]}\n\n"
            f"{_p(lang, 'dc_context')}:\n{poles[2]}"
        },
    ]
    synthesis, _ = _graph_generate(synthesis_messages, max_tokens=1000, temp=0.7, top_k=top_k)

    # Compute per-pole confidence via embeddings
    from .api_backend import api_get_embedding
    result = {"synthesis": synthesis, "poles": poles}

    try:
        stmt_emb = api_get_embedding(statement)
        pole_embs = [api_get_embedding(p) for p in poles]
        syn_emb = api_get_embedding(synthesis)

        if stmt_emb and all(pole_embs) and syn_emb:
            stmt_arr = np.array(stmt_emb, dtype=np.float32)

            # Per-pole similarity to statement
            t_conf = float(cosine_similarity(np.array(pole_embs[0], dtype=np.float32), stmt_arr))
            a_conf = float(cosine_similarity(np.array(pole_embs[1], dtype=np.float32), stmt_arr))

            result["thesis_conf"] = round(t_conf, 3)
            result["antithesis_conf"] = round(a_conf, 3)
            result["lean"] = round(t_conf - a_conf, 3)

            # Tension between thesis and antithesis
            tension = float(cosine_similarity(
                np.array(pole_embs[0], dtype=np.float32),
                np.array(pole_embs[1], dtype=np.float32)))
            result["tension"] = round(tension, 3)

            # Synthesis confidence (centroid)
            centroid = np.mean([np.array(e, dtype=np.float32) for e in pole_embs], axis=0)
            syn_arr = np.array(syn_emb, dtype=np.float32)
            cent_sim = float(cosine_similarity(syn_arr, centroid))
            result["confidence"] = round(min(0.95, max(0.3, cent_sim)), 3)

    except Exception as e:
        log.warning(f"[pump verify] embedding error: {e}")
        result["confidence"] = 0.5

    return result


def _compute_bridge_quality(dc_result: dict) -> float:
    """Compute bridge quality from SmartDC pole analysis.

    quality = balance × depth
    - balance: lean ≈ 0 means genuine debate (not trivial, not weak)
    - depth: low tension = thesis and antithesis are far apart = deep disagreement
    """
    lean = dc_result.get("lean", 0)
    tension = dc_result.get("tension", 0.5)
    dc_conf = dc_result.get("confidence", 0.5)

    balance = 1.0 - min(1.0, abs(lean) * 5)   # lean=0 → balance=1, lean=0.2 → balance=0
    depth = 1.0 - max(0, tension - 0.5) * 2    # tension=0.5 → depth=1, tension=1.0 → depth=0

    quality = balance * depth * dc_conf
    return max(0.05, min(0.95, quality))


def _evaluate_bridge(bridge_text: str, cloud_a: list[str], cloud_b: list[str]) -> tuple:
    """Evaluate bridge confidence via embedding similarity to both cloud centroids.

    Returns (confidence, sim_to_a, sim_to_b).
    """
    from .api_backend import api_get_embedding

    try:
        bridge_emb = api_get_embedding(bridge_text)
        if not bridge_emb:
            return 0.0, 0.0, 0.0

        # Centroid of cloud A
        embs_a = [api_get_embedding(t) for t in cloud_a[:5]]
        embs_a = [e for e in embs_a if e]
        if not embs_a:
            return 0.0, 0.0, 0.0
        centroid_a = np.mean(embs_a, axis=0)

        # Centroid of cloud B
        embs_b = [api_get_embedding(t) for t in cloud_b[:5]]
        embs_b = [e for e in embs_b if e]
        if not embs_b:
            return 0.0, 0.0, 0.0
        centroid_b = np.mean(embs_b, axis=0)

        bridge_arr = np.array(bridge_emb, dtype=np.float32)
        sim_a = float(cosine_similarity(bridge_arr, np.array(centroid_a, dtype=np.float32)))
        sim_b = float(cosine_similarity(bridge_arr, np.array(centroid_b, dtype=np.float32)))

        confidence = min(sim_a, sim_b)
        return confidence, sim_a, sim_b

    except Exception as e:
        log.warning(f"[pump] bridge evaluation failed: {e}")
        return 0.0, 0.0, 0.0
