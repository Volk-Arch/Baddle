"""Baddle Assistant — chat-first interface.

One endpoint turns user messages into graph operations.
User sees conversation. Baddle runs the graph underneath.
"""
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict

from flask import Blueprint, request, jsonify

log = logging.getLogger(__name__)

from .modes import get_mode
from .hrv_manager import get_manager as get_hrv_manager
from .watchdog import get_watchdog
from .assistant_exec import execute as execute_mode

assistant_bp = Blueprint("assistant", __name__)


# ── Energy / decisions store ────────────────────────────────────────────

_STATE_FILE = Path(__file__).parent.parent / "user_state.json"


def _load_state() -> dict:
    if _STATE_FILE.exists():
        try:
            return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "decisions_today": 0,
        "last_reset_date": None,
        "last_interaction": None,
        "total_decisions": 0,
        "streaks": {},       # habit_name → consecutive_days
        "history": [],       # last 100 interactions (trimmed)
    }


def _save_state(state: dict):
    try:
        _STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"[assistant] state save error: {e}")


def _today_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _ensure_daily_reset(state: dict) -> dict:
    """Reset daily counters if date changed."""
    today = _today_date()
    if state.get("last_reset_date") != today:
        state["decisions_today"] = 0
        state["last_reset_date"] = today
    return state


def _compute_energy(state: dict, hrv_recovery: Optional[float] = None) -> dict:
    """Compute current energy level 0-100.

    Base: 100 decrementing by decisions_today * 6.
    HRV modulation: recovery affects daily max.
    """
    decisions = state.get("decisions_today", 0)
    base_max = 100.0

    # If HRV available and shows poor recovery, lower the ceiling
    if hrv_recovery is not None:
        base_max = 40 + 60 * hrv_recovery  # 40-100 range

    energy = max(0.0, base_max - decisions * 6.0)
    return {
        "energy": round(energy, 0),
        "max": round(base_max, 0),
        "decisions_today": decisions,
        "recovery": hrv_recovery,
    }


def _log_decision(state: dict, kind: str, meta: dict = None):
    """Record a decision/interaction in history."""
    state["decisions_today"] = state.get("decisions_today", 0) + 1
    state["total_decisions"] = state.get("total_decisions", 0) + 1
    state["last_interaction"] = time.time()

    entry = {
        "ts": time.time(),
        "kind": kind,
    }
    if meta:
        entry.update(meta)
    state.setdefault("history", []).append(entry)
    if len(state["history"]) > 200:
        state["history"] = state["history"][-200:]


# ── Shared context helper (state + HRV + energy) ──────────────────────

def _get_context(reset_daily: bool = True) -> Dict:
    """Load user state + HRV snapshot + computed energy.

    Returns:
      {
        "state": dict (loaded user_state.json, daily-reset applied),
        "hrv": dict | None (baddle_state or None if HRV off),
        "energy": dict (computed from state + hrv.energy_recovery),
      }
    """
    state = _load_state()
    if reset_daily:
        state = _ensure_daily_reset(state)

    hrv_mgr = get_hrv_manager()
    hrv_state = hrv_mgr.get_baddle_state() if hrv_mgr.is_running else None
    recovery = hrv_state.get("energy_recovery") if hrv_state else None
    energy = _compute_energy(state, recovery)

    return {"state": state, "hrv": hrv_state, "energy": energy}


# ── Mode → user-facing response templates ──────────────────────────────

def _response_for_mode(mode_id: str, message: str, lang: str = "ru") -> Dict:
    """Immediate confirmation — data-driven from modes.py."""
    mode = get_mode(mode_id)
    name = mode.get("name", mode_id) if lang == "ru" else mode.get("name_en", mode_id)
    intro_key = "intro" if lang == "ru" else "intro_en"
    intro = mode.get(intro_key) or mode.get("intro") or "..."
    return {"mode": mode_id, "mode_name": name, "intro": intro}


# ── Intent & mode classification (single LLM call, keyword fallback) ─

_MODE_DESCRIPTIONS_RU = """
- dispute: дебаты, за/против, диалектика противоречий
- tournament: сравнение вариантов, выбор одного лучшего
- bayes: вероятностная гипотеза, проверка наблюдениями
- fan: мозговой штурм, много идей без фильтра
- rhythm: ежедневная привычка, регулярное действие
- horizon: глубокое исследование темы
- vector: одна конкретная задача с финалом
- scout: блуждание без цели, серендипити
- builder: многокомпонентная сборка, все части нужны
- pipeline: последовательные шаги в строгом порядке
- cascade: приоритеты, срочное первым
- scales: баланс между несколькими областями
- race: любой подходящий вариант из нескольких
- free: ручной режим, не ясно что делать
"""

def classify_intent_llm(message: str, context: str = "", state_hint: str = "",
                        lang: str = "ru") -> dict:
    """Один LLM-вызов: mode + intent + confidence. Заменяет detect_mode+detect_intent.

    LLM получает:
      - текущее сообщение
      - краткий контекст (последние turns, опционально)
      - state_hint (текущее состояние CognitiveState — если система устала,
        юзер давно молчит, sync_error растёт — это влияет на интерпретацию)

    Возвращает:
      {
        "mode": "tournament" | ... | "free",
        "intent": "direct" | "complex_goal" | "ambiguous" | "simple_note",
        "confidence": 0.0-1.0,
        "source": "llm" | "fallback"
      }
    """
    from .graph_logic import _graph_generate

    if not message.strip():
        return {"mode": "free", "intent": "direct", "confidence": 1.0, "source": "empty"}

    # Short path for crystal-clear ambiguous markers (saves LLM call)
    lower = message.lower().strip()
    if len(lower) < 4 or lower in ("?", "что?", "как?", "почему?", "помоги"):
        return {"mode": "free", "intent": "ambiguous", "confidence": 0.95, "source": "fast"}

    # Build LLM prompt
    if lang == "ru":
        system = ("/no_think\nТы классификатор намерений. Получаешь сообщение пользователя "
                  "и возвращаешь СТРОГО одну строку в формате:\n"
                  "mode=<id> intent=<id> confidence=<0.0-1.0>\n\n"
                  "mode — один из:" + _MODE_DESCRIPTIONS_RU + "\n"
                  "intent — один из:\n"
                  "  direct: обычный прямой запрос\n"
                  "  complex_goal: сложная цель, нужна декомпозиция на подзадачи\n"
                  "  ambiguous: неясно что хочет юзер, нужно уточнить\n"
                  "  simple_note: короткая заметка, просто записать\n\n"
                  "Без объяснений. Только одна строка.")
    else:
        system = ("/no_think\nClassify user message. Return STRICTLY one line:\n"
                  "mode=<id> intent=<id> confidence=<0.0-1.0>\n\n"
                  "mode: dispute|tournament|bayes|fan|rhythm|horizon|vector|scout|builder|pipeline|cascade|scales|race|free\n"
                  "intent: direct|complex_goal|ambiguous|simple_note\n"
                  "No explanation. One line.")

    user = f"Сообщение: {message[:300]}"
    if context:
        user += f"\nКонтекст: {context[:200]}"
    if state_hint:
        user += f"\nСостояние системы: {state_hint}"

    try:
        result, _ = _graph_generate(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            max_tokens=80, temp=0.2, top_k=10,
        )
        parsed = _parse_classify_output(result)
        if parsed:
            parsed["source"] = "llm"
            return parsed
    except Exception as e:
        log.warning(f"[classify] LLM failed: {e}")

    # LLM недоступна или вернула мусор — safe default
    return {"mode": "free", "intent": "direct", "confidence": 0.3, "source": "default"}


def _parse_classify_output(text: str) -> Optional[dict]:
    """Parse 'mode=X intent=Y confidence=Z' line from LLM output."""
    valid_modes = {"dispute", "tournament", "bayes", "fan", "rhythm", "horizon",
                   "vector", "scout", "builder", "pipeline", "cascade", "scales",
                   "race", "free"}
    valid_intents = {"direct", "complex_goal", "ambiguous", "simple_note"}
    mode = None
    intent = None
    confidence = 0.5
    for part in text.replace("\n", " ").split():
        if "=" not in part:
            continue
        k, _, v = part.partition("=")
        k = k.strip().lower()
        v = v.strip().strip(",").lower()
        if k == "mode" and v in valid_modes:
            mode = v
        elif k == "intent" and v in valid_intents:
            intent = v
        elif k == "confidence":
            try:
                confidence = max(0.0, min(1.0, float(v)))
            except ValueError:
                pass
    if mode and intent:
        return {"mode": mode, "intent": intent, "confidence": confidence}
    return None




# ── Main /assist endpoint ──────────────────────────────────────────────

@assistant_bp.route("/assist", methods=["POST"])
def assist():
    """Single entry point for chat interface.

    Request:
      {"message": "what should I eat?", "lang": "ru"}

    Response:
      {
        "text": "...",
        "mode": "tournament",
        "mode_name": "Выбор",
        "energy": {...},
        "hrv": {...},
        "actions": [...],   # suggested next steps
        "graph_updated": bool
      }
    """
    d = request.get_json(force=True)
    message = d.get("message", "").strip()
    lang = d.get("lang", "ru")

    if not message:
        return jsonify({"error": "empty message"})

    # Inject NE spike — user engagement = Horizon takes budget from DMN (v5a/v5d)
    from .horizon import get_global_state
    cs = get_global_state()
    cs.inject_ne(0.4)

    ctx = _get_context()
    state, hrv_state, energy = ctx["state"], ctx["hrv"], ctx["energy"]

    # Build state hint for classifier (brief CognitiveState summary)
    neuro = cs.get_metrics().get("neurochem", {})
    state_hint = (f"state={cs.state} "
                  f"NE={neuro.get('norepinephrine', 0):.2f} "
                  f"DA={neuro.get('dopamine', 0):.2f} "
                  f"S={neuro.get('serotonin', 0):.2f} "
                  f"burnout={neuro.get('burnout', 0):.2f}")

    # Recent context from state_graph (last 3 user-initiated actions)
    context_parts = []
    try:
        from .state_graph import get_state_graph
        sg = get_state_graph()
        recent = [e for e in sg.tail(10) if e.get("user_initiated")][-3:]
        for e in recent:
            context_parts.append(e.get("reason", "")[:60])
    except Exception:
        pass
    context = " | ".join(context_parts)

    # ── ONE LLM call: mode + intent + confidence ──
    classification = classify_intent_llm(message, context=context, state_hint=state_hint, lang=lang)
    mode_id = classification.get("mode", "free")
    intent = classification.get("intent", "direct")
    confidence = classification.get("confidence", 0.5)
    response = _response_for_mode(mode_id, message, lang)

    # Check energy — warn if low
    warnings = []
    if energy["energy"] < 20:
        warnings.append({
            "type": "low_energy",
            "text": "Энергия низкая. Сложные решения лучше оставить на утро." if lang == "ru"
                    else "Energy low. Heavy decisions are better left for morning.",
        })
    if hrv_state and hrv_state.get("coherence") is not None and hrv_state["coherence"] < 0.3:
        warnings.append({
            "type": "low_coherence",
            "text": "Coherence падает — может стоит сделать паузу." if lang == "ru"
                    else "Coherence dropping — consider a break.",
        })

    # Ambiguous → задать clarifying question вместо полноценного execute
    if intent == "ambiguous" or confidence < 0.4:
        from .graph_logic import _graph_generate
        if lang == "ru":
            sys_prompt = ("/no_think\nПользователь написал короткое/неясное сообщение. "
                          "Задай ОДИН уточняющий вопрос (максимум 20 слов) чтобы понять что он хочет. "
                          "Без вступления. Один вопрос.")
            fallback_q = "Что именно ты хочешь — подумать, сравнить, решить?"
        else:
            sys_prompt = ("/no_think\nUser sent short/ambiguous message. "
                          "Ask ONE clarifying question (max 20 words). No preamble.")
            fallback_q = "What do you want — think, compare, decide?"
        try:
            q_text, _ = _graph_generate(
                [{"role": "system", "content": sys_prompt},
                 {"role": "user", "content": message}],
                max_tokens=60, temp=0.5, top_k=40,
            )
            clarify_q = (q_text or fallback_q).strip().split("\n")[0]
        except Exception:
            clarify_q = fallback_q
        _log_decision(state, kind="assist_clarify", meta={"mode": mode_id, "message": message[:200]})
        _save_state(state)
        return jsonify({
            "text": clarify_q,
            "intro": clarify_q,
            "mode": mode_id,
            "mode_name": "уточнение",
            "message_echo": message,
            "cards": [{"type": "clarify", "question": clarify_q, "prompt_user": True}],
            "steps": [f"Неопределённость (conf={confidence:.2f}) — спрашиваю" if lang == "ru"
                      else f"Ambiguity (conf={confidence:.2f}) — asking back"],
            "energy": energy, "hrv": hrv_state, "warnings": warnings,
            "awaiting_input": True, "graph_updated": False,
            "lang": lang, "intent": intent, "confidence": confidence,
            "classify_source": classification.get("source"),
        })

    # ── Actually execute the mode ──
    exec_result = execute_mode(mode_id, message, lang)
    response_text = exec_result.get("text") or response["intro"]
    cards = exec_result.get("cards", [])
    steps = exec_result.get("steps", [])

    # Complex goal → inline decompose-suggestion card (после основного ответа)
    if intent == "complex_goal":
        cards = list(cards)
        cards.append({
            "type": "decompose_suggestion",
            "message": message,
            "hint": ("Задача выглядит сложной. Разбить на подзадачи?" if lang == "ru"
                     else "Task looks complex. Split into subtasks?"),
            "cta": "Разбить" if lang == "ru" else "Split",
        })

    # Log this interaction
    _log_decision(state, kind="assist", meta={"mode": mode_id, "message": message[:200],
                                                "intent": intent, "confidence": confidence})
    _save_state(state)

    return jsonify({
        "text": response_text,
        "intro": response["intro"],
        "mode": mode_id,
        "mode_name": response["mode_name"],
        "message_echo": message,
        "cards": cards,
        "steps": steps,
        "energy": energy,
        "hrv": hrv_state,
        "warnings": warnings,
        "awaiting_input": exec_result.get("awaiting_input", False),
        "graph_updated": len(cards) > 0,
        "lang": lang,
        "intent": intent,
        "confidence": confidence,
        "classify_source": classification.get("source"),
        "error": exec_result.get("error"),
    })


# ── Status / energy ────────────────────────────────────────────────────

@assistant_bp.route("/assist/status", methods=["GET"])
def assist_status():
    """Current user state — energy, HRV, recent activity."""
    ctx = _get_context()
    state, hrv_state, energy = ctx["state"], ctx["hrv"], ctx["energy"]
    return jsonify({
        "energy": energy,
        "hrv": hrv_state,
        "total_decisions": state.get("total_decisions", 0),
        "streaks": state.get("streaks", {}),
        "last_interaction": state.get("last_interaction"),
    })


@assistant_bp.route("/assist/feedback", methods=["POST"])
def assist_feedback():
    """User feedback — converted to pseudo-d and fed into neurochem EMA.

    accepted → d=0.2 (low distance = system guessed right, dopamine EMA drifts down toward confirmation)
    rejected → d=0.8 (high distance = system was wrong, dopamine spike + freeze accumulator grows)
    ignored  → no update

    Body: { "feedback": "accepted" | "rejected" | "ignored" }
    """
    from .horizon import get_global_state
    d = request.get_json(force=True) or {}
    kind = d.get("feedback", "").strip()
    if kind not in ("accepted", "rejected", "ignored"):
        return jsonify({"error": "invalid feedback"})
    cs = get_global_state()
    # Feedback маппится в d: accepted (модель угадала) → низкое d,
    # rejected (промах) → высокое d, ignored — ничего
    d_map = {"accepted": 0.2, "rejected": 0.8, "ignored": None}
    d_val = d_map.get(kind)
    if d_val is not None:
        cs.update_neurochem(d=d_val)
    return jsonify({"ok": True, "neurochem": cs.get_metrics().get("neurochem", {})})


@assistant_bp.route("/assist/camera", methods=["POST"])
def assist_camera():
    """Toggle Camera mode (v8c) — sensory deprivation.

    Body: { "enabled": true/false }
    When enabled, llm_disabled=True: tick works only on existing embeddings,
    no new LLM calls. Useful for reflection + finding hidden patterns in
    what's already there.
    """
    from .horizon import get_global_state
    d = request.get_json(force=True) or {}
    enabled = bool(d.get("enabled", False))
    cs = get_global_state()
    cs.llm_disabled = enabled
    return jsonify({"ok": True, "camera": enabled})


@assistant_bp.route("/assist/state", methods=["GET"])
def assist_state():
    """Return full CognitiveState metrics (for UI panel, diagnostics)."""
    from .horizon import get_global_state
    return jsonify(get_global_state().get_metrics())


@assistant_bp.route("/graph/assist", methods=["POST"])
def graph_assist():
    """Dialogical loop (third control circuit).

    Given current graph state, LLM asks ONE clarifying question whose answer
    would most reduce uncertainty. Optionally takes an answer to a prior
    question and materializes it as the appropriate node type:
      - mode=bayes      → evidence node on prior hypothesis
      - goal+subgoals   → new subgoal under goal (AND-like filling)
      - otherwise       → seed hypothesis

    Closes the third loop: system asks → user answers → graph grows.

    Request:
      { "lang": "ru" }                          # fresh question
      { "lang": "ru", "answer": "...",          # materialize an answer
        "question": "...", "mode": "bayes" }
    """
    from .graph_logic import _graph, _graph_generate, _add_node
    d = request.get_json(force=True) or {}
    lang = d.get("lang", "ru")
    answer = (d.get("answer") or "").strip()
    question = (d.get("question") or "").strip()
    requested_mode = d.get("mode")

    nodes = _graph["nodes"]
    goal_nodes = [(i, n) for i, n in enumerate(nodes)
                  if n.get("type") == "goal" and n.get("depth", 0) >= 0]
    goal_idx, goal_node = goal_nodes[0] if goal_nodes else (None, None)
    mode_id = requested_mode or (goal_node.get("mode") if goal_node else None) or \
              _graph.get("meta", {}).get("mode", "horizon")

    # NE spike on any /graph/assist activity (dialogical loop is engagement too)
    from .horizon import get_global_state
    cs = get_global_state()
    cs.inject_ne(0.3)
    # Answer = модель угадала запрос → низкое d = подтверждение
    if answer:
        cs.update_neurochem(d=0.2)

    # ── Materialize path: user answered, add node of appropriate type ──
    if answer:
        directed = _graph["edges"]["directed"]
        manual_links = _graph["edges"]["manual_links"]

        if mode_id == "bayes" and goal_idx is not None:
            # Answer → evidence on the hypothesis (goal). Auto-classify support vs contradict.
            from .graph_logic import _auto_evidence_relation, _bayesian_update_distinct, _d_from_relation
            rel, strength = _auto_evidence_relation(goal_node["text"], answer)
            d_val = _d_from_relation(rel, strength)
            old_conf = goal_node["confidence"]
            goal_node["confidence"] = _bayesian_update_distinct(old_conf, d_val)
            new_idx = _add_node(answer, depth=goal_node.get("depth", 0) + 1,
                                topic=goal_node.get("topic", ""),
                                confidence=strength, node_type="evidence")
            nodes[new_idx]["evidence_relation"] = rel
            nodes[new_idx]["evidence_strength"] = strength
            nodes[new_idx]["evidence_target"] = goal_idx
            directed.append([goal_idx, new_idx])
            pair = [min(goal_idx, new_idx), max(goal_idx, new_idx)]
            if pair not in manual_links:
                manual_links.append(pair)
            return jsonify({
                "ok": True, "node_idx": new_idx, "kind": "evidence",
                "relation": rel, "strength": strength,
                "prior": old_conf, "posterior": goal_node["confidence"],
            })

        elif goal_node is not None and (goal_node.get("subgoals") or
                                         mode_id in ("builder", "pipeline", "cascade", "scales", "tournament", "race")):
            # Answer → new subgoal under the goal
            subgoals = goal_node.setdefault("subgoals", [])
            new_idx = _add_node(answer, depth=goal_node.get("depth", 0),
                                topic=goal_node.get("topic", ""),
                                node_type="hypothesis")
            subgoals.append(new_idx)
            directed.append([goal_idx, new_idx])
            pair = [min(goal_idx, new_idx), max(goal_idx, new_idx)]
            if pair not in manual_links:
                manual_links.append(pair)
            return jsonify({"ok": True, "node_idx": new_idx, "kind": "subgoal"})

        else:
            # Answer → seed hypothesis (loose context)
            new_idx = _add_node(answer, depth=0, topic="", node_type="hypothesis")
            if goal_idx is not None:
                directed.append([new_idx, goal_idx])
                pair = [min(goal_idx, new_idx), max(goal_idx, new_idx)]
                if pair not in manual_links:
                    manual_links.append(pair)
            return jsonify({"ok": True, "node_idx": new_idx, "kind": "seed"})

    # ── Question path: generate one clarifying question ──
    # Build graph snapshot context for LLM
    context_lines = []
    if goal_node:
        context_lines.append(f"Цель: {goal_node['text'][:100]}")
    hypotheses = [n for n in nodes if n.get("type") in ("hypothesis", "thought")
                  and n.get("depth", 0) >= 0][:5]
    if hypotheses:
        context_lines.append("Текущие гипотезы:")
        for h in hypotheses:
            context_lines.append(f"- {h['text'][:80]} (conf={h.get('confidence', 0.5):.0%})")
    unverified = [n for n in nodes if n.get("type") in ("hypothesis", "thought")
                  and n.get("confidence", 0.5) < 0.6][:3]

    if lang == "ru":
        system = ("/no_think\nТы задаёшь ОДИН короткий уточняющий вопрос, "
                  "ответ на который сильнее всего уменьшит неопределённость в графе. "
                  "Без вступления. Максимум 20 слов. Один вопрос.")
        fallback_q = "Что важнее всего уточнить прямо сейчас?"
    else:
        system = ("/no_think\nAsk ONE short clarifying question whose answer "
                  "would most reduce graph uncertainty. No preamble. Max 20 words.")
        fallback_q = "What's most important to clarify right now?"

    ctx_text = "\n".join(context_lines) if context_lines else (
        "Граф пуст. Задай вопрос чтобы начать." if lang == "ru"
        else "Graph is empty. Ask to start.")
    try:
        q_text, _ = _graph_generate(
            [{"role": "system", "content": system},
             {"role": "user", "content": ctx_text}],
            max_tokens=60, temp=0.5, top_k=40,
        )
        q = (q_text or fallback_q).strip().split("\n")[0].strip()
        if not q:
            q = fallback_q
    except Exception as e:
        log.warning(f"[graph_assist] question gen failed: {e}")
        q = fallback_q

    # Hint on what the answer will become (helps UI preview)
    if mode_id == "bayes":
        answer_kind = "evidence"
    elif goal_node and (goal_node.get("subgoals") or
                        mode_id in ("builder", "pipeline", "cascade", "scales", "tournament", "race")):
        answer_kind = "subgoal"
    else:
        answer_kind = "seed"

    return jsonify({
        "question": q,
        "mode": mode_id,
        "answer_kind": answer_kind,
        "goal_idx": goal_idx,
        "graph_size": len(nodes),
    })


@assistant_bp.route("/assist/decompose", methods=["POST"])
def assist_decompose():
    """Goal decomposition — LLM splits a complex goal into subgoals.

    Used when user message looks like a big task. Returns list of subgoals
    which the UI can confirm/edit before creating.
    """
    from .graph_logic import _graph_generate
    d = request.get_json(force=True)
    message = d.get("message", "")
    lang = d.get("lang", "ru")
    temp = float(d.get("temp", 0.5))
    top_k = int(d.get("top_k", 40))

    if not message:
        return jsonify({"error": "empty message"})

    if lang == "ru":
        system = ("/no_think\nРазбей задачу на 3-5 подзадач. Одна подзадача = одна строка. "
                  "Коротко, конкретно. Без нумерации, без вступления.")
    else:
        system = ("/no_think\nSplit this task into 3-5 subtasks. One subtask = one line. "
                  "Short, concrete. No numbering, no preamble.")

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": message},
    ]
    try:
        text, _ = _graph_generate(messages, max_tokens=250, temp=temp, top_k=top_k)
    except Exception as e:
        return jsonify({"error": str(e)})

    lines = [ln.strip(" -•*1234567890.") for ln in text.split("\n")]
    subgoals = [ln for ln in lines if len(ln) > 3][:5]

    return jsonify({
        "subgoals": subgoals,
        "raw": text,
    })


# ── Morning briefing ──────────────────────────────────────────────────

@assistant_bp.route("/assist/morning", methods=["POST"])
def assist_morning():
    """Generate a morning briefing based on HRV recovery + pending tasks."""
    lang = request.get_json(force=True).get("lang", "ru") if request.is_json else "ru"

    ctx = _get_context()
    state, hrv_state, energy = ctx["state"], ctx["hrv"] or {}, ctx["energy"]
    recovery = (hrv_state or {}).get("energy_recovery") if hrv_state else None

    # Compose greeting
    recovery_pct = round((recovery or 0.7) * 100)
    energy_val = energy["energy"]

    if lang == "ru":
        if recovery_pct >= 80:
            greeting = f"Доброе утро. Восстановление {recovery_pct}%. Отличный день для сложных задач."
        elif recovery_pct >= 60:
            greeting = f"Доброе утро. Восстановление {recovery_pct}%. Средний день — начни с важного."
        else:
            greeting = f"Доброе утро. Восстановление {recovery_pct}%. Береги энергию, лёгкие задачи первыми."
        greeting += f" Бюджет: {int(energy_val)}/100."
    else:
        if recovery_pct >= 80:
            greeting = f"Good morning. Recovery {recovery_pct}%. Great day for complex tasks."
        elif recovery_pct >= 60:
            greeting = f"Good morning. Recovery {recovery_pct}%. Medium day — start with priorities."
        else:
            greeting = f"Good morning. Recovery {recovery_pct}%. Save energy, light tasks first."
        greeting += f" Budget: {int(energy_val)}/100."

    _log_decision(state, kind="morning_briefing")
    _save_state(state)

    return jsonify({
        "text": greeting,
        "energy": energy,
        "hrv": hrv_state,
        "recovery_pct": recovery_pct,
    })


# ── Weekly review ─────────────────────────────────────────────────────

@assistant_bp.route("/assist/weekly", methods=["POST"])
def assist_weekly():
    """Generate weekly review from history."""
    state = _load_state()
    history = state.get("history", [])
    lang = request.get_json(force=True).get("lang", "ru") if request.is_json else "ru"

    # Filter last 7 days
    cutoff = time.time() - 7 * 86400
    recent = [h for h in history if h.get("ts", 0) > cutoff]

    # Count by mode
    mode_counts = {}
    for h in recent:
        m = h.get("mode") or h.get("kind", "?")
        mode_counts[m] = mode_counts.get(m, 0) + 1

    streaks = state.get("streaks", {})

    if lang == "ru":
        text = f"За неделю: {len(recent)} решений. "
        if mode_counts:
            top = sorted(mode_counts.items(), key=lambda x: -x[1])[:3]
            text += "Топ режимов: " + ", ".join(f"{k} ({v})" for k, v in top) + "."
        if streaks:
            text += " Streak: " + ", ".join(f"{k}={v}" for k, v in streaks.items()) + "."
    else:
        text = f"This week: {len(recent)} decisions. "
        if mode_counts:
            top = sorted(mode_counts.items(), key=lambda x: -x[1])[:3]
            text += "Top modes: " + ", ".join(f"{k} ({v})" for k, v in top) + "."
        if streaks:
            text += " Streaks: " + ", ".join(f"{k}={v}" for k, v in streaks.items()) + "."

    return jsonify({
        "text": text,
        "decisions_this_week": len(recent),
        "mode_counts": mode_counts,
        "streaks": streaks,
    })


# ── Proactive alerts (polled by UI) ────────────────────────────────────

@assistant_bp.route("/assist/alerts", methods=["GET"])
def assist_alerts():
    """Return pending proactive alerts. UI polls this periodically."""
    ctx = _get_context()
    state, hrv_state, energy = ctx["state"], ctx["hrv"] or {}, ctx["energy"]
    alerts = []

    # Low energy + more decisions needed today
    if energy["energy"] < 20 and state.get("decisions_today", 0) > 5:
        alerts.append({
            "type": "energy_critical",
            "severity": "warning",
            "text": "Энергия <20. Отложи сложные решения до утра.",
            "text_en": "Energy <20. Postpone heavy decisions until morning.",
        })

    # Coherence dropping
    if hrv_state:
        coh = hrv_state.get("coherence")
        if coh is not None and coh < 0.4:
            alerts.append({
                "type": "low_coherence",
                "severity": "info",
                "text": f"Coherence {coh:.2f}. Минутку подыши.",
                "text_en": f"Coherence {coh:.2f}. Take a breath.",
            })

    # Background watchdog alerts (Scout, DMN)
    wd = get_watchdog()
    watchdog_alerts = wd.get_alerts(clear=True)
    alerts.extend(watchdog_alerts)

    return jsonify({
        "alerts": alerts,
        "count": len(alerts),
        "energy": energy,
        "hrv": hrv_state,
        "watchdog": wd.get_status(),
    })


# ── Watchdog control ─────────────────────────────────────────────────

@assistant_bp.route("/watchdog/start", methods=["POST"])
def watchdog_start():
    wd = get_watchdog()
    wd.start()
    return jsonify({"ok": True, "status": wd.get_status()})


@assistant_bp.route("/watchdog/stop", methods=["POST"])
def watchdog_stop():
    wd = get_watchdog()
    wd.stop()
    return jsonify({"ok": True})


@assistant_bp.route("/watchdog/status", methods=["GET"])
def watchdog_status():
    return jsonify(get_watchdog().get_status())
