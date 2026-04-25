"""Detector layer — pure-function детекторы для Signal dispatcher.

Заменяют 13 alert-emitting check-функций из `cognitive_loop.py`. Контракт:

    detect_X(ctx: DetectorContext) -> Optional[Signal]

Детектор:
  - НЕ знает про throttle / `*_INTERVAL` / `_last_*` timestamps
  - НЕ мутирует state (read-only от ctx)
  - Возвращает None если primary условия не выполнены
  - Возвращает Signal с urgency, expires_at, dedup_key — dispatcher решает
    что эмитить юзеру

См. правило 1 в [docs/architecture-rules.md](../docs/architecture-rules.md).

## Side-effect work (DMN, night cycle)

DMN/night — heavy функции с side effects (pump между нодами, save graph,
add edges). Они НЕ становятся pure detector'ами целиком. Разделение:

    run_dmn_continuous(ctx) -> Optional[BridgeResult]   # heavy work
    detect_dmn_bridge(ctx, result) -> Optional[Signal]  # envelope decision

Work-функции остаются в cognitive_loop / src/dmn.py (если выделим). Сайд-
эффекты внутри них; dispatcher отдельно решает, **показать ли** результат.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Iterable, Optional, Union

from .signals import Signal

log = logging.getLogger(__name__)

# Детектор может вернуть:
#   - None (нет сигнала)
#   - Signal (один сигнал)
#   - Iterable[Signal] (батч — observation_suggestion с несколькими карточками)
DetectorReturn = Union[None, Signal, Iterable[Signal]]

if TYPE_CHECKING:
    from .cognitive_loop import CognitiveLoop
    from .neurochem import Neurochem, ProtectiveFreeze
    from .user_state import UserState


@dataclass
class DetectorContext:
    """Read-only контекст для детектора. Собирается раз в loop tick.

    Содержит **часто-используемые** ссылки + ссылку на `loop` для редких
    случаев (graph, activity_log, _recent_bridges, hrv_manager). Это
    pragmatic compromise: полностью flat dict с 15 полями был бы громоздок,
    тонкий context с loop-fallback позволяет детекторам жить просто.

    Принципы:
      - Детектор НЕ мутирует state. Любая мутация (record_baddle_action,
        save graph) идёт в caller'е после dispatch.
      - Никаких side-effects при чтении (loop.method() допустимо если
        method() pure).

    Поля:
        now: unix ts текущего tick
        user/neuro/freeze: per-class ссылки на state объекты
        loop: CognitiveLoop для доступа к graph/_recent_bridges/etc
        dmn_eligible: gate из _loop — True если `not_frozen AND ne_quiet
            AND idle_enough`. DMN-эвристические детекторы (dmn_bridge,
            dmn_deep, dmn_converge, state_walk, night_cycle) проверяют это
            в первой строке и возвращают None если False — heavy work не
            запускается во время foreground / freeze / high-NE.
    """

    now: float
    user: "UserState"
    neuro: "Neurochem"
    freeze: "ProtectiveFreeze"
    loop: "CognitiveLoop"   # для доступа к graph, activity_log, plans, etc.
    dmn_eligible: bool = True


def build_detector_context(loop: "CognitiveLoop", now: float) -> DetectorContext:
    """Собрать DetectorContext из текущего state. Вызывается раз за tick.

    DMN gate (`not_frozen AND ne_quiet AND idle_enough`) считается здесь —
    DMN-эвристические детекторы проверяют `ctx.dmn_eligible` для skip
    heavy work во время freeze/foreground/high-NE.

    Lazy-import объектов state — не тащим их в module-level чтобы избежать
    circular import (cognitive_loop ← signals ← detectors ← cognitive_loop).
    """
    from .user_state import get_user_state
    from .horizon import get_global_state, PROTECTIVE_FREEZE

    user = get_user_state()
    gs = get_global_state()
    neuro = gs.neuro
    freeze = loop._get_freeze()

    # DMN gate
    try:
        idle_enough = (now - loop._last_foreground_tick) >= loop.FOREGROUND_COOLDOWN
        ne_quiet = neuro.norepinephrine < loop.NE_HIGH_GATE
        not_frozen = gs.state != PROTECTIVE_FREEZE
        dmn_eligible = not_frozen and ne_quiet and idle_enough
    except Exception:
        dmn_eligible = False

    return DetectorContext(
        now=now, user=user, neuro=neuro, freeze=freeze, loop=loop,
        dmn_eligible=dmn_eligible,
    )


# ── DETECTORS registry ─────────────────────────────────────────────────────
#
# Список будет заполнен в Шаге 3 миграции. Каждый детектор — pure function
# (DetectorContext) -> Optional[Signal]. Dispatcher итерирует список,
# собирает кандидаты, фильтрует/сортирует/эмитит.

# ── Detector implementations ──────────────────────────────────────────────
#
# Каждый детектор:
#   - Pure function: state не мутирует
#   - Возвращает None если primary условия не выполнены
#   - urgency считается по контексту (см. phase-b spec § 6 эвристики)
#   - expires_at: после этого dispatcher дропает с reason="expired"
#   - dedup_key: блокирует повтор того же сигнала в окно dispatcher'а


def detect_coherence_crit(ctx: DetectorContext) -> Optional[Signal]:
    """HRV coherence < 0.25 → critical warning.

    urgency = 1.0 - coherence (0.75..1.0 при coh<0.25), bypass'ит budget
    через critical_threshold.

    Defensive: внешние вызовы (hrv_manager) могут упасть — возвращаем None.
    """
    try:
        from .hrv_manager import get_manager as get_hrv_manager
        mgr = get_hrv_manager()
        if not mgr.is_running:
            return None
        state = mgr.get_baddle_state()
        coh = state.get("coherence")
        if coh is None or coh >= 0.25:
            return None

        urgency = max(0.75, min(1.0, 1.0 - float(coh)))
        return Signal(
            type="coherence_crit",
            urgency=urgency,
            content={
                "type": "coherence_crit",
                "severity": "warning",
                "text": "Coherence очень низкая. Сделай паузу.",
                "text_en": "Coherence very low. Take a break.",
            },
            expires_at=ctx.now + 600,   # 10 мин — после пауза/восстановление
            dedup_key="coherence_crit",
            source="detect_coherence_crit",
        )
    except Exception:
        return None


def detect_low_capacity_heavy(ctx: DetectorContext) -> Optional[Signal]:
    """capacity_zone == red + есть heavy-mode goal → предложить перенести.

    Phase C migration: legacy `daily_energy < 30` gate → 3-zone capacity.
    Type alert остаётся `low_energy_heavy` для UI backward-compat (alert.type
    consumed by JS handlers, ext libs etc).

    urgency:
        2 fail (red minimal): 0.6
        3 fail (всё провисает — exhausted): 0.95 critical
    Heavy modes (HEAVY_MODES на CognitiveLoop): tournament/bayes/race/etc.

    Defensive: external calls могут упасть → None.
    """
    try:
        if ctx.user.capacity_zone != "red":
            return None

        from .goals_store import list_goals
        open_goals = list_goals(status="open", limit=20)
        heavy = [g for g in open_goals if g.get("mode") in ctx.loop.HEAVY_MODES]
        if not heavy:
            return None
        g0 = heavy[0]
        txt = (g0.get("text") or "")[:80]

        # urgency: чем больше fail'ов тем выше. capacity_reason — list строк.
        n_fails = len(ctx.user.capacity_reason or [])
        urgency = 0.6 + 0.1 * min(3, n_fails)   # 0.6..0.9
        if n_fails >= 4:
            urgency = 0.95   # critical: physical+emotional+cognitive все провисают

        # Reason для UI — переводим первые 2 reason'а в человеческую строку
        reason_tags = ctx.user.capacity_reason or []
        from .assistant import _capacity_reason_text
        reason_ru = _capacity_reason_text(reason_tags[:2], "ru")
        reason_en = _capacity_reason_text(reason_tags[:2], "en")

        return Signal(
            type="low_energy_heavy",   # backward-compat alert.type
            urgency=urgency,
            content={
                "type": "low_energy_heavy",
                "severity": "warning",
                "text": f"Capacity red — {reason_ru}. Тяжёлое решение «{txt}» — "
                        f"перенести на утро?",
                "text_en": f"Capacity red — {reason_en}. Heavy decision '{txt}' — "
                           f"move to tomorrow morning?",
                "goal_id": g0.get("id"),
                "goal_text": txt,
                "goal_mode": g0.get("mode"),
                "zone": "red",
                "reason": reason_tags,
                "actions": [
                    {"label": "Перенести", "label_en": "Postpone",
                     "action": "postpone_goal_tomorrow", "goal_id": g0.get("id")},
                    {"label": "Нет, сейчас", "label_en": "No, now",
                     "action": "dismiss"},
                ],
            },
            expires_at=ctx.now + 1800,
            dedup_key=f"low_energy_heavy:{g0.get('id')}",
            source="detect_low_capacity_heavy",
        )
    except Exception:
        return None


# Backward-compat alias — старое имя в DETECTORS list
detect_low_energy = detect_low_capacity_heavy


def detect_plan_reminder(ctx: DetectorContext) -> Optional[Signal]:
    """Planned events в окне 0..PLAN_REMINDER_MINUTES — push reminder.

    urgency = 0.7 + 0.3 * (1 - mins_left/10). Critical когда <2 мин.
    Возвращает ближайший event в окне (остальные подхватятся следующим
    tick'ом т.к. dedup_key per-plan).

    NOTE: исправлен баг в legacy `_check_plan_reminders` — там переменная
    `now` не определялась в функции, исключение глоталось try/except. Здесь
    `ctx.now`.
    """
    try:
        from .plans import schedule_for_day
        sched = schedule_for_day()
        window_s = ctx.loop.PLAN_REMINDER_MINUTES * 60   # 10 min default
        best = None    # ближайший pending plan в окне
        for it in sched:
            if it.get("done") or it.get("skipped"):
                continue
            planned = it.get("planned_ts")
            if not planned:
                continue
            delta = planned - ctx.now
            if not (0 < delta <= window_s):
                continue
            if best is None or delta < (best.get("planned_ts") - ctx.now):
                best = it
        if best is None:
            return None

        delta = best["planned_ts"] - ctx.now
        mins_left = max(1, int(delta / 60))
        fraction = mins_left / float(ctx.loop.PLAN_REMINDER_MINUTES)
        urgency = min(1.0, 0.7 + 0.3 * (1.0 - fraction))
        if mins_left <= 2:
            urgency = max(urgency, 0.95)   # critical

        import datetime as _dt
        today_str = _dt.date.today().strftime("%Y-%m-%d")
        for_date = best.get("for_date") or today_str

        return Signal(
            type="plan_reminder",
            urgency=urgency,
            content={
                "type": "plan_reminder",
                "severity": "info",
                "text": f"Через {mins_left} мин: {best.get('name', '')}"
                        + (f" ({best.get('category')})" if best.get("category") else ""),
                "text_en": f"In {mins_left} min: {best.get('name', '')}",
                "plan_id": best["id"],
                "plan_name": best.get("name", ""),
                "plan_category": best.get("category"),
                "for_date": for_date,
                "planned_ts": best["planned_ts"],
                "minutes_before": mins_left,
            },
            expires_at=best["planned_ts"],   # после события — stale
            dedup_key=f"plan_reminder:{best['id']}:{for_date}",
            source="detect_plan_reminder",
        )
    except Exception:
        return None


def detect_recurring_lag(ctx: DetectorContext) -> Optional[Signal]:
    """Recurring goal с lag ≥ RECURRING_LAG_MIN → push reminder.

    urgency = 0.3 + 0.15 * min(5, lag) → 0.3..1.05 (capped 1.0).
    Per-goal dedup_key — разные цели лагают независимо.

    Возвращает самый отстающий goal — остальные подхватятся следующими
    tick'ами (dedup_key per-goal).

    Defensive: внешние вызовы (recurring.list_lagging) могут упасть → None.
    """
    try:
        from .recurring import list_lagging
        lagging = list_lagging(min_lag=ctx.loop.RECURRING_LAG_MIN)
        if not lagging:
            return None

        # Берём самый отстающий (max lag) — попадёт первым по urgency-sort
        p = max(lagging, key=lambda x: x.get("lag", 0))
        gid = p.get("goal_id") or ""
        lag = p.get("lag", 0)
        done = p.get("done_today", 0)
        tpd = p.get("times_per_day", 0)

        urgency = min(1.0, 0.3 + 0.15 * min(5, lag))

        text = (f"⏰ «{p.get('text','')}» — отставание {lag} "
                f"(сегодня {done}/{tpd}). Напомню через 30 мин если не отметишь.")
        return Signal(
            type="recurring_lag",
            urgency=urgency,
            content={
                "type": "recurring_lag",
                "severity": "info",
                "text": text,
                "text_en": (f"«{p.get('text','')}» lagging {lag} ({done}/{tpd} today)."),
                "goal_id": gid,
                "lag": lag,
                "done_today": done,
                "times_per_day": tpd,
            },
            # Dedup window dispatcher'а (1h) ≈ RECURRING_LAG_CHECK_INTERVAL*2
            expires_at=ctx.now + 1800,
            dedup_key=f"recurring_lag:{gid}",
            source="detect_recurring_lag",
        )
    except Exception:
        return None


def detect_sync_seeking(ctx: DetectorContext) -> Optional[Signal]:
    """Resonance protocol: silence высокая И юзер давно молчит → reach out.

    Conditions (все):
      - silence_pressure > SYNC_SEEKING_SILENCE_MIN (0.3)
      - idle_seconds > SYNC_SEEKING_IDLE_SECONDS (7200s)

    urgency = 0.3 + 0.5*(silence-0.3)/0.7 + 0.2*hrv_surprise → 0.3..1.0.
    expires_at = now + 1h.
    dedup_key = "sync_seeking" — один за окно dispatcher'а.

    Note: legacy `quiet_after_other` gate (30 мин после других proactive) и
    `interval` throttle убраны — их роль берёт dispatcher (budget+window).

    Counterfactual 10% skip: side-effect — запись action_memory
    `sync_seeking_counterfactual` для A/B сравнения recovery-time с
    вмешательством vs без.
    """
    try:
        from .user_state import get_user_state

        silence = float(ctx.freeze.silence_pressure)
        if silence < 0.3:   # SYNC_SEEKING_SILENCE_MIN
            return None

        last_input_ts = ctx.user._last_input_ts or 0.0
        idle_seconds = ctx.now - last_input_ts if last_input_ts else float("inf")
        if idle_seconds < 7200.0:   # SYNC_SEEKING_IDLE_SECONDS (2ч)
            return None

        # Counterfactual A/B: random 10% skip когда все gate'ы прошли.
        # Записываем в action_memory как side-effect для последующего анализа.
        import random as _rnd
        if _rnd.random() < 0.10:   # SYNC_SEEKING_COUNTERFACTUAL_RATE
            try:
                ctx.loop._record_baddle_action(
                    "sync_seeking_counterfactual",
                    text=f"Counterfactual skip: silence={silence:.2f} "
                         f"idle={idle_seconds/3600:.1f}h",
                    extras={"silence_at_skip": round(silence, 3),
                            "idle_hours": round(idle_seconds / 3600.0, 1)},
                )
            except Exception:
                pass
            log.info(f"[detect_sync_seeking] COUNTERFACTUAL: silence={silence:.2f}")
            return None

        # Compute message + tone (LLM call, side effect внутри)
        text, tone = ctx.loop._generate_sync_seeking_message(
            silence=silence, idle_hours=idle_seconds / 3600.0)
        if not text:
            return None

        # urgency: 0.3 floor + scale by silence (above min) + bonus from hrv_surprise
        try:
            hrv_surprise = float(ctx.user.hrv_surprise)
        except Exception:
            hrv_surprise = 0.0
        urgency = min(1.0, 0.3 + 0.5 * (silence - 0.3) / 0.7
                              + 0.2 * hrv_surprise)

        return Signal(
            type="sync_seeking",
            urgency=urgency,
            content={
                "type": "sync_seeking",
                "severity": "info",
                "text": text,
                "text_en": text,
                "tone": tone,
                "silence_level": round(silence, 3),
                "idle_hours": round(idle_seconds / 3600.0, 1),
            },
            expires_at=ctx.now + 3600,   # 1 час до stale (контекст уезжает)
            dedup_key="sync_seeking",
            source="detect_sync_seeking",
        )
    except Exception as e:
        log.debug(f"[detect_sync_seeking] failed: {e}")
        return None


def detect_evening_retro(ctx: DetectorContext) -> Optional[Signal]:
    """Вечернее ретро — раз в день после wake_hour + 14h.

    urgency = 0.7 fixed (важный ежедневный anchor, но не critical).
    Tracking: `loop._last_evening_retro_date` (str date) — устанавливается в
    детекторе перед return для preserving legacy semantic.
    expires_at = end of day local time.
    """
    try:
        import datetime as _dt
        today_str = _dt.date.today().strftime("%Y-%m-%d")
        if ctx.loop._last_evening_retro_date == today_str:
            return None

        try:
            from .user_profile import load_profile
            wake = int((load_profile().get("context") or {}).get(
                "wake_hour", ctx.loop.DEFAULT_WAKE_HOUR))
        except Exception:
            wake = ctx.loop.DEFAULT_WAKE_HOUR
        retro_hour = min(23, wake + ctx.loop.EVENING_RETRO_HOUR_OFFSET)
        local_dt = _dt.datetime.now()
        if local_dt.hour < retro_hour:
            return None

        try:
            from .plans import schedule_for_day
            sched = schedule_for_day()
            unfinished = [
                {"id": s["id"], "name": s.get("name", ""),
                 "category": s.get("category"),
                 "planned_ts": s.get("planned_ts"),
                 "kind": s.get("kind")}
                for s in sched
                if not s.get("done") and not s.get("skipped")
            ]
        except Exception:
            unfinished = []

        # Set state BEFORE return — same legacy semantic
        ctx.loop._last_evening_retro_date = today_str

        n_un = len(unfinished)
        text = (f"Ретро дня: {n_un} невыполнен{'о' if n_un == 1 else 'ы'}. "
                f"Откроем check-in?") if n_un \
               else "Ретро дня: всё по плану. Сделаем check-in?"

        # End of day — expires_at = midnight local
        midnight = local_dt.replace(hour=23, minute=59, second=59).timestamp()

        return Signal(
            type="evening_retro",
            urgency=0.7,
            content={
                "type": "evening_retro",
                "severity": "info",
                "text": text,
                "text_en": text,
                "unfinished": unfinished,
                "hour": local_dt.hour,
            },
            expires_at=midnight,
            dedup_key=f"evening_retro:{today_str}",
            source="detect_evening_retro",
        )
    except Exception as e:
        log.debug(f"[detect_evening_retro] failed: {e}")
        return None


def detect_morning_briefing(ctx: DetectorContext) -> Optional[Signal]:
    """Push morning briefing раз в сутки после wake_hour.

    urgency = 0.8 fixed (ежедневный якорь, важен). expires_at = end of day.
    Tracking: `loop._last_briefing` ts (persist в user_state.json через
    assistant._save_state). Lazy-load из диска на первом вызове.
    """
    try:
        import datetime as _dt

        # Lazy-load last_briefing_ts
        if getattr(ctx.loop, "_briefing_loaded_from_disk", False) is False:
            try:
                from .assistant import _load_state
                persisted = float((_load_state().get("last_briefing_ts") or 0.0))
                if persisted > ctx.loop._last_briefing:
                    ctx.loop._last_briefing = persisted
            except Exception:
                pass
            ctx.loop._briefing_loaded_from_disk = True

        if ctx.now - ctx.loop._last_briefing < ctx.loop.BRIEFING_INTERVAL:
            return None

        try:
            from .user_profile import load_profile
            wake_hour = int((load_profile().get("context") or {}).get(
                "wake_hour", ctx.loop.DEFAULT_WAKE_HOUR))
        except Exception:
            wake_hour = ctx.loop.DEFAULT_WAKE_HOUR
        local_dt = _dt.datetime.now()
        if local_dt.hour < wake_hour:
            return None

        # Set state + persist BEFORE building text/sections — даже если
        # сборка упадёт, интервал зачитан и повторы не сработают (legacy).
        ctx.loop._last_briefing = ctx.now
        try:
            from .assistant import _load_state, _save_state
            st = _load_state()
            st["last_briefing_ts"] = ctx.now
            _save_state(st)
        except Exception as e:
            log.debug(f"[detect_morning_briefing] persist failed: {e}")

        try:
            text = ctx.loop._build_morning_briefing_text()
        except Exception as e:
            log.warning(f"[detect_morning_briefing] text failed: {e}")
            return None
        try:
            sections = ctx.loop._build_morning_briefing_sections()
        except Exception:
            sections = []

        # Expires at end of local day
        midnight = local_dt.replace(hour=23, minute=59, second=59).timestamp()
        today_str = local_dt.strftime("%Y-%m-%d")

        return Signal(
            type="morning_briefing",
            urgency=0.8,
            content={
                "type": "morning_briefing",
                "severity": "info",
                "text": text,
                "text_en": text,
                "hour": local_dt.hour,
                "sections": sections,
            },
            expires_at=midnight,
            dedup_key=f"morning_briefing:{today_str}",
            source="detect_morning_briefing",
        )
    except Exception as e:
        log.debug(f"[detect_morning_briefing] failed: {e}")
        return None


def detect_observation_suggestions(ctx: DetectorContext) -> Iterable[Signal]:
    """Раз в сутки: до 2 carded suggestions из patterns/checkins/stress.

    Возвращает list[Signal] (0..2). Каждая карточка отдельный Signal с
    distinct dedup_key (по trigger.type), чтобы dispatcher не блокировал
    второй sibling.

    urgency = 0.2 + 0.6 * pattern_strength_proxy (0.5 fallback). Не critical.
    expires_at = +6h (карточка живёт во время текущей фазы дня).

    Compute-throttle (LLM expensive): `loop._last_suggestions_check` daily +
    user-active 10min skip без обновления throttle (так юзер не пропускает).
    """
    try:
        # Skip если юзер активен — не долбим во время работы (без update throttle)
        last_ts = ctx.user._last_input_ts
        if last_ts and (ctx.now - last_ts) < 600:
            return []

        # Skip при высоком focus_residue — юзер в хаосе переключений, не
        # добавляем новых сигналов (Counter-wave: пауза вместо давления).
        if getattr(ctx.user, "focus_residue", 0.0) > 0.5:
            return []

        # Compute throttle daily
        if not ctx.loop._throttled("_last_suggestions_check",
                                    ctx.loop.SUGGESTIONS_CHECK_INTERVAL):
            return []

        from .suggestions import collect_suggestions, make_suggestion_card
        items = collect_suggestions(lang="ru")
        if not items:
            return []

        out: list[Signal] = []
        cap = ctx.loop.SUGGESTIONS_MAX_PER_DAY
        for item in items[:cap]:
            try:
                card = make_suggestion_card(item, lang="ru")
                draft_text = ((card.get("draft") or {}).get("text") or "").strip()
                card_title = (card.get("title") or "").strip()
                if len(draft_text) < 3 or not card_title:
                    log.info(f"[detect_observation_suggestions] skipped empty draft")
                    continue
                trigger = (item.get("trigger") or {}).get("type", "")
                # urgency: pattern strength heuristic
                strength = float(item.get("strength") or 0.5)
                urgency = min(0.85, 0.2 + 0.6 * strength)
                # expires +6h — карточки релевантны на фазу дня
                out.append(Signal(
                    type="observation_suggestion",
                    urgency=urgency,
                    content={
                        "type": "observation_suggestion",
                        "severity": "info",
                        "text": f"💡 {card_title}",
                        "text_en": card_title,
                        "card": card,
                        "source": trigger,
                    },
                    expires_at=ctx.now + 21600,   # 6 hours
                    dedup_key=f"observation_suggestion:{trigger}",
                    source="detect_observation_suggestions",
                ))
            except Exception as e:
                log.debug(f"[detect_observation_suggestions] card build failed: {e}")
        return out
    except Exception as e:
        log.debug(f"[detect_observation_suggestions] failed: {e}")
        return []


def detect_state_walk(ctx: DetectorContext) -> Optional[Signal]:
    """Episodic recall: similar past moment via state_graph embedding query.

    urgency = 0.3 + 0.5 * similarity → 0.3..0.8.
    expires_at = +30min (recall stale fast).

    Compute-throttle (embedding query expensive):
    `loop._last_state_walk × idle_multiplier` (20 мин base, растягивается
    по burnout). State_graph count >= 10 — иначе нет истории.

    DMN-eligible: skip если frozen / high-NE / foreground (heavy compute).
    """
    if not ctx.dmn_eligible:
        return None
    try:
        from .state_graph import get_state_graph
        sg = get_state_graph()
        if sg.count() < 10:
            return None
        if not ctx.loop._throttled_idle("_last_state_walk",
                                         ctx.loop.STATE_WALK_INTERVAL):
            return None

        # Прогрев embedding-кэша + query
        try:
            for entry in sg.tail(30):
                sg.ensure_embedding(entry)
        except Exception as e:
            log.debug(f"[detect_state_walk] warm embeddings failed: {e}")

        from .api_backend import api_get_embedding
        sig_text = ctx.loop._build_current_state_signature()
        query_emb = api_get_embedding(sig_text)
        if not query_emb:
            return None

        similar = sg.query_similar(query_emb, k=3, exclude_recent=3)
        if not similar:
            return None

        # Filter: skip too-recent matches (<1h)
        from datetime import datetime, timezone
        now_utc = datetime.now(timezone.utc)
        best = None
        for entry in similar:
            ts_iso = entry.get("timestamp")
            if not ts_iso:
                continue
            try:
                ts = datetime.fromisoformat(str(ts_iso).replace("Z", "+00:00"))
                if (now_utc - ts).total_seconds() < 3600:
                    continue
            except Exception:
                pass
            best = entry
            break
        if best is None:
            return None

        ts_disp = str(best.get("timestamp", "?"))[:10]
        action = best.get("action", "?")
        reason = (best.get("reason") or "")[:100]

        # Translation tables — same as legacy для UI compat
        _ACTION_RU = {
            "think_toward": "генерировал новые идеи",
            "elaborate": "углублял важную мысль",
            "elaborate_toward": "углублял в сторону цели",
            "smartdc": "проверял противоречия",
            "doubt": "ставил гипотезу под сомнение",
            "expand": "расширял линию мышления",
            "collapse": "сжимал похожие идеи",
            "compare": "сравнивал варианты",
            "pump": "искал мост между далёкими идеями",
            "synthesize": "собирал итог",
            "ask": "задавал вопрос",
            "stable": "отдыхал (достиг стабильности)",
            "merge": "объединял близкие идеи",
            "walk": "гулял по графу мыслей",
        }
        _ACTION_EN = {
            "think_toward": "generated new ideas",
            "elaborate": "deepened a key thought",
            "elaborate_toward": "deepened toward a goal",
            "smartdc": "checked contradictions",
            "doubt": "doubted a hypothesis",
            "expand": "expanded a line of thinking",
            "collapse": "merged similar ideas",
            "compare": "compared options",
            "pump": "searched bridges between distant ideas",
            "synthesize": "synthesized",
            "ask": "asked a question",
            "stable": "rested (converged)",
            "merge": "merged close ideas",
            "walk": "walked the graph",
        }
        verb_ru = _ACTION_RU.get(action, action)
        verb_en = _ACTION_EN.get(action, action)

        # urgency proxy: based on (1 - distance) — query_similar возвращает
        # results sorted by similarity. Берём score если есть, иначе 0.5.
        sim_score = float(best.get("score") or best.get("similarity") or 0.5)
        urgency = min(0.85, 0.3 + 0.5 * sim_score)

        return Signal(
            type="state_walk",
            urgency=urgency,
            content={
                "type": "state_walk",
                "severity": "info",
                "text": f"🕰 Похожий момент ({ts_disp}): тогда я {verb_ru}.",
                "text_en": f"🕰 Similar moment ({ts_disp}): back then I {verb_en}.",
                "match": {
                    "hash": best.get("hash"),
                    "action": action,
                    "reason": reason,
                    "timestamp": best.get("timestamp"),
                },
            },
            expires_at=ctx.now + 1800,   # 30 min
            dedup_key=f"state_walk:{best.get('hash', 'unknown')}",
            source="detect_state_walk",
        )
    except Exception as e:
        log.debug(f"[detect_state_walk] failed: {e}")
        return None


# ── Heavy-work detectors (delegate to CognitiveLoop._run_*) ───────────────
#
# DMN и night_cycle делают heavy graph ops + side effects (save graph, mutate
# nodes, record actions). Их работа остаётся методами CognitiveLoop т.к. они
# тесно связаны с её внутренним state (_graph, _recent_bridges, etc).
#
# Pattern (правило 1 в docs/architecture-rules.md):
#
#   1. CognitiveLoop._run_X(ctx) -> Optional[Signal]
#      — heavy work, side effects внутри, возвращает Signal или None
#   2. detectors.detect_X(ctx)
#      — тонкий wrapper, делегирует в loop._run_X(ctx)
#
# Step 3c добавляет wrappers (методы _run_* пока не существуют → return None).
# Step 4 экстрагирует _run_* из _check_* и переписывает _loop() на DETECTORS.


def _delegate_heavy(ctx: DetectorContext, method_name: str,
                     detector_name: str) -> Optional[Signal]:
    """Common wrapper: вызвать loop.method_name(ctx) если существует."""
    run_method = getattr(ctx.loop, method_name, None)
    if run_method is None:
        return None
    try:
        return run_method(ctx)
    except Exception as e:
        log.debug(f"[{detector_name}] failed: {e}")
        return None


def detect_dmn_bridge(ctx: DetectorContext) -> Optional[Signal]:
    """DMN pump-bridge между двумя удалёнными нодами графа.

    Heavy work in `CognitiveLoop._run_dmn_continuous(ctx)`:
      - Граф ≥ 4 ноды
      - Throttle: DMN_INTERVAL × idle_multiplier (10 мин base)
      - Gate: not frozen, ne < NE_HIGH_GATE, idle ≥ FOREGROUND_COOLDOWN
      - Side effects: _recent_bridges.append, _record_baddle_action
      - Filter: quality > 0.5 AND len(text) >= 10

    urgency = 0.2 + 0.7 × bridge.quality (0.55..0.9 при quality≥0.5).
    """
    if not ctx.dmn_eligible:
        return None
    return _delegate_heavy(ctx, "_run_dmn_continuous", "detect_dmn_bridge")


def detect_dmn_deep_research(ctx: DetectorContext) -> Optional[Signal]:
    """3-step autonomous research на одной open-goal.

    Heavy work in `CognitiveLoop._run_dmn_deep_research(ctx)`:
      - Throttle: DMN_DEEP_INTERVAL × idle_multiplier (30 мин base)
      - ≥ 1 open goal, граф < 30 нод
      - Side effects: _recent_bridges, _record_baddle_action

    urgency = 0.4 + 0.3 × novelty (0.4..0.7).
    """
    if not ctx.dmn_eligible:
        return None
    return _delegate_heavy(ctx, "_run_dmn_deep_research", "detect_dmn_deep_research")


def detect_dmn_converge(ctx: DetectorContext) -> Optional[Signal]:
    """Server-side autorun loop до STABLE state.

    Heavy work in `CognitiveLoop._run_dmn_converge(ctx)`:
      - Throttle: DMN_CONVERGE_INTERVAL × idle_multiplier (1 ч base)
      - Граф 5..40 нод
      - Loop guards: max_steps=100, wall_time<15 мин, stall_window=12

    urgency = 0.5 fixed (редкий, средняя важность).
    """
    if not ctx.dmn_eligible:
        return None
    return _delegate_heavy(ctx, "_run_dmn_converge", "detect_dmn_converge")


def detect_night_cycle(ctx: DetectorContext) -> Optional[Signal]:
    """24h ночной цикл — Scout + REM emotional + REM creative + Consolidation.

    Heavy work in `CognitiveLoop._run_night_cycle(ctx)`:
      - Throttle: NIGHT_CYCLE_INTERVAL × idle_multiplier (24 ч base)
      - 5 phases: Scout pump, REM emotional, REM creative, consolidation, patterns
      - Side effects: _last_night_summary, _recent_bridges, archive rotation

    urgency = 0.6 fixed (ежедневный summary).
    """
    if not ctx.dmn_eligible:
        return None
    return _delegate_heavy(ctx, "_run_night_cycle", "detect_night_cycle")


# ── DETECTORS registry — все 13 ────────────────────────────────────────────

DETECTORS: list[Callable[[DetectorContext], DetectorReturn]] = [
    # Simple — ~30 строк каждая, pure compute
    detect_coherence_crit,
    detect_low_energy,
    detect_plan_reminder,
    detect_recurring_lag,
    # Medium — compute-throttle для дорогих работ (state_walk embedding,
    # morning_briefing build, observation LLM)
    detect_sync_seeking,
    detect_evening_retro,
    detect_morning_briefing,
    detect_observation_suggestions,
    detect_state_walk,
    # Heavy — delegate в CognitiveLoop._run_* (Step 4 экстрагирует)
    detect_dmn_bridge,
    detect_dmn_deep_research,
    detect_dmn_converge,
    detect_night_cycle,
]
