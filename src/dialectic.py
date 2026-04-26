"""Dialectic core — shared thesis/antithesis/neutral/synthesis generation.

Used by both:
- /graph/smartdc route (with embedding-based confidence, per-pole analysis)
- /assist execute_dispute (simpler, returns structured cards)

Extracted to avoid duplicating 4 LLM-call flow in two places.
"""
import logging
from typing import Dict

from .prompts import _p
from .graph_logic import _graph_generate

log = logging.getLogger(__name__)


def generate_poles(statement: str, lang: str = "ru",
                    temp: float = 0.7, top_k: int = 40,
                    pole_tokens: int = 150, seed: int = -1,
                    context_block: str = "",
                    return_entropy: bool = False) -> Dict:
    """Generate thesis/antithesis/neutral for a statement via 3 LLM calls.

    Parameters:
      context_block — optional extra text appended to user prompt (evidence, etc.)
      return_entropy — if True, returns dicts with 'text' + 'entropy' per pole.
                      If False (default), returns just strings per pole.

    Returns:
      { "thesis": str|{text,entropy}, "antithesis": ..., "neutral": ... }
    """
    role_keys = {"thesis": "dc_thesis", "antithesis": "dc_antithesis", "neutral": "dc_neutral"}
    results = {}
    for name, role_key in role_keys.items():
        messages = [
            {"role": "system", "content": _p(lang, role_key)},
            {"role": "user", "content": f"{_p(lang, 'dc_statement')}: {statement}{context_block}"},
        ]
        try:
            text, ent = _graph_generate(messages, max_tokens=pole_tokens, temp=temp, top_k=top_k, seed=seed)
            clean_text = (text or "").strip()
            if return_entropy:
                results[name] = {"text": clean_text, "entropy": ent}
            else:
                results[name] = clean_text
        except Exception as e:
            log.warning(f"[dialectic] {name} failed: {e}")
            results[name] = {"text": "", "entropy": {}} if return_entropy else ""
    return results


def synthesize(statement: str, thesis: str, antithesis: str, neutral: str,
                lang: str = "ru", temp: float = 0.7, top_k: int = 40,
                max_tokens: int = 400, concise: bool = True, seed: int = -1,
                return_entropy: bool = False):
    """Generate synthesis from three poles via 1 LLM call.

    concise=True limits synthesis length (for chat UI).
    return_entropy=True returns (text, entropy_dict). Default returns just text.
    """
    system_prompt = _p(lang, "dc_synthesis")
    if concise:
        system_prompt += " Максимум 4 предложения." if lang == "ru" else " Maximum 4 sentences."

    user = (
        f"{_p(lang, 'dc_statement')}: {statement}\n\n"
        f"{_p(lang, 'dc_for')}:\n{thesis}\n\n"
        f"{_p(lang, 'dc_against')}:\n{antithesis}\n\n"
        f"{_p(lang, 'dc_context')}:\n{neutral}"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user},
    ]
    try:
        text, ent = _graph_generate(messages, max_tokens=max_tokens, temp=temp, top_k=top_k, seed=seed)
        clean = (text or "").strip()
        return (clean, ent) if return_entropy else clean
    except Exception as e:
        log.warning(f"[dialectic] synthesis failed: {e}")
        return ("", {}) if return_entropy else ""


def dialectic_flow(statement: str, lang: str = "ru",
                   temp: float = 0.7, top_k: int = 40,
                   concise: bool = True) -> Dict:
    """Full dialectic: 3 poles + synthesis. 4 LLM calls total.

    Returns:
      {thesis, antithesis, neutral, synthesis}
    """
    poles = generate_poles(statement, lang, temp, top_k)
    synthesis = synthesize(
        statement,
        poles.get("thesis", ""),
        poles.get("antithesis", ""),
        poles.get("neutral", ""),
        lang=lang, temp=temp, top_k=top_k, concise=concise,
    )
    return {
        "thesis": poles.get("thesis", ""),
        "antithesis": poles.get("antithesis", ""),
        "neutral": poles.get("neutral", ""),
        "synthesis": synthesis,
    }
