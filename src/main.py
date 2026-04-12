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


def entropy_from_logprob(logprob: float) -> float:
    """Convert a single token logprob to entropy contribution (-log p).
    Higher = more uncertain. Used for confidence estimation."""
    return -float(logprob) if logprob else 0.0
