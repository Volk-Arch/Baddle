"""Briefings routes — /assist/morning + /assist/weekly + /assist/alerts (W14.6b4).

Morning briefing с recovery%/sections; weekly review с digest (habits, food,
scout bridges, recommendations); proactive alerts UI poll endpoint
(graph query через workspace.list_recent_alerts).
"""
import logging
import time
from datetime import datetime, timedelta

from flask import request, jsonify

from . import assistant_bp
from ..state import _get_context, _load_state, _save_state, _log_decision

log = logging.getLogger(__name__)


@assistant_bp.route("/assist/morning", methods=["POST"])
def assist_morning():
    """Generate a morning briefing based on HRV recovery + pending tasks."""
    lang = request.get_json(force=True).get("lang", "ru") if request.is_json else "ru"

    ctx = _get_context()
    state, hrv_state = ctx["state"], ctx["hrv"] or {}
    capacity = ctx.get("capacity") or {}
    recovery = (hrv_state or {}).get("energy_recovery") if hrv_state else None

    # Compose greeting. Phase C cleanup (2026-04-26): убрана строка "Бюджет:
    # {energy_val}/100" — Phase C перешёл на 3-zone capacity вместо single
    # energy budget; `energy` variable не определялась → runtime NameError.
    # Capacity zone live-обновляется через morning briefing sections (capacity),
    # см. _briefing_capacity helper в cognitive_loop.py.
    recovery_pct = round((recovery or 0.7) * 100)

    if lang == "ru":
        if recovery_pct >= 80:
            greeting = f"Доброе утро. Восстановление {recovery_pct}%. Отличный день для сложных задач."
        elif recovery_pct >= 60:
            greeting = f"Доброе утро. Восстановление {recovery_pct}%. Средний день — начни с важного."
        else:
            greeting = f"Доброе утро. Восстановление {recovery_pct}%. Береги энергию, лёгкие задачи первыми."
    else:
        if recovery_pct >= 80:
            greeting = f"Good morning. Recovery {recovery_pct}%. Great day for complex tasks."
        elif recovery_pct >= 60:
            greeting = f"Good morning. Recovery {recovery_pct}%. Medium day — start with priorities."
        else:
            greeting = f"Good morning. Recovery {recovery_pct}%. Save energy, light tasks first."

    _log_decision(state, kind="morning_briefing")
    _save_state(state)

    # Rich sections: тот же builder что использует cognitive_loop для push-alert'ов
    # (sleep / recovery / energy / overnight bridges / activity / goals / pattern).
    # UI рендерит их как мокап-карточку; text остаётся fallback'ом.
    sections: list = []
    try:
        from ...process.cognitive_loop import get_cognitive_loop
        cl = get_cognitive_loop()
        if hasattr(cl, "_build_morning_briefing_sections"):
            sections = cl._build_morning_briefing_sections() or []
    except Exception as e:
        log.debug(f"[/assist/morning] sections builder failed: {e}")

    # Action timeline (W14.4): brief_morning. accumulate=False + immediate
    # commit — briefing explicit user request.
    from ...memory import workspace
    workspace.record_committed(
        actor="baddle", action_kind="brief_morning",
        text=greeting, urgency=0.6, accumulate=False,
        ttl_seconds=24 * 3600,
        extras={"sections_count": len(sections),
                 "recovery_pct": recovery_pct, "lang": lang},
    )

    import datetime as _dt
    return jsonify({
        "text": greeting,
        "sections": sections,
        "capacity": capacity,
        "hrv": hrv_state,
        "recovery_pct": recovery_pct,
        "hour": _dt.datetime.now().hour,
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

    # Daily breakdown для charts (7 столбцов — решения за каждый день недели)
    daily_buckets: dict = {}
    for h in recent:
        try:
            ts = float(h.get("ts", 0))
            day_key = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            daily_buckets[day_key] = daily_buckets.get(day_key, 0) + 1
        except Exception:
            continue
    # Сортируем по дате + заполняем пропуски нулями
    now = datetime.now()
    daily_series = []
    for i in range(6, -1, -1):
        dk = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        daily_series.append({"date": dk, "count": daily_buckets.get(dk, 0)})

    # HRV trend — если в истории есть hrv snapshots
    hrv_trend = []
    for h in recent:
        if "hrv_coherence" in h and h.get("hrv_coherence") is not None:
            hrv_trend.append({
                "ts": h.get("ts"),
                "coherence": h.get("hrv_coherence"),
            })

    # Correlation layer: time-of-day × outcome → actionable recommendation.
    # Критерий: решения группируем по 4 бакетам (morning/afternoon/evening/night).
    # Outcome-метрика: accepted/rejected feedback (если нет — skip бакет).
    # Если разница accept-rate между лучшим и худшим бакетом ≥ 20 pp и
    # сэмплов в каждом ≥ 3 — выдаём рекомендацию.
    def _bucket_for_hour(h):
        if 5 <= h < 12: return "morning"
        if 12 <= h < 18: return "afternoon"
        if 18 <= h < 23: return "evening"
        return "night"
    bucket_stats: dict = {}
    for h in recent:
        fb = h.get("feedback")
        if fb not in ("accepted", "rejected"):
            continue
        try:
            hr = datetime.fromtimestamp(float(h.get("ts", 0))).hour
        except Exception:
            continue
        b = _bucket_for_hour(hr)
        st = bucket_stats.setdefault(b, {"accepted": 0, "rejected": 0})
        st[fb] = st.get(fb, 0) + 1

    recommendations = []
    bucket_rates = {}
    for b, st in bucket_stats.items():
        total = st.get("accepted", 0) + st.get("rejected", 0)
        if total < 3:
            continue
        bucket_rates[b] = st.get("accepted", 0) / total

    if len(bucket_rates) >= 2:
        best_b, best_r = max(bucket_rates.items(), key=lambda kv: kv[1])
        worst_b, worst_r = min(bucket_rates.items(), key=lambda kv: kv[1])
        if (best_r - worst_r) >= 0.20:
            ru_names = {"morning": "утром", "afternoon": "днём",
                        "evening": "вечером", "night": "ночью"}
            en_names = {"morning": "morning", "afternoon": "afternoon",
                        "evening": "evening", "night": "night"}
            if lang == "ru":
                msg = (f"Решения {ru_names[best_b]} принимаются {int(best_r*100)}%, "
                       f"{ru_names[worst_b]} — {int(worst_r*100)}%. "
                       f"Переноси важное на {ru_names[best_b]} "
                       f"({(best_r/worst_r):.1f}x лучше исход).")
            else:
                msg = (f"Decisions {en_names[best_b]}: {int(best_r*100)}% accept, "
                       f"{en_names[worst_b]}: {int(worst_r*100)}%. "
                       f"Move important to {en_names[best_b]} "
                       f"({(best_r/worst_r):.1f}x better outcome).")
            recommendations.append({
                "kind": "time_of_day",
                "best_bucket": best_b,
                "worst_bucket": worst_b,
                "best_rate": round(best_r, 2),
                "worst_rate": round(worst_r, 2),
                "text": msg,
            })
    elif len(bucket_rates) == 1:
        # Слабый сигнал — одна группа не даёт сравнения
        recommendations.append({
            "kind": "insufficient_data",
            "text": ("Данных по feedback мало — нужно хотя бы 3 accept/reject "
                     "в разных частях дня для рекомендаций."
                     if lang == "ru" else
                     "Not enough feedback data — need ≥3 accept/reject in different "
                     "parts of the day for recommendations."),
        })

    # Activity summary last 7d → mean work/health/food hours — дополнительная
    # рекомендация при сильном перекосе
    try:
        from ...activity_log import _replay as _replay_act
        week_cutoff = cutoff
        totals: dict[str, float] = {}
        for a in _replay_act().values():
            if (a.get("started_at") or 0) < week_cutoff:
                continue
            cat = a.get("category") or "uncategorized"
            totals[cat] = totals.get(cat, 0) + (a.get("duration_s") or 0)
        total_all = sum(totals.values())
        if total_all > 3600:  # хотя бы час трекинга
            work_h = totals.get("work", 0) / 3600
            health_h = totals.get("health", 0) / 3600
            if work_h > 30 and health_h < 2:
                recommendations.append({
                    "kind": "work_heavy",
                    "work_hours": round(work_h, 1),
                    "health_hours": round(health_h, 1),
                    "text": (f"За неделю {work_h:.0f}ч работы и {health_h:.1f}ч отдыха. "
                             f"Риск выгорания — добавь паузы."
                             if lang == "ru" else
                             f"{work_h:.0f}h work vs {health_h:.1f}h rest this week. "
                             f"Burnout risk — add pauses."),
                })
    except Exception:
        pass

    # Weekly digest блок: habit completion + food variety + scout bridges + checkin avg
    digest: dict = {}
    # Habits completion rate (plans.recurring)
    try:
        from ...plans import _replay as _plans_replay
        import datetime as _dt
        today_d = _dt.date.today()
        from ...plans import _matches_recurring as _mr
        completed = 0
        planned = 0
        top_habits = []
        for p in _plans_replay().values():
            if p.get("status") == "deleted" or not p.get("recurring"):
                continue
            rec = p["recurring"]
            done_dates = {c.get("for_date") for c in p.get("completions", []) if c.get("for_date")}
            # Считаем за последние 7 дней
            h_planned = 0
            h_done = 0
            for i in range(7):
                d = today_d - _dt.timedelta(days=i)
                if _mr(rec, d):
                    h_planned += 1
                    if d.strftime("%Y-%m-%d") in done_dates:
                        h_done += 1
            if h_planned > 0:
                planned += h_planned
                completed += h_done
                top_habits.append({
                    "name": p.get("name", ""),
                    "done": h_done, "planned": h_planned,
                    "streak": None,
                })
        digest["habits"] = {
            "completed": completed, "planned": planned,
            "rate": round(completed / planned, 2) if planned else None,
            "top": top_habits[:5],
        }
    except Exception as e:
        digest["habits"] = {"error": str(e)}

    # Food variety (уникальных блюд + суммарное время food)
    try:
        from ...activity_log import _replay as _act_replay
        week_start = time.time() - 7 * 86400
        food_names = []
        food_time_s = 0
        for a in _act_replay().values():
            if (a.get("started_at") or 0) < week_start:
                continue
            if a.get("category") == "food":
                name = (a.get("name") or "").strip()
                if name:
                    food_names.append(name)
                food_time_s += (a.get("duration_s") or 0)
        digest["food"] = {
            "entries": len(food_names),
            "unique_names": len(set(food_names)),
            "top_names": list({n: food_names.count(n) for n in set(food_names)}.items())[:5]
                         if food_names else [],
            "total_minutes": round(food_time_s / 60),
        }
    except Exception as e:
        digest["food"] = {"error": str(e)}

    # Scout bridges за неделю — graph query через workspace.list_recent_bridges
    # (W14.5c-3: _recent_bridges deque удалена, bridges теперь committed actions).
    try:
        from ...memory import workspace
        now_ts = time.time()
        week_ago = now_ts - 7 * 86400
        bridges = workspace.list_recent_bridges(since_ts=week_ago, limit=10)
        digest["scout_bridges"] = [{
            "text": (b.get("text") or "")[:120],
            "source": b.get("source") or b.get("action_kind"),
            "ts": b.get("committed_at"),
        } for b in bridges]
    except Exception:
        digest["scout_bridges"] = []

    # Check-in averages (7-day)
    try:
        from ...checkins import rolling_averages
        digest["checkin"] = rolling_averages(days=7)
    except Exception as e:
        digest["checkin"] = {"error": str(e)}

    # Patterns detected recently
    try:
        from ...patterns import read_recent_patterns
        digest["patterns"] = read_recent_patterns(hours=7 * 24)[:5]
    except Exception:
        digest["patterns"] = []

    # Action timeline (W14.4): brief_weekly через workspace.
    from ...memory import workspace
    workspace.record_committed(
        actor="baddle", action_kind="brief_weekly",
        text=text, urgency=0.6, accumulate=False,
        ttl_seconds=7 * 24 * 3600,
        extras={"decisions_this_week": len(recent),
                 "mode_counts": mode_counts, "lang": lang},
    )

    return jsonify({
        "text": text,
        "decisions_this_week": len(recent),
        "mode_counts": mode_counts,
        "streaks": streaks,
        "daily_series": daily_series,
        "hrv_trend": hrv_trend,
        "recommendations": recommendations,
        "bucket_rates": bucket_rates,
        "digest": digest,
    })


# ── Proactive alerts (polled by UI) ────────────────────────────────────

@assistant_bp.route("/assist/alerts", methods=["GET"])
def assist_alerts():
    """Return pending proactive alerts. UI polls this periodically.

    W14.5c: все alerts (regime/capacity/coherence/zone + dispatched DMN/scout/
    suggestions) идут через единый path: detector → Signal → Dispatcher →
    workspace.record_committed → graph. UI читает через workspace.list_recent_alerts
    с since_ts cursor. Computed-on-the-fly блок (~70 LOC) удалён —
    state-indicator detectors (regime_state/capacity_red_state/activity_zone)
    в src/process/detectors.py вместо.

    Response также содержит current state fields (capacity / hrv / sync_regime)
    как live indicators для UI header — они НЕ alerts, а snapshot текущего
    состояния (read each poll, не каждый push event).
    """
    from ...substrate.horizon import get_global_state
    from ...process.cognitive_loop import get_cognitive_loop
    ctx = _get_context()
    hrv_state = ctx["hrv"] or {}
    capacity = ctx.get("capacity") or {}
    alerts = []

    cs = get_global_state()
    regime = cs.sync_regime
    sync_err = cs.sync_error

    loop = get_cognitive_loop()
    try:
        from ...memory import workspace
        since = float(loop._last_alerts_poll_ts or 0.0)
        recent = workspace.list_recent_alerts(since)
        for node in recent:
            alert = {
                "type": node.get("action_kind", ""),
                "text": node.get("text", ""),
            }
            for k in ("severity", "text_en", "card", "source", "ts",
                       "zone", "reason", "regime", "sync_error"):
                if k in node:
                    alert[k] = node[k]
            alert.setdefault("ts", node.get("committed_at"))
            alerts.append(alert)
        loop._last_alerts_poll_ts = time.time()
    except Exception as e:
        log.debug(f"[/assist/alerts] graph query failed: {e}")

    return jsonify({
        "alerts": alerts,
        "count": len(alerts),
        "capacity": capacity,
        "hrv": hrv_state,
        "sync_regime": regime,
        "sync_error": round(sync_err, 3),
        "loop": loop.get_status(),
    })
