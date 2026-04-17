"""CognitiveHorizon — adaptive controller for the thinking cycle.

Sits between tick() and LLM calls. Does not generate content — controls
HOW to generate: temperature, top_k, top_p based on precision.

Three parameters:
  Π (precision)      : 0.0–1.0. Confidence in current model. Higher = narrower cone.
  Λ (policy_weights) : {generate, merge, elaborate, doubt}. Which phase to pick.
  Γ (context_frame)  : Active prompt/rules. Switches on high novelty.

Four states:
  EXPLORATION  : precision 0.3–0.5, wide search
  EXECUTION    : precision 0.7–0.9, narrow focus
  RECOVERY     : precision drops after surprise spike
  INTEGRATION  : precision 0.5–0.6, balanced synthesis

One mechanism drives everything: prediction error.
  ε = surprise - target_surprise
  ε > 0 → too chaotic → precision ↑ → cone narrows
  ε < 0 → too predictable → precision ↓ → cone widens
  ε ≈ 0 → flow zone → system moves forward
"""

import math


# ── States ──────────────────────────────────────────────────────────────────

EXPLORATION = "exploration"
EXECUTION = "execution"
RECOVERY = "recovery"
INTEGRATION = "integration"

# Extended states (used when HRV is connected)
STABILIZE = "stabilize"  # coherence < 0.3, calibration / noise reset
SHIFT = "shift"          # context switch, φ change detected
CONFLICT = "conflict"    # incompatible priors, merge fail


# ── CognitiveHorizon ────────────────────────────────────────────────────────

class CognitiveHorizon:
    """Adaptive controller: precision → LLM params, policy → phase selection."""

    def __init__(self,
                 precision: float = 0.4,
                 policy_weights: dict = None,
                 target_surprise: float = 0.3,
                 alpha: float = 0.1,
                 beta: float = 0.2):
        self.precision = max(0.05, min(0.95, precision))
        self.policy_weights = policy_weights or {
            "generate": 0.3, "merge": 0.2, "elaborate": 0.2, "doubt": 0.3,
        }
        self.target_surprise = target_surprise
        self.alpha = alpha  # precision learning rate
        self.beta = beta    # policy learning rate
        self.state = EXPLORATION
        self._history = []  # last N surprises for state detection
        self._pending_state = None  # debounce: target state waiting confirmation
        self._pending_count = 0     # debounce: consecutive ticks wanting same state

        # NAND-architecture additions
        self.gamma = 2.0            # Bayesian sensitivity (auto-calibrated)
        self.gamma_0 = 2.0          # baseline
        self.temperature_nand = 0.1  # NAND temperature (separate from LLM temp)
        self.temperature_nand_0 = 0.1  # baseline T₀
        self._d_self_history = []   # EMA basis for γ calibration
        self.sync_error = 0.0       # how bad system predicts user (0=perfect, 1=lost)
        self._prev_policy_weights = None  # snapshot for KL(W_t ‖ W_{t-1})
        self.kl_divergence = 0.0    # latest KL, diagnostic metric

        # HRV state (set by update_from_hrv)
        self.hrv_coherence = None   # 0-1, None if no sensor
        self.hrv_stress = None      # 0-1, from 1/RMSSD
        self.hrv_rmssd = None       # ms

        # Body-computed thresholds (defaults, overridden by HRV)
        self.tau_in = 0.3
        self.tau_out = 0.7

    # ── LLM params ──────────────────────────────────────────────────────────

    def to_llm_params(self) -> dict:
        """Convert precision to LLM generation parameters + adaptive novelty."""
        p = self.precision
        return {
            "temperature": max(0.1, min(1.5, 1.0 - p)),
            "top_k": int(max(10, min(100, 10 + 90 * (1 - p)))),
            "top_p": max(0.7, min(0.95, 0.5 + 0.5 * p)),
            "novelty_threshold": round(max(0.8, min(0.96, 0.85 + 0.1 * p)), 3),
        }

    # ── Phase selection ─────────────────────────────────────────────────────

    def select_phase(self, available: dict) -> str:
        """Pick phase based on policy weights × availability.

        available: {phase_name: bool} — which phases have work to do.
        Returns the phase with highest weight among available ones.
        If no available phase matches policy, returns first available.
        """
        best_phase = None
        best_weight = -1

        for phase, has_work in available.items():
            if not has_work:
                continue
            weight = self.policy_weights.get(phase, 0)
            if weight > best_weight:
                best_weight = weight
                best_phase = phase

        # Fallback: first available
        if best_phase is None:
            for phase, has_work in available.items():
                if has_work:
                    return phase

        return best_phase

    # ── NAND-architecture: γ autocalibration, sync_error ───────────────────

    def update_gamma(self, d_self_value: float = None):
        """Auto-calibrate γ based on self-distance (stability) history.

        γ ← clip(γ₀ / (ε + EMA(d(A,A))), γ_min, γ_max)

        Stable system → γ↑ (strict filtering)
        Unstable system → γ↓ (more cautious)
        """
        if d_self_value is not None:
            self._d_self_history.append(d_self_value)
            if len(self._d_self_history) > 10:
                self._d_self_history.pop(0)
        if not self._d_self_history:
            return
        ema = sum(self._d_self_history) / len(self._d_self_history)
        new_gamma = self.gamma_0 / (1e-6 + ema)
        self.gamma = max(0.1, min(10.0, new_gamma))

    def update_temperature(self, alpha: float = 0.5):
        """Adapt T based on KL divergence between current and previous policy weights.

        T ← T₀ · (1 + α · KL(W_t ‖ W_{t-1}))

        Large belief shift (high KL) → T↑ → softer, more exploratory choices
        Stable beliefs (low KL) → T↓ → sharper, more decisive choices

        Called automatically after policy update. Diagnostic metric — not applied
        to LLM temperature (which is driven by precision via to_llm_params()).

        See docs/nand-architecture.md
        """
        if self._prev_policy_weights is None:
            # First tick — snapshot and skip
            self._prev_policy_weights = dict(self.policy_weights)
            self.kl_divergence = 0.0
            return

        # KL(current ‖ previous) = Σ p_i · log(p_i / q_i)
        # Clamp weights to avoid log(0)
        kl = 0.0
        for phase, p in self.policy_weights.items():
            p = max(1e-6, p)
            q = max(1e-6, self._prev_policy_weights.get(phase, 1e-6))
            kl += p * math.log(p / q)
        kl = max(0.0, kl)  # numerical floor

        self.kl_divergence = round(kl, 4)
        new_t = self.temperature_nand_0 * (1 + alpha * kl)
        self.temperature_nand = max(0.05, min(2.0, new_t))

        # Snapshot current as previous for next tick
        self._prev_policy_weights = dict(self.policy_weights)

    def update_sync_error(self, distance: float):
        """Update sync_error — how well system predicts user.

        distance: d(predicted_response, actual_response) in [0,1]
        Low → system understands user. High → desync, needs clarification.
        """
        self.sync_error = max(0.0, min(1.0, distance))

    def update_from_hrv(self, coherence: float = None, rmssd: float = None,
                         stress: float = None, lf_hf: float = None):
        """Map HRV metrics to γ, τ, α.

        γ = γ₀ + η · (Stress - Coherence)       — stress → stricter, calm → gentler
        τ_in  = τ₀ · (1 + k₁·Stress)             — stress → wider net
        τ_out = τ₀ · (1 - k₂·Coherence)           — coherence → narrower horizon
        α    = α₀ · Coherence                     — coherence → smoother learning

        See docs/hrv-design.md, docs/nand-architecture.md
        """
        if coherence is not None:
            self.hrv_coherence = max(0.0, min(1.0, coherence))
        if rmssd is not None:
            self.hrv_rmssd = rmssd
            # Infer stress from RMSSD: lower RMSSD = higher stress
            self.hrv_stress = max(0.0, min(1.0, 1.0 - (rmssd / 80.0)))
        if stress is not None:
            self.hrv_stress = max(0.0, min(1.0, stress))

        # Apply if we have both metrics
        if self.hrv_stress is not None and self.hrv_coherence is not None:
            eta = 2.0
            self.gamma = max(0.1, min(10.0,
                self.gamma_0 + eta * (self.hrv_stress - self.hrv_coherence)
            ))
            self.tau_in = max(0.1, min(0.5,
                0.3 * (1 + 0.5 * self.hrv_stress)
            ))
            self.tau_out = max(0.5, min(0.9,
                0.7 * (1 - 0.2 * self.hrv_coherence)
            ))
            self.alpha = max(0.01, min(0.5,
                0.1 * (0.5 + self.hrv_coherence)
            ))

            # Coherence also nudges precision: high coherence → steadier focus
            delta = (self.hrv_coherence - 0.5) * 0.1
            self.precision = max(0.05, min(0.95, self.precision + delta))

    # ── Update (feedback loop) ──────────────────────────────────────────────

    def update(self, surprise: float = None, gradient: float = None,
               novelty: float = None, phase: str = None):
        """Update horizon after a tick step.

        surprise : 1 - confidence_after_smartdc (or None if not a doubt step)
        gradient : +1 if phase succeeded, -1 if failed, 0 if neutral
        novelty  : 1 - max_similarity(new, existing) (or None)
        phase    : which phase just ran (for policy weight update)
        """
        # 1. Precision update (prediction error)
        if surprise is not None:
            error = surprise - self.target_surprise
            self.precision = max(0.05, min(0.95,
                self.precision - self.alpha * error
            ))
            self._history.append(surprise)
            if len(self._history) > 10:
                self._history.pop(0)

        # 2. Policy weight update
        if gradient is not None and phase and phase in self.policy_weights:
            self.policy_weights[phase] += self.beta * gradient
            # Clamp to [0.05, ∞) then normalize
            for k in self.policy_weights:
                self.policy_weights[k] = max(0.05, self.policy_weights[k])
            total = sum(self.policy_weights.values())
            for k in self.policy_weights:
                self.policy_weights[k] = round(self.policy_weights[k] / total, 3)
            # 2b. Adapt T based on belief shift (KL divergence)
            self.update_temperature()

        # 3. State transition
        self._update_state(novelty)

    def _update_state(self, novelty: float = None):
        """Determine current state based on precision + recent history.

        Full hysteresis + debounce:
        - Entry thresholds differ from exit thresholds (gap prevents dithering)
        - State change requires 2 consecutive ticks wanting the same target (debounce)
        - RECOVERY triggered instantly on surprise spike (no debounce)
        """
        p = self.precision

        # Surprise spike → RECOVERY (instant, no debounce)
        if self._history and len(self._history) >= 2:
            last = self._history[-1]
            prev = self._history[-2]
            if last - prev > 0.3:
                self.state = RECOVERY
                self._pending_state = None
                self._pending_count = 0
                return

        # Determine target state with hysteresis
        target = self._target_state(p, novelty)

        # Same as current → reset pending, stay
        if target == self.state:
            self._pending_state = None
            self._pending_count = 0
            return

        # Different → debounce: need 2 consecutive ticks
        if not hasattr(self, '_pending_state'):
            self._pending_state = None
            self._pending_count = 0

        if target == self._pending_state:
            self._pending_count += 1
        else:
            self._pending_state = target
            self._pending_count = 1

        if self._pending_count >= 2:
            self.state = target
            self._pending_state = None
            self._pending_count = 0

    def _target_state(self, p: float, novelty: float = None) -> str:
        """Determine target state using hysteresis thresholds.

        With HRV: extended state machine (7 states).
        Without HRV: original 4 states (EXPLORATION/EXECUTION/RECOVERY/INTEGRATION).
        """
        # HRV-driven states (if HRV sensor connected)
        if self.hrv_coherence is not None:
            # Very low coherence → STABILIZE (reset/calibrate)
            if self.hrv_coherence < 0.3:
                return STABILIZE
            # Sync error growing → CONFLICT (system doesn't understand user)
            if self.sync_error > 0.75:
                return CONFLICT

        # Exit thresholds (harder to leave current state)
        if self.state == EXPLORATION and p < 0.45:
            return EXPLORATION
        if self.state == EXECUTION and p > 0.65:
            return EXECUTION
        if self.state == RECOVERY and p < 0.55:
            return RECOVERY
        if self.state == INTEGRATION and 0.45 < p < 0.65:
            return INTEGRATION
        if self.state == STABILIZE and self.hrv_coherence is not None and self.hrv_coherence < 0.5:
            return STABILIZE
        if self.state == CONFLICT and self.sync_error > 0.5:
            return CONFLICT

        # Entry thresholds (need clear signal to enter)
        if p < 0.4:
            return EXPLORATION
        if p > 0.7:
            return EXECUTION
        if novelty is not None and novelty < 0.3:
            return INTEGRATION
        return EXPLORATION if p < 0.5 else INTEGRATION

    # ── Metrics ─────────────────────────────────────────────────────────────

    def get_metrics(self) -> dict:
        """Metrics for UI overlay."""
        weights = self.policy_weights
        # Focus entropy: how spread is attention across phases
        entropy = 0
        for w in weights.values():
            if w > 0:
                entropy -= w * math.log2(w)

        return {
            "precision": round(self.precision, 3),
            "width": round(1.0 / (self.precision + 0.01), 2),
            "state": self.state,
            "focus_entropy": round(entropy, 3),
            "policy": {k: round(v, 3) for k, v in weights.items()},
            "params": self.to_llm_params(),
            "gamma": round(self.gamma, 3),
            "temperature_nand": round(self.temperature_nand, 3),
            "kl_divergence": round(self.kl_divergence, 4),
            "sync_error": round(self.sync_error, 3),
            "tau_in": round(self.tau_in, 3),
            "tau_out": round(self.tau_out, 3),
            "hrv": {
                "coherence": self.hrv_coherence,
                "rmssd": self.hrv_rmssd,
                "stress": self.hrv_stress,
            } if self.hrv_coherence is not None else None,
        }

    # ── Serialization ───────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialize for storage in graph state."""
        return {
            "precision": self.precision,
            "policy_weights": dict(self.policy_weights),
            "target_surprise": self.target_surprise,
            "alpha": self.alpha,
            "beta": self.beta,
            "state": self.state,
            "_history": list(self._history),
            "gamma": self.gamma,
            "gamma_0": self.gamma_0,
            "temperature_nand": self.temperature_nand,
            "temperature_nand_0": self.temperature_nand_0,
            "kl_divergence": self.kl_divergence,
            "_prev_policy_weights": dict(self._prev_policy_weights) if self._prev_policy_weights else None,
            "sync_error": self.sync_error,
            "tau_in": self.tau_in,
            "tau_out": self.tau_out,
            "hrv_coherence": self.hrv_coherence,
            "hrv_stress": self.hrv_stress,
            "hrv_rmssd": self.hrv_rmssd,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CognitiveHorizon":
        """Restore from stored state."""
        h = cls(
            precision=d.get("precision", 0.4),
            policy_weights=d.get("policy_weights"),
            target_surprise=d.get("target_surprise", 0.3),
            alpha=d.get("alpha", 0.1),
            beta=d.get("beta", 0.2),
        )
        h.state = d.get("state", EXPLORATION)
        h._history = d.get("_history", [])
        h.gamma = d.get("gamma", 2.0)
        h.gamma_0 = d.get("gamma_0", 2.0)
        h.temperature_nand = d.get("temperature_nand", 0.1)
        h.temperature_nand_0 = d.get("temperature_nand_0", 0.1)
        h.kl_divergence = d.get("kl_divergence", 0.0)
        h._prev_policy_weights = d.get("_prev_policy_weights")
        h.sync_error = d.get("sync_error", 0.0)
        h.tau_in = d.get("tau_in", 0.3)
        h.tau_out = d.get("tau_out", 0.7)
        h.hrv_coherence = d.get("hrv_coherence")
        h.hrv_stress = d.get("hrv_stress")
        h.hrv_rmssd = d.get("hrv_rmssd")
        return h


# ── Factory ─────────────────────────────────────────────────────────────────

def create_horizon(mode_id: str) -> CognitiveHorizon:
    """Create a CognitiveHorizon with preset for given mode."""
    from .modes import get_mode

    PRESETS = {
        "free":       {"precision": 0.5, "policy": {"generate": 0.25, "merge": 0.25, "elaborate": 0.25, "doubt": 0.25}, "target": 0.3},
        "scout":      {"precision": 0.3, "policy": {"generate": 0.5,  "merge": 0.1,  "elaborate": 0.1,  "doubt": 0.3},  "target": 0.5},
        "vector":     {"precision": 0.7, "policy": {"generate": 0.1,  "merge": 0.2,  "elaborate": 0.2,  "doubt": 0.5},  "target": 0.15},
        "rhythm":     {"precision": 0.5, "policy": {"generate": 0.2,  "merge": 0.2,  "elaborate": 0.3,  "doubt": 0.3},  "target": 0.2},
        "horizon":    {"precision": 0.4, "policy": {"generate": 0.3,  "merge": 0.2,  "elaborate": 0.2,  "doubt": 0.3},  "target": 0.3},
        "builder":    {"precision": 0.6, "policy": {"generate": 0.1,  "merge": 0.2,  "elaborate": 0.3,  "doubt": 0.4},  "target": 0.2},
        "pipeline":   {"precision": 0.6, "policy": {"generate": 0.1,  "merge": 0.1,  "elaborate": 0.4,  "doubt": 0.4},  "target": 0.15},
        "cascade":    {"precision": 0.6, "policy": {"generate": 0.1,  "merge": 0.2,  "elaborate": 0.3,  "doubt": 0.4},  "target": 0.2},
        "scales":     {"precision": 0.5, "policy": {"generate": 0.2,  "merge": 0.3,  "elaborate": 0.2,  "doubt": 0.3},  "target": 0.25},
        "race":       {"precision": 0.5, "policy": {"generate": 0.3,  "merge": 0.1,  "elaborate": 0.2,  "doubt": 0.4},  "target": 0.3},
        "fan":        {"precision": 0.3, "policy": {"generate": 0.5,  "merge": 0.1,  "elaborate": 0.1,  "doubt": 0.3},  "target": 0.5},
        "tournament": {"precision": 0.7, "policy": {"generate": 0.1,  "merge": 0.1,  "elaborate": 0.3,  "doubt": 0.5},  "target": 0.15},
        "dispute":    {"precision": 0.5, "policy": {"generate": 0.1,  "merge": 0.1,  "elaborate": 0.2,  "doubt": 0.6},  "target": 0.25},
        "bayes":      {"precision": 0.6, "policy": {"generate": 0.1,  "merge": 0.1,  "elaborate": 0.3,  "doubt": 0.5},  "target": 0.2},
    }

    preset = PRESETS.get(mode_id, PRESETS["horizon"])
    return CognitiveHorizon(
        precision=preset["precision"],
        policy_weights=dict(preset["policy"]),
        target_surprise=preset["target"],
    )
