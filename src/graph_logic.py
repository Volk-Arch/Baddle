"""baddle — graph thinking logic (nodes, edges, Bayes, similarity, generation)."""

import re
import random
import logging
import threading
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from .main import cosine_similarity
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


def _bayesian_update_distinct(prior: float, d: float) -> float:
    """NAND Bayes update через глобальную нейрохимию (γ derived).

    d ∈ [0,1]: дистанция между evidence и hypothesis. Делегирует в
    `CognitiveState.apply_to_bayes` (γ из neuro.gamma, блокируется при
    PROTECTIVE_FREEZE), затем кормит **RPE** (prior, posterior) в neurochem
    — автономный dopamine drift по неожиданности Δconfidence (без юзера).
    """
    from .horizon import get_global_state
    cs = get_global_state()
    posterior = cs.apply_to_bayes(prior, d)
    try:
        cs.neuro.record_outcome(prior, posterior)
    except Exception as e:
        log.debug(f"[bayes] RPE record failed: {e}")
    # Maturity drift: нода пересекла verified threshold → система взрослеет
    try:
        if prior < 0.8 and posterior >= 0.8:
            cs.note_verified()
    except Exception as e:
        log.debug(f"[bayes] maturity note failed: {e}")
    return posterior


def sample_in_embedding_space(
    seed_embedding: list[float],
    n: int = 5,
    sigma: float = 1.0,
    novelty_threshold: float = 0.2,
    max_distance_from_seed: float = 0.6,
    existing_embeddings: list | None = None,
    max_attempts: int = 100,
) -> list[list[float]]:
    """Brainstorm в embedding space — N перturb'овских векторов без LLM текста.

    sigma = desired L2 norm шума (dimension-invariant, scaled per-dim как
    sigma/sqrt(dim)). sigma=1.0 → distinct(candidate, seed) ≈ 0.25. Больше
    sigma → дальше от seed.

    Returns unit-normalized candidate embeddings, каждый:
      • distinct(candidate, seed) < max_distance_from_seed  (не улетел в область)
      • distinct(candidate, e) > novelty_threshold ∀ e ∈ existing + accepted
        (новизна относительно графа + уже принятых sample'ов)

    Используется в /graph/brainstorm-seed: дешёвая генерация идей в виде
    векторов; текст рендерится лениво только для тех что пользователь откроет.
    Экономит токены (не генерируем текст до вычисления novelty).
    """
    import numpy as np
    from .main import distinct

    seed_vec = np.asarray(seed_embedding, dtype=np.float32)
    if seed_vec.size == 0:
        return []

    existing = [np.asarray(e, dtype=np.float32) for e in (existing_embeddings or []) if e]
    results: list[np.ndarray] = []
    attempts = 0
    rng = np.random.default_rng()
    # Scale noise stddev с размерностью: expected ‖noise‖ ≈ sigma независимо от dim.
    # Без этого в 768-d noise тонет/доминирует — dimension-invariance важна.
    per_dim = float(sigma) / (seed_vec.size ** 0.5)

    while len(results) < n and attempts < max_attempts:
        attempts += 1
        noise = rng.normal(0.0, per_dim, size=seed_vec.shape).astype(np.float32)
        cand = seed_vec + noise
        norm = float(np.linalg.norm(cand))
        if norm < 1e-6:
            continue
        cand = cand / norm   # unit-normalize как настоящие embeddings

        if distinct(cand, seed_vec) > max_distance_from_seed:
            continue
        if any(distinct(cand, e) < novelty_threshold for e in existing):
            continue
        if any(distinct(cand, r) < novelty_threshold for r in results):
            continue
        results.append(cand)

    return [r.tolist() for r in results]


def _d_from_relation(relation: str, strength: float) -> float:
    """Map (relation, strength) → distinct distance d.

    supports,   strength s → d = 1 − s  (high s = low d = close to H)
    contradicts,strength s → d = s      (high s = high d = far from H)
    neutral,    any        → d = 0.5    (no update)
    """
    s = max(0.0, min(1.0, float(strength)))
    if relation == "supports":
        return 1.0 - s
    if relation == "contradicts":
        return s
    return 0.5


def _beta_prior_update(alpha: float, beta: float, supports: bool, strength: float = 1.0) -> tuple:
    """Beta distribution prior update.

    Prior: Beta(alpha, beta) → mean = alpha/(alpha+beta), confidence ~ alpha+beta
    Observation: supports (True/False) with strength in [0,1]
    Returns: (new_alpha, new_beta)

    Gives both probability AND confidence in that probability.
    See docs/nand-architecture.md
    """
    alpha = max(0.5, float(alpha))
    beta = max(0.5, float(beta))
    if supports:
        alpha += strength
    else:
        beta += strength
    return (round(alpha, 2), round(beta, 2))


def _beta_mean_ci(alpha: float, beta: float) -> dict:
    """Extract mean and 95% credible interval from Beta(alpha, beta)."""
    import math
    alpha = max(0.5, float(alpha))
    beta = max(0.5, float(beta))
    total = alpha + beta
    mean = alpha / total if total > 0 else 0.5
    # Approximation for variance/std
    var = (alpha * beta) / ((total ** 2) * (total + 1)) if total > 1 else 0.25
    std = math.sqrt(var)
    ci_lower = max(0.0, mean - 1.96 * std)
    ci_upper = min(1.0, mean + 1.96 * std)
    return {
        "mean": round(mean, 3),
        "std": round(std, 3),
        "ci_lower": round(ci_lower, 3),
        "ci_upper": round(ci_upper, 3),
        "confidence_strength": round(total, 2),  # higher = more certain
    }


# ── node helpers ─────────────────────────────────────────────────────────────

def _make_node(node_id: int, text: str, depth: int = 0, topic: str = "",
               entropy: dict | None = None, confidence: float = 0.5,
               node_type: str = "thought",
               embedding: list | None = None,
               rendered: bool = True) -> dict:
    """Create a node dict with all required fields.

    embeddings-first: embedding field is primary. Text stays for display.
    If `embedding` is None, it'll be populated by _ensure_embeddings on next pass.
    distinct() can read from node["embedding"] directly, no LLM hop required.

    `rendered=False` обозначает ноду созданную через embedding-first путь
    (brainstorm-seed: perturbed vectors без текста). UI рендерит text только
    по клику через /graph/render-node — text-on-demand.
    """
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": node_id,
        "text": text,
        "embedding": embedding,
        "entropy": entropy or {"avg": 0.0, "unc": 0.0},
        "depth": depth,
        "topic": topic,
        "confidence": round(confidence, 2),
        "type": node_type,
        "rendered": rendered,
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
        node.setdefault("rendered", True)    # legacy nodes считаются уже отрендеренными
        node.setdefault("created_at", None)
        node.setdefault("last_accessed", None)
        # Action Memory fields (only populated for type=action/outcome nodes)
        if node.get("type") == "action":
            node.setdefault("actor", "baddle")
            node.setdefault("action_kind", "unknown")
            node.setdefault("context", {})
            node.setdefault("closed", False)
            node.setdefault("outcome_idx", None)
        elif node.get("type") == "outcome":
            node.setdefault("linked_action_idx", None)
            node.setdefault("delta_sync_error", 0.0)
            node.setdefault("user_reaction", "silence")
            node.setdefault("latency_s", 0.0)


# ── Hebbian: touch (обращение к ноде) ───────────────────────────────────────

# Стандартный boost на одно обращение. Подобран под daily decay 0.005 в
# consolidation.decay_unused_nodes — безубыточность ≈ 1 касание в 4 дня.
# Четыре дня — это «неделя ±», мягкий ритм напоминания о ноде.
TOUCH_BOOST_DEFAULT = 0.02

def touch_node(idx: int, boost: float = TOUCH_BOOST_DEFAULT) -> bool:
    """Hebbian: зафиксировать обращение к ноде.

    Обновляет `last_accessed = now` и чуть усиливает `confidence`.
    Каждое реальное использование (elaborate / smartdc / участие в pump /
    reinforce / рендер по клику) должно проходить через эту функцию.

    Ноды к которым не обращаются не получают boost и постепенно гаснут
    в ночном цикле через `consolidation.decay_unused_nodes`.

    Args:
        idx: индекс ноды в `_graph["nodes"]`
        boost: сколько прибавить к confidence (0 = только last_accessed).
               Default 0.02 — маленький, hebbian. Передать 0 если нужно
               только отметить факт обращения (например UI click / view).

    Returns:
        True если нода существует и была обновлена.
    """
    nodes = _graph.get("nodes", [])
    if not (0 <= idx < len(nodes)):
        return False
    node = nodes[idx]
    node["last_accessed"] = datetime.now(timezone.utc).isoformat()
    if boost > 0:
        cur = float(node.get("confidence", 0.5))
        node["confidence"] = round(min(1.0, cur + boost), 3)
    return True


def touch_nodes(indices, boost: float = TOUCH_BOOST_DEFAULT) -> int:
    """Batch-версия touch_node для списка индексов. Возвращает сколько затронуто."""
    n = 0
    for idx in indices:
        if touch_node(idx, boost=boost):
            n += 1
    return n


# ── Action Memory (самообучение через граф) ─────────────────────────────────
#
# Action / outcome — ноды того же графа что и мысли. DMN / pump / consolidate
# / hebbian decay работают на них автоматически. Cм. docs/action-memory-design.md.


def _current_snapshot() -> dict:
    """Snapshot pre-state для контекста action-ноды.

    Собирает: sync_error, user-state скаляры, system-state (neurochem +
    freeze), sync_regime, hrv_regime, time_of_day. Все опционально —
    если что-то не доступно, поле опускается (не падаем).
    """
    import datetime as _dt
    snap: dict = {"ts": _dt.datetime.now(timezone.utc).isoformat()}

    # Time of day
    try:
        h = _dt.datetime.now().hour
        if 5 <= h < 11:      snap["time_of_day"] = "morning"
        elif 11 <= h < 17:   snap["time_of_day"] = "day"
        elif 17 <= h < 23:   snap["time_of_day"] = "evening"
        else:                 snap["time_of_day"] = "night"
    except Exception:
        pass

    # User state (4 скаляра + agency + valence)
    try:
        from .user_state import get_user_state
        u = get_user_state()
        snap["user_state_before"] = {
            "dopamine":        round(u.dopamine, 3),
            "serotonin":       round(u.serotonin, 3),
            "norepinephrine":  round(u.norepinephrine, 3),
            "burnout":         round(u.burnout, 3),
            "agency":          round(u.agency, 3),
            "valence":         round(u.valence, 3),
        }
    except Exception:
        pass

    # System state (Neurochem + freeze) + sync_error + regimes
    try:
        from .horizon import get_global_state
        gs = get_global_state()
        snap["system_state_before"] = {
            "dopamine":             round(gs.neuro.dopamine, 3),
            "serotonin":            round(gs.neuro.serotonin, 3),
            "norepinephrine":       round(gs.neuro.norepinephrine, 3),
            "conflict_accumulator": round(gs.freeze.conflict_accumulator, 3),
            "desync_pressure":      round(gs.freeze.desync_pressure, 3),
        }
        snap["sync_error_before"] = round(float(gs.sync_error), 3)
        snap["sync_regime"] = gs.sync_regime
    except Exception:
        pass

    # HRV regime (activity_zone из UserState)
    try:
        from .user_state import get_user_state
        snap["hrv_regime"] = get_user_state().activity_zone
    except Exception:
        pass

    return snap


def record_action(actor: str, action_kind: str, text: str,
                   context: Optional[dict] = None,
                   extras: Optional[dict] = None) -> int:
    """Записать action-ноду в граф. Возвращает её idx.

    Args:
        actor: 'baddle' | 'user'
        action_kind: тип действия (sync_seeking, dmn_bridge, user_chat, ...).
                      Cм. docs/action-memory-design.md#action_kind-enum.
        text: human-readable описание для UI/LLM-контекста.
        context: снапшот состояния ДО action'а. Если None — берём текущее
                  через `_current_snapshot()`. Можно передать свой чтобы
                  включить specific поля (sentiment для user_chat).
        extras: любые дополнительные metadata на верхнем уровне ноды
                 (например specific-to-kind details). Сливается с node.

    Нода получает: type='action', actor, action_kind, text, context,
    closed=False, outcome_idx=None, плюс стандартные fields через _make_node.
    """
    from datetime import datetime, timezone as _tz
    ctx = dict(context) if context is not None else _current_snapshot()
    with graph_lock:
        nodes = _graph["nodes"]
        new_id = len(nodes)
        node = _make_node(new_id, text, depth=0, topic="action",
                          confidence=0.5, node_type="action",
                          embedding=None, rendered=True)
        node["actor"] = str(actor or "baddle")
        node["action_kind"] = str(action_kind or "unknown")
        node["context"] = ctx
        node["closed"] = False
        node["outcome_idx"] = None
        if extras:
            for k, v in extras.items():
                if k not in node:  # не перезаписываем стандартные fields
                    node[k] = v
        nodes.append(node)
        _graph.pop("_tick_tried", None)
        log.debug(f"[action-memory] record_action #{new_id}: {actor}/{action_kind} — {text[:60]!r}")
        return new_id


def close_action(action_idx: int, delta_sync_error: float,
                  user_reaction: str = "silence",
                  latency_s: float = 0.0,
                  confidence: float = 0.5,
                  outcome_text: Optional[str] = None) -> Optional[int]:
    """Закрыть action-ноду созданием outcome-ноды + edge caused_by.

    Args:
        action_idx: idx action-ноды которую закрываем.
        delta_sync_error: sync_error_before - sync_error_after.
                           Отрицательное = action улучшил resonance (good).
        user_reaction: 'chat' | 'accept' | 'reject' | 'ignore' | 'silence' |
                        другое. Не-enumerated значения допустимы.
        latency_s: время от ts action'а до now.
        confidence: уверенность в самом measurement (sync_seeking через 2 мин
                     уверенно, через 4 часа шумно).
        outcome_text: если None, автогенерится из параметров.

    Returns: outcome_idx либо None если action_idx невалидный / уже closed.
    """
    with graph_lock:
        nodes = _graph["nodes"]
        if not (0 <= action_idx < len(nodes)):
            return None
        action = nodes[action_idx]
        if action.get("type") != "action" or action.get("closed"):
            return None

        # Текст outcome (human-readable)
        if not outcome_text:
            delta_str = f"{delta_sync_error:+.3f}"
            outcome_text = (f"Δsync_error={delta_str} · reaction={user_reaction} · "
                            f"latency={latency_s:.0f}s")

        new_id = len(nodes)
        onode = _make_node(new_id, outcome_text, depth=0, topic="outcome",
                           confidence=float(confidence), node_type="outcome",
                           embedding=None, rendered=True)
        onode["linked_action_idx"] = int(action_idx)
        onode["delta_sync_error"] = round(float(delta_sync_error), 4)
        onode["user_reaction"] = str(user_reaction)
        onode["latency_s"] = round(float(latency_s), 1)
        nodes.append(onode)

        # Edge caused_by: outcome → action
        caused_by = _graph["edges"].setdefault("caused_by", [])
        caused_by.append([new_id, action_idx])

        # Закрываем action
        action["closed"] = True
        action["outcome_idx"] = new_id

        _graph.pop("_tick_tried", None)
        log.info(f"[action-memory] close_action #{action_idx} "
                 f"({action.get('action_kind')}) → outcome #{new_id}: "
                 f"Δ={delta_sync_error:+.3f}, reaction={user_reaction}")
        return new_id


def score_action_candidates(action_kind: str, candidates: list[str],
                             variant_field: str = "tone",
                             time_of_day: Optional[str] = None,
                             min_history: int = 3) -> dict[str, float]:
    """Для action_kind вернуть {candidate: score} по past outcomes.

    **score > 0** = действие в среднем снижало sync_error (good).
    **score < 0** = в среднем повышало (избегать).
    **score = 0** = нет данных / нейтрально.

    `candidates` — варианты внутри kind (например для sync_seeking это
    tones: ['caring', 'ambient', 'curious', 'reference', 'simple']).
    `variant_field` — по какому полю action-ноды группировать варианты
    (для sync_seeking это `tone` в extras).
    `time_of_day` — опциональный фильтр: считать только actions из того
    же времени суток (morning / day / evening / night).
    `min_history` — минимум closed actions чтобы scoring имел вес. Иначе
    всем возвращаем 0.0 (cold start, fall back to heuristic).

    Реализация через прямой scan графа — O(N) по nodes. На малых
    графах (<10k actions) быстро. Позже можно переделать на embedding
    similarity для лучшей context-match.
    """
    nodes = _graph.get("nodes", [])
    # Собираем per-candidate delta lists
    buckets: dict[str, list[float]] = {c: [] for c in candidates}
    for n in nodes:
        if n.get("type") != "action":
            continue
        if n.get("action_kind") != action_kind:
            continue
        if not n.get("closed"):
            continue
        cand = n.get(variant_field)
        if cand not in buckets:
            continue
        # Context filter
        if time_of_day:
            ctx = n.get("context") or {}
            if ctx.get("time_of_day") != time_of_day:
                continue
        # Outcome delta
        oidx = n.get("outcome_idx")
        if oidx is None:
            continue
        if not (0 <= oidx < len(nodes)):
            continue
        outcome = nodes[oidx]
        if outcome.get("type") != "outcome":
            continue
        try:
            delta = float(outcome.get("delta_sync_error", 0.0))
        except Exception:
            continue
        # Convention: delta = after - before. Negative = sync_error упал = good.
        # Score для максимизации: -delta (positive = good).
        buckets[cand].append(-delta)

    total = sum(len(v) for v in buckets.values())
    if total < min_history:
        return {c: 0.0 for c in candidates}
    # Mean per candidate; empty buckets → 0 (neutral)
    out: dict[str, float] = {}
    for c in candidates:
        vals = buckets[c]
        out[c] = round(sum(vals) / len(vals), 4) if vals else 0.0
    return out


def link_chat_continuation(new_idx: int, chat_kinds: tuple = ("user_chat", "baddle_reply"),
                             window_s: float = 3600) -> Optional[int]:
    """Связать `new_idx` с предыдущим chat-сообщением через `followed_by` edge.

    Ищет последнее action с action_kind ∈ `chat_kinds` до `new_idx`.
    Если найдено и оно в окне `window_s` — добавляет edge `[new_idx, prev]`
    в `_graph.edges.followed_by` (temporal chain). Иначе — new_idx считается
    корневым сообщением, edge не создаётся.

    Returns: prev_idx если linked, None если корневое.
    """
    import datetime as _dt
    nodes = _graph.get("nodes", [])
    if not (0 <= new_idx < len(nodes)):
        return None
    new_node = nodes[new_idx]
    try:
        new_ts = _dt.datetime.fromisoformat(
            str(new_node.get("created_at", "")).replace("Z", "+00:00")
        ).timestamp()
    except Exception:
        return None

    # Идём с конца назад, ищем последний chat-msg
    for i in range(new_idx - 1, -1, -1):
        n = nodes[i]
        if n.get("type") != "action":
            continue
        if n.get("action_kind") not in chat_kinds:
            continue
        try:
            prev_ts = _dt.datetime.fromisoformat(
                str(n.get("created_at", "")).replace("Z", "+00:00")
            ).timestamp()
        except Exception:
            continue
        if new_ts - prev_ts > window_s:
            return None  # слишком давно — это корневое сообщение
        # Link!
        fb = _graph["edges"].setdefault("followed_by", [])
        fb.append([new_idx, i])
        return i
    return None


def list_open_actions(action_kinds: Optional[list[str]] = None) -> list[tuple[int, dict]]:
    """Вернуть list (idx, node) action-нод с closed=False.

    Фильтр по kinds если передан. Используется `_check_action_outcomes`
    чтобы найти какие actions пора закрыть по timeout.
    """
    out = []
    nodes = _graph.get("nodes", [])
    for idx, n in enumerate(nodes):
        if n.get("type") != "action":
            continue
        if n.get("closed"):
            continue
        if action_kinds and n.get("action_kind") not in action_kinds:
            continue
        out.append((idx, n))
    return out


def _get_texts(nodes: list[dict] | None = None) -> list[str]:
    """Return list of texts from nodes (for similarity, prompts)."""
    if nodes is None:
        nodes = _graph["nodes"]
    return [n["text"] for n in nodes]


def force_synthesize_top(n: int = 5, lang: str = "ru",
                          max_tokens: int = 3000) -> Optional[dict]:
    """Forced collapse: top-N hypothesis/evidence/thought по confidence
    + LLM-синтез одним абзацем + добавление synthesis-ноды в граф.

    Общий helper используется `_check_dmn_converge` (фон) и `/graph/synthesize`
    endpoint'ом для graph tab autorun. Source of truth — один.

    Возвращает {text, confidence, node_idx} или None если граф пустой.
    """
    nodes = _graph.get("nodes", [])
    if not nodes:
        return None
    cand = [(i, n) for i, n in enumerate(nodes)
            if n.get("type") in ("hypothesis", "evidence", "thought", "synthesis")]
    if not cand:
        return None
    cand.sort(key=lambda p: p[1].get("confidence", 0.5), reverse=True)
    top = cand[:n]
    avg_conf = round(sum(p[1].get("confidence", 0.5) for p in top) / len(top), 2)
    texts = "\n".join(f"- {p[1].get('text','')[:200]} (conf {p[1].get('confidence',0.5):.2f})"
                       for p in top)
    goal_text = next((n.get("text", "") for n in nodes if n.get("type") == "goal"), "")
    if lang == "ru":
        prompt = (f"Цель: {goal_text}\n"
                  f"Найденные мысли (от сильной к слабой):\n{texts}\n\n"
                  f"Напиши финальный синтез одним абзацем (3-5 предложений). "
                  f"Если уверенность низкая — честно признайся об этом.")
        system = "/no_think\nТы ассистент-синтезатор."
    else:
        prompt = (f"Goal: {goal_text}\nThoughts (strong to weak):\n{texts}\n\n"
                  f"Final synthesis, one paragraph (3-5 sentences). Be honest "
                  f"about low confidence.")
        system = "/no_think\nYou are a synthesizer."
    try:
        res, _ = _graph_generate(
            [{"role": "system", "content": system},
             {"role": "user", "content": prompt}],
            max_tokens=max_tokens, temp=0.5, top_k=30,
        )
    except Exception as e:
        log.debug(f"[force_synthesize_top] LLM failed: {e}")
        return None
    text = (res or "").strip()[:2000]
    if not text:
        return None
    try:
        idx = _add_node(text[:500], depth=0, topic="",
                        confidence=avg_conf, node_type="synthesis")
    except Exception:
        idx = None
    return {"text": text, "confidence": avg_conf, "node_idx": idx,
            "source_indices": [p[0] for p in top]}


def _add_node(text: str, depth: int = 0, topic: str = "",
              entropy: dict | None = None, confidence: float = 0.5,
              node_type: str = "thought",
              embedding: list | None = None,
              rendered: bool = True) -> int:
    """Create node with next id, append to graph, return new index.

    embedding/rendered передаются в _make_node — для embedding-first brainstorm
    (unrendered seed с perturbed embedding без реального текста).
    """
    with graph_lock:
        nodes = _graph["nodes"]
        new_id = len(nodes)
        nodes.append(_make_node(new_id, text, depth, topic, entropy, confidence,
                                node_type, embedding=embedding, rendered=rendered))
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


# ── state ────────────────────────────────────────────────────────────────────
def _fresh_graph():
    return {
        "nodes": [],
        "edges": {
            "manual_links": [],
            "manual_unlinks": [],
            "directed": [],
            # caused_by: action-outcome причинность (outcome_idx → action_idx).
            # Отдельно от `directed` чтобы pump/DMN по умолчанию их не смешивали
            # с semantic-рёбрами. См. docs/action-memory-design.md.
            "caused_by": [],
            # followed_by: temporal chain (prev_action_idx → next_action_idx).
            # Без causal claim — просто «за этим пришло то». Для policy-planning.
            "followed_by": [],
        },
        "meta": {
            "topic": "",
            "hub_nodes": set(),
            "mode": "horizon",
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

def _graph_generate(messages: list[dict], max_tokens: int = 60, temp: float = 0.9, top_k: int = 40, seed: int = -1, horizon_params: dict = None) -> tuple[str, dict]:
    """Generate text from chat messages via OpenAI-compatible API backend.
    If horizon_params provided, uses dynamic temperature/top_k from CognitiveState.
    Returns (text, entropy_info)."""
    from .api_backend import api_chat_completion

    # Horizon overrides fixed params
    if horizon_params:
        temp = horizon_params.get("temperature", temp)
        top_k = horizon_params.get("top_k", top_k)

    try:
        text, avg_ent, unc_pct, token_ents_raw, token_texts = api_chat_completion(
            messages, max_tokens=max_tokens, temperature=temp, top_k=top_k,
        )
    except (KeyError, IndexError, TypeError) as e:
        log.error(f"[_graph_generate] Failed to parse API response: {e}")
        return "", {"avg": 0.0, "unc": 0.0, "tokens": []}
    text = _clean_thinking(text)
    token_ents = []
    for i, e in enumerate(token_ents_raw):
        tok = token_texts[i] if i < len(token_texts) else ""
        token_ents.append({"token": tok, "ent": round(float(e), 3)})
    return text, {"avg": round(float(avg_ent), 3), "unc": round(float(unc_pct), 3), "tokens": token_ents}


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


def parse_lines_clean(raw: str, min_len: int = 8, max_n: int = 5,
                       topic: str = "") -> list[str]:
    """Универсальный парсер multi-line LLM ответа → список чистых идей.

    Шаги: split по \\n → strip маркеры (- • * цифры.) → фильтр по min_len →
    `_clean_thought` на каждой → обрезать max_n. Заменяет 7+ дублирующих
    мест в execute_deep / _deepen_round / execute_via_zones / cognitive_loop.
    """
    lines = [l.strip(" -•*1234567890.") for l in (raw or "").split("\n") if l.strip()]
    cleaned = [_clean_thought(l, topic) for l in lines if len(l) > min_len]
    # Фильтр опустошённых после clean
    cleaned = [c for c in cleaned if c]
    return cleaned[:max_n]


def parse_smartdc_triple(raw: str) -> tuple[str, str, str]:
    """Парсит FOR/AGAINST/SYNTHESIS (а также ЗА/ПРОТИВ/СИНТЕЗ) из LLM ответа.

    Возвращает (thesis, antithesis, synthesis). Пустые строки если секция
    не найдена. Заменяет 3-4 дублирующих места в execute_deep / _deepen_round /
    cognitive_loop._check_dmn_converge.
    """
    thesis = antithesis = synthesis = ""
    for line in (raw or "").split("\n"):
        L = line.strip()
        if not L:
            continue
        up = L.upper()
        if up.startswith("FOR:") or up.startswith("ЗА:"):
            thesis = L.split(":", 1)[1].strip()
        elif up.startswith("AGAINST:") or up.startswith("ПРОТИВ:"):
            antithesis = L.split(":", 1)[1].strip()
        elif up.startswith("SYNTHESIS:") or up.startswith("СИНТЕЗ:"):
            synthesis = L.split(":", 1)[1].strip()
    return thesis, antithesis, synthesis


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
    """Compute and cache embeddings. v8b: mirror into node["embedding"] too.

    Camera mode (v8c): if CognitiveState.llm_disabled is True and an API call
    would be needed, fall back to None (system keeps thinking on existing
    embeddings only, no new fetches).
    """
    from .api_backend import api_get_embedding
    try:
        from .horizon import get_global_state
        llm_off = get_global_state().llm_disabled
    except Exception:
        llm_off = False

    cache = _graph.setdefault("embeddings", [])
    nodes = _graph.get("nodes", [])
    while len(cache) < len(texts):
        idx = len(cache)
        emb = None if llm_off else api_get_embedding(texts[idx])
        cache.append(emb if emb else None)
    while len(cache) > len(texts):
        cache.pop()

    # v8b: mirror cache into node.embedding so downstream distinct() reads
    # directly off the node, no parallel-list juggling.
    for i, n in enumerate(nodes):
        if i < len(cache) and cache[i] and not n.get("embedding"):
            n["embedding"] = cache[i]


def _jaccard(i: int, j: int, texts: list[str]) -> float:
    """Jaccard similarity on word sets (simple fallback)."""
    toks_i = set(texts[i].lower().split())
    toks_j = set(texts[j].lower().split())
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

    # Remap Action Memory edges (caused_by, followed_by) — те же ordered pairs
    for key in ("caused_by", "followed_by"):
        old = edges_dict.get(key, [])
        new = []
        for pair in old:
            a, b = pair
            if a in removed or b in removed:
                continue
            new.append([remap(a), remap(b)])
        edges_dict[key] = new

    # Also remap action.outcome_idx and outcome.linked_action_idx in nodes
    nodes = _graph.get("nodes", [])
    for n in nodes:
        if n.get("type") == "action" and n.get("outcome_idx") is not None:
            oi = n["outcome_idx"]
            if oi in removed:
                n["outcome_idx"] = None
                n["closed"] = False  # outcome исчез — action становится снова open
            elif oi > 0:
                n["outcome_idx"] = remap(oi)
        elif n.get("type") == "outcome" and n.get("linked_action_idx") is not None:
            ai = n["linked_action_idx"]
            if ai in removed:
                n["linked_action_idx"] = None
            elif ai > 0:
                n["linked_action_idx"] = remap(ai)

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
