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

## User-side surprise (отдельный API в конце файла)

`detect_user_surprise(text, activity, use_llm)` — детекция момента когда
**юзер** встретил неожиданное (HRV-drop / text markers / LLM classify).
Отдельный контракт: возвращает dict, не Signal. Caller
(`cognitive_loop._check_user_surprise`) сам throttle'ит, эмитит event и
вызывает `apply_subjective_surprise`. Жил отдельно по historical reasons,
по семантике — 14-й детектор.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
import time
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
    from ..substrate.rgk import РГК


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
        rgk: substrate (chem/freeze/sync) — единственная ссылка на state
        loop: CognitiveLoop для доступа к graph/_recent_bridges/etc
        dmn_eligible: gate из _loop — True если `not_frozen AND ne_quiet
            AND idle_enough`.
    """

    now: float
    rgk: "РГК"
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
    from ..substrate.horizon import get_global_state, PROTECTIVE_FREEZE

    gs = get_global_state()

    # DMN gate
    try:
        idle_enough = (now - loop._last_foreground_tick) >= loop.FOREGROUND_COOLDOWN
        ne_quiet = float(gs.rgk.system.aperture.value) < loop.NE_HIGH_GATE
        not_frozen = gs.state != PROTECTIVE_FREEZE
        dmn_eligible = not_frozen and ne_quiet and idle_enough
    except Exception:
        dmn_eligible = False

    return DetectorContext(
        now=now, rgk=gs.rgk, loop=loop,
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
        from ..sensors.manager import get_manager as get_hrv_manager
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
        if ctx.rgk.project("capacity")["zone"] != "red":
            return None

        from ..goals_store import list_goals
        open_goals = list_goals(status="open", limit=20)
        heavy = [g for g in open_goals if g.get("mode") in ctx.loop.HEAVY_MODES]
        if not heavy:
            return None
        g0 = heavy[0]
        txt = (g0.get("text") or "")[:80]

        # urgency: чем больше fail'ов тем выше. capacity_reason — list строк.
        n_fails = len(ctx.rgk.project("capacity")["reasons"] or [])
        urgency = 0.6 + 0.1 * min(3, n_fails)   # 0.6..0.9
        if n_fails >= 4:
            urgency = 0.95   # critical: physical+emotional+cognitive все провисают

        # Reason для UI — переводим первые 2 reason'а в человеческую строку
        reason_tags = ctx.rgk.project("capacity")["reasons"] or []
        from ..assistant import _capacity_reason_text
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
        from ..plans import schedule_for_day
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
        from ..recurring import list_lagging
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
        silence = float(ctx.rgk.silence_press)
        if silence < 0.3:   # SYNC_SEEKING_SILENCE_MIN
            return None

        last_input_ts = ctx.rgk._last_input_ts or 0.0
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
            hrv_surprise = float(ctx.rgk.hrv_surprise())
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
            from ..user_profile import load_profile
            wake = int((load_profile().get("context") or {}).get(
                "wake_hour", ctx.loop.DEFAULT_WAKE_HOUR))
        except Exception:
            wake = ctx.loop.DEFAULT_WAKE_HOUR
        retro_hour = min(23, wake + ctx.loop.EVENING_RETRO_HOUR_OFFSET)
        local_dt = _dt.datetime.now()
        if local_dt.hour < retro_hour:
            return None

        try:
            from ..plans import schedule_for_day
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

        from ..prompts import _p
        n_un = len(unfinished)
        if n_un == 0:
            text = _p("ru", "retro_all_done")
        elif n_un == 1:
            text = _p("ru", "retro_unfinished_one").format(n=n_un)
        else:
            text = _p("ru", "retro_unfinished_many").format(n=n_un)

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
                from ..assistant import _load_state
                persisted = float((_load_state().get("last_briefing_ts") or 0.0))
                if persisted > ctx.loop._last_briefing:
                    ctx.loop._last_briefing = persisted
            except Exception:
                pass
            ctx.loop._briefing_loaded_from_disk = True

        if ctx.now - ctx.loop._last_briefing < ctx.loop.BRIEFING_INTERVAL:
            return None

        try:
            from ..user_profile import load_profile
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
            from ..assistant import _load_state, _save_state
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
        last_ts = ctx.rgk._last_input_ts
        if last_ts and (ctx.now - last_ts) < 600:
            return []

        # Skip при высоком focus_residue — юзер в хаосе переключений, не
        # добавляем новых сигналов (Counter-wave: пауза вместо давления).
        if getattr(ctx.rgk, "focus_residue", 0.0) > 0.5:
            return []

        # Compute throttle daily
        if not ctx.loop._throttled("_last_suggestions_check",
                                    ctx.loop.SUGGESTIONS_CHECK_INTERVAL):
            return []

        from ..suggestions import collect_suggestions, make_suggestion_card
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
                    log.info("[detect_observation_suggestions] skipped empty draft")
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
        from ..state_graph import get_state_graph
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

        from ..api_backend import api_get_embedding
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


# ════════════════════════════════════════════════════════════════════════════
#  USER-SIDE SURPRISE DETECTION (отдельный API, не Signal-style)
# ════════════════════════════════════════════════════════════════════════════
#
# Момент когда **юзер** встретил неожиданное — не когда Baddle ошибся про
# юзера. Три источника сигнала:
#
#   A. HRV-based — RMSSD dropped significantly from rolling baseline.
#      Читает `sensor_stream.recent(KIND_HRV_SNAPSHOT)`. Требует реального
#      источника (Polar / симулятор должен быть запущен).
#
#   B. Text markers (regex) — «воу», «не ожидал», «??», многоточие, капс.
#      Работает всегда, но шумит на нетипичных формулировках.
#
#   C. LLM classify — light 1-number prompt в borderline-диапазоне (когда
#      regex score ∈ [0.15, 0.45]). Ловит иронию, сарказм, длинные фразы
#      без явных маркеров. С кэшем по SHA1 текста (как в sentiment.py).
#
# Combined check в `detect_user_surprise(text, activity, use_llm=True)` —
# OR всех сигналов. Caller (cognitive_loop._check_user_surprise) throttle'ит
# + записывает event + вызывает `user_state.apply_surprise_boost()`.
#
# Для подхода см. [docs/friston-loop.md](../docs/friston-loop.md) и OQ #7.


# ── HRV-based detector ──────────────────────────────────────────────────────

HRV_SHORT_WINDOW_S = 30.0      # текущее состояние (последние 30 сек)
HRV_BASELINE_WINDOW_S = 300.0  # baseline (последние 5 мин)
HRV_MIN_BASELINE_SAMPLES = 5   # без этого — невозможно std оценить
HRV_THRESHOLD_SIGMA = 1.5      # отклонение больше 1.5σ считается surprise
HRV_MIN_DROP_MS = 5.0          # минимальная абс разница (ignore шум при низком std)
HRV_ACTIVITY_THRESHOLD = 0.5   # выше — физнагрузка, игнорим (не surprise)


def detect_hrv_surprise(activity_magnitude: Optional[float] = None) -> dict:
    """RMSSD drop detection по rolling window.

    Алгоритм:
      1. Baseline: RMSSD readings за последние 5 мин → mean + std
      2. Current: latest RMSSD reading (≤30 сек от now)
      3. surprise = |current − baseline_mean| > max(1.5σ, 5ms)

    Args:
        activity_magnitude: если > HRV_ACTIVITY_THRESHOLD (физнагрузка) —
            forced no-surprise (HRV drop от бега не считается surprise).
            None → проверка пропускается.

    Returns:
        {
            "event": bool,
            "score": float,                # |Δ| / baseline_std (z-score-like)
            "latest_rmssd": float | None,
            "baseline_mean": float | None,
            "baseline_std": float | None,
            "reason": str,                 # "no_data" | "insufficient_baseline" |
                                            # "activity_filter" | "stable" |
                                            # "surprise_detected"
        }
    """
    try:
        from ..sensors.stream import get_stream, KIND_HRV_SNAPSHOT
    except Exception as e:
        return {"event": False, "reason": f"import_failed:{e}", "score": 0.0}

    # Activity gate
    if (activity_magnitude is not None
            and float(activity_magnitude) > HRV_ACTIVITY_THRESHOLD):
        return {
            "event": False, "reason": "activity_filter",
            "score": 0.0, "activity": float(activity_magnitude),
        }

    stream = get_stream()
    baseline_readings = stream.recent(
        kinds=[KIND_HRV_SNAPSHOT],
        since_seconds=HRV_BASELINE_WINDOW_S,
    )
    rmssds = [float(r.metrics["rmssd"])
              for r in baseline_readings
              if r.metrics and r.metrics.get("rmssd") is not None]
    if len(rmssds) < HRV_MIN_BASELINE_SAMPLES:
        return {
            "event": False, "reason": "insufficient_baseline",
            "samples": len(rmssds), "score": 0.0,
        }

    mean = sum(rmssds) / len(rmssds)
    variance = sum((x - mean) ** 2 for x in rmssds) / len(rmssds)
    std = math.sqrt(variance)

    # Latest — самое свежее чтение
    now = time.time()
    recent_short = [r for r in baseline_readings
                    if (now - r.ts) <= HRV_SHORT_WINDOW_S
                    and r.metrics
                    and r.metrics.get("rmssd") is not None]
    if not recent_short:
        return {
            "event": False, "reason": "no_recent_reading",
            "score": 0.0, "baseline_mean": round(mean, 2),
            "baseline_std": round(std, 2),
        }
    latest_rmssd = float(recent_short[-1].metrics["rmssd"])

    # Z-score-like (robust если std близка к 0 → защита)
    abs_delta = abs(latest_rmssd - mean)
    if std < 1.0:
        # Baseline почти плоский — можем ловить только большие абсолютные drop'ы
        # чтобы не триггерить на микро-шум.
        score = abs_delta / max(1.0, std)
        is_surprise = abs_delta >= HRV_MIN_DROP_MS * 2  # нужен более сильный сигнал
    else:
        score = abs_delta / std
        is_surprise = (score >= HRV_THRESHOLD_SIGMA
                       and abs_delta >= HRV_MIN_DROP_MS)

    return {
        "event": bool(is_surprise),
        "reason": "surprise_detected" if is_surprise else "stable",
        "score": round(score, 2),
        "latest_rmssd": round(latest_rmssd, 2),
        "baseline_mean": round(mean, 2),
        "baseline_std": round(std, 2),
        "delta_ms": round(latest_rmssd - mean, 2),  # signed
        "samples": len(rmssds),
    }


# ── Text-based detector ─────────────────────────────────────────────────────

# Lightweight regex markers. Покрывает ru + en + нейтральное.
# Score компоненты:
#   • сильные маркеры ("не ожидал", "wow") → +0.45
#   • средние маркеры ("странно", "really") → +0.30
#   • мягкие ("hmm", многоточие) → +0.15
#   • капс > 50% (min 4 букв) → +0.25
#   • '??' / '!!!' → +0.20
#   Сумма клампится в [0, 1].

STRONG_MARKERS = [
    r"\bне\s+ожидал",  r"\bне\s+ожидала",
    r"\bвот\s+это\s+да",
    r"\bохре",  r"\bохуе",
    r"\bнифига\s+себе",
    r"(?:^|\s)воу\b", r"(?:^|\s)вау\b",
    r"(?:^|\s)ого\b", r"(?:^|\s)ого-го",
    r"\bwow\b", r"\bwhoa\b",
    r"\bdidn'?t\s+expect", r"\bno\s+way\b",
    r"\bholy\s+(?:shit|moly|crap)",
]
MEDIUM_MARKERS = [
    r"\bстранно\b", r"\bинтересно\b", r"\bнеожид",
    r"\bсерь[её]зно\b",          # "серьёзно" | "серьезно" (обе формы)
    r"(?:^|\s)блин\b", r"(?:^|\s)хм+\b",
    r"\breally\?", r"\bseriously\?", r"\bwait\s+what",
    r"\bhuh\b", r"\bweird\b",
]
SOFT_MARKERS = [
    r"\.\.\.\.+",       # 4+ точек
    r"(?:^|\s)хм\b",    # hmm ru
    r"\bhmm\b",
    r"(?:^|\s)эм+\b",
]

_STRONG_RE = re.compile("|".join(STRONG_MARKERS), re.IGNORECASE)
_MEDIUM_RE = re.compile("|".join(MEDIUM_MARKERS), re.IGNORECASE)
_SOFT_RE = re.compile("|".join(SOFT_MARKERS), re.IGNORECASE)
_QUESTION_BURST = re.compile(r"\?{2,}")
_EXCLAIM_BURST = re.compile(r"!{3,}")

TEXT_SURPRISE_THRESHOLD = 0.35   # выше — event


def text_surprise_score(text: str) -> dict:
    """Regex + эвристики → score [0, 1] + breakdown.

    Без LLM на MVP — дёшево, работает оффлайн, предсказуемо. Если позже
    окажется что шумит — в `_check_user_surprise` добавим LLM classify на
    borderline (0.2–0.4) случаи.
    """
    if not text or not isinstance(text, str):
        return {"event": False, "score": 0.0, "markers": []}
    snippet = text.strip()
    if len(snippet) < 2:
        return {"event": False, "score": 0.0, "markers": []}

    score = 0.0
    markers: list = []

    strong_hits = _STRONG_RE.findall(snippet)
    if strong_hits:
        score += 0.45 + 0.05 * min(3, len(strong_hits) - 1)
        markers.append(f"strong({len(strong_hits)})")

    medium_hits = _MEDIUM_RE.findall(snippet)
    if medium_hits:
        score += 0.30 + 0.05 * min(2, len(medium_hits) - 1)
        markers.append(f"medium({len(medium_hits)})")

    soft_hits = _SOFT_RE.findall(snippet)
    if soft_hits:
        score += 0.15
        markers.append("soft")

    if _QUESTION_BURST.search(snippet):
        score += 0.25
        markers.append("??+")

    if _EXCLAIM_BURST.search(snippet):
        score += 0.25
        markers.append("!!!+")

    # Капс > 50% среди букв (min 4 буквы длинной)
    letters = [c for c in snippet if c.isalpha()]
    if len(letters) >= 4:
        upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
        if upper_ratio > 0.5:
            score += 0.25
            markers.append(f"caps({upper_ratio:.2f})")

    score = min(1.0, score)
    return {
        "event": score >= TEXT_SURPRISE_THRESHOLD,
        "score": round(score, 2),
        "markers": markers,
    }


# ── LLM-based detector (fallback для borderline regex scores) ──────────────

# LLM trigger: regex ≥ LLM_BORDERLINE_HIGH → уверенный regex, скипаем LLM.
# Regex ниже → LLM решает. Плюс min-length guard: очень короткие сообщения
# («ok», «да», «нет») — не зовём LLM даже если regex = 0.
LLM_BORDERLINE_HIGH = 0.45     # выше — regex уверенно triggered
LLM_MIN_TEXT_LEN = 15          # короче — только regex (не тратим LLM на «ok»)
LLM_SURPRISE_THRESHOLD = 0.5   # LLM score ≥ 0.5 → event

# Cache hash(text) → LLM score. Параллельный sentiment'у.
_llm_cache: dict[str, float] = {}
_LLM_CACHE_MAX = 500


def _text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _trim_llm_cache():
    if len(_llm_cache) > _LLM_CACHE_MAX:
        items = list(_llm_cache.items())[-(_LLM_CACHE_MAX // 2):]
        _llm_cache.clear()
        _llm_cache.update(items)


def llm_surprise_score(text: str) -> float:
    """LLM-classify: вернуть surprise score в [0, 1].

    Ловит случаи которые regex не покрывает:
      - Длинные описательные сообщения без ярких маркеров
      - Ирония / сарказм («ага, конечно, это я не ожидал»)
      - Нетипичные формулировки («зашквар», «я в шоке», «серьёзно что ли»)

    Light LLM call (max_tokens=6, temp=0). Cache по SHA1. При ошибке → 0.0.
    Пустой / короткий текст (<3 символов) → 0.0 без LLM.
    """
    if not text or len(text.strip()) < 3:
        return 0.0
    h = _text_hash(text)
    if h in _llm_cache:
        return _llm_cache[h]

    try:
        from ..graph_logic import _graph_generate
        system = (
            "/no_think\n"
            "Return ONE number between 0.0 and 1.0 — how strongly the user "
            "expresses surprise / unexpectedness in this message.\n"
            "0.0 = neutral, factual, expected tone\n"
            "0.3 = mild surprise or curiosity (hmm, interesting)\n"
            "0.7 = clear surprise (wow, didn't expect, really?)\n"
            "1.0 = strong shock (no way, holy, что?!)\n"
            "NO explanation. NO prefix. JUST the number."
        )
        res, _ent = _graph_generate(
            [{"role": "system", "content": system},
             {"role": "user", "content": text[:400]}],
            max_tokens=6, temp=0.0, top_k=1,
        )
        if res:
            cleaned = res.strip().strip(' ".,\n`')
            try:
                score = float(cleaned)
            except ValueError:
                score = 0.0
                for p in cleaned.split():
                    p2 = p.strip(' .,;:"`')
                    try:
                        score = float(p2)
                        break
                    except ValueError:
                        continue
            score = max(0.0, min(1.0, score))
            _llm_cache[h] = score
            _trim_llm_cache()
            return score
    except Exception as e:
        log.debug(f"[surprise_detector] LLM classify failed for {text[:30]!r}: {e}")

    _llm_cache[h] = 0.0
    return 0.0


def clear_llm_cache():
    """Очистка cache — вызывается при /reset эндпоинтах."""
    _llm_cache.clear()


# ── Combined detector ──────────────────────────────────────────────────────

def detect_user_surprise(text: Optional[str] = None,
                          activity_magnitude: Optional[float] = None,
                          use_llm: bool = True) -> dict:
    """Combined HRV + text + (optional) LLM check. OR signals.

    Args:
        text: последнее user-сообщение (для B + C-каналов). None → только HRV.
        activity_magnitude: текущий activity (для HRV activity gate).
        use_llm: если True И regex score в borderline [0.15, 0.45] — зовём
            LLM classifier. LLM score ≥ 0.5 считается event. Ставим False
            для tests без LLM инфраструктуры.

    Returns:
        {
            "event": bool,
            "source": "hrv" | "text" | "llm" | "both" | "triple" | None,
            "confidence": float,     # оценка уверенности [0, 1]
            "hrv":  {...}  (из detect_hrv_surprise),
            "text": {...} (из text_surprise_score),
            "llm":  {score, used}  — если LLM вызывался
        }

    `source=None` если ни один не сработал. `confidence` = max из всех
    нормализованных каналов — не probability, просто relative strength.
    """
    hrv = detect_hrv_surprise(activity_magnitude=activity_magnitude)
    txt = (text_surprise_score(text or "") if text
           else {"event": False, "score": 0.0, "markers": []})

    # LLM fallback когда regex не уверен. Экономия: confident regex (≥0.45)
    # skip'аем; короткие сообщения (<15 симв.) тоже — регексы их покрывают.
    llm_info = {"used": False, "score": 0.0}
    txt_score = float(txt.get("score", 0.0))
    if (use_llm and text
            and len(text.strip()) >= LLM_MIN_TEXT_LEN
            and txt_score < LLM_BORDERLINE_HIGH):
        llm_score = llm_surprise_score(text)
        llm_info = {"used": True, "score": round(llm_score, 2)}

    hrv_event = bool(hrv.get("event"))
    txt_event = bool(txt.get("event"))
    llm_event = llm_info["used"] and llm_info["score"] >= LLM_SURPRISE_THRESHOLD

    if not (hrv_event or txt_event or llm_event):
        return {
            "event": False, "source": None, "confidence": 0.0,
            "hrv": hrv, "text": txt, "llm": llm_info,
        }

    # source label — учитываем сколько сигналов сработало
    active = []
    if hrv_event: active.append("hrv")
    if txt_event: active.append("text")
    if llm_event: active.append("llm")
    if len(active) == 3:
        source = "triple"
    elif len(active) == 2:
        source = "both"
    else:
        source = active[0]

    hrv_conf = min(1.0, float(hrv.get("score", 0.0)) / 3.0)
    txt_conf = txt_score
    llm_conf = float(llm_info.get("score", 0.0)) if llm_info["used"] else 0.0
    confidence = max(hrv_conf, txt_conf, llm_conf)

    return {
        "event": True,
        "source": source,
        "confidence": round(confidence, 2),
        "hrv": hrv, "text": txt, "llm": llm_info,
    }
