"""Plans routes — /plan/* endpoints (W14.6b3 extract).

Карта будущего: events + recurring habits. add/complete/skip/delete +
today schedule. complete_plan feed surprise в РГК через
expected_difficulty vs actual_difficulty.
"""
from flask import request, jsonify

from . import assistant_bp


@assistant_bp.route("/plan/today", methods=["GET"])
def plans_today():
    """Расписание на сегодня (или ?date=YYYY-MM-DD). Разворачивает recurring."""
    from ...plans import schedule_for_day
    import datetime as _dt
    ds = request.args.get("date")
    target = None
    if ds:
        try:
            target = _dt.date.fromisoformat(ds)
        except ValueError:
            pass
    return jsonify({"schedule": schedule_for_day(target=target)})


@assistant_bp.route("/plan/add", methods=["POST"])
def plans_add():
    """Body: {name, category?, ts_start?, ts_end?, recurring?{days:[0..6],time:"HH:MM"},
             expected_difficulty?, note?, goal_id?}.

    `goal_id` — привязка к recurring-цели (goals_store). Complete plan
    будет auto-увеличивать прогресс этой цели.
    """
    from ...plans import add_plan
    d = request.get_json(force=True) or {}
    name = (d.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name_required"}), 400
    pid = add_plan(
        name=name,
        category=d.get("category"),
        ts_start=d.get("ts_start"),
        ts_end=d.get("ts_end"),
        recurring=d.get("recurring"),
        expected_difficulty=d.get("expected_difficulty"),
        note=d.get("note", ""),
        goal_id=d.get("goal_id"),
    )
    return jsonify({"ok": True, "id": pid})


@assistant_bp.route("/plan/complete", methods=["POST"])
def plans_complete():
    """Body: {id, for_date?, actual_ts?, actual_difficulty?, note?}

    Если у plan есть `goal_id`, возвращает `linked_goal` с прогрессом
    увеличенной recurring-цели (UI показывает badge «♻✓»).
    """
    from ...plans import complete_plan
    d = request.get_json(force=True) or {}
    pid = d.get("id")
    if not pid:
        return jsonify({"error": "id_required"}), 400
    link_info = complete_plan(
        plan_id=pid, for_date=d.get("for_date"),
        actual_ts=d.get("actual_ts"),
        actual_difficulty=d.get("actual_difficulty"),
        note=d.get("note", ""),
    )
    # Feed surprise в UserState (expected vs actual_difficulty).
    # До Фазы A было `user.surprise = user.surprise * 0.6 + s * 0.4` —
    # молча ломалось после того как surprise стал derived @property.
    # Правильный fix: nudge expectation baseline через shared helper.
    try:
        from ...plans import get_plan
        from ...substrate.rgk import get_global_rgk
        p = get_plan(pid)
        if p and p.get("expected_difficulty") and d.get("actual_difficulty"):
            exp = int(p["expected_difficulty"])
            act = int(d["actual_difficulty"])
            s = (act - exp) / 4.0  # norm в [-1, 1]
            get_global_rgk().u_apply_surprise(s, blend=0.4)
    except Exception:
        pass
    resp = {"ok": True}
    if link_info and link_info.get("linked_goal"):
        resp["linked_goal"] = link_info["linked_goal"]
    return jsonify(resp)


@assistant_bp.route("/plan/skip", methods=["POST"])
def plans_skip():
    """Body: {id, for_date?, reason?}"""
    from ...plans import skip_plan
    d = request.get_json(force=True) or {}
    if not d.get("id"):
        return jsonify({"error": "id_required"}), 400
    skip_plan(plan_id=d["id"], for_date=d.get("for_date"), reason=d.get("reason", ""))
    return jsonify({"ok": True})


@assistant_bp.route("/plan/delete", methods=["POST"])
def plans_delete():
    from ...plans import delete_plan
    d = request.get_json(force=True) or {}
    if not d.get("id"):
        return jsonify({"error": "id_required"}), 400
    delete_plan(plan_id=d["id"])
    return jsonify({"ok": True})
