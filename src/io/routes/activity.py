"""Activity log routes — /activity/* endpoints (W14.6b3 extract).

Manual ground-truth tracker — start/stop/today/history/update/delete.
_sync_activity_to_graph helper создаёт type='activity' ноды в графе для
наглядности activity-потока + linkage к recurring цели + constraint
violation detection.
"""
import logging
import time

from flask import request, jsonify

from . import assistant_bp
from ..state import _push_event_to_chat

log = logging.getLogger(__name__)


@assistant_bp.route("/activity/active", methods=["GET"])
def activity_active():
    """Текущая активная задача + список шаблонов."""
    from ...activity_log import get_active, get_templates
    cur = get_active()
    if cur:
        cur = dict(cur)
        if cur.get("started_at"):
            cur["elapsed_s"] = max(0, int(time.time() - float(cur["started_at"])))
    return jsonify({
        "active": cur,
        "templates": get_templates(),
    })


def _sync_activity_to_graph(activity_id: str, name: str, category,
                            node_index=None, finalize: bool = False,
                            ts_start=None, ts_end=None, duration_s=None):
    """Создать/обновить ноду type=activity в графе.

    - При start: добавляем ноду, возвращаем её index.
    - При stop (finalize=True): обновляем ts_end + duration_s на существующей.
    Нейтрально к ошибкам — activity-лог не должен падать из-за графа.
    """
    try:
        from ...graph_logic import _graph, _add_node, graph_lock
        if finalize and node_index is not None:
            with graph_lock:
                nodes = _graph.get("nodes") or []
                if 0 <= node_index < len(nodes):
                    n = nodes[node_index]
                    n["activity_ts_end"] = ts_end
                    n["activity_duration_s"] = duration_s
                    # Визуальная пометка что задача закрыта
                    n["activity_done"] = True
                    return node_index
            return None
        # Start: create node
        new_idx = _add_node(
            text=name,
            node_type="activity",
        )
        with graph_lock:
            nodes = _graph.get("nodes") or []
            if 0 <= new_idx < len(nodes):
                n = nodes[new_idx]
                n["activity_id"] = activity_id
                n["activity_category"] = category
                n["activity_ts_start"] = ts_start
                n["activity_done"] = False
        return new_idx
    except Exception as e:
        log.warning(f"[activity] graph sync failed: {e}")
        return None


@assistant_bp.route("/activity/start", methods=["POST"])
def activity_start():
    """Начать новую задачу. Если есть активная — она автоматически стопается
    со `stop_reason='switch'` (поведение кнопки «Следующая» в Time Player).

    Body: {name, category?}
    """
    from ...activity_log import start_activity, get_active, update_activity

    d = request.get_json(force=True) or {}
    name = (d.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name_required"}), 400

    # Закрываем предыдущую ноду в графе (обновляем duration)
    prev = get_active()
    if prev and prev.get("node_index") is not None:
        started = prev.get("started_at") or 0
        _sync_activity_to_graph(
            activity_id=prev["id"], name=prev.get("name", ""),
            category=prev.get("category"),
            node_index=prev["node_index"], finalize=True,
            ts_end=time.time(),
            duration_s=round(time.time() - float(started)),
        )

    # Старт новой (автоматический stop_reason='switch' для предыдущей происходит внутри)
    category = d.get("category")
    aid = start_activity(name=name, category=category)

    # Создаём ноду в графе и связываем
    node_idx = _sync_activity_to_graph(
        activity_id=aid, name=name, category=category,
        ts_start=time.time(),
    )
    if node_idx is not None:
        update_activity(aid, {"node_index": node_idx})

    # Activity ↔ recurring: если activity-имя похоже на одну из recurring-целей,
    # auto-записать instance. Например start_activity("Обед") → +1 для
    # цели «покушать 3 раза в день». Не блокирующий LLM-call (~0.5-1 сек).
    matched_recurring = None
    try:
        from ...activity_log import try_match_recurring_instance
        matched_recurring = try_match_recurring_instance(
            activity_name=name, activity_category=category, lang="ru",
        )
    except Exception as _e:
        log.debug(f"[/activity/start] recurring match failed: {_e}")

    # Activity ↔ constraint: если activity-имя нарушает один из constraints,
    # пишем violation.
    violations = []
    try:
        from ...activity_log import try_detect_constraint_violation
        violations = try_detect_constraint_violation(name, lang="ru")
    except Exception as _e:
        log.debug(f"[/activity/start] violation scan failed: {_e}")

    resp = {"ok": True, "id": aid, "node_index": node_idx,
            "name": name}
    if matched_recurring:
        resp["matched_recurring"] = matched_recurring
    if violations:
        resp["violations"] = violations
    # В чат — «я засёк задачу X» чтобы история показывала activity-поток
    cat_s = f" · {category}" if category else ""
    _push_event_to_chat(f"▶ Задача: «{name[:120]}»{cat_s}", mode_name="Activity")
    # Action Memory: user_activity_start
    try:
        from ...graph_logic import record_action
        record_action(actor="user", action_kind="user_activity_start",
                      text=f"Start activity: {name[:120]}",
                      extras={"activity_id": aid, "category": category})
    except Exception as e:
        log.debug(f"[action-memory] user_activity_start failed: {e}")
    return jsonify(resp)


@assistant_bp.route("/activity/stop", methods=["POST"])
def activity_stop():
    """Остановить текущую активную задачу. Body: {reason?}"""
    from ...activity_log import stop_activity
    d = request.get_json(silent=True) or {}
    rec = stop_activity(reason=d.get("reason") or "manual")
    if not rec:
        return jsonify({"ok": True, "stopped": None})
    # Финализируем графовую ноду
    if rec.get("node_index") is not None:
        _sync_activity_to_graph(
            activity_id=rec["id"], name=rec.get("name", ""),
            category=rec.get("category"),
            node_index=rec["node_index"], finalize=True,
            ts_end=rec.get("stopped_at"),
            duration_s=round(rec.get("duration_s") or 0),
        )
    # Action Memory: user_activity_stop
    try:
        from ...graph_logic import record_action
        record_action(actor="user", action_kind="user_activity_stop",
                      text=f"Stop activity: {rec.get('name', '')[:120]}",
                      extras={"activity_id": rec.get("id"),
                               "duration_s": round(rec.get("duration_s") or 0),
                               "category": rec.get("category")})
    except Exception as e:
        log.debug(f"[action-memory] user_activity_stop failed: {e}")
    return jsonify({"ok": True, "stopped": rec})


@assistant_bp.route("/activity/today", methods=["GET"])
def activity_today():
    """Агрегат по локальному дню: total / by_category / top_names / switches."""
    from ...activity_log import day_summary
    return jsonify(day_summary())


@assistant_bp.route("/activity/history", methods=["GET"])
def activity_history():
    """Последние N задач (завершённые + активная).

    Query: ?limit=100 &category=food|work|health|social|learning &days=7
    Фильтр по category + days даёт ответ на вопросы типа «что я ел за неделю»,
    «сколько времени на работу за 30 дней».
    """
    from ...activity_log import list_activities
    try:
        limit = int(request.args.get("limit", 100))
    except ValueError:
        limit = 100
    cat = request.args.get("category")
    since = None
    days_s = request.args.get("days")
    if days_s:
        try:
            since = time.time() - int(days_s) * 86400
        except ValueError:
            since = None
    acts = list_activities(since_ts=since, limit=limit * 3 if cat else limit)
    if cat:
        acts = [a for a in acts if a.get("category") == cat][:limit]
    return jsonify({"activities": acts})


@assistant_bp.route("/activity/update", methods=["POST"])
def activity_update():
    """Body: {id, fields:{name,category,started_at,stopped_at}}.

    started_at/stopped_at — unix timestamps, для коррекции времени
    («забыл выключить Код → подрежь»).
    """
    from ...activity_log import update_activity
    d = request.get_json(force=True) or {}
    aid = d.get("id", "")
    if not aid:
        return jsonify({"error": "id_required"}), 400
    try:
        update_activity(aid, d.get("fields") or {})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@assistant_bp.route("/activity/delete", methods=["POST"])
def activity_delete():
    """Body: {id}. Мягкое удаление — событие `delete` в activity.jsonl."""
    from ...activity_log import delete_activity
    d = request.get_json(force=True) or {}
    aid = d.get("id", "")
    if not aid:
        return jsonify({"error": "id_required"}), 400
    delete_activity(aid)
    return jsonify({"ok": True})
