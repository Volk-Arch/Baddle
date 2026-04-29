"""Check-ins routes — /checkin/* endpoints (W14.6b3 extract).

Ручной subjective-сигнал (когда HRV off) — focus/stress/expected/reality.
Корректирует UserState: stress→NE, focus→serotonin, reality→valence,
(reality−expected)→surprise. Replaces HRV-контур когда трекера нет.
"""
import logging

from flask import request, jsonify

from . import assistant_bp
from ..state import _push_event_to_chat

log = logging.getLogger(__name__)


@assistant_bp.route("/checkin", methods=["POST"])
def checkin_add():
    """Body: {focus?, stress?, expected?, reality?, note?}.

    Все поля опциональны. Записывает событие + корректирует UserState:
    stress→NE, focus→serotonin, reality→valence,
    (reality−expected)→surprise. Replaces HRV-контур когда трекера нет.
    """
    from ...checkins import add_checkin, apply_to_user_state
    d = request.get_json(force=True) or {}
    entry = add_checkin(
        energy=d.get("energy"),
        focus=d.get("focus"),
        stress=d.get("stress"),
        expected=d.get("expected"),
        reality=d.get("reality"),
        note=d.get("note", ""),
    )
    apply_to_user_state(entry)
    # В чат — summary check-in'а чтобы он оставался в истории
    parts = []
    for k, lbl in (("energy", "E"), ("focus", "F"), ("stress", "S")):
        v = entry.get(k)
        if v is not None:
            parts.append(f"{lbl}{int(v)}")
    surp = None
    if entry.get("expected") is not None and entry.get("reality") is not None:
        s = entry["reality"] - entry["expected"]
        surp = f"Δ{'+' if s >= 0 else ''}{int(s)}"
    summary = " · ".join(parts) if parts else "—"
    if surp:
        summary += f" · сюрприз {surp}"
    note = (entry.get("note") or "").strip()
    if note:
        summary += f" · «{note[:60]}»"
    _push_event_to_chat(f"📝 Check-in: {summary}", mode_name="Check-in")
    # Action Memory: user_checkin — значимый event, часто после alert
    try:
        from ...graph_logic import record_action
        record_action(actor="user", action_kind="user_checkin",
                      text=f"Check-in: {summary[:120]}",
                      extras={"energy": entry.get("energy"),
                               "focus": entry.get("focus"),
                               "stress": entry.get("stress")})
    except Exception as e:
        log.debug(f"[action-memory] user_checkin failed: {e}")
    return jsonify({"ok": True, "entry": entry})


@assistant_bp.route("/checkin/latest", methods=["GET"])
def checkin_latest():
    """Последний check-in за последние 24ч (для UI-восстановления формы)."""
    from ...checkins import latest_checkin
    return jsonify({"entry": latest_checkin(hours=24)})


@assistant_bp.route("/checkin/history", methods=["GET"])
def checkin_history():
    """Список за последние N дней (?days=14, default)."""
    from ...checkins import list_checkins, rolling_averages
    try:
        days = int(request.args.get("days", 14))
    except ValueError:
        days = 14
    return jsonify({
        "items": list_checkins(days=days),
        "averages": rolling_averages(days=7),
    })
