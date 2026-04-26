#!/usr/bin/env python3
"""baddle — shared utilities (API-only mode).

Heavy lifting (generation, embeddings, logprobs) is done via OpenAI-compatible API
backend — see api_backend.py. This module keeps only helpers that don't need a model
in process: data classes, similarity math, entropy.
"""

import dataclasses
import numpy as np


# ── config dataclass ──────────────────────────────────────────────────────────

@dataclasses.dataclass
class StreamCfg:
    label: str
    temp:  float = 0.7
    top_k: int   = 40
    color: str   = "cyan"
    seed:  int   = -1     # -1 = random


# ── math helpers ──────────────────────────────────────────────────────────────

def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two vectors."""
    if len(a) == 0 or len(b) == 0:
        return 0.0
    dot = np.dot(a, b)
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    if norm == 0:
        return 0.0
    return float(dot / norm)


def distinct(a: np.ndarray, b: np.ndarray) -> float:
    """NAND-architecture primitive: d = 1 - cos_sim, normalized to [0,1].

    d ≈ 0    → согласие (CONFIRM)
    d ≈ 0.5  → конфликт (EXPLORE / branching)
    d ≈ 1    → отрицание (CONFLICT)

    Equivalent to predictive error (Free Energy).
    See docs/nand-architecture.md
    """
    if len(a) == 0 or len(b) == 0:
        return 0.5  # unknown → middle
    sim = cosine_similarity(a, b)
    # cosine may return [-1, 1] for non-normalized. Clamp and transform.
    return max(0.0, min(1.0, (1.0 - sim) / 2.0 if sim < 0 else 1.0 - sim))


def distinct_decision(d: float, tau_in: float = 0.3, tau_out: float = 0.7) -> str:
    """Decision output from distinct().

    d < tau_in  → CONFIRM  (согласие, усиление связи)
    d > tau_out → CONFLICT (конфликт, ветвление или архив)
    else        → EXPLORE  (зона неопределённости, требует данных)
    """
    if d < tau_in:
        return "CONFIRM"
    if d > tau_out:
        return "CONFLICT"
    return "EXPLORE"
