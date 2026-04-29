"""Goals routes — /goals/* endpoints (W14.6b1 extract).

Manual goals + recurring + constraints + solved archive. UI's all
goal-related interactions через эти routes.
"""
import logging

from flask import request, jsonify

from . import assistant_bp

log = logging.getLogger(__name__)


# ── Goals store endpoints ──────────────────────────────────────────────

@assistant_bp.route("/goals", methods=["GET"])
def goals_list():
    """Query: ?status=open|done|abandoned &category=Y &limit=N"""
    from ...goals_store import list_goals
    status = request.args.get("status")
    cat = request.args.get("category")
    try:
        limit = int(request.args.get("limit", 100))
    except ValueError:
        limit = 100
    return jsonify({"goals": list_goals(status=status,
                                        category=cat, limit=limit)})


@assistant_bp.route("/goals/stats", methods=["GET"])
def goals_stats():
    from ...goals_store import goal_stats
    return jsonify(goal_stats())


from ..state import _push_event_to_chat  # used by goals_add (mode 'Новая цель')


@assistant_bp.route("/goals/add", methods=["POST"])
def goals_add():
    """Manual add (обычно создаётся автоматом из /graph/add node_type=goal).

    Body: {text, mode, priority, deadline, category,
           kind?, schedule?, polarity?}

    kind: "oneshot" (default) | "recurring" | "constraint"
    schedule: {times_per_day, days?, time_windows?} — для recurring
    polarity: "avoid" | "prefer" — для constraint
    """
    from ...goals_store import add_goal
    d = request.get_json(force=True) or {}
    kind = d.get("kind", "oneshot")
    text = d.get("text", "")
    gid = add_goal(
        text=text,
        mode=d.get("mode", "horizon"),
        priority=d.get("priority"),
        deadline=d.get("deadline"),
        category=d.get("category"),
        kind=kind,
        schedule=d.get("schedule"),
        polarity=d.get("polarity"),
    )
    # В чат — «я создал цель / привычку / ограничение»
    icon = {"oneshot": "🎯", "recurring": "♻", "constraint": "⛔"}.get(kind, "🎯")
    label = {"oneshot": "Новая цель", "recurring": "Новая привычка",
             "constraint": "Новое ограничение"}.get(kind, "Новая цель")
    _push_event_to_chat(f"{icon} {label}: «{text[:120]}»", mode_name=label)
    # Action Memory: user создал цель/привычку/ограничение
    try:
        from ...graph_logic import record_action
        record_action(actor="user", action_kind=f"user_goal_create_{kind}",
                      text=f"{label}: {text[:120]}",
                      extras={"goal_id": gid, "goal_kind": kind})
    except Exception as e:
        log.debug(f"[action-memory] user_goal_create failed: {e}")
    return jsonify({"ok": True, "id": gid})


@assistant_bp.route("/goals/instance", methods=["POST"])
def goals_instance():
    """Отметить выполнение recurring-цели. Body: {id, note?}"""
    from ...goals_store import record_instance, get_goal
    d = request.get_json(force=True) or {}
    gid = d.get("id", "")
    g = get_goal(gid)
    if not g:
        return jsonify({"error": "goal_not_found"}), 404
    if g.get("kind") != "recurring":
        return jsonify({"error": "not_recurring",
                        "kind": g.get("kind")}), 400
    record_instance(gid, note=d.get("note", ""))
    from ...recurring import get_progress
    return jsonify({"ok": True, "progress": get_progress(gid)})


@assistant_bp.route("/goals/violation", methods=["POST"])
def goals_violation():
    """Отметить нарушение constraint. Body: {id, note?, detected?}

    detected: "manual" (default) | "llm_scan" | "tick"
    """
    from ...goals_store import record_violation, get_goal
    d = request.get_json(force=True) or {}
    gid = d.get("id", "")
    g = get_goal(gid)
    if not g:
        return jsonify({"error": "goal_not_found"}), 404
    if g.get("kind") != "constraint":
        return jsonify({"error": "not_constraint",
                        "kind": g.get("kind")}), 400
    record_violation(gid, note=d.get("note", ""),
                     detected=d.get("detected", "manual"))
    return jsonify({"ok": True})


@assistant_bp.route("/goals/confirm-draft", methods=["POST"])
def goals_confirm_draft():
    """Подтверждение черновика от intent_router.

    Body: {draft: {kind: "new_recurring"|"new_constraint"|"new_goal",
                   text, schedule?, polarity?, mode?, category?}}

    Создаёт соответствующий goal через `add_goal` и возвращает ID.
    """
    from ...goals_store import add_goal
    d = request.get_json(force=True) or {}
    draft = d.get("draft") or {}
    kind_sub = draft.get("kind") or "new_goal"
    text = (draft.get("text") or "").strip()
    if not text:
        return jsonify({"error": "empty text"}), 400

    kind_map = {
        "new_goal":        "oneshot",
        "new_recurring":   "recurring",
        "new_constraint":  "constraint",
    }
    kind = kind_map.get(kind_sub, "oneshot")
    try:
        gid = add_goal(
            text=text,
            mode=draft.get("mode") or ("rhythm" if kind == "recurring"
                                        else "horizon"),
            category=draft.get("category"),
            kind=kind,
            schedule=draft.get("schedule"),
            polarity=draft.get("polarity"),
        )
        return jsonify({"ok": True, "id": gid, "kind": kind})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@assistant_bp.route("/goals/recurring", methods=["GET"])
def goals_recurring_list():
    """Recurring-цели с прогрессом за сегодня."""
    from ...recurring import list_recurring, get_progress
    out = []
    for g in list_recurring(active_only=True):
        p = get_progress(g["id"])
        if p:
            out.append(p)
    return jsonify({"recurring": out})


@assistant_bp.route("/goals/constraints", methods=["GET"])
def goals_constraints_list():
    """Constraint-цели со статусом нарушений за 7 дней."""
    from ...recurring import list_constraint_status
    return jsonify({"constraints": list_constraint_status(days=7)})


@assistant_bp.route("/goals/complete", methods=["POST"])
def goals_complete():
    """Body: {id, reason}"""
    from ...goals_store import complete_goal
    d = request.get_json(force=True) or {}
    complete_goal(d.get("id", ""), reason=d.get("reason", ""))
    return jsonify({"ok": True})


@assistant_bp.route("/goals/abandon", methods=["POST"])
def goals_abandon():
    """Body: {id, reason}"""
    from ...goals_store import abandon_goal
    d = request.get_json(force=True) or {}
    abandon_goal(d.get("id", ""), reason=d.get("reason", ""))
    return jsonify({"ok": True})


@assistant_bp.route("/goals/postpone", methods=["POST"])
def goals_postpone():
    """Отложить цель до завтрашнего wake_hour. Используется в low_energy_heavy alert.

    Body: {id, until?: "tomorrow"|"next_week"}  — default "tomorrow"
    """
    from ...goals_store import update_goal, get_goal
    from ...user_profile import load_profile
    import datetime as _dt
    d = request.get_json(force=True) or {}
    gid = d.get("id") or ""
    if not gid or not get_goal(gid):
        return jsonify({"error": "goal_not_found"}), 404
    until = d.get("until", "tomorrow")
    prof = load_profile()
    wake = int(((prof.get("context") or {}).get("wake_hour")) or 7)
    now = _dt.datetime.now()
    if until == "next_week":
        target = now + _dt.timedelta(days=7)
    else:
        target = now + _dt.timedelta(days=1)
    target = target.replace(hour=wake, minute=0, second=0, microsecond=0)
    # deadline — это существующее поле, переиспользуем с семантикой postpone
    update_goal(gid, {"deadline": target.isoformat(timespec="seconds")})
    return jsonify({"ok": True, "postponed_until": target.isoformat(timespec="seconds")})


# ── Solved tasks archive ──────────────────────────────────────────────

@assistant_bp.route("/goals/solved", methods=["GET"])
def goals_solved_list():
    from ...solved_archive import list_solved
    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        limit = 50
    return jsonify({"solved": list_solved(limit=limit)})


@assistant_bp.route("/goals/solved/<snapshot_ref>", methods=["GET"])
def goals_solved_get(snapshot_ref):
    from ...solved_archive import load_solved
    data = load_solved(snapshot_ref)
    if not data:
        return jsonify({"error": "not_found"}), 404
    return jsonify(data)
