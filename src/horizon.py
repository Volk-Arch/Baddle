"""CognitiveState — unified adaptive controller for the thinking cycle.

Replaces the former CognitiveHorizon + Neurochem split (v12 collapse, pre-emptive).
One object, one to_dict, one UI panel. Holds:

  • Horizon layer (precision, policy, γ, τ, temperature_nand, state machine)
  • Neurochem layer (S pliability, NE arousal, DA_tonic/phasic, burnout_idx)
  • HRV layer (coherence, stress, rmssd — inputs)
  • Mode flags (llm_disabled for v8c Camera, PROTECTIVE_FREEZE)

Key formulas (docs/nand-architecture.md, TODO v5d):
  γ_eff = γ · S                            pliability gates Bayesian sensitivity
  logit(post) = logit(prior) + γ_eff·(1−2d)    signed NAND Bayes
  T_eff = T₀ · (1 − κ·NE) + T_floor        arousal sharpens choice
  burnout_idx = EMA(max(0, d − τ_stable)) · (1 − S)
  PROTECTIVE_FREEZE when burnout_idx > θ_burnout; recovery gated by DA_tonic

States (hysteresis + debounce):
  EXPLORATION  (precision 0.3–0.5, wide)
  EXECUTION    (0.7–0.9, narrow)
  RECOVERY     (post-surprise drop)
  INTEGRATION  (0.5–0.6, synthesis)
  STABILIZE    (HRV coherence < 0.3)
  CONFLICT     (sync_error spike)
  PROTECTIVE_FREEZE (burnout trip)

`CognitiveHorizon` is kept as a backward-compat alias at the bottom.
"""

import math
import random


# ── State constants ─────────────────────────────────────────────────────────

EXPLORATION = "exploration"
EXECUTION = "execution"
RECOVERY = "recovery"
INTEGRATION = "integration"

# Extended states (HRV / burnout driven)
STABILIZE = "stabilize"
SHIFT = "shift"
CONFLICT = "conflict"
PROTECTIVE_FREEZE = "protective_freeze"


# ── Neurochem tuning knobs (override via settings.json later) ──────────────

# EMA decay lambdas (per update step)
LAMBDA_NE = 0.3          # fast
LAMBDA_S = 0.1           # medium
LAMBDA_DA_FAST = 0.4     # fast phasic
LAMBDA_DA_SLOW = 0.02    # slow tonic
LAMBDA_BURNOUT = 0.05    # slow accumulation

# Thresholds
D_BASELINE = 0.4
TAU_STABLE = 0.6
THETA_BURNOUT = 0.35
THETA_RECOVERY = 0.2
THETA_DA_RECOVERY = 0.45
KAPPA_NE_TEMP = 0.8
T_FLOOR = 0.05
DA_TONIC_BASELINE = 0.5
S_BASELINE = 0.6


# ── CognitiveState ──────────────────────────────────────────────────────────

class CognitiveState:
    """Unified Horizon + Neurochem. One adaptive controller for the whole loop."""

    def __init__(self,
                 precision: float = 0.4,
                 policy_weights: dict = None,
                 target_surprise: float = 0.3,
                 alpha: float = 0.1,
                 beta: float = 0.2):
        # ── Horizon layer ───────────────────────────────────────────────
        self.precision = max(0.05, min(0.95, precision))
        self.policy_weights = policy_weights or {
            "generate": 0.3, "merge": 0.2, "elaborate": 0.2, "doubt": 0.3,
        }
        self.target_surprise = target_surprise
        self.alpha = alpha
        self.beta = beta
        self.state = EXPLORATION
        self._prev_state = None           # remember pre-FREEZE state for recovery
        self._history = []
        self._pending_state = None
        self._pending_count = 0

        # NAND core parameters
        self.gamma = 2.0
        self.gamma_0 = 2.0
        self.temperature_nand = 0.1
        self.temperature_nand_0 = 0.1
        self._d_self_history = []
        self.sync_error = 0.0
        self._prev_policy_weights = None
        self.kl_divergence = 0.0

        # Thresholds for distinct-zone decisions (can be nudged by HRV)
        self.tau_in = 0.3
        self.tau_out = 0.7

        # ── HRV layer ───────────────────────────────────────────────────
        self.hrv_coherence = None
        self.hrv_stress = None
        self.hrv_rmssd = None

        # ── Neurochem layer ─────────────────────────────────────────────
        # S (серотонин) — plasticity / cost of update
        self.S = S_BASELINE
        # NE (норадреналин) — arousal / attention budget
        self.NE = 0.3
        self._ne_d_ema = 0.0              # rolling EMA of (d − baseline) for NE
        # DA (дофамин) — reward/drive. Tonic = mood, phasic = event response
        self.DA_tonic = DA_TONIC_BASELINE
        self.DA_phasic = 0.0
        # Burnout — protective index
        self.burnout_idx = 0.0
        self._burnout_trip_count = 0

        # ── Mode flags ──────────────────────────────────────────────────
        self.llm_disabled = False         # v8c camera (sensory deprivation)
        self.state_origin_hint = "1_rest" # last computed state_origin (v8c)

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

            # Neurochem coupling (v5d):
            # Coherence → pliability S (calm body → system easier to update)
            self.S = max(0.2, min(1.0,
                LAMBDA_S * (0.4 + 0.6 * self.hrv_coherence) + (1 - LAMBDA_S) * self.S
            ))
            # Coherence → DA_tonic (sustained calm = contentment baseline)
            self.DA_tonic = max(0.0, min(1.0,
                LAMBDA_DA_SLOW * self.hrv_coherence + (1 - LAMBDA_DA_SLOW) * self.DA_tonic
            ))
            # Stress → NE baseline push
            self.NE = max(0.0, min(1.0, 0.5 * self.NE + 0.5 * (0.3 + 0.5 * self.hrv_stress)))

    # ── Neurochem: NE, S, DA, burnout ───────────────────────────────────────

    def inject_ne(self, amount: float = 0.3):
        """User input arrives → NE spike. Shifts budget toward Horizon, away from DMN."""
        self.NE = max(0.0, min(1.0, self.NE + amount))

    def update_neurochem(self,
                         d: float = None,
                         rpe: float = None,
                         d_self: float = None,
                         user_feedback: str = None,
                         time_delta: float = 1.0):
        """EMA update of S, NE, DA, burnout. Call after each cognitive step.

        d           : last distinct distance (drives NE via |d − baseline|)
        rpe         : reward prediction error (signed, drives DA_phasic)
        d_self      : distinct between predicted and actual (self-consistency, drives S)
        user_feedback: "accepted" | "rejected" | "ignored" | None (drives DA, S)
        time_delta  : seconds since last update (for decay calibration, advisory)
        """
        # NE: driven by recent surprise (|d − baseline|)
        if d is not None:
            surprise_mag = max(0.0, abs(d - D_BASELINE))
            self._ne_d_ema = LAMBDA_NE * surprise_mag + (1 - LAMBDA_NE) * self._ne_d_ema
            # NE decays toward _ne_d_ema + small baseline pull
            target_ne = min(1.0, self._ne_d_ema * 2.0)
            self.NE = LAMBDA_NE * target_ne + (1 - LAMBDA_NE) * self.NE
            self.NE = max(0.0, min(1.0, self.NE))

        # DA_phasic: fast-response to RPE
        if rpe is not None:
            self.DA_phasic = LAMBDA_DA_FAST * rpe + (1 - LAMBDA_DA_FAST) * self.DA_phasic
            self.DA_phasic = max(-1.0, min(1.0, self.DA_phasic))
        else:
            # Phasic decays toward 0 in absence of events
            self.DA_phasic *= (1 - LAMBDA_DA_FAST)

        # DA_tonic: slow integration of phasic (mood)
        self.DA_tonic = LAMBDA_DA_SLOW * (DA_TONIC_BASELINE + self.DA_phasic * 0.5) \
                        + (1 - LAMBDA_DA_SLOW) * self.DA_tonic
        self.DA_tonic = max(0.0, min(1.0, self.DA_tonic))

        # S: feedback-driven plasticity
        #   accepted → higher S (we're learning successfully, stay open)
        #   rejected → lower S (step back, use priors more)
        #   d_self large → lower S (we're inconsistent, be rigid)
        if user_feedback == "accepted":
            target_s = 0.85
            self.S = LAMBDA_S * target_s + (1 - LAMBDA_S) * self.S
        elif user_feedback == "rejected":
            target_s = 0.35
            self.S = LAMBDA_S * target_s + (1 - LAMBDA_S) * self.S
        if d_self is not None:
            target_s = max(0.2, 1.0 - d_self)
            self.S = LAMBDA_S * target_s + (1 - LAMBDA_S) * self.S
        self.S = max(0.2, min(1.0, self.S))

        # Burnout: chronic conflict × low plasticity
        if d is not None:
            conflict_signal = max(0.0, d - TAU_STABLE)
            plasticity_deficit = (1.0 - self.S)
            burn_increment = conflict_signal * plasticity_deficit
            self.burnout_idx = LAMBDA_BURNOUT * burn_increment \
                              + (1 - LAMBDA_BURNOUT) * self.burnout_idx
            self.burnout_idx = max(0.0, min(1.0, self.burnout_idx))

        # Mode transitions: PROTECTIVE_FREEZE entry / recovery
        if self.state != PROTECTIVE_FREEZE and self.burnout_idx > THETA_BURNOUT:
            self._prev_state = self.state
            self.state = PROTECTIVE_FREEZE
            self._burnout_trip_count += 1
        elif self.state == PROTECTIVE_FREEZE:
            # Recovery requires low burnout AND restored DA_tonic
            if self.burnout_idx < THETA_RECOVERY and self.DA_tonic > THETA_DA_RECOVERY:
                self.state = self._prev_state or EXPLORATION

        # Update state_origin (v8c)
        if self.NE > 0.55 or self.burnout_idx > 0.2:
            self.state_origin_hint = "1_held"
        else:
            self.state_origin_hint = "1_rest"

    def apply_to_bayes(self, prior: float, d: float) -> float:
        """NAND Bayes update through CognitiveState: uses γ_eff = γ·S.

        If PROTECTIVE_FREEZE: return prior unchanged (ΔlogW = 0).

        logit(post) = logit(prior) + γ_eff · (1 − 2d)
        γ_eff = γ · S (plasticity gates Bayesian sensitivity)

        See TODO v5d, docs/nand-architecture.md
        """
        if self.state == PROTECTIVE_FREEZE:
            return prior
        prior = max(0.01, min(0.99, prior))
        gamma_eff = self.gamma * self.S
        log_prior = math.log(prior / (1 - prior))
        signed = gamma_eff * (1 - 2 * d)
        log_posterior = log_prior + signed
        posterior = 1.0 / (1.0 + math.exp(-log_posterior))
        return round(max(0.01, min(0.99, posterior)), 3)

    def effective_temperature(self) -> float:
        """T_eff = T₀ · (1 − κ·NE) + T_floor. Higher NE → sharper choice."""
        return max(T_FLOOR, self.temperature_nand_0 * (1 - KAPPA_NE_TEMP * self.NE) + T_FLOOR)

    def horizon_budget(self) -> float:
        """Share of cognitive budget given to Horizon (vs DMN). budget_H = f(NE)."""
        # clamp(0.8·NE + 0.2, 0.2, 0.95)
        if self.state == PROTECTIVE_FREEZE:
            return 0.3   # minimal active processing during freeze
        return max(0.2, min(0.95, 0.8 * self.NE + 0.2))

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

        State priority: PROTECTIVE_FREEZE > HRV-driven (STABILIZE/CONFLICT)
        > precision-driven (EXPLORATION/EXECUTION/INTEGRATION).
        """
        # Highest priority: burnout trip → PROTECTIVE_FREEZE (handled in update_neurochem,
        # but honor it here too for external callers)
        if self.state == PROTECTIVE_FREEZE and self.burnout_idx > THETA_RECOVERY:
            return PROTECTIVE_FREEZE

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
            "gamma_eff": round(self.gamma * self.S, 3),
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
            # Neurochem layer (v5d)
            "neurochem": {
                "S": round(self.S, 3),
                "NE": round(self.NE, 3),
                "DA_tonic": round(self.DA_tonic, 3),
                "DA_phasic": round(self.DA_phasic, 3),
                "burnout_idx": round(self.burnout_idx, 3),
                "state_origin": self.state_origin_hint,
            },
            "llm_disabled": self.llm_disabled,
            "horizon_budget": round(self.horizon_budget(), 3),
            "t_effective": round(self.effective_temperature(), 3),
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
            "_prev_state": self._prev_state,
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
            # Neurochem (v5d)
            "S": self.S,
            "NE": self.NE,
            "_ne_d_ema": self._ne_d_ema,
            "DA_tonic": self.DA_tonic,
            "DA_phasic": self.DA_phasic,
            "burnout_idx": self.burnout_idx,
            "_burnout_trip_count": self._burnout_trip_count,
            "llm_disabled": self.llm_disabled,
            "state_origin_hint": self.state_origin_hint,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CognitiveState":
        """Restore from stored state. Backward-compatible with old Horizon dumps."""
        h = cls(
            precision=d.get("precision", 0.4),
            policy_weights=d.get("policy_weights"),
            target_surprise=d.get("target_surprise", 0.3),
            alpha=d.get("alpha", 0.1),
            beta=d.get("beta", 0.2),
        )
        h.state = d.get("state", EXPLORATION)
        h._prev_state = d.get("_prev_state")
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
        # Neurochem (v5d) with sensible defaults for old dumps
        h.S = d.get("S", S_BASELINE)
        h.NE = d.get("NE", 0.3)
        h._ne_d_ema = d.get("_ne_d_ema", 0.0)
        h.DA_tonic = d.get("DA_tonic", DA_TONIC_BASELINE)
        h.DA_phasic = d.get("DA_phasic", 0.0)
        h.burnout_idx = d.get("burnout_idx", 0.0)
        h._burnout_trip_count = d.get("_burnout_trip_count", 0)
        h.llm_disabled = d.get("llm_disabled", False)
        h.state_origin_hint = d.get("state_origin_hint", "1_rest")
        return h


# ── Backward-compat alias ─────────────────────────────────────────────────
# Old code imports `CognitiveHorizon` — keep alias to avoid breaking callers.
CognitiveHorizon = CognitiveState


# ── Global state singleton (one neurochem per person, see TODO v5d prime) ──

_global_state: "CognitiveState | None" = None


def get_global_state() -> CognitiveState:
    """Global CognitiveState — one per person, shared across all workspaces.

    Per prime directive (sync with human): neurochem is the body-side scalar
    that lives once, not per-graph. Workspaces can carry their own Horizon
    snapshots later if needed, but for now there's one state for the system.
    """
    global _global_state
    if _global_state is None:
        _global_state = CognitiveState()
    return _global_state


def set_global_state(state: CognitiveState):
    """Replace global state (for tests or restart)."""
    global _global_state
    _global_state = state


# ── Factory ─────────────────────────────────────────────────────────────────

def create_horizon(mode_id: str) -> CognitiveState:
    """Create a CognitiveState with preset for given mode.

    Note: this creates a fresh CognitiveState; for the global singleton (one per
    person), use get_global_state() instead. This factory is useful for
    per-session Horizon snapshots stored in graph state.
    """
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
    return CognitiveState(
        precision=preset["precision"],
        policy_weights=dict(preset["policy"]),
        target_surprise=preset["target"],
    )
