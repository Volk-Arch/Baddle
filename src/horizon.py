"""CognitiveState — адаптивный контроллер цикла мышления (SystemState side).

Композиция из:
  • Horizon-слой: precision, policy, τ_in/τ_out, temperature_nand, state machine
  • self.neuro (Neurochem): dopamine/serotonin/norepinephrine + derived γ
  • self.freeze (ProtectiveFreeze): защитный режим при хроническом конфликте
  • Derived: sync_error, sync_regime, hrv_* — читают глобальный UserState
  • Mode flags: llm_disabled (Camera)

Bayesian update делегирован в `self.neuro.apply_to_bayes`. См. neurochem.py для
формул и neurochem-design.md для спецификации.

Прайм-директива: sync_error = ‖user − system‖ в 4-мерном нейрохимическом
пространстве. UserState питается сигналами юзера (HRV, тайминги, feedback),
SystemState — динамикой графа. См. docs/symbiosis-design.md.

States (гистерезис + debounce):
  EXPLORATION  (precision 0.3–0.5, широкий фокус)
  EXECUTION    (0.7–0.9, узкий)
  RECOVERY     (post-surprise drop)
  INTEGRATION  (0.5–0.6, синтез)
  STABILIZE    (HRV coherence < 0.3 — derived из UserState)
  CONFLICT     (sync_error > 0.75 — расхождение user/system)
  PROTECTIVE_FREEZE (freeze.accumulator > 0.15)
"""

import math


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


# ── Tuning knobs (only ones still used) ────────────────────────────────

KAPPA_NE_TEMP = 0.8          # NE → T_eff coupling
T_FLOOR = 0.05                # минимальная температура
MATURITY_GROWTH_RATE = 0.003  # логистический рост на один verified-event
MATURITY_GAIN = 0.4           # полный диапазон сдвига effective_precision (±MATURITY_GAIN/2)


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

        # NAND temperature + метрики
        self.temperature_nand = 0.1
        self.temperature_nand_0 = 0.1
        self._prev_policy_weights = None
        self.kl_divergence = 0.0

        # Thresholds for distinct-zone decisions
        self.tau_in = 0.3
        self.tau_out = 0.7

        # ── Maturity drift ──────────────────────────────────────────────
        # Растёт монотонно к 1.0 по мере накопления verified-нод и resolved-goals.
        # Сдвигает effective_precision на ±MATURITY_GAIN вокруг raw precision:
        # младенец (maturity=0) → exploratory (wide cone), зрелый (maturity=1) → narrow.
        self.maturity: float = 0.0

        # ── Neurochem layer (composition — проще старой монолитной модели) ──
        # Три скаляра + отдельный защитный режим. См. src/neurochem.py.
        # B0: shared singleton РГК — каскад зеркал (UserState/Neurochem/
        # ProtectiveFreeze) работает на одном объекте.
        from .neurochem import Neurochem, ProtectiveFreeze
        from .rgk import get_global_rgk
        rgk = get_global_rgk()
        self.neuro = Neurochem(rgk=rgk)
        self.freeze = ProtectiveFreeze(rgk=rgk)
        self._burnout_trip_count = 0  # kept for metrics

        # ── Mode flags ──────────────────────────────────────────────────
        self.llm_disabled = False         # v8c camera (sensory deprivation)
        self.state_origin_hint = "1_rest" # last computed state_origin (v8c)


    # ── Maturity drift (0.2 младенец → 0.7+ зрелый) ─────────────────────────

    @property
    def effective_precision(self) -> float:
        """Raw precision + maturity-зависимый сдвиг [−MATURITY_GAIN/2, +MATURITY_GAIN/2].

        maturity=0.0 → сдвиг −0.2 (всё возможно, младенческий широкий конус)
        maturity=1.0 → сдвиг +0.2 (конус сужается, взрослая точность)
        Всегда в [0.05, 0.95].
        """
        shift = MATURITY_GAIN * (self.maturity - 0.5)
        return max(0.05, min(0.95, self.precision + shift))

    def note_verified(self):
        """Verified-событие (нода пересекла conf ≥ 0.8 или goal resolved).

        Логистический рост: шаг замедляется при приближении к 1.0.
        """
        self.maturity = min(1.0, self.maturity + MATURITY_GROWTH_RATE * (1.0 - self.maturity))

    # ── LLM params ──────────────────────────────────────────────────────────

    def to_llm_params(self) -> dict:
        """Convert effective precision (с maturity drift) to LLM generation parameters."""
        p = self.effective_precision
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

    # ── NAND-architecture: KL-T adaptation, sync_error ─────────────────────

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

    # ── Derived: sync_error + hrv passthrough from UserState ──────────────
    # Prime-directive: sync_error = ‖user − system‖ (L2 в 4-мерном пространстве
    # dopamine/serotonin/norepinephrine/burnout). Не скаляр, не хранится — вычисляется
    # из глобального UserState. См. src/user_state.py и docs/symbiosis-design.md.

    @property
    def sync_error(self) -> float:
        try:
            from .user_state import get_user_state, compute_sync_error
            return compute_sync_error(get_user_state(), self.neuro, self.freeze)
        except Exception:
            return 0.0

    @property
    def sync_regime(self) -> str:
        try:
            from .user_state import get_user_state, compute_sync_regime
            return compute_sync_regime(get_user_state(), self.neuro, self.freeze)
        except Exception:
            return "flow"

    @property
    def hrv_coherence(self):
        try:
            from .user_state import get_user_state
            return get_user_state().hrv_coherence
        except Exception:
            return None

    @property
    def hrv_stress(self):
        try:
            from .user_state import get_user_state
            return get_user_state().hrv_stress
        except Exception:
            return None

    @property
    def hrv_rmssd(self):
        try:
            from .user_state import get_user_state
            return get_user_state().hrv_rmssd
        except Exception:
            return None

    # ── Neurochem + freeze: делегируем в self.neuro / self.freeze ──────────

    def inject_ne(self, amount: float = 0.3):
        """User input → bump norepinephrine (внимание/напряжение). Backward compat."""
        self.neuro.norepinephrine = max(0.0, min(1.0, self.neuro.norepinephrine + amount))

    def update_neurochem(self, d=None, w_change=None, weights=None):
        """Обновить нейрохимию + защитный режим по сигналам тика.

        d        → dopamine EMA (новизна)
        w_change → serotonin EMA (стабильность весов)
        weights  → norepinephrine EMA (энтропия распределения)
        d + текущий serotonin → ProtectiveFreeze accumulator
        """
        self.neuro.update(d=d, w_change=w_change, weights=weights)

        if d is not None:
            self.freeze.update(d=d, serotonin=self.neuro.serotonin)
            # Синхронизация state machine с freeze.active
            if self.freeze.active and self.state != PROTECTIVE_FREEZE:
                self._prev_state = self.state
                self.state = PROTECTIVE_FREEZE
                self._burnout_trip_count += 1
            elif not self.freeze.active and self.state == PROTECTIVE_FREEZE:
                self.state = self._prev_state or EXPLORATION

        # state_origin derived
        if self.neuro.norepinephrine > 0.55 or self.freeze.conflict_accumulator > 0.1:
            self.state_origin_hint = "1_held"
        else:
            self.state_origin_hint = "1_rest"

    def apply_to_bayes(self, prior: float, d: float) -> float:
        """NAND Bayes через Neurochem.apply_to_bayes. Заблокирован в freeze.

        γ derived из norepinephrine и serotonin (см. Neurochem).
        """
        if self.freeze.active or self.state == PROTECTIVE_FREEZE:
            return prior
        return self.neuro.apply_to_bayes(prior, d)

    def effective_temperature(self) -> float:
        """T_eff базируется на NE (norepinephrine). Высокое напряжение → острее выбор."""
        return max(T_FLOOR, self.temperature_nand_0 * (1 - KAPPA_NE_TEMP * self.neuro.norepinephrine) + T_FLOOR)

    def horizon_budget(self) -> float:
        """Доля бюджета Horizon (vs DMN). Низкое внимание → DMN берёт бюджет."""
        if self.freeze.active:
            return 0.3   # минимум при freeze
        # Высокое NE (напряжение/внимание) → Horizon в фокусе
        return max(0.2, min(0.95, 0.8 * self.neuro.norepinephrine + 0.2))

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

        # Determine target state with hysteresis (use effective_precision so
        # maturity-драйфт сдвигает переходы — младенец сидит в EXPLORATION чаще)
        target = self._target_state(self.effective_precision, novelty)

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
        # Highest priority: honor active freeze until accumulator drops below recovery threshold
        if self.state == PROTECTIVE_FREEZE and self.freeze.active:
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

        neuro_dict = self.neuro.to_dict()
        # Pull user-side from global UserState for full symbiosis picture
        try:
            from .user_state import get_user_state
            user_dict = get_user_state().to_dict()
        except Exception:
            user_dict = None
        eff_p = self.effective_precision
        return {
            "precision": round(self.precision, 3),
            "effective_precision": round(eff_p, 3),
            "maturity": round(self.maturity, 3),
            "width": round(1.0 / (eff_p + 0.01), 2),
            "state": self.state,
            "focus_entropy": round(entropy, 3),
            "policy": {k: round(v, 3) for k, v in weights.items()},
            "params": self.to_llm_params(),
            "gamma": neuro_dict["gamma"],
            "temperature_nand": round(self.temperature_nand, 3),
            "kl_divergence": round(self.kl_divergence, 4),
            "sync_error": round(self.sync_error, 3),
            "sync_regime": self.sync_regime,
            "tau_in": round(self.tau_in, 3),
            "tau_out": round(self.tau_out, 3),
            "hrv": {
                "coherence": self.hrv_coherence,
                "rmssd": self.hrv_rmssd,
                "stress": self.hrv_stress,
            } if self.hrv_coherence is not None else None,
            "neurochem": {
                "dopamine":       neuro_dict["dopamine"],
                "serotonin":      neuro_dict["serotonin"],
                "norepinephrine": neuro_dict["norepinephrine"],
                # Phase D: 5-axis chem + balance diagnostic
                "acetylcholine":  neuro_dict.get("acetylcholine", 0.5),
                "gaba":           neuro_dict.get("gaba", 0.5),
                "balance":        neuro_dict.get("balance", 1.0),
                # burnout = display_burnout: max(conflict_accumulator,
                # silence_pressure, imbalance_pressure). Три feeder'а:
                #   • conflict — графовые конфликты (единственный активирует freeze)
                #   • silence  — хроническое молчание юзера (таймер)
                #   • imbalance — EMA aggregate 4-х PE-каналов (Friston-loop)
                "burnout":             round(self.freeze.display_burnout, 3),
                "burnout_conflict":    round(self.freeze.conflict_accumulator, 3),
                "burnout_silence":     round(self.freeze.silence_pressure, 3),
                "burnout_imbalance":   round(self.freeze.imbalance_pressure, 3),
                # Прайм-директива: EMA sync_error для валидации через 2 мес.
                # Fast (1ч) — для UI тренда; slow (3д) — для weekly aggregate.
                # Пишется раз в час в data/prime_directive.jsonl.
                "sync_error_ema_fast": round(self.freeze.sync_error_ema_fast, 4),
                "sync_error_ema_slow": round(self.freeze.sync_error_ema_slow, 4),
                # Self-prediction: Baddle PE на её же baseline.
                # Входит одной из 4-х компонент в burnout_imbalance.
                "self_imbalance":      neuro_dict.get("self_imbalance", 0.0),
                "freeze_active":       self.freeze.active,
                "state_origin":        self.state_origin_hint,
                "recent_rpe":          neuro_dict.get("recent_rpe", 0.0),
            },
            "user_state": user_dict,
            "llm_disabled": self.llm_disabled,
            "horizon_budget": round(self.horizon_budget(), 3),
            "t_effective": round(self.effective_temperature(), 3),
        }

    # ── Serialization ───────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialize for storage in graph state.

        sync_error и hrv_* — derived properties, не сериализуются (живут в UserState).
        """
        return {
            "precision": self.precision,
            "policy_weights": dict(self.policy_weights),
            "target_surprise": self.target_surprise,
            "alpha": self.alpha,
            "beta": self.beta,
            "state": self.state,
            "_prev_state": self._prev_state,
            "_history": list(self._history),
            "temperature_nand": self.temperature_nand,
            "temperature_nand_0": self.temperature_nand_0,
            "kl_divergence": self.kl_divergence,
            "_prev_policy_weights": dict(self._prev_policy_weights) if self._prev_policy_weights else None,
            "tau_in": self.tau_in,
            "tau_out": self.tau_out,
            "neuro": self.neuro.to_dict(),
            "freeze": self.freeze.to_dict(),
            "_burnout_trip_count": self._burnout_trip_count,
            "maturity": self.maturity,
            "llm_disabled": self.llm_disabled,
            "state_origin_hint": self.state_origin_hint,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CognitiveState":
        """Восстановление из dump'а. Legacy поля sync_error/hrv_* игнорируются."""
        from .neurochem import Neurochem, ProtectiveFreeze

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
        h.temperature_nand = d.get("temperature_nand", 0.1)
        h.temperature_nand_0 = d.get("temperature_nand_0", 0.1)
        h.kl_divergence = d.get("kl_divergence", 0.0)
        h._prev_policy_weights = d.get("_prev_policy_weights")
        h.tau_in = d.get("tau_in", 0.3)
        h.tau_out = d.get("tau_out", 0.7)
        h.neuro = Neurochem.from_dict(d.get("neuro", {}))
        h.freeze = ProtectiveFreeze.from_dict(d.get("freeze", {}))
        h._burnout_trip_count = d.get("_burnout_trip_count", 0)
        h.maturity = float(d.get("maturity", 0.0))
        h.llm_disabled = d.get("llm_disabled", False)
        h.state_origin_hint = d.get("state_origin_hint", "1_rest")
        return h

    @property
    def gamma(self) -> float:
        """γ derived из neurochem."""
        return self.neuro.gamma


# ── Global state singleton (одна нейрохимия на человека) ─────────────

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
    """Create a fresh CognitiveState with preset for given mode.

    Preset (precision, policy, target_surprise) читается из
    `modes.get_mode(mode_id).preset` — один источник истины. Для глобального
    singleton per-person используй get_global_state().
    """
    from .modes import get_mode
    preset = get_mode(mode_id).get("preset") or get_mode("horizon")["preset"]
    return CognitiveState(
        precision=preset["precision"],
        policy_weights=dict(preset["policy"]),
        target_surprise=preset["target"],
    )
