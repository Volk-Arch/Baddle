"""Meta-tick — tick читает свою историю и адаптирует policy/поведение.

До этого tick решал «что делать дальше» по мгновенному снимку графа +
CognitiveState. Meta-tick добавляет второй порядок: смотрим на **хвост
state_graph** (последние 20 tick'ов) и детектим паттерны которые не видны
в моменте.

Примеры паттернов:

  • stuck_execution    — 10+ подряд в EXECUTION, sync_error не падает
                         → рекомендуем `ask` (спросить юзера)
  • action_monotony    — 5 одинаковых actions подряд
                         → рекомендуем `compare` или policy nudge на doubt
  • rpe_negative_streak — recent_rpe < 0 в 6+ из 10 последних
                         → система стабильно переоценивает reward
                         → рекомендуем `stabilize` (force INTEGRATION)
  • high_rejection     — user_feedback=rejected в 3+ из 5 последних
                         → пересинхрон нужен
                         → рекомендуем `ask`

Результат `analyze_tail(tail) → {pattern, recommend, policy_nudge}`.

tick_nand.py применяет рекомендацию: emit action или мутирует
`horizon.policy_weights` (лёгкий толчок ±0.1 к весам с нормализацией).
"""
from typing import Optional


def _safe_get(entry: dict, *path, default=None):
    """Безопасно достать вложенное поле (для state_snapshot.neurochem.recent_rpe)."""
    cur = entry
    for k in path:
        if isinstance(cur, dict):
            cur = cur.get(k)
        else:
            return default
    return cur if cur is not None else default


def analyze_tail(tail: list[dict]) -> dict:
    """Анализ последних N state_nodes. Возвращает dict:

        {
          "pattern": "stuck_execution" | "action_monotony" | "rpe_negative_streak"
                     | "high_rejection" | "normal" | "not_enough_data",
          "recommend": "ask" | "compare" | "stabilize" | None,
          "policy_nudge": {phase: delta, ...} | None,
          "detail": "human-readable summary"
        }

    Возвращает первый сработавший паттерн (приоритет: rejection > stuck >
    rpe streak > monotony). «Normal» если ничего не сработало.
    """
    if not tail or len(tail) < 5:
        return {"pattern": "not_enough_data", "recommend": None,
                "policy_nudge": None, "detail": f"tail={len(tail)} < 5"}

    # Signal: high rejection rate (user пушит back)
    feedbacks = [e.get("user_feedback") for e in tail[-5:]]
    rejects = sum(1 for f in feedbacks if f == "rejected")
    if rejects >= 3:
        return {
            "pattern": "high_rejection",
            "recommend": "ask",
            "policy_nudge": {"doubt": +0.1, "generate": -0.05, "elaborate": -0.05},
            "detail": f"{rejects}/5 recent rejections — user out of sync",
        }

    # Signal: stuck in EXECUTION with no sync progress
    if len(tail) >= 10:
        last10 = tail[-10:]
        states = [_safe_get(e, "state_snapshot", "state", default="") for e in last10]
        sync_errors = [_safe_get(e, "state_snapshot", "sync_error", default=0.0)
                       for e in last10]
        if states.count("execution") >= 9:
            sync_delta = abs(float(sync_errors[-1]) - float(sync_errors[0]))
            if sync_delta < 0.05:
                return {
                    "pattern": "stuck_execution",
                    "recommend": "ask",
                    "policy_nudge": None,
                    "detail": f"{states.count('execution')}/10 in execution, "
                              f"sync Δ={sync_delta:.2f}",
                }

    # Signal: negative RPE streak → система над-ожидает reward
    rpes = []
    for e in tail[-10:]:
        r = _safe_get(e, "state_snapshot", "neurochem", "recent_rpe", default=None)
        if isinstance(r, (int, float)):
            rpes.append(float(r))
    if len(rpes) >= 10:
        negative = sum(1 for r in rpes if r < -0.05)
        if negative >= 6:
            return {
                "pattern": "rpe_negative_streak",
                "recommend": "stabilize",
                "policy_nudge": {"merge": +0.1, "generate": -0.1},
                "detail": f"{negative}/10 recent_rpe < -0.05 — overpredicting",
            }

    # Signal: action monotony
    if len(tail) >= 5:
        actions = [e.get("action", "") for e in tail[-5:]]
        if len(set(actions)) == 1 and actions[0] not in ("stable", "none", ""):
            return {
                "pattern": "action_monotony",
                "recommend": "compare",
                "policy_nudge": {"doubt": +0.1, "merge": -0.05, "generate": -0.05},
                "detail": f"5x '{actions[0]}' подряд — выход из рут'а",
            }

    return {"pattern": "normal", "recommend": None,
            "policy_nudge": None, "detail": "no anomaly"}


def apply_policy_nudge(horizon, nudge: dict):
    """Лёгкий сдвиг policy weights (±delta) с нормализацией.

    Используется когда meta-tick детектит паттерн но не эмитит action.
    Effect — следующий tick через `select_phase` выберет другую фазу.
    """
    if not nudge:
        return
    weights = getattr(horizon, "policy_weights", None)
    if not isinstance(weights, dict):
        return
    for phase, delta in nudge.items():
        if phase in weights:
            weights[phase] = max(0.05, weights[phase] + float(delta))
    total = sum(weights.values())
    if total > 0:
        for k in weights:
            weights[k] = round(weights[k] / total, 3)
