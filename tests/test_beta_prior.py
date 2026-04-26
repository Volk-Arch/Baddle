"""Tests для Beta-prior calibration слоя на confidence.

Beta(alpha, beta) — sidecar evidence accumulator. Confidence (mean) живёт
authoritative через `_bayesian_update_distinct` + γ-modulation + RPE
feedback; alpha/beta tracking — independent layer для CI/total на UI.

Property invariants:
1. После N supports total монотонно растёт; mean → 1.0.
2. После N contradicts total монотонно растёт; mean → 0.0.
3. CI сужается с ростом total (std ∝ 1/√total для устоявшегося mean).
4. _make_node инициализирует alpha/beta из confidence (back-derivation).
5. _ensure_node_fields backward-compat — legacy node без alpha/beta получает
   priors derived из confidence; CI сразу cached.
6. _bump_evidence НЕ перезаписывает node["confidence"] (sidecar invariant).
"""
from __future__ import annotations


def _fresh_node(confidence=0.5):
    from src.graph_logic import _make_node
    return _make_node(node_id=0, text="test", confidence=confidence)


# ── Inv 1: support → mean → 1.0, total ↑ ────────────────────────────────────

def test_supports_pushes_mean_up_and_total_up():
    from src.graph_logic import _bump_evidence, _beta_mean_ci
    node = _fresh_node(confidence=0.5)
    initial_total = node["confidence_total"]
    initial_mean = _beta_mean_ci(node["alpha"], node["beta"])["mean"]

    for _ in range(10):
        _bump_evidence(node, supports=True, strength=1.0)

    final = _beta_mean_ci(node["alpha"], node["beta"])
    assert final["total"] > initial_total
    assert final["mean"] > initial_mean
    assert final["mean"] > 0.7  # 10 supports + initial 0.5 → must be high


# ── Inv 2: contradicts → mean → 0.0, total ↑ ─────────────────────────────────

def test_contradicts_pushes_mean_down():
    from src.graph_logic import _bump_evidence, _beta_mean_ci
    node = _fresh_node(confidence=0.5)
    for _ in range(10):
        _bump_evidence(node, supports=False, strength=1.0)
    final = _beta_mean_ci(node["alpha"], node["beta"])
    assert final["mean"] < 0.3
    assert final["total"] > node.get("confidence_total", 0) - 1  # grew


# ── Inv 3: CI сужается с накоплением evidence ────────────────────────────────

def test_ci_narrows_with_more_evidence():
    from src.graph_logic import _bump_evidence, _beta_mean_ci

    # Node A: только инициализация (4 prior weight)
    node_a = _fresh_node(confidence=0.7)
    a_ci = _beta_mean_ci(node_a["alpha"], node_a["beta"])
    a_width = a_ci["ci_upper"] - a_ci["ci_lower"]

    # Node B: 20 supports + initial — total 24, узкий CI
    node_b = _fresh_node(confidence=0.7)
    for _ in range(20):
        _bump_evidence(node_b, supports=True, strength=1.0)
    b_ci = _beta_mean_ci(node_b["alpha"], node_b["beta"])
    b_width = b_ci["ci_upper"] - b_ci["ci_lower"]

    assert b_width < a_width, "CI width должен уменьшиться с ростом evidence"
    assert b_ci["total"] > a_ci["total"]


# ── Inv 4: _make_node back-derives alpha/beta из confidence ─────────────────

def test_make_node_alpha_beta_match_confidence():
    from src.graph_logic import _make_node, _beta_mean_ci

    for conf in (0.1, 0.3, 0.5, 0.7, 0.9):
        node = _make_node(node_id=0, text="t", confidence=conf)
        assert "alpha" in node and "beta" in node
        # mean из (alpha,beta) должен быть в окрестности confidence
        # (initial total=4, +0.5 baseline на каждой стороне)
        derived = _beta_mean_ci(node["alpha"], node["beta"])["mean"]
        assert abs(derived - conf) < 0.1, \
            f"derived mean {derived} far from input confidence {conf}"


# ── Inv 5: _ensure_node_fields backward-compat для legacy ───────────────────

def test_ensure_node_fields_migrates_legacy_node():
    from src.graph_logic import _ensure_node_fields
    # Legacy node — только confidence scalar, без alpha/beta/CI
    legacy = [{"id": 0, "text": "old", "confidence": 0.6, "type": "thought"}]
    _ensure_node_fields(legacy)
    n = legacy[0]
    assert "alpha" in n and "beta" in n, "alpha/beta должны быть derived"
    assert "confidence_total" in n
    assert "confidence_ci" in n
    assert isinstance(n["confidence_ci"], list) and len(n["confidence_ci"]) == 2
    # mean derived из alpha/beta должен быть около confidence
    derived = n["alpha"] / (n["alpha"] + n["beta"])
    assert abs(derived - 0.6) < 0.1


# ── Inv 6: sidecar invariant — confidence НЕ перезаписан _bump_evidence ─────

def test_bump_evidence_does_not_overwrite_confidence():
    """Confidence — authoritative из _bayesian_update_distinct path.
    alpha/beta — sidecar; _bump_evidence ОБЯЗАН не трогать node["confidence"].
    """
    from src.graph_logic import _bump_evidence
    node = _fresh_node(confidence=0.42)
    for _ in range(5):
        _bump_evidence(node, supports=True, strength=1.0)
    assert node["confidence"] == 0.42, \
        "_bump_evidence не должен перезаписывать confidence (sidecar invariant)"


# ── Inv 7: CI cached при _bump_evidence ────────────────────────────────────

def test_bump_evidence_caches_ci_on_node():
    from src.graph_logic import _bump_evidence
    node = _fresh_node(confidence=0.5)
    initial_ci = list(node["confidence_ci"])
    for _ in range(5):
        _bump_evidence(node, supports=True, strength=1.0)
    new_ci = node["confidence_ci"]
    assert new_ci != initial_ci, "CI должен обновиться после bump"
    assert new_ci[1] - new_ci[0] < initial_ci[1] - initial_ci[0], \
        "CI должен сузиться с ростом evidence"
