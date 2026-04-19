"""NAND emergent tick — logic emerges from distinct() zones, not primitives.

v8 architecture: instead of switch(primitive) → hardcoded logic,
compute distinct() between pairs, let zones decide actions:

  d < tau_in             → CONFIRM  → merge similar (zone AND)
  tau_in < d < tau_out   → EXPLORE  → elaborate / pump (zone XOR)
  d > tau_out            → CONFLICT → doubt / branch / compare (zone NOR)

No primitive switch. Same algorithm for all 14 modes — only thresholds differ.
Stop condition: distinct(goal, best_verified) < τ_in, or subgoal-cluster
convergence decided emergently by avg_d between subgoals.

This is the single tick engine — exported as both `tick` and `tick_emergent`.
"""
import logging
from typing import Optional

from .thinking import classify_nodes, _find_similar_group, _pick_target, _pick_distant_pair, _tick_force_collapse
from .main import distinct, distinct_decision

log = logging.getLogger(__name__)


def tick_emergent(nodes, edges, graph, threshold=0.91, stable_threshold=0.8,
                  force_collapse=False, max_meta=2, min_hyp=5, **kwargs):
    """Emergent tick — action determined by distinct() zones, not primitives.

    Uses Horizon thresholds tau_in/tau_out to classify pair distances into
    CONFIRM/EXPLORE/CONFLICT zones, and routes to collapse/pump/smartdc/compare
    accordingly.
    """
    if not nodes:
        return {"action": "none", "reason": "Graph is empty.", "phase": "none"}

    cl = classify_nodes(nodes, edges, graph, stable_threshold)
    if not cl["active_nodes"]:
        return {"action": "none", "reason": "No active nodes.", "phase": "none"}

    goal_idx = cl["goal_idx"]
    goal_text = cl["goal_text"]
    goal_node = nodes[goal_idx] if goal_idx is not None else None

    # Read mode from goal for Horizon preset (thresholds still matter)
    mode_id = "horizon"
    if goal_node is not None:
        mode_id = goal_node.get("mode", "horizon")

    # Load/create Horizon
    from .horizon import CognitiveState, create_horizon
    horizon_data = graph.get("_horizon")
    if horizon_data:
        horizon = CognitiveState.from_dict(horizon_data)
    else:
        horizon = create_horizon(mode_id)

    # Feedback from previous step
    last_feedback = graph.pop("_horizon_feedback", None)
    if last_feedback:
        horizon.update(
            surprise=last_feedback.get("surprise"),
            gradient=last_feedback.get("gradient"),
            novelty=last_feedback.get("novelty"),
            phase=last_feedback.get("phase"),
        )

    horizon_params = horizon.to_llm_params()
    tau_in = horizon.tau_in
    tau_out = horizon.tau_out
    camera_mode = bool(getattr(horizon, "llm_disabled", False))
    log.info(f"[tick-nand] state={horizon.state} p={horizon.precision:.2f} γ={horizon.gamma:.2f} "
             f"τ_in={tau_in:.2f} τ_out={tau_out:.2f} camera={camera_mode}")

    hypotheses = cl["hypotheses"]
    bare = cl["bare"]
    unverified = cl["unverified"]
    verified = cl["verified"]

    # ── Subgoal filter ── if goal declares subgoals, scope everything to that cluster
    subgoals = goal_node.get("subgoals", []) if goal_node else []
    if subgoals:
        sub_set = set(subgoals)
        hypotheses = [(i, n) for i, n in hypotheses if i in sub_set]
        unverified = [(i, n) for i, n in unverified if i in sub_set]
        verified = [(i, n) for i, n in verified if i in sub_set]
        bare = [(i, n) for i, n in bare if i in sub_set]
        cl = {**cl, "hypotheses": hypotheses, "bare": bare,
              "unverified": unverified, "verified": verified}

    if force_collapse:
        return _tick_force_collapse(cl["active_nodes"], stable_threshold)

    # ── Distinct-matrix: compute once, reuse for neurochem feed + routing ──
    import numpy as np
    embeddings = graph.get("embeddings", [])
    hyp_indices = [i for i, _ in hypotheses]

    confirm_pairs = []    # d < tau_in → merge candidates
    explore_pairs = []    # tau_in < d < tau_out → pump/elaborate
    conflict_pairs = []   # d > tau_out → doubt candidates
    all_ds = []

    for ii in range(len(hyp_indices)):
        for jj in range(ii + 1, len(hyp_indices)):
            i = hyp_indices[ii]
            j = hyp_indices[jj]
            if i >= len(embeddings) or j >= len(embeddings):
                continue
            emb_a = embeddings[i]
            emb_b = embeddings[j]
            if emb_a is None or emb_b is None:
                continue
            emb_a = np.array(emb_a, dtype=np.float32)
            emb_b = np.array(emb_b, dtype=np.float32)
            if emb_a.size == 0 or emb_b.size == 0:
                continue
            d_val = distinct(emb_a, emb_b)
            all_ds.append(d_val)
            decision = distinct_decision(d_val, tau_in, tau_out)
            if decision == "CONFIRM":
                confirm_pairs.append((i, j, d_val))
            elif decision == "EXPLORE":
                explore_pairs.append((i, j, d_val))
            else:  # CONFLICT
                conflict_pairs.append((i, j, d_val))

    # ── Feed neurochem from tick signals (архитектурный контур замкнут) ──
    # d     → dopamine EMA  (новизна: средняя дистанция между идеями)
    # weights → norepinephrine EMA (энтропия распределения confidences)
    # Обновляем и глобальную нейрохимию (singleton per-person), и локальный
    # horizon который потом сохранится в graph["_horizon"] — чтобы get_metrics()
    # в emit отразил свежие значения для state_graph.
    try:
        from .horizon import get_global_state
        mean_d = sum(all_ds) / len(all_ds) if all_ds else None
        confidences = [n.get("confidence", 0.5) for _, n in hypotheses]
        signals = dict(d=mean_d, weights=confidences if confidences else None)
        get_global_state().update_neurochem(**signals)
        horizon.update_neurochem(**signals)
    except Exception as e:
        log.debug(f"[tick-nand] neurochem feed failed: {e}")

    # ── Stuck detection: если одна и та же action повторяется без прогресса,
    # значит executor не выполняет (bug, бэкенд-ошибка, не те args). Policy
    # не должна зацикливаться — переключаемся на alternative path после
    # STUCK_THRESHOLD одинаковых эмиссий подряд.
    STUCK_THRESHOLD = 3
    ALT_PATHS = {
        # Если X застрял — попробуй Y
        "collapse":     "elaborate",    # ноды не сливаются → углубить одну
        "think_toward": "elaborate",    # не можем думать → углубить существующую
        "elaborate":    "think_toward", # не можем углубить → сгенерить свежих
        "pump":         "collapse",     # мост не находится → попробовать слить
        "compare":      "elaborate",
        "smartdc":      "elaborate",
    }
    # History храним в graph-dict (persist с workspace.json). Формат:
    # [action, action, action] — последние N эмиссий.
    def _check_stuck(next_action: str):
        """Вернуть (stuck_count, alternative_action_or_None)."""
        hist = graph.get("_tick_action_hist") or []
        # Нужно N+1 элементов чтобы проверить что all N last были same.
        if len(hist) >= STUCK_THRESHOLD and all(a == next_action for a in hist[-STUCK_THRESHOLD:]):
            alt = ALT_PATHS.get(next_action)
            return (len(hist), alt)
        return (0, None)

    def _push_hist(action: str):
        hist = graph.setdefault("_tick_action_hist", [])
        hist.append(action)
        # Keep last 10
        if len(hist) > 10:
            del hist[:-10]

    def _emit(action_dict):
        # Stuck detection BEFORE adding metadata
        act = action_dict.get("action", "unknown")
        stuck_n, alt = _check_stuck(act)
        if alt and stuck_n >= STUCK_THRESHOLD:
            # Переключаемся на alternative action и помечаем reason
            orig = act
            action_dict["action"] = alt
            action_dict["reason"] = (
                f"STUCK[{orig}×{stuck_n}] → fallback to {alt} · "
                + (action_dict.get("reason") or "")
            )[:200]
            # Target мог быть bind'нут для orig — для alt подбираем сами
            if alt == "elaborate" and action_dict.get("target") is None:
                # Берём самую низкую confidence-ноду
                if hypotheses:
                    lo = min(hypotheses, key=lambda i: nodes[i].get("confidence", 0.5))
                    action_dict["target"] = lo
            if alt == "think_toward":
                action_dict["target"] = goal_idx or 0
            if alt == "collapse" and not isinstance(action_dict.get("target"), list):
                # Brute collapse: берём 2 ближайшие hypothesis'ы
                if len(hypotheses) >= 2:
                    action_dict["target"] = hypotheses[:2]
            log.info(f"[tick-nand] STUCK on {orig}×{stuck_n}, falling back to {alt}")
        # Особый случай: после N stuck-fallback'ов тоже — эмитим STABLE
        # чтобы остановить loop наверху.
        if stuck_n >= STUCK_THRESHOLD * 2:
            action_dict["action"] = "stable"
            action_dict["reason"] = f"STUCK: tried alternatives, giving up after {stuck_n} repeats"
        _push_hist(action_dict["action"])

        action_dict["horizon_params"] = horizon_params
        action_dict["horizon_metrics"] = horizon.get_metrics()
        action_dict["tick_engine"] = "nand"
        graph["_horizon"] = horizon.to_dict()

        # ── State graph append (v5e) ──
        # Record every tick emission as a state_node. Non-blocking, best-effort.
        try:
            from .state_graph import get_state_graph
            sg = get_state_graph()
            target = action_dict.get("target")
            if isinstance(target, list):
                content_touched = list(target)
            elif isinstance(target, int):
                content_touched = [target]
            else:
                content_touched = []
            sg.append(
                action=action_dict.get("action", "unknown"),
                phase=action_dict.get("phase", ""),
                user_initiated=bool(kwargs.get("user_initiated", False)),
                content_touched=content_touched,
                state_snapshot=horizon.get_metrics(),
                reason=action_dict.get("reason", ""),
                state_origin=getattr(horizon, "state_origin_hint", "1_rest"),
            )
        except Exception as e:
            log.debug(f"[tick-nand] state_graph append failed: {e}")

        return action_dict

    # ── STOP CHECK: universal should_stop via distinct zones ──
    if goal_node is not None:
        from .modes import should_stop
        stop = should_stop(cl, graph, horizon, goal_node=goal_node)
        if stop["resolved"]:
            log.info(f"[tick-nand] GOAL REACHED: {stop['reason']}")
            # Goal resolved → взрослеем (maturity drift). Global state singleton —
            # драйфт per-person, не per-graph.
            try:
                from .horizon import get_global_state
                get_global_state().note_verified()
            except Exception as e:
                log.debug(f"[tick-nand] maturity note on stop failed: {e}")

            # Persistent goal lifecycle: archive snapshot + complete_goal().
            # Hook срабатывает один раз на goal — маркируем через _goal_completed
            # чтобы повторный tick на том же состоянии не дублировал архив.
            try:
                if not goal_node.get("_goal_completed"):
                    from .goals_store import complete_goal
                    from .solved_archive import archive_solved
                    gid = goal_node.get("goal_id")
                    if gid:
                        snapshot_ref = archive_solved(
                            goal_id=gid,
                            goal_text=goal_node.get("text", ""),
                            workspace=graph.get("meta", {}).get("workspace", "main"),
                            reason=stop["reason"],
                        )
                        complete_goal(gid, reason=stop["reason"],
                                      snapshot_ref=snapshot_ref)
                        goal_node["_goal_completed"] = True
                        goal_node["_snapshot_ref"] = snapshot_ref
            except Exception as e:
                log.debug(f"[tick-nand] goal archive failed: {e}")

            return _emit({
                "action": "stable", "phase": "synthesize",
                "reason": f"GOAL REACHED: {stop['reason']}",
            })

    # ── ASK CHECK: high uncertainty + low norepinephrine → pause for user clarification ──
    # Conditions: enough nodes exist, sync_error growing, not a FREEZE state
    try:
        from .horizon import PROTECTIVE_FREEZE
        ne_low = horizon.neuro.norepinephrine < 0.35
        high_sync_err = getattr(horizon, "sync_error", 0.0) > 0.6
        many_uncertain = len(unverified) >= 3 and len(verified) == 0
        not_frozen = horizon.state != PROTECTIVE_FREEZE
        ask_counter = graph.get("_ask_count", 0)
        if not_frozen and ask_counter < 1 and (high_sync_err or (ne_low and many_uncertain)):
            graph["_ask_count"] = ask_counter + 1
            return _emit({
                "action": "ask",
                "target": goal_idx or 0,
                "phase": "dialogue",
                "reason": "EMERGENT[ask]: uncertainty high, system needs user input",
                "text": goal_text,
            })
    except Exception as e:
        log.debug(f"[tick-nand] ask check failed: {e}")

    # ── META-TICK: читаем хвост state_graph, детектим паттерны ────────────
    # Если сами застряли / юзер отказывается / RPE стабильно негативный —
    # эмитим ask / compare или толкаем policy weights. Второй порядок поверх
    # мгновенного решения выше.
    try:
        from .state_graph import get_state_graph
        from .meta_tick import analyze_tail, apply_policy_nudge
        from .horizon import INTEGRATION, PROTECTIVE_FREEZE
        sg = get_state_graph()
        tail = sg.tail(20)
        # Markov transitions over larger window — для markov_anomaly детекции
        try:
            transitions = sg.action_transitions(tail_n=200)
        except Exception:
            transitions = None
        meta = analyze_tail(tail, transitions=transitions)
        recommend = meta.get("recommend")
        not_frozen = horizon.state != PROTECTIVE_FREEZE

        if recommend == "ask" and not_frozen and graph.get("_ask_count", 0) < 1:
            graph["_ask_count"] = graph.get("_ask_count", 0) + 1
            return _emit({
                "action": "ask",
                "target": goal_idx or 0,
                "phase": "dialogue",
                "reason": f"META[{meta['pattern']}]: {meta.get('detail', '')}",
                "text": goal_text,
            })
        if recommend == "compare" and len(verified) >= 2:
            target_ids = [v[0] for v in verified[:3]]
            return _emit({
                "action": "compare",
                "target": target_ids,
                "phase": "synthesize",
                "reason": f"META[{meta['pattern']}]: {meta.get('detail', '')}",
                "text": ", ".join(nodes[i]["text"][:30] for i in target_ids),
            })
        if recommend == "stabilize" and not_frozen:
            horizon.state = INTEGRATION
            log.info(f"[meta_tick] forcing INTEGRATION: {meta.get('detail')}")
        # Lightweight policy nudge — даже без action: изменит select_phase на следующем тике
        if meta.get("policy_nudge"):
            apply_policy_nudge(horizon, meta["policy_nudge"])
    except Exception as e:
        log.debug(f"[tick-nand] meta-tick failed: {e}")

    # ── 1. Not enough nodes? GENERATE (skipped in Camera mode) ──
    generated = graph.get("_generated", False)
    need_generate = not generated and len(hypotheses) < min_hyp
    if need_generate and not camera_mode:
        return _emit({
            "action": "think_toward",
            "target": goal_idx or 0,
            "phase": "generate",
            "reason": f"EMERGENT: {len(hypotheses)}/{min_hyp} nodes. Need mass.",
            "text": goal_text,
        })

    if len(hypotheses) >= min_hyp:
        graph["_generated"] = True

    # ── 2. Emergent routing by zone density (pairs precomputed above) ──

    # CONFIRM zone dense → MERGE cluster
    if confirm_pairs:
        # Group into clusters: greedy
        group = [confirm_pairs[0][0], confirm_pairs[0][1]]
        seen = set(group)
        for i, j, d in confirm_pairs[1:]:
            if i in seen or j in seen:
                if i not in seen:
                    group.append(i); seen.add(i)
                if j not in seen:
                    group.append(j); seen.add(j)
            if len(group) >= 4:
                break
        if len(group) >= 2:
            return _emit({
                "action": "collapse",
                "target": group,
                "phase": "merge",
                "reason": f"EMERGENT[CONFIRM zone]: {len(confirm_pairs)} agreeing pairs → merge",
                "text": ", ".join(nodes[g]["text"][:25] for g in group[:3]) + "...",
            })

    # Bare nodes → ELABORATE (skipped in Camera mode — elaborate needs LLM)
    if bare and not camera_mode:
        target = _pick_target(bare, goal_idx, edges)
        if target:
            return _emit({
                "action": "elaborate",
                "target": target[0],
                "phase": "elaborate",
                "reason": f"EMERGENT: #{target[0]} bare — need evidence before comparison",
                "text": target[1]["text"][:80],
            })

    # CONFLICT zone → DOUBT (skipped in Camera mode — smartdc needs LLM)
    if conflict_pairs and not camera_mode:
        # Pick a node from conflict that is also unverified
        unverified_ids = {i for i, _ in unverified}
        for i, j, d in conflict_pairs:
            if i in unverified_ids:
                target_idx = i
                break
            if j in unverified_ids:
                target_idx = j
                break
        else:
            target_idx = conflict_pairs[0][0]
        n = nodes[target_idx]
        return _emit({
            "action": "smartdc",
            "target": target_idx,
            "phase": "doubt",
            "reason": f"EMERGENT[CONFLICT zone]: d>{tau_out:.2f} ({len(conflict_pairs)} pairs) → doubt",
            "text": n["text"][:80],
        })

    # EXPLORE zone dense → PUMP between most distant pair
    if explore_pairs and len(hypotheses) >= 4:
        pump_count = graph.get("_pump_count", 0)
        if pump_count < 3:
            # Pick pair closest to tau_out (most distant within EXPLORE zone)
            explore_pairs.sort(key=lambda p: -p[2])
            i, j, d = explore_pairs[0]
            graph["_pump_count"] = pump_count + 1
            return _emit({
                "action": "pump",
                "target": [i, j],
                "phase": "generate",
                "reason": f"EMERGENT[EXPLORE zone]: d={d:.2f} → find hidden axis",
                "text": f"{nodes[i]['text'][:30]} ↔ {nodes[j]['text'][:30]}",
            })

    # ── 4. Unverified remaining → DOUBT them ──
    doubt_candidates = [u for u in unverified if u[0] not in {i for i, _ in bare}]
    if doubt_candidates:
        target = _pick_target(doubt_candidates, goal_idx, edges)
        if target:
            return _emit({
                "action": "smartdc",
                "target": target[0],
                "phase": "doubt",
                "reason": f"EMERGENT: #{target[0]} unverified — standard doubt",
                "text": target[1]["text"][:80],
            })

    # ── 5. Multiple verified + CONFLICT-zone → COMPARE (XOR-like, emergent) ──
    if len(verified) >= 2:
        # Do verified nodes conflict? If so, external judge needed to pick one
        verified_conflict = [
            (i, j, d) for i, j, d in conflict_pairs
            if any(v[0] == i for v in verified) and any(v[0] == j for v in verified)
        ]
        if verified_conflict and not unverified:
            target_ids = [v[0] for v in verified]
            return _emit({
                "action": "compare",
                "target": target_ids,
                "phase": "synthesize",
                "reason": f"EMERGENT[CONFLICT+verified]: {len(verified)} verified in conflict — compare",
                "text": ", ".join(nodes[i]["text"][:30] for i in target_ids[:3]),
            })

    # ── 6. META ──
    meta_count = graph.get("_meta_count", 0)
    can_meta = meta_count < max_meta and len(verified) >= 3
    if can_meta:
        graph["_meta_count"] = meta_count + 1
        return _emit({
            "action": "think_toward",
            "target": goal_idx or 0,
            "phase": "generate",
            "reason": f"EMERGENT META: {len(verified)} verified. Search for gaps.",
            "text": goal_text,
        })

    # ── 7. Scout: PUMP between most distant pair (DMN mode) ──
    if mode_id == "scout" and len(hypotheses) >= 4:
        pump_count = graph.get("_pump_count", 0)
        if pump_count < 3:
            pair = _pick_distant_pair(hypotheses, edges)
            if pair:
                graph["_pump_count"] = pump_count + 1
                return _emit({
                    "action": "pump", "target": list(pair), "phase": "generate",
                    "reason": f"EMERGENT[SCOUT]: pump #{pair[0]}↔#{pair[1]}",
                    "text": f"{nodes[pair[0]]['text'][:30]} ↔ {nodes[pair[1]]['text'][:30]}",
                })

    # ── 8. SYNTHESIZE ──
    avg = sum(n.get("confidence", 0.5) for _, n in cl["active_nodes"]) / max(len(cl["active_nodes"]), 1)
    return _emit({
        "action": "stable",
        "phase": "synthesize",
        "reason": f"EMERGENT SYNTHESIZE: {len(hypotheses)} ideas, {len(verified)} verified, avg {avg:.0%}",
    })


# Alias — NAND emergent is the single tick engine
tick = tick_emergent
