"""Chat commands — управление Baddle через естественный текст в чате.

Идея: юзер пишет «как я?» / «запусти код» / «что сегодня?» / «сколько я ел
за неделю» — и Baddle не зовёт LLM, а локально выполняет команду, возвращая
structured response.

Prefilter перед classify_intent_llm в assistant._assist. Если паттерн
матчится — возвращаем ответ немедленно, экономя LLM-вызов и давая
детерминированный UX.

Команды:
  status      — «как я» / «состояние» / «статус» / «how am I»
  start X     — «запусти X» / «начни X» / «start X»
  stop        — «стоп» / «останови» / «stop»
  next X      — «следующая X» — переключение activity
  plan_today  — «план» / «что сегодня» / «что у меня»
  food_week   — «что я ел» (с опциональным «вчера» / «за неделю»)
  checkin     — «check-in» / «проверка»

Возвращает dict с теми же полями что и `/assist`: text, cards, mode,
mode_name, energy, hrv, steps, graph_updated и т.д.
"""
from __future__ import annotations
import re
import time
from typing import Optional


# ── Command patterns (regex — compiled lazy) ───────────────────────────

_STATUS_PATTERNS = (
    r"^как\s+я\b",
    r"^как\s+дела\b",
    r"^\s*(моё?|моего|мой)?\s*состояни[ея]\b",
    r"^статус\b",
    r"^\s*status\b",
    r"^how\s+am\s+i\b",
    r"^state\b",
)

_START_PATTERNS = (
    r"^запусти\s+(.+)$",
    r"^начни\s+(.+)$",
    r"^старт\s+(.+)$",
    r"^таймер\s+(.+)$",
    r"^start\s+(.+)$",
)

_STOP_PATTERNS = (
    r"^стоп\s*(задач[ау])?\s*$",
    r"^останов\w*\s*$",
    r"^\s*stop\s*$",
    r"^выключи\s+таймер\s*$",
)

_NEXT_PATTERNS = (
    r"^следующ\w+\s+(.+)$",
    r"^переключи\s+на\s+(.+)$",
    r"^next\s+(.+)$",
)

_PLAN_TODAY_PATTERNS = (
    r"^план\s*(на\s+сегодня)?\s*$",
    r"^что\s+(у\s+меня\s+)?сегодня\s*\?*$",
    r"^расписание\s*$",
    r"^schedule\s*$",
    r"^today'?s?\s+plan\s*$",
)

_FOOD_PATTERNS = (
    r"^что\s+я\s+ел\b",
    r"^что\s+ел\s+",
    r"^(покажи\s+)?(еду|еда|мо[её]\s+питани[ея])\b",
    r"^food\s+(week|yesterday|today)\b",
)

_CHECKIN_PATTERNS = (
    r"^check-?in\s*$",
    r"^чек-?ин\s*$",
    r"^проверка\s*$",
    r"^self-?report\s*$",
)

_HELP_PATTERNS = (
    r"^(что\s+ты\s+умеешь|команды|help|\?+)\s*$",
)


def _match_any(message: str, patterns) -> Optional[re.Match]:
    low = message.lower().strip()
    for p in patterns:
        m = re.match(p, low, flags=re.IGNORECASE)
        if m:
            return m
    return None


# ── Command handlers ───────────────────────────────────────────────────

def _card_status(lang: str = "ru") -> dict:
    """Текущее состояние юзера одной карточкой."""
    from .horizon import get_global_state
    from .hrv_manager import get_manager as get_hrv_mgr
    from .activity_log import get_active, day_summary
    from .checkins import latest_checkin
    from .plans import schedule_for_day
    from .goals_store import list_goals

    cs = get_global_state()
    m = cs.get_metrics()
    user = m.get("user_state") or {}
    neuro = m.get("neurochem") or {}

    sections = []
    # Energy / reserve — to_dict() не включает long_reserve_pct,
    # считаем локально из абсолютного значения.
    lr = user.get("long_reserve")
    LONG_MAX = 2000.0
    lr_pct = (float(lr) / LONG_MAX) if lr is not None else None
    if lr is not None:
        sections.append({
            "emoji": "🔋", "title": f"Резерв {int((lr_pct or 0)*100)}%",
            "subtitle": f"{int(lr)}/{int(LONG_MAX)}",
            "kind": "info" if (lr_pct or 0) > 0.6 else "warn",
        })
    # Named state
    ns = user.get("named_state") or {}
    if ns.get("label"):
        sections.append({
            "emoji": "🎭", "title": f"{ns.get('label')}",
            "subtitle": ns.get("advice") or "",
            "kind": "neutral",
        })
    # Sync regime
    regime = m.get("sync_regime")
    sync_err = m.get("sync_error")
    if regime:
        kind = "info" if regime == "flow" else ("warn" if regime in ("stress", "protect") else "neutral")
        sections.append({
            "emoji": "⚡", "title": f"Симбиоз: {regime.upper()}",
            "subtitle": f"sync_error {sync_err:.2f}" if sync_err is not None else "",
            "kind": kind,
        })
    # Neuro scalars
    sections.append({
        "emoji": "🧬", "title": "Нейрохимия",
        "subtitle": (f"DA {neuro.get('dopamine', 0):.2f} · "
                     f"S {neuro.get('serotonin', 0):.2f} · "
                     f"NE {neuro.get('norepinephrine', 0):.2f} · "
                     f"burnout {neuro.get('burnout', 0):.2f}"),
        "kind": "neutral",
    })
    # Active activity
    act = get_active()
    if act:
        elapsed = int(time.time() - float(act.get("started_at") or 0)) // 60
        sections.append({
            "emoji": "🎯", "title": f"Сейчас: {act.get('name')}",
            "subtitle": f"категория: {act.get('category') or '—'} · {elapsed} мин",
            "kind": "highlight",
        })
    else:
        today = day_summary()
        sections.append({
            "emoji": "💤", "title": "Активной задачи нет",
            "subtitle": f"Сегодня {today.get('total_tracked_h', 0)}ч · "
                        f"{today.get('activity_count', 0)} задач",
            "kind": "neutral",
        })
    # Plans today
    sched = schedule_for_day()
    if sched:
        todo = sum(1 for s in sched if not s.get("done") and not s.get("skipped"))
        sections.append({
            "emoji": "📋", "title": f"План: {todo}/{len(sched)} осталось",
            "subtitle": "Открой «Задачи» чтобы увидеть полный список",
            "kind": "info" if todo == 0 else "neutral",
            "actions": [{"label": "Открыть", "action": "open_plan"}],
        })
    # Latest check-in
    ci = latest_checkin(hours=24)
    if ci:
        hrs = int((time.time() - float(ci.get("ts") or 0)) / 3600)
        parts = []
        for k in ("energy", "focus", "stress"):
            if ci.get(k) is not None:
                parts.append(f"{k[0].upper()}{int(ci[k])}")
        sections.append({
            "emoji": "📝", "title": f"Check-in {hrs}ч назад",
            "subtitle": " · ".join(parts) if parts else "—",
            "kind": "info",
        })
    else:
        sections.append({
            "emoji": "📝", "title": "Check-in не было",
            "subtitle": "Открой чтобы зарегистрировать subjective state",
            "kind": "warn",
            "actions": [{"label": "Сделать check-in", "action": "open_checkin"}],
        })

    intro = "Вот что я о тебе знаю прямо сейчас:" if lang == "ru" else "Here's what I know about you right now:"
    return {
        "text": intro,
        "intro": intro,
        "mode": "free",
        "mode_name": "Состояние",
        "cards": [{
            "type": "status_briefing",
            "sections": sections,
        }],
        "steps": [],
        "awaiting_input": False,
        "graph_updated": False,
        "chat_command": "status",
    }


def _card_start(name: str, lang: str = "ru") -> dict:
    """Запустить activity."""
    from .activity_log import start_activity, detect_category
    from .workspace import get_workspace_manager
    try:
        ws_id = get_workspace_manager().active_id or "main"
    except Exception:
        ws_id = "main"
    name = name.strip().strip(".").strip('"')[:200]
    if not name:
        return _card_error("name_required", lang)
    cat = detect_category(name)
    try:
        aid = start_activity(name=name, category=cat, workspace=ws_id)
    except Exception as e:
        return _card_error(str(e), lang)
    # Graph-sync delegated to main endpoint; тут просто подтверждение
    text = (f"▶ Запустил: {name}" + (f" ({cat})" if cat else "")) if lang == "ru" \
           else f"▶ Started: {name}" + (f" ({cat})" if cat else "")
    return {
        "text": text, "intro": text,
        "mode": "free",
        "mode_name": "Activity",
        "cards": [{
            "type": "activity_action",
            "action": "started",
            "activity_id": aid,
            "name": name, "category": cat,
        }],
        "steps": [],
        "awaiting_input": False,
        "graph_updated": False,
        "chat_command": "start",
    }


def _card_stop(lang: str = "ru") -> dict:
    from .activity_log import stop_activity, get_active
    cur = get_active()
    if not cur:
        text = "Нет активной задачи." if lang == "ru" else "No active task."
        return {"text": text, "intro": text, "mode": "free", "mode_name": "—",
                "cards": [], "steps": [], "awaiting_input": False,
                "graph_updated": False, "chat_command": "stop"}
    rec = stop_activity(reason="manual")
    dur_min = int((rec.get("duration_s") or 0) / 60) if rec else 0
    text = (f"⏹ Остановил «{cur.get('name')}» · {dur_min} мин") if lang == "ru" \
           else f"⏹ Stopped «{cur.get('name')}» · {dur_min} min"
    return {"text": text, "intro": text, "mode": "free", "mode_name": "Activity",
            "cards": [{
                "type": "activity_action", "action": "stopped",
                "activity_id": cur.get("id"), "duration_min": dur_min,
            }],
            "steps": [], "awaiting_input": False, "graph_updated": False,
            "chat_command": "stop"}


def _card_plan_today(lang: str = "ru") -> dict:
    from .plans import schedule_for_day
    import datetime as _dt
    sched = schedule_for_day()
    if not sched:
        text = "На сегодня ничего не запланировано." if lang == "ru" \
               else "Nothing planned for today."
        # Вместо «Открой Задачи → +Добавить» в text — actionable карточка
        # с кнопкой. Клик переключает на sub-page Задачи и раскрывает
        # форму добавления (см. briefingOpenPlan в assistant.js).
        sections = [{
            "emoji": "📋",
            "title": "Ничего не запланировано" if lang == "ru" else "Nothing planned",
            "subtitle": "Добавь событие на сегодня" if lang == "ru"
                        else "Add an event for today",
            "kind": "neutral",
            "actions": [{"label": "＋ Добавить событие" if lang == "ru"
                         else "＋ Add event",
                         "action": "open_plan"}],
        }]
        return {"text": text, "intro": text, "mode": "free", "mode_name": "План",
                "cards": [{"type": "status_briefing", "sections": sections}],
                "steps": [], "awaiting_input": False,
                "graph_updated": False, "chat_command": "plan_today"}
    sections = []
    for it in sched:
        t = _dt.datetime.fromtimestamp(it.get("planned_ts") or 0).strftime("%H:%M")
        kind = "info" if it.get("done") else ("warn" if it.get("skipped") else "neutral")
        emoji = "✓" if it.get("done") else ("✕" if it.get("skipped") else "◻")
        streak_s = ""
        if it.get("kind") == "recurring" and (it.get("streak") or 0) > 0:
            streak_s = f" · 🔥{it['streak']}"
        sections.append({
            "emoji": emoji, "title": f"{t}  {it.get('name', '')}{streak_s}",
            "subtitle": it.get("category") or "",
            "kind": kind,
        })
    text = f"Сегодня запланировано {len(sched)} событий:" if lang == "ru" \
           else f"Today: {len(sched)} events"
    return {"text": text, "intro": text, "mode": "free", "mode_name": "План",
            "cards": [{"type": "status_briefing", "sections": sections}],
            "steps": [], "awaiting_input": False, "graph_updated": False,
            "chat_command": "plan_today"}


def _card_food_history(message: str, lang: str = "ru") -> dict:
    """Filter activity history by category=food. По умолчанию 7 дней."""
    from .activity_log import list_activities
    low = message.lower()
    days = 7
    if "вчера" in low or "yesterday" in low:
        days = 2
    elif "сегодня" in low or "today" in low:
        days = 1
    elif "месяц" in low or "month" in low:
        days = 30
    elif "недел" in low or "week" in low:
        days = 7
    since = time.time() - days * 86400
    acts = [a for a in list_activities(since_ts=since, limit=200)
            if a.get("category") == "food"]
    if not acts:
        text = f"За последние {days} дней я не вижу записей о еде. Трекай категорию food в «Задачи»." if lang == "ru" \
               else f"No food entries in the last {days} days."
        return {"text": text, "intro": text, "mode": "free", "mode_name": "История",
                "cards": [], "steps": [], "awaiting_input": False,
                "graph_updated": False, "chat_command": "food_history"}
    # Агрегат по имени
    by_name: dict = {}
    for a in acts:
        n = (a.get("name") or "").strip() or "(без имени)"
        by_name[n] = by_name.get(n, 0) + 1
    top = sorted(by_name.items(), key=lambda kv: kv[1], reverse=True)[:10]
    sections = [
        {"emoji": "🍽", "title": n, "subtitle": f"×{c}", "kind": "neutral"}
        for n, c in top
    ]
    total = sum(by_name.values())
    unique = len(by_name)
    text = (f"За {days} дней: {total} записей, {unique} уникальных блюд:") if lang == "ru" \
           else f"Last {days} days: {total} entries, {unique} unique items"
    return {"text": text, "intro": text, "mode": "free", "mode_name": "Еда",
            "cards": [{"type": "status_briefing", "sections": sections}],
            "steps": [], "awaiting_input": False, "graph_updated": False,
            "chat_command": "food_history"}


def _card_checkin_prompt(lang: str = "ru") -> dict:
    text = "Открываю check-in. Поставь слайдеры как чувствуешь сейчас." if lang == "ru" \
           else "Opening check-in."
    return {"text": text, "intro": text, "mode": "free", "mode_name": "Check-in",
            "cards": [{"type": "open_modal", "modal": "checkin"}],
            "steps": [], "awaiting_input": False, "graph_updated": False,
            "chat_command": "checkin"}


def _card_help(lang: str = "ru") -> dict:
    items = [
        ("как я?", "текущее состояние: резерв, нейрохимия, активная задача, план"),
        ("запусти <название>", "старт activity-таймера"),
        ("стоп", "остановить текущую задачу"),
        ("следующая <название>", "переключиться на новую задачу"),
        ("план / что сегодня", "расписание на сегодня"),
        ("что я ел / за неделю", "история food-активностей"),
        ("check-in", "открыть форму subjective ввода"),
    ]
    sections = [
        {"emoji": "›", "title": cmd, "subtitle": desc, "kind": "neutral"}
        for cmd, desc in items
    ]
    text = "Команды, которые я понимаю без LLM:" if lang == "ru" \
           else "Commands I understand without LLM:"
    return {"text": text, "intro": text, "mode": "free", "mode_name": "Команды",
            "cards": [{"type": "status_briefing", "sections": sections}],
            "steps": [], "awaiting_input": False, "graph_updated": False,
            "chat_command": "help"}


def _card_error(err: str, lang: str = "ru") -> dict:
    text = f"⚠ {err}"
    return {"text": text, "intro": text, "mode": "free", "mode_name": "Ошибка",
            "cards": [], "steps": [], "awaiting_input": False,
            "graph_updated": False, "chat_command": "error",
            "error": err}


# ── Main dispatcher ────────────────────────────────────────────────────

def try_handle(message: str, lang: str = "ru") -> Optional[dict]:
    """Префильтр: если message матчит команду — вернуть response. Иначе None
    (тогда assistant продолжит с classify_intent_llm).
    """
    if not message:
        return None
    m = message.strip()
    if _match_any(m, _HELP_PATTERNS):
        return _card_help(lang)
    if _match_any(m, _STATUS_PATTERNS):
        return _card_status(lang)
    if _match_any(m, _STOP_PATTERNS):
        return _card_stop(lang)
    if _match_any(m, _CHECKIN_PATTERNS):
        return _card_checkin_prompt(lang)
    if _match_any(m, _PLAN_TODAY_PATTERNS):
        return _card_plan_today(lang)
    if _match_any(m, _FOOD_PATTERNS):
        return _card_food_history(m, lang)
    # Start/next — с группой (имя задачи)
    res = _match_any(m, _START_PATTERNS)
    if res:
        return _card_start(res.group(1), lang)
    res = _match_any(m, _NEXT_PATTERNS)
    if res:
        # «следующая» = stop current + start new
        from .activity_log import get_active
        cur = get_active()
        # start_activity уже автостопает текущую с reason=switch
        return _card_start(res.group(1), lang)
    return None
