"""Baddle Assistant — chat-first interface.

One endpoint turns user messages into graph operations.
User sees conversation. Baddle runs the graph underneath.
"""
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Optional, Dict

from flask import Blueprint, request, jsonify

log = logging.getLogger(__name__)

from .modes import get_mode
from .sensors.manager import get_manager as get_hrv_manager
from .process.cognitive_loop import get_cognitive_loop
from .assistant_exec import execute as execute_mode


# Phase C Шаг 6: _MODE_COST / _decision_cost удалены — энергоёмкость per-mode
# больше не используется как gate. Decision-gate теперь идёт через 3-zone
# capacity (capacity_zone), cost per activity снимается через activity_log
# duration × category rate (CATEGORY_ENERGY_COST_PER_MIN в activity_log.py)
# и питает cognitive_load_today.


# ── Category detection (lightweight keyword-first) ─────────────────────
# Категория используется для инжекции profile.preferences/constraints
# в LLM-промпты. Keyword match — быстрый фолбэк, можно расширить LLM-classify.

_CATEGORY_KEYWORDS = {
    "food": ("еда", "кушать", "поесть", "завтрак", "обед", "ужин", "блюдо",
             "готовить", "food", "meal", "eat", "breakfast", "lunch", "dinner"),
    "work": ("работа", "работе", "проект", "дедлайн", "задач", "встреч", "код",
             "митинг", "work", "project", "meeting", "deadline", "code"),
    "health": ("здоровье", "здоров", "сон", "тренировк", "зарядк", "спорт",
               "устал", "бег", "health", "sleep", "exercise", "gym", "tired"),
    "social": ("друг", "семь", "подруг", "партнёр", "родител", "дети",
               "friend", "family", "partner", "parent"),
    "learning": ("учит", "курс", "книг", "статью", "изучит", "выучить",
                 "study", "book", "learn", "course", "article"),
}


def _detect_category(message: str) -> Optional[str]:
    """Keyword-based category detection. Returns None если ничего не подошло."""
    if not message:
        return None
    lower = message.lower()
    for cat, kws in _CATEGORY_KEYWORDS.items():
        for kw in kws:
            if kw in lower:
                return cat
    return None

assistant_bp = Blueprint("assistant", __name__)


# ── Energy / decisions store ────────────────────────────────────────────

from .paths import USER_STATE_FILE as _STATE_FILE

# In-process lock сериализует load↔save между параллельными Flask-threads.
# Устраняет race: thread A читает dump, thread B читает тот же dump,
# оба пишут обратно свои версии → чекмарк теряется. Atomic write через
# temp + replace даёт файловую консистентность; lock — семантическую.
import threading as _threading
_state_lock = _threading.RLock()

_user_state_restored = False   # guard — restore from disk ОДИН раз при первой загрузке


def _load_state() -> dict:
    global _user_state_restored
    with _state_lock:
        if _STATE_FILE.exists():
            try:
                data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
            except Exception:
                data = None
        else:
            data = None
        if not data:
            data = {
                "decisions_today": 0,
                "last_reset_date": None,
                "last_interaction": None,
                "total_decisions": 0,
                "streaks": {},       # habit_name → consecutive_days
                "history": [],       # last 100 interactions (trimmed)
            }
        # Восстановим user-side РГК ТОЛЬКО ОДИН раз за процесс.
        if not _user_state_restored:
            try:
                from .substrate.rgk import get_global_rgk
                us_dump = data.get("user_state_dump")
                if isinstance(us_dump, dict):
                    get_global_rgk().load_user(us_dump)
            except Exception as e:
                print(f"[assistant] user_state restore error: {e}")
            _user_state_restored = True
        return data


def _save_state(state: dict):
    with _state_lock:
        # Сериализуем текущий UserState вместе с остальным для continuity
        try:
            from .substrate.rgk import get_global_rgk
            state["user_state_dump"] = get_global_rgk().serialize_user()
        except Exception:
            pass
        try:
            # Atomic write: temp file → replace, чтобы half-written файл
            # не мог прочитать параллельный reader.
            tmp = _STATE_FILE.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False),
                           encoding="utf-8")
            tmp.replace(_STATE_FILE)
        except Exception as e:
            print(f"[assistant] state save error: {e}")


def _today_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")


_CAPACITY_REASON_RU = {
    "hrv_coherence_low": "когерентность HRV низкая",
    "burnout_high":      "выгорание высокое",
    "serotonin_low":     "серотонин низкий",
    "dopamine_low":      "мотивация просела",
    "cogload_high":      "когнитивная нагрузка высокая",
}
_CAPACITY_REASON_EN = {
    "hrv_coherence_low": "HRV coherence low",
    "burnout_high":      "burnout high",
    "serotonin_low":     "serotonin low",
    "dopamine_low":      "motivation low",
    "cogload_high":      "cognitive load high",
}


def _capacity_reason_text(reasons: list, lang: str = "ru") -> str:
    """Перевод [hrv_coherence_low, cogload_high] → 'когерентность HRV низкая,
    когнитивная нагрузка высокая'. Используется в decision-gate explanation."""
    table = _CAPACITY_REASON_RU if lang == "ru" else _CAPACITY_REASON_EN
    parts = [table.get(r, r) for r in (reasons or [])]
    if not parts:
        return "общая нагрузка высокая" if lang == "ru" else "load is high"
    return ", ".join(parts)


def _ensure_daily_reset(state: dict) -> dict:
    """Reset daily counters if date changed. Phase C: вызывает
    `UserState.rollover_day(hrv_recovery)` — он:
      • Persist'ит yesterday's cognitive_load в day_summary
      • Snapshot'ит sync_error_at_dawn для today (для progress_delta)
      • Reset'ит cognitive_load_today

    Idempotent через date-gate на state["last_reset_date"]."""
    today = _today_date()
    if state.get("last_reset_date") != today:
        prev_date = state.get("last_reset_date")
        state["decisions_today"] = 0
        state["last_reset_date"] = today
        if prev_date:
            try:
                from .substrate.rgk import get_global_rgk
                from .user_dynamics import rollover_day
                hrv_mgr = get_hrv_manager()
                rec = None
                if hrv_mgr.is_running:
                    rec = (hrv_mgr.get_baddle_state() or {}).get("energy_recovery")
                rollover_day(get_global_rgk(), hrv_recovery=rec)
            except Exception as e:
                print(f"[assistant] overnight rollover error: {e}")
    return state


def _log_decision(state: dict, kind: str, meta: dict = None, mode_id: str = None,
                  hrv_recovery: Optional[float] = None):
    """Record decision history + increment decisions_today counter.

    Phase C: dual-pool debit removed. Cost per mode (`_MODE_COST`) удалён —
    burnout EMA теперь питается через `update_from_energy(decisions_today)`,
    а decision-gate идёт через `capacity_zone` (см. docs/capacity-design.md).
    """
    state["decisions_today"] = state.get("decisions_today", 0) + 1
    state["total_decisions"] = state.get("total_decisions", 0) + 1
    state["last_interaction"] = time.time()

    entry = {"ts": time.time(), "kind": kind}
    if meta:
        entry.update(meta)
    state.setdefault("history", []).append(entry)
    if len(state["history"]) > 200:
        state["history"] = state["history"][-200:]


# ── Shared context helper (state + HRV + energy) ──────────────────────

def _get_context(reset_daily: bool = True) -> Dict:
    """Load user state + HRV snapshot + capacity (Phase C 3-zone gate).

    Returns:
      {
        "state": dict (loaded user_state.json, daily-reset applied),
        "hrv": dict | None (baddle_state or None if HRV off),
        "capacity": dict {zone, reason[], phys_ok, affect_ok, cogload_ok,
                          cognitive_load_today} — primary decision gate,
      }
    """
    state = _load_state()
    if reset_daily:
        state = _ensure_daily_reset(state)

    hrv_mgr = get_hrv_manager()
    hrv_state = hrv_mgr.get_baddle_state() if hrv_mgr.is_running else None

    # Capacity — Phase C decision-gate model (3-zone)
    from .substrate.rgk import get_global_rgk
    r = get_global_rgk()
    indicators = r.project("capacity")
    capacity = {
        "zone": indicators["zone"],
        "reason": indicators["reasons"],
        "phys_ok": indicators["phys_ok"],
        "affect_ok": indicators["affect_ok"],
        "cogload_ok": indicators["cogload_ok"],
        "cognitive_load_today": round(float(r.cognitive_load_today), 3),
    }

    return {"state": state, "hrv": hrv_state, "capacity": capacity}


# ── Mode → user-facing response templates ──────────────────────────────

def _response_for_mode(mode_id: str, message: str, lang: str = "ru") -> Dict:
    """Immediate confirmation — data-driven from modes.py."""
    mode = get_mode(mode_id)
    name = mode.get("name", mode_id) if lang == "ru" else mode.get("name_en", mode_id)
    intro_key = "intro" if lang == "ru" else "intro_en"
    intro = mode.get(intro_key) or mode.get("intro") or "..."
    return {"mode": mode_id, "mode_name": name, "intro": intro}


# ── Classify cache (TTL + LRU) ───────────────────────────────────────
# Один и тот же message после reload/retry не должен дёргать LLM повторно.
# Ключ — нормализованный (message, lang). TTL короткий (5 мин) чтобы:
#   • ретраи/refresh в течение сессии не жрут токены
#   • после дня настроение юзера меняется → перекласифицирует на свежую
# Кешируем ТОЛЬКО реальные LLM-результаты (source="llm"). Failures и
# defaults не кешируются — чтобы LLM восстановившись из даун'а сразу
# начал работать.

_CLASSIFY_CACHE: dict = {}
_CLASSIFY_CACHE_MAX = 100
_CLASSIFY_CACHE_TTL = 300  # seconds


def _classify_cache_key(message: str, lang: str) -> tuple:
    return (message.strip().lower()[:300], lang)


def _classify_cache_get(key: tuple):
    entry = _CLASSIFY_CACHE.get(key)
    if not entry:
        return None
    expires, result = entry
    if time.time() > expires:
        _CLASSIFY_CACHE.pop(key, None)
        return None
    return dict(result)   # copy чтобы caller не мутировал


def _classify_cache_put(key: tuple, result: dict):
    if len(_CLASSIFY_CACHE) >= _CLASSIFY_CACHE_MAX:
        # Dict preserves insertion order — oldest first
        oldest = next(iter(_CLASSIFY_CACHE))
        _CLASSIFY_CACHE.pop(oldest, None)
    _CLASSIFY_CACHE[key] = (time.time() + _CLASSIFY_CACHE_TTL, dict(result))


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
                        profile_hint: str = "", lang: str = "ru") -> dict:
    """Один LLM-вызов: mode + intent + confidence. Заменяет detect_mode+detect_intent.

    LLM получает:
      - текущее сообщение
      - краткий контекст (последние turns, опционально)
      - state_hint (текущее состояние CognitiveState — если система устала,
        юзер давно молчит, sync_error растёт — это влияет на интерпретацию)
      - profile_hint (preferences/constraints из user_profile в релевантной
        категории — влияет на mode-selection, напр. «не ем орехи» может
        склонить к tournament вместо fan)

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

    # Cache: проверяем идентичный message+lang до похода в LLM
    cache_key = _classify_cache_key(message, lang)
    cached = _classify_cache_get(cache_key)
    if cached is not None:
        cached["source"] = "cache"
        return cached

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
    if profile_hint:
        user += f"\n{profile_hint[:300]}"

    try:
        result, _ = _graph_generate(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            max_tokens=80, temp=0.2, top_k=10,
        )
        parsed = _parse_classify_output(result)
        if parsed:
            parsed["source"] = "llm"
            _classify_cache_put(cache_key, parsed)
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


# ── Router fast-paths ──────────────────────────────────────────────────
# Четыре короткие ветки `router_intent`, которые отвечают сразу, не ходя
# в execute_mode. Каждая возвращает полный response-dict или None (нет
# применимости — идём к execute_mode). Общий envelope + list-based dispatch
# убирают 4 параллельных `if ... try ... jsonify({...}) except ...` блока
# из assist(): было ~200 строк, стало ~115 + один вызов `_try_fastpath`.

def _fastpath_envelope(*, text, mode, mode_name, cards, message,
                       router_intent, lang, **extra) -> Dict:
    """Response-dict одинаковой формы для всех fastpath-ов.

    Экстра-поля (например `awaiting_input`) — через kwargs.
    """
    ctx_e = _get_context()
    resp = {
        "text": text,
        "mode": mode,
        "mode_name": mode_name,
        "message_echo": message,
        "cards": cards,
        "steps": [],
        "capacity": ctx_e.get("capacity"),
        "hrv": ctx_e.get("hrv"),
        "intent_router": router_intent,
        "lang": lang,
        "graph_updated": False,
        "api_offline": False,
        "warnings": [],
    }
    resp.update(extra)
    return resp


def _fastpath_activity(router_intent: dict, message: str, lang: str):
    """fact/activity — «начал тренировку» / «пошёл гулять».

    Запускаем taskplayer-трекер + матчинг recurring-цели (симметрично
    с /activity/start).
    """
    from .intent_router import extract_activity_name
    from .activity_log import (start_activity, try_match_recurring_instance,
                               detect_category)
    act_name = extract_activity_name(message, lang=lang)
    if not act_name:
        return None
    category = detect_category(act_name)
    aid = start_activity(name=act_name, category=category)
    matched_rec = None
    try:
        matched_rec = try_match_recurring_instance(
            activity_name=act_name, activity_category=category, lang=lang,
        )
    except Exception:
        pass
    reply_parts = [f"🎬 Запустил трекер: «{act_name}»"
                   + (f" ({category})" if category else "")]
    if matched_rec:
        p = matched_rec.get("progress") or {}
        reply_parts.append(
            f"♻✓ Засчитано в «{matched_rec['goal_text']}» — "
            f"{p.get('done_today', 0)}/{p.get('times_per_day', 0)}"
        )
    return _fastpath_envelope(
        text="\n".join(reply_parts),
        mode="activity_started", mode_name="Запустил",
        cards=[{
            "type": "activity_started",
            "activity_id": aid,
            "activity_name": act_name,
            "category": category,
            "matched_recurring": matched_rec,
        }],
        message=message, router_intent=router_intent, lang=lang,
    )


def _fastpath_instance(router_intent: dict, message: str, lang: str):
    """fact/instance — «только что выпил воды» → засчитать recurring."""
    from .goals_store import record_instance, get_goal
    from .recurring import get_progress
    gid = router_intent["target_goal_id"]
    goal = get_goal(gid)
    if not goal:
        return None
    record_instance(gid, note=message[:200])
    progress = get_progress(gid)
    txt = goal.get("text", "")
    done = progress.get("done_today", 0) if progress else 0
    tpd = progress.get("times_per_day", 0) if progress else 0
    reply = (f"✓ Засчитал «{txt}» — прогресс {done}/{tpd} сегодня"
             if lang == "ru" else
             f"✓ Recorded «{txt}» — progress {done}/{tpd} today")
    return _fastpath_envelope(
        text=reply, mode="instance_ack", mode_name="Отметил",
        cards=[{
            "type": "instance_ack",
            "goal_id": gid,
            "goal_text": txt,
            "progress": progress,
        }],
        message=message, router_intent=router_intent, lang=lang,
    )


def _fastpath_chat(router_intent: dict, message: str, lang: str):
    """chat — свободный разговор, быстрый LLM-ответ без графа."""
    from .graph_logic import _graph_generate
    sys_p = ("/no_think\nТы дружелюбный ассистент. Отвечай кратко "
             "и по делу, 1-2 предложения."
             if lang == "ru" else
             "/no_think\nYou're a friendly assistant. Reply briefly, 1-2 sentences.")
    reply, _ = _graph_generate(
        [{"role": "system", "content": sys_p},
         {"role": "user", "content": message[:400]}],
        max_tokens=200, temp=0.7, top_k=40,
    )
    return _fastpath_envelope(
        text=(reply or "").strip() or "...",
        mode="chat", mode_name="Разговор",
        cards=[],
        message=message, router_intent=router_intent, lang=lang,
    )


def _fastpath_draft(router_intent: dict, message: str, lang: str):
    """task/new_{recurring,constraint,goal} — draft-карточка для подтверждения.

    Не тратим токены на execute_mode пока юзер не решил что создавать.
    """
    from .intent_router import make_draft_card
    card = make_draft_card(
        router_intent["kind"], router_intent["subtype"],
        message, lang=lang,
    )
    return _fastpath_envelope(
        text=card.get("title", ""),
        mode=router_intent["subtype"], mode_name="Подтверди",
        cards=[card],
        message=message, router_intent=router_intent, lang=lang,
        awaiting_input=True,
    )


def _try_fastpath(router_intent, message: str, lang: str):
    """Проверить router-intent fastpath'ы. Возвращает response-dict если
    сработал, или None (пропускаем к execute_mode).

    Ошибки handler'а логируются и не блокируют остальные (прежнее поведение
    чётырёх if-блоков — молча идти к следующему после warning).
    """
    if not router_intent:
        return None
    ri = router_intent
    kind = ri.get("kind")
    sub = ri.get("subtype")
    c_sub = ri.get("confidence_sub", 0)
    c_top = ri.get("confidence_top", 0)
    DRAFTS = ("new_recurring", "new_constraint", "new_goal")

    route = None
    if kind == "fact" and sub == "activity" and c_sub >= 0.7:
        route = (_fastpath_activity, "activity")
    elif kind == "fact" and sub == "instance" and ri.get("target_goal_id") and c_sub >= 0.7:
        route = (_fastpath_instance, "instance")
    elif kind == "chat" and c_top >= 0.7:
        route = (_fastpath_chat, "chat")
    elif kind == "task" and sub in DRAFTS and c_sub >= 0.7:
        route = (_fastpath_draft, "draft")

    if route is None:
        return None
    handler, tag = route
    try:
        return handler(ri, message, lang)
    except Exception as e:
        log.warning(f"[/assist] fastpath {tag} failed: {e}")
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

    # Deterministic chat-commands prefilter: «как я?», «запусти код», «план»,
    # «что я ел» — обрабатываем локально без LLM. Экономит токены и даёт
    # мгновенный UX. Если ни один паттерн не матчится — продолжаем как обычно.
    try:
        from .chat_commands import try_handle
        cmd_res = try_handle(message, lang=lang)
        if cmd_res is not None:
            # Минимальный engagement-ping — РГК видит что юзер активен
            from .substrate.rgk import get_global_rgk
            get_global_rgk().u_register_input()
            # Привязываем capacity+hrv к ответу (UI ожидает эти поля)
            ctx = _get_context()
            cmd_res.setdefault("capacity", ctx.get("capacity"))
            cmd_res.setdefault("hrv", ctx.get("hrv"))
            cmd_res.setdefault("warnings", [])
            cmd_res.setdefault("lang", lang)
            cmd_res.setdefault("message_echo", message)
            cmd_res.setdefault("api_offline", False)
            # Логируем в state_graph (не-LLM action)
            state = ctx["state"]
            _log_decision(state, kind="chat_command",
                          meta={"command": cmd_res.get("chat_command"),
                                "message": message[:200]},
                          mode_id="free",
                          hrv_recovery=(ctx.get("hrv") or {}).get("energy_recovery"))
            _save_state(state)
            return jsonify(cmd_res)
    except Exception as e:
        log.warning(f"[/assist] chat-command prefilter failed: {e}")

    # ── Intent router prefilter ────────────────────────────────────────
    # Двухуровневый LLM-классификатор: сначала определяем что вообще юзер
    # хочет (task/fact/constraint_event/chat), потом подтип. Для некоторых
    # kind'ов обрабатываем прямо тут без execute_mode/classify_intent_llm —
    # это сильно экономит tokens на простых сообщениях.
    router_intent = None
    try:
        from .intent_router import route as _route_intent
        router_intent = _route_intent(message, lang=lang)
    except Exception as _e:
        log.debug(f"[/assist] intent_router failed: {_e}")

    # 4 fast-path ветки (activity / instance / chat / draft) — сразу
    # возвращаем ответ без execute_mode. Определения и dispatcher — выше.
    _fastpath_resp = _try_fastpath(router_intent, message, lang)
    if _fastpath_resp is not None:
        return jsonify(_fastpath_resp)
    # ── конец router prefilter ─────────────────────────────────────────

    # Inject NE spike — user engagement = Horizon takes budget from DMN.
    # 0.4 здесь vs 0.3 в /graph/assist — user-initiated chat ярче активирует
    # внимание, чем background dialogical loop (intentional).
    from .substrate.horizon import get_global_state
    from .substrate.rgk import get_global_rgk
    cs = get_global_state()
    cs.inject_ne(0.4)

    # User signal: timestamp + мягкий engagement-feeder в dopamine (0.65 EMA).
    # Без него dopamine юзера не менялся бы вообще между click-feedback'ами,
    # что делает sync_error статичным (было видно в «метрики не меняются»).
    rgk = get_global_rgk()
    rgk.u_register_input()
    rgk.u_engage()

    ctx = _get_context()
    state, hrv_state = ctx["state"], ctx["hrv"]
    capacity = ctx.get("capacity") or {}
    rgk.u_energy(state.get("decisions_today", 0))

    # Build state hint for classifier (brief CognitiveState summary)
    neuro = cs.get_metrics().get("neurochem", {})
    state_hint = (f"state={cs.state} "
                  f"NE={neuro.get('norepinephrine', 0):.2f} "
                  f"DA={neuro.get('dopamine', 0):.2f} "
                  f"S={neuro.get('serotonin', 0):.2f} "
                  f"burnout={neuro.get('burnout', 0):.2f}")

    # Profile-aware: detect category → pull profile constraints/preferences
    from .user_profile import profile_summary_for_prompt, is_category_empty, load_profile
    detected_category = _detect_category(message)
    _user_profile = load_profile()
    # Relevance-фильтр: `query=message` отбрасывает preferences далёкие
    # от текущего вопроса (`distinct(query_emb, pref_emb) > 0.7`). Чинит
    # кейс «спросил про рыбный суп → инжектнулось preference 'сладкое'».
    # Constraints (аллергии) не фильтруются — они нужны всегда когда
    # категория активна. Safe-degrade: при ошибке API ведёт себя без гейта.
    profile_hint = (profile_summary_for_prompt([detected_category], lang=lang,
                                                profile=_user_profile,
                                                query=message)
                    if detected_category else "")

    # Recurring/constraint context: активные вечные цели и ограничения
    # из goals_store. LLM видит текущий прогресс и учитывает при ответе.
    # Пример: юзер спрашивает «что поесть» — в промпте видит
    # «привычка: покушать 3 раза (1/3 сегодня)» + «ограничение: не орехи».
    try:
        from .recurring import build_active_context_summary
        recurring_ctx = build_active_context_summary()
        if recurring_ctx:
            profile_hint = (profile_hint + "\n" + recurring_ctx).strip()
    except Exception as _e:
        log.debug(f"[assist] recurring context failed: {_e}")

    # Solved archive RAG: если юзер уже решал похожее — подтягиваем
    # synthesis. Это даёт continuity между сессиями: «2 недели назад ты
    # решил похожее вопросом X, ответ был Y».
    similar_past = []
    try:
        from .solved_archive import find_similar_solved
        similar_past = find_similar_solved(message, top_k=2, min_similarity=0.6)
        if similar_past:
            rag_lines = ["Похожие решённые раньше задачи (для контекста):"]
            for s in similar_past:
                synth = (s.get("final_synthesis") or "")[:200]
                rag_lines.append(f"  — «{s['goal_text'][:80]}» "
                                 f"(sim {s['similarity']:.2f}): {synth}")
            profile_hint = (profile_hint + "\n" + "\n".join(rag_lines)).strip()
    except Exception as _e:
        log.debug(f"[assist] solved archive RAG failed: {_e}")

    # Recent context from state_graph (last 3 user-initiated actions)
    context_parts = []
    try:
        from .state_graph import get_state_graph
        sg = get_state_graph()
        recent = [e for e in sg.tail(10) if e.get("user_initiated")][-3:]
        for e in recent:
            context_parts.append(e.get("reason", "")[:60])
    except Exception as e:
        log.debug(f"[recent_briefing] state_graph parse failed: {e}")
    context = " | ".join(context_parts)

    # ── Forced mode (юзер явно выбрал режим в UI) → skip classify ──
    # Экономит LLM-вызов + даёт детерминированное поведение когда юзер
    # хочет конкретно dispute / tournament / bayes / и т.д.
    forced = (d.get("mode") or "").strip()
    _valid_modes = {"dispute","tournament","bayes","fan","rhythm","horizon",
                    "vector","scout","builder","pipeline","cascade","scales",
                    "race","free"}
    if forced and forced in _valid_modes:
        classification = {"mode": forced, "intent": "direct",
                          "confidence": 1.0, "source": "forced"}
    else:
        # ── ONE LLM call: mode + intent + confidence ──
        classification = classify_intent_llm(message, context=context, state_hint=state_hint,
                                             profile_hint=profile_hint, lang=lang)
    mode_id = classification.get("mode", "free")
    intent = classification.get("intent", "direct")
    confidence = classification.get("confidence", 0.5)
    response = _response_for_mode(mode_id, message, lang)

    # Check capacity — warn if zone red (Phase C decision gate).
    # Заменяет старый `energy < 20` gate на 3-зонную модель из docs/capacity-design.md.
    warnings = []
    capacity = ctx.get("capacity") or {}
    if capacity.get("zone") == "red":
        reason_ru = _capacity_reason_text(capacity.get("reason"), "ru")
        reason_en = _capacity_reason_text(capacity.get("reason"), "en")
        warnings.append({
            "type": "low_capacity",
            "zone": "red",
            "reason": capacity.get("reason"),
            "text": f"Capacity red — {reason_ru}. Сложные решения лучше отложить." if lang == "ru"
                    else f"Capacity red — {reason_en}. Heavy decisions better postponed.",
        })
    if hrv_state and hrv_state.get("coherence") is not None and hrv_state["coherence"] < 0.3:
        warnings.append({
            "type": "low_coherence",
            "text": "Coherence падает — может стоит сделать паузу." if lang == "ru"
                    else "Coherence dropping — consider a break.",
        })

    # Uncertainty-driven profile learning:
    # Если категория распознана, но в профиле по ней пусто — сначала спросим
    # предпочтения/ограничения и **сохраним в profile**, чтобы следующий раз
    # не переспрашивать. Это замыкает цикл: state + profile + goals + info.
    # (Работает и при intent=ambiguous — category keyword match сам по себе
    # уже даёт достаточный сигнал что юзер хочет вопрос именно в этой теме.)
    if (detected_category and is_category_empty(detected_category, _user_profile)):
        from .user_profile import CATEGORY_LABELS_RU
        label = (CATEGORY_LABELS_RU.get(detected_category, detected_category)
                 if lang == "ru" else detected_category)
        if lang == "ru":
            q = (f"Чтобы помочь лучше, мне нужно знать твои предпочтения и "
                 f"ограничения в категории «{label}». Расскажи кратко: что "
                 f"любишь, чего избегаешь?")
        else:
            q = (f"To help better I need to know your preferences and "
                 f"constraints for «{label}». Briefly: what do you like, "
                 f"what do you avoid?")
        _log_decision(state, kind="profile_ask",
                      meta={"category": detected_category, "mode": mode_id,
                            "message": message[:200]},
                      mode_id="free",
                      hrv_recovery=(hrv_state or {}).get("energy_recovery"))
        _save_state(state)
        return jsonify({
            "text": q, "intro": q, "mode": mode_id,
            "mode_name": "уточнение профиля" if lang == "ru" else "profile clarify",
            "message_echo": message,
            "cards": [{
                "type": "profile_clarify",
                "question": q,
                "category": detected_category,
                "original_message": message,
            }],
            "steps": [f"Категория «{detected_category}» в профиле пустая — спрашиваю"
                      if lang == "ru"
                      else f"Profile for '{detected_category}' empty — asking"],
            "capacity": capacity, "hrv": hrv_state, "warnings": warnings,
            "awaiting_input": True, "graph_updated": False,
            "lang": lang, "intent": intent, "confidence": confidence,
            "profile_category": detected_category,
            "classify_source": classification.get("source"),
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
        _log_decision(state, kind="assist_clarify",
                      meta={"mode": mode_id, "message": message[:200]},
                      mode_id=mode_id,
                      hrv_recovery=(hrv_state or {}).get("energy_recovery"))
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
            "capacity": capacity, "hrv": hrv_state, "warnings": warnings,
            "awaiting_input": True, "graph_updated": False,
            "lang": lang, "intent": intent, "confidence": confidence,
            "classify_source": classification.get("source"),
        })

    # ── Actually execute the mode (profile_hint injects constraints) ──
    # Graceful degradation: если LM упал на 3 retry — возвращаем
    # user-friendly fallback-карточку вместо 500. state_graph всё равно
    # пишет assist-event с пометкой api_offline.
    api_offline = False
    # Manual continue: UI передаёт prev_session_indices когда юзер нажал
    # «↳ Продолжить» — это расширяет session whitelist synthesis на ноды
    # предыдущего ответа, давая continuity между сообщениями.
    _prev_session_indices = d.get("prev_session_indices")
    if not isinstance(_prev_session_indices, list):
        _prev_session_indices = None
    try:
        exec_result = execute_mode(mode_id, message, lang,
                                    profile_hint=profile_hint,
                                    prev_session_indices=_prev_session_indices)
    except RuntimeError as e:
        api_offline = True
        log.error(f"[/assist] LM offline: {e}")
        if lang == "ru":
            msg = ("⚠ LM offline — не отвечает после 3 попыток. "
                   "Твой запрос я сохранил, верну ответ как только LM восстановится.")
        else:
            msg = ("⚠ LM offline — no response after 3 retries. Saved your "
                   "request, will reply when LM recovers.")
        exec_result = {
            "text": msg,
            "cards": [{
                "type": "lm_offline",
                "message_echo": message,
                "error": str(e)[:200],
                "retry_hint": ("Проверь LM Studio / api_url в Settings" if lang == "ru"
                               else "Check LM Studio / api_url in Settings"),
            }],
            "steps": [],
        }
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

    # Auto-detect constraint violations: один LLM-скан сообщения юзера
    # против активных constraint-целей. Если есть — добавляем info-карточку
    # юзеру (прозрачность: «я записал нарушение») и пишем в goals.jsonl.
    # Skipped если api_offline или constraints нет.
    violations_found = []
    if not api_offline:
        try:
            from .recurring import scan_message_for_violations
            violations_found = scan_message_for_violations(message, lang=lang)
        except Exception as _e:
            log.debug(f"[assist] violation scan failed: {_e}")
    if violations_found:
        cards = list(cards)
        v_list = ", ".join(f"«{v['text']}»" for v in violations_found)
        cards.append({
            "type": "constraint_violation",
            "violations": violations_found,
            "text": (f"⚠ Зафиксировал нарушение ограничений: {v_list}"
                     if lang == "ru" else
                     f"⚠ Recorded constraint violation: {v_list}"),
        })

    # Log this interaction
    _log_decision(state, kind="assist",
                  meta={"mode": mode_id, "message": message[:200],
                        "intent": intent, "confidence": confidence},
                  mode_id=mode_id,
                  hrv_recovery=(hrv_state or {}).get("energy_recovery"))
    _save_state(state)

    # Action Memory: baddle's reply to user — action-нода actor=baddle.
    # user_chat + baddle_reply в хронологическом порядке = conversation
    # timeline в графе. Card-actions (sync_seeking / bridge / suggestion)
    # уже отдельные action-ноды — они не дублируются здесь.
    # Path через workspace (W14.2): add(accumulate=False) → immediate commit.
    if response_text:
        try:
            from .memory import workspace
            from .graph_logic import link_chat_continuation
            br_idx = workspace.add(
                actor="baddle",
                action_kind="baddle_reply",
                text=response_text[:200],
                urgency=1.0,
                accumulate=False,
                extras={"mode": mode_id, "intent": intent,
                         "cards_count": len(cards) if cards else 0},
            )
            workspace.commit([br_idx])
            link_chat_continuation(br_idx)
        except Exception as e:
            log.debug(f"[action-memory] baddle_reply record failed: {e}")

    return jsonify({
        "text": response_text,
        "intro": response["intro"],
        "mode": mode_id,
        "mode_name": response["mode_name"],
        "message_echo": message,
        "cards": cards,
        "steps": steps,
        "capacity": capacity,
        "hrv": hrv_state,
        "warnings": warnings,
        "awaiting_input": exec_result.get("awaiting_input", False),
        "graph_updated": len(cards) > 0,
        "lang": lang,
        "intent": intent,
        "confidence": confidence,
        "classify_source": classification.get("source"),
        "error": exec_result.get("error"),
        "api_offline": api_offline,
        # Manual continuity: UI сохраняет эти indices и при нажатии
        # «↳ Продолжить» передаёт обратно как prev_session_indices.
        "session_indices": exec_result.get("session_indices") or [],
    })


# ── Status / energy ────────────────────────────────────────────────────

@assistant_bp.route("/assist/status", methods=["GET"])
def assist_status():
    """Current user state — energy, HRV, recent activity."""
    ctx = _get_context()
    state, hrv_state = ctx["state"], ctx["hrv"]
    capacity = ctx.get("capacity") or {}
    return jsonify({
        "capacity": capacity,
        "hrv": hrv_state,
        "total_decisions": state.get("total_decisions", 0),
        "decisions_today": state.get("decisions_today", 0),
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
    from .substrate.horizon import get_global_state
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
    # Mirror signal into РГК (accept ↑ dopamine, reject ↑ burnout)
    from .substrate.rgk import get_global_rgk
    get_global_rgk().u_feedback(kind)
    # Action Memory: user_accept / user_reject — закрывают открытые
    # baddle-actions (suggestion_*) через `_check_action_outcomes`.
    if kind in ("accepted", "rejected"):
        try:
            from .graph_logic import record_action
            action_kind = "user_accept" if kind == "accepted" else "user_reject"
            record_action(actor="user", action_kind=action_kind,
                          text=f"User {kind}", context=None)
        except Exception as e:
            log.debug(f"[action-memory] feedback action record failed: {e}")
    return jsonify({"ok": True, "neurochem": cs.get_metrics().get("neurochem", {})})


@assistant_bp.route("/assist/camera", methods=["POST"])
def assist_camera():
    """Toggle Camera mode (v8c) — sensory deprivation.

    Body: { "enabled": true/false }
    When enabled, llm_disabled=True: tick works only on existing embeddings,
    no new LLM calls. Useful for reflection + finding hidden patterns in
    what's already there.
    """
    from .substrate.horizon import get_global_state
    d = request.get_json(force=True) or {}
    enabled = bool(d.get("enabled", False))
    cs = get_global_state()
    cs.llm_disabled = enabled
    return jsonify({"ok": True, "camera": enabled})


@assistant_bp.route("/assist/state", methods=["GET"])
def assist_state():
    """Return full CognitiveState metrics (for UI panel, diagnostics).

    UserState через `user_state` ключ уже включает: dopamine/serotonin/
    norepinephrine/acetylcholine/gaba/balance, burnout, expectation/reality/
    surprise/imbalance (signed prediction error), named_state (Voronoi region).

    `thinking` — что cognitive_loop делает в фоне прямо сейчас (pump /
    elaborate / scout / idle). UI polls этот endpoint и рисует cone-viz
    в соответствии: dual cones для pump, pulse для идей, freeze-overlay и т.д.
    """
    from .substrate.horizon import get_global_state
    from .api_backend import get_api_health
    from .process.cognitive_loop import get_cognitive_loop
    data = get_global_state().get_metrics()
    data["api_health"] = get_api_health()
    try:
        data["thinking"] = get_cognitive_loop().get_thinking()
    except Exception:
        data["thinking"] = {"kind": "idle", "started_at": 0}
    return jsonify(data)


@assistant_bp.route("/patterns", methods=["GET"])
def patterns_list():
    """Recent detected patterns (weekday × category × outcome).

    Query: ?today=1 — only today's weekday, ?hours=N — lookback (default 36).
    """
    from .patterns import read_recent_patterns, patterns_for_today
    only_today = (request.args.get("today", "0") in ("1", "true"))
    if only_today:
        return jsonify({"patterns": patterns_for_today()})
    try:
        hours = int(request.args.get("hours", 36))
    except ValueError:
        hours = 36
    return jsonify({"patterns": read_recent_patterns(hours=hours)})


@assistant_bp.route("/patterns/run", methods=["POST"])
def patterns_run():
    """Manual trigger — запускает детектор сейчас (полезно для тестов и
    когда night_cycle ещё не отработал).
    """
    from .patterns import detect_all
    d = request.get_json(silent=True) or {}
    try:
        days = int(d.get("days_back", 21))
    except (TypeError, ValueError):
        days = 21
    found = detect_all(days_back=days)
    return jsonify({"ok": True, "detected": len(found), "patterns": found})


@assistant_bp.route("/assist/history", methods=["GET"])
def assist_history():
    """Time-series из state_graph для UI-дашбордов.

    Query params:
      limit: int (default 50)  — max entries
      kind: str                 — фильтр по action (optional)

    Returns:
      {
        "entries": [
          {ts, sync_error, dopamine, serotonin, norepinephrine, burnout,
           action, mode, user_feedback}
        ],
        "top_rejected_modes": [{mode, count}, ...]  — top-3
      }
    """
    from .state_graph import get_state_graph
    from datetime import datetime

    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        limit = 50
    kind = request.args.get("kind")

    sg = get_state_graph()
    try:
        raw = sg.read_all()
    except Exception:
        raw = []

    if kind:
        raw = [e for e in raw if e.get("action") == kind]
    raw = raw[-limit:]

    out_entries = []
    reject_by_mode: dict = {}
    for e in raw:
        snap = e.get("state_snapshot") or {}
        neuro = snap.get("neurochem") or {}
        ts_iso = e.get("timestamp") or ""
        try:
            ts_epoch = datetime.fromisoformat(
                str(ts_iso).replace("Z", "+00:00")
            ).timestamp()
        except Exception:
            ts_epoch = None
        meta_mode = (e.get("reason") or "").split("[")[1].split("]")[0] if "[" in (e.get("reason") or "") else None
        out_entries.append({
            "ts": ts_epoch,
            "timestamp": ts_iso,
            "sync_error": snap.get("sync_error"),
            "dopamine_gain": neuro.get("dopamine_gain"),
            "serotonin_hysteresis": neuro.get("serotonin_hysteresis"),
            "norepinephrine_aperture": neuro.get("norepinephrine_aperture"),
            "burnout": neuro.get("burnout"),
            "recent_rpe": neuro.get("recent_rpe"),
            "state": snap.get("state"),
            "action": e.get("action"),
            "state_origin": e.get("state_origin"),
            "reason": (e.get("reason") or "")[:80],
            "user_feedback": e.get("user_feedback"),
        })
        if e.get("user_feedback") == "rejected":
            mode = meta_mode or e.get("action") or "?"
            reject_by_mode[mode] = reject_by_mode.get(mode, 0) + 1

    top = sorted(reject_by_mode.items(), key=lambda x: -x[1])[:3]
    top_rejected = [{"mode": m, "count": c} for m, c in top]

    return jsonify({
        "entries": out_entries,
        "top_rejected_modes": top_rejected,
        "count": len(out_entries),
    })


@assistant_bp.route("/assist/prime-directive", methods=["GET"])
def assist_prime_directive():
    """Агрегат sync_error trend из `data/prime_directive.jsonl`.

    Query params:
      window_days: float (optional) — окно аггрегации. Если не задан —
                    весь лог. Например 7 = неделя, 30 = месяц.
      daily: '1' → добавить per-day breakdown (default выключен).

    Returns:
      {
        ok: True,
        count, days_span, first_ts, last_ts,
        mean_sync_error, mean_ema_fast, mean_ema_slow,
        mean_imbalance, mean_silence, mean_conflict,
        trend_slow_delta, trend_verdict,   # last-third minus first-third
        daily?: [{date, count, mean_fast, mean_slow}, ...]
      }

    Валидация через 2 мес use: если `trend_slow_delta` < 0 (mean slow EMA
    упал) — резонансный протокол работает, прайм-директива validates.
    """
    from .prime_directive import aggregate, daily_bins

    try:
        window_str = request.args.get("window_days", "").strip()
        window_days = float(window_str) if window_str else None
    except Exception:
        window_days = None
    include_daily = request.args.get("daily", "").strip() == "1"

    summary = aggregate(window_days=window_days)
    if include_daily:
        from .prime_directive import _CHEM_DAILY_FIELDS, _PE_DAILY_FIELDS
        days = int(window_days) if window_days else 30
        summary["daily"] = daily_bins(window_days=days)  # sync EMA fast/slow
        summary["daily_chem"] = daily_bins(window_days=days, fields=_CHEM_DAILY_FIELDS)
        summary["daily_pe"]   = daily_bins(window_days=days, fields=_PE_DAILY_FIELDS)
    return jsonify({"ok": True, **summary})


@assistant_bp.route("/assist/bookmark", methods=["POST"])
def assist_bookmark():
    """Insight bookmark — субъективный «эта мысль повлияла» маркер.

    Action Memory ловит actions/outcomes, episodic memory — nodes, но
    subjective marker отсутствовал. Пользователь жмёт ⭐ → нода в графе
    с типом "insight_bookmark", полным контекстом (capacity_zone,
    mode, balance, frequency_regime, named_state) и опц. session_indices.

    Через год: distinct(query) на insight bookmarks → найти «где сегодня
    случился сдвиг похожий на текущий». Бесплатно для long-term self-research.

    Body:
        {text: str, session_indices?: list[int]}
    Returns:
        {ok: True, node_id: int, context: dict}
    """
    from .graph_logic import _add_node, _graph
    from .substrate.rgk import get_global_rgk

    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "text required"}), 400

    # Snapshot текущего контекста — что было в моменте сдвига.
    try:
        r = get_global_rgk()
        us  = r.project("user_state")
        cap = r.project("capacity")
        ns  = r.project("named_state")
        context = {
            "capacity_zone":    cap.get("zone"),
            "frequency_regime": us.get("frequency_regime"),
            "mode_user":        us.get("mode"),
            "balance_user":     us.get("balance"),
            "named_state":      {"key": ns.get("key"), "label": ns.get("label")},
        }
    except Exception as e:
        log.debug(f"[bookmark] context snapshot failed: {e}")
        context = {}

    session_indices = data.get("session_indices") or []

    node_id = _add_node(text, depth=0, topic="insight",
                        confidence=1.0, node_type="insight_bookmark")
    # Прикрепляем context как extra fields на ноде (не в _make_node signature).
    try:
        node = _graph["nodes"][node_id]
        node["bookmark_context"] = context
        if session_indices:
            node["session_indices"] = list(session_indices)
    except Exception:
        pass

    return jsonify({"ok": True, "node_id": node_id, "context": context})


# ── User Profile endpoints ─────────────────────────────────────────────

@assistant_bp.route("/profile", methods=["GET"])
def profile_get():
    """Return full user profile."""
    from .user_profile import load_profile, CATEGORIES, CATEGORY_LABELS_RU
    return jsonify({
        "profile": load_profile(),
        "categories": list(CATEGORIES),
        "labels_ru": CATEGORY_LABELS_RU,
    })


@assistant_bp.route("/profile/add", methods=["POST"])
def profile_add():
    """Body: {category, kind: preferences|constraints, text}"""
    from .user_profile import add_item
    d = request.get_json(force=True) or {}
    try:
        p = add_item(d.get("category", ""), d.get("kind", ""), d.get("text", ""))
        return jsonify({"ok": True, "profile": p})
    except ValueError as e:
        return jsonify({"error": str(e)})


@assistant_bp.route("/profile/remove", methods=["POST"])
def profile_remove():
    """Body: {category, kind, text}"""
    from .user_profile import remove_item
    d = request.get_json(force=True) or {}
    p = remove_item(d.get("category", ""), d.get("kind", ""), d.get("text", ""))
    return jsonify({"ok": True, "profile": p})


@assistant_bp.route("/profile/context", methods=["POST"])
def profile_context():
    """Body: {key, value} для свободного context-поля."""
    from .user_profile import set_context
    d = request.get_json(force=True) or {}
    p = set_context(d.get("key", ""), d.get("value"))
    return jsonify({"ok": True, "profile": p})


@assistant_bp.route("/profile/learn", methods=["POST"])
def profile_learn():
    """Uncertainty-learning: LLM-разбор ответа юзера на profile_clarify-вопрос.

    Body: { "category": "food", "answer": "не ем орехи, люблю курицу",
            "original_message": "хочу покушать", "lang": "ru" }

    Парсит answer на preferences/constraints, сохраняет в profile[category].
    Возвращает добавленные items + сохраняет в profile автоматически.
    """
    from .user_profile import parse_category_answer, add_item, CATEGORIES
    d = request.get_json(force=True) or {}
    cat = d.get("category")
    answer = (d.get("answer") or "").strip()
    lang = d.get("lang", "ru")
    if cat not in CATEGORIES:
        return jsonify({"error": f"unknown category: {cat}"})
    if not answer:
        return jsonify({"error": "empty answer"})

    parsed = parse_category_answer(answer, cat, lang=lang)
    for text in parsed.get("preferences", []):
        add_item(cat, "preferences", text)
    for text in parsed.get("constraints", []):
        add_item(cat, "constraints", text)

    return jsonify({
        "ok": True,
        "category": cat,
        "added": parsed,
        "original_message": d.get("original_message", ""),
    })


# ── Goals store endpoints ──────────────────────────────────────────────

@assistant_bp.route("/goals", methods=["GET"])
def goals_list():
    """Query: ?status=open|done|abandoned &category=Y &limit=N"""
    from .goals_store import list_goals
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
    from .goals_store import goal_stats
    return jsonify(goal_stats())


# ── Helper: событие в chat_history ─────────────────────────────────────
# Ручные действия через UI (добавил цель, check-in, старт активности)
# должны появляться в чате — чтобы история ничего не теряла.

def _push_event_to_chat(text: str, mode_name: str = "Событие"):
    """Добавить assistant-message в chat_history + записать baddle-action
    в граф (Action Memory). Silent on failure.
    """
    try:
        from .chat_history import append_entry
        append_entry({
            "kind": "msg", "role": "assistant",
            "content": text,
            "meta": {"mode_name": mode_name},
        })
    except Exception as e:
        log.debug(f"[chat_event] failed: {e}")
    # Action Memory: любое baddle-сообщение в чат → action-нода.
    # Это разные action_kind'ы в зависимости от mode_name (Activity, Check-in,
    # Новая цель и т.д.). Позволяет искать mosts между проактивным
    # сообщением и последующим user-behavior.
    try:
        from .graph_logic import record_action
        # Слоганируем mode_name в snake_case для action_kind
        kind_slug = (mode_name or "event").lower().replace(" ", "_")[:40]
        record_action(actor="baddle", action_kind=f"chat_event_{kind_slug}",
                      text=text[:200], context=None)
    except Exception as e:
        log.debug(f"[action-memory] chat_event record failed: {e}")


@assistant_bp.route("/goals/add", methods=["POST"])
def goals_add():
    """Manual add (обычно создаётся автоматом из /graph/add node_type=goal).

    Body: {text, mode, priority, deadline, category,
           kind?, schedule?, polarity?}

    kind: "oneshot" (default) | "recurring" | "constraint"
    schedule: {times_per_day, days?, time_windows?} — для recurring
    polarity: "avoid" | "prefer" — для constraint
    """
    from .goals_store import add_goal
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
        from .graph_logic import record_action
        record_action(actor="user", action_kind=f"user_goal_create_{kind}",
                      text=f"{label}: {text[:120]}",
                      extras={"goal_id": gid, "goal_kind": kind})
    except Exception as e:
        log.debug(f"[action-memory] user_goal_create failed: {e}")
    return jsonify({"ok": True, "id": gid})


@assistant_bp.route("/goals/instance", methods=["POST"])
def goals_instance():
    """Отметить выполнение recurring-цели. Body: {id, note?}"""
    from .goals_store import record_instance, get_goal
    d = request.get_json(force=True) or {}
    gid = d.get("id", "")
    g = get_goal(gid)
    if not g:
        return jsonify({"error": "goal_not_found"}), 404
    if g.get("kind") != "recurring":
        return jsonify({"error": "not_recurring",
                        "kind": g.get("kind")}), 400
    record_instance(gid, note=d.get("note", ""))
    from .recurring import get_progress
    return jsonify({"ok": True, "progress": get_progress(gid)})


@assistant_bp.route("/goals/violation", methods=["POST"])
def goals_violation():
    """Отметить нарушение constraint. Body: {id, note?, detected?}

    detected: "manual" (default) | "llm_scan" | "tick"
    """
    from .goals_store import record_violation, get_goal
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
    from .goals_store import add_goal
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
    from .recurring import list_recurring, get_progress
    out = []
    for g in list_recurring(active_only=True):
        p = get_progress(g["id"])
        if p:
            out.append(p)
    return jsonify({"recurring": out})


@assistant_bp.route("/goals/constraints", methods=["GET"])
def goals_constraints_list():
    """Constraint-цели со статусом нарушений за 7 дней."""
    from .recurring import list_constraint_status
    return jsonify({"constraints": list_constraint_status(days=7)})


@assistant_bp.route("/goals/complete", methods=["POST"])
def goals_complete():
    """Body: {id, reason}"""
    from .goals_store import complete_goal
    d = request.get_json(force=True) or {}
    complete_goal(d.get("id", ""), reason=d.get("reason", ""))
    return jsonify({"ok": True})


@assistant_bp.route("/goals/abandon", methods=["POST"])
def goals_abandon():
    """Body: {id, reason}"""
    from .goals_store import abandon_goal
    d = request.get_json(force=True) or {}
    abandon_goal(d.get("id", ""), reason=d.get("reason", ""))
    return jsonify({"ok": True})


@assistant_bp.route("/goals/postpone", methods=["POST"])
def goals_postpone():
    """Отложить цель до завтрашнего wake_hour. Используется в low_energy_heavy alert.

    Body: {id, until?: "tomorrow"|"next_week"}  — default "tomorrow"
    """
    from .goals_store import update_goal, get_goal
    from .user_profile import load_profile
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
    from .solved_archive import list_solved
    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        limit = 50
    return jsonify({"solved": list_solved(limit=limit)})


@assistant_bp.route("/goals/solved/<snapshot_ref>", methods=["GET"])
def goals_solved_get(snapshot_ref):
    from .solved_archive import load_solved
    data = load_solved(snapshot_ref)
    if not data:
        return jsonify({"error": "not_found"}), 404
    return jsonify(data)


# ── Activity log endpoints (ручной ground-truth трекер) ──────────────

@assistant_bp.route("/activity/active", methods=["GET"])
def activity_active():
    """Текущая активная задача + список шаблонов."""
    from .activity_log import get_active, get_templates
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
        from .graph_logic import _graph, _add_node, graph_lock
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
        import logging
        logging.getLogger(__name__).warning(f"[activity] graph sync failed: {e}")
        return None


@assistant_bp.route("/activity/start", methods=["POST"])
def activity_start():
    """Начать новую задачу. Если есть активная — она автоматически стопается
    со `stop_reason='switch'` (поведение кнопки «Следующая» в Time Player).

    Body: {name, category?}
    """
    from .activity_log import start_activity, get_active, update_activity

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
        from .activity_log import try_match_recurring_instance
        matched_recurring = try_match_recurring_instance(
            activity_name=name, activity_category=category, lang="ru",
        )
    except Exception as _e:
        log.debug(f"[/activity/start] recurring match failed: {_e}")

    # Activity ↔ constraint: если activity-имя нарушает один из constraints,
    # пишем violation.
    violations = []
    try:
        from .activity_log import try_detect_constraint_violation
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
        from .graph_logic import record_action
        record_action(actor="user", action_kind="user_activity_start",
                      text=f"Start activity: {name[:120]}",
                      extras={"activity_id": aid, "category": category})
    except Exception as e:
        log.debug(f"[action-memory] user_activity_start failed: {e}")
    return jsonify(resp)


@assistant_bp.route("/activity/stop", methods=["POST"])
def activity_stop():
    """Остановить текущую активную задачу. Body: {reason?}"""
    from .activity_log import stop_activity
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
        from .graph_logic import record_action
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
    from .activity_log import day_summary
    return jsonify(day_summary())


@assistant_bp.route("/activity/history", methods=["GET"])
def activity_history():
    """Последние N задач (завершённые + активная).

    Query: ?limit=100 &category=food|work|health|social|learning &days=7
    Фильтр по category + days даёт ответ на вопросы типа «что я ел за неделю»,
    «сколько времени на работу за 30 дней».
    """
    from .activity_log import list_activities
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
    from .activity_log import update_activity
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
    from .activity_log import delete_activity
    d = request.get_json(force=True) or {}
    aid = d.get("id", "")
    if not aid:
        return jsonify({"error": "id_required"}), 400
    delete_activity(aid)
    return jsonify({"ok": True})


# ── Plans: карта будущего (events + recurring habits) ──────────────────

@assistant_bp.route("/plan/today", methods=["GET"])
def plans_today():
    """Расписание на сегодня (или ?date=YYYY-MM-DD). Разворачивает recurring."""
    from .plans import schedule_for_day
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
    from .plans import add_plan
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
    from .plans import complete_plan
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
        from .plans import get_plan
        from .substrate.rgk import get_global_rgk
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
    from .plans import skip_plan
    d = request.get_json(force=True) or {}
    if not d.get("id"):
        return jsonify({"error": "id_required"}), 400
    skip_plan(plan_id=d["id"], for_date=d.get("for_date"), reason=d.get("reason", ""))
    return jsonify({"ok": True})


@assistant_bp.route("/plan/delete", methods=["POST"])
def plans_delete():
    from .plans import delete_plan
    d = request.get_json(force=True) or {}
    if not d.get("id"):
        return jsonify({"error": "id_required"}), 400
    delete_plan(plan_id=d["id"])
    return jsonify({"ok": True})


# ── Daily check-in endpoints (ручной subjective-сигнал, если HRV off) ──

@assistant_bp.route("/checkin", methods=["POST"])
def checkin_add():
    """Body: {focus?, stress?, expected?, reality?, note?}.

    Все поля опциональны. Записывает событие + корректирует UserState:
    stress→NE, focus→serotonin, reality→valence,
    (reality−expected)→surprise. Replaces HRV-контур когда трекера нет.
    """
    from .checkins import add_checkin, apply_to_user_state
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
        from .graph_logic import record_action
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
    from .checkins import latest_checkin
    return jsonify({"entry": latest_checkin(hours=24)})


@assistant_bp.route("/checkin/history", methods=["GET"])
def checkin_history():
    """Список за последние N дней (?days=14, default)."""
    from .checkins import list_checkins, rolling_averages
    try:
        days = int(request.args.get("days", 14))
    except ValueError:
        days = 14
    return jsonify({
        "items": list_checkins(days=days),
        "averages": rolling_averages(days=7),
    })


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
    requested_mode = d.get("mode")

    nodes = _graph["nodes"]
    goal_nodes = [(i, n) for i, n in enumerate(nodes)
                  if n.get("type") == "goal" and n.get("depth", 0) >= 0]
    goal_idx, goal_node = goal_nodes[0] if goal_nodes else (None, None)
    mode_id = requested_mode or (goal_node.get("mode") if goal_node else None) or \
              _graph.get("meta", {}).get("mode", "horizon")

    # NE spike on any /graph/assist activity (dialogical loop is engagement too)
    from .substrate.horizon import get_global_state
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


_DECOMPOSE_MODE_SUGGESTION = {
    "and": "builder",           # все обязательны, порядок не строгий
    "xor": "tournament",        # выбор одного
    "research": "horizon",      # открытое исследование
}


def _parse_decompose_groups(text: str) -> dict:
    """Разобрать структурированный вывод LLM на 3 группы подзадач.

    Ожидаемый формат:
      AND: подзадача 1
      AND: подзадача 2
      XOR: вариант A
      XOR: вариант B
      RESEARCH: направление исследования

    Префикс case-insensitive, может быть с двоеточием, тире или пробелом.
    Строки без префикса → в AND (самый нейтральный bucket).
    """
    groups = {"and": [], "xor": [], "research": []}
    for raw_line in text.split("\n"):
        # lstrip только bullets/numbering (не трогаем трейлинг — там могут быть
        # значимые цифры типа «шаг 1»). rstrip пробельные.
        line = raw_line.lstrip(" \t-•*1234567890.)]:").rstrip()
        if not line or len(line) < 3:
            continue
        lower = line.lower()
        bucket = "and"  # default fallback
        content = line
        for prefix, key in [("research:", "research"), ("xor:", "xor"),
                            ("and:", "and"), ("research ", "research"),
                            ("xor ", "xor"), ("and ", "and")]:
            if lower.startswith(prefix):
                bucket = key
                content = line[len(prefix):].strip(" :-")
                break
        if content and len(content) > 2:
            groups[bucket].append(content)
    return groups


@assistant_bp.route("/assist/decompose", methods=["POST"])
def assist_decompose():
    """Goal decomposition → **подграфы разных режимов** (не плоский список).

    LLM классифицирует каждую подзадачу по трём bucket'ам:
      - AND      — все обязательны (сборка, шаги, баланс) → mode=builder
      - XOR      — выбор одного (сравнение вариантов) → mode=tournament
      - RESEARCH — открытое исследование (без финала) → mode=horizon

    UI может создать три раздельных subgraph'а с соответствующими
    пресетами precision/policy вместо одного плоского goal'а.

    Response:
      {
        "groups": {"and": [...], "xor": [...], "research": [...]},
        "mode_suggestions": {"and": "builder", ...},
        "subgoals": [...],     # backward compat: concat всех групп
        "raw": "..."
      }
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
        system = (
            "/no_think\nРазбей задачу на подзадачи, класифицируя каждую в одну "
            "из трёх категорий:\n"
            "  AND      — все обязательны (части сборки, шаги плана, баланс)\n"
            "  XOR      — выбор одного варианта (сравнение альтернатив)\n"
            "  RESEARCH — открытое исследование без финала\n"
            "Формат вывода: каждая строка начинается с префикса + двоеточие.\n"
            "Пример:\n"
            "  AND: купить продукты\n"
            "  AND: приготовить блюдо\n"
            "  XOR: какое именно блюдо\n"
            "  RESEARCH: диетические ограничения гостей\n"
            "Не все категории обязательны. Без вступления, без нумерации, "
            "3-7 строк всего."
        )
    else:
        system = (
            "/no_think\nSplit task into subtasks, classifying each into one of "
            "three categories:\n"
            "  AND      — all required (assembly parts, pipeline steps, balance)\n"
            "  XOR      — pick one option (compare alternatives)\n"
            "  RESEARCH — open-ended exploration, no final state\n"
            "Format: each line starts with prefix + colon.\n"
            "Example:\n"
            "  AND: buy groceries\n"
            "  AND: cook dish\n"
            "  XOR: which dish to cook\n"
            "  RESEARCH: guests' dietary restrictions\n"
            "Not all categories required. No preamble, no numbering, "
            "3-7 lines total."
        )

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": message},
    ]
    try:
        text, _ = _graph_generate(messages, max_tokens=300, temp=temp, top_k=top_k)
    except Exception as e:
        return jsonify({"error": str(e)})

    groups = _parse_decompose_groups(text)
    # Backward compat: flat list (сохраняем порядок AND → XOR → RESEARCH)
    flat = groups["and"] + groups["xor"] + groups["research"]
    # Clamp: не больше 7 чтобы UI не перегружать
    flat = flat[:7]

    mode_suggestions = {
        key: _DECOMPOSE_MODE_SUGGESTION[key]
        for key in ("and", "xor", "research") if groups[key]
    }

    return jsonify({
        "groups": groups,
        "mode_suggestions": mode_suggestions,
        "subgoals": flat,
        "raw": text,
    })


# ── Morning briefing ──────────────────────────────────────────────────

# ── Sensor stream (polymorphic body sensors) ──────────────────────────
# Unified поток от любого источника: simulator, Polar, Apple Watch,
# manual check-in. UserState читает агрегат отсюда (не из конкретного
# HRVManager). См. docs/alerts-and-cycles.md + src/sensors/stream.py

@assistant_bp.route("/sensor/readings", methods=["GET"])
def sensor_readings():
    """Последние readings по kind/source за окно (секунды).

    Query: ?kind=hrv_snapshot&since=300&source=simulator
    """
    from .sensors.stream import get_stream
    kind = request.args.get("kind")
    source = request.args.get("source")
    try:
        since = float(request.args.get("since", 300))
    except ValueError:
        since = 300.0
    readings = get_stream().recent(
        kinds=[kind] if kind else None,
        sources=[source] if source else None,
        since_seconds=since,
    )
    return jsonify({
        "count": len(readings),
        "active_sources": get_stream().active_sources(),
        "readings": [
            {"ts": r.ts, "source": r.source, "kind": r.kind,
             "metrics": r.metrics, "confidence": r.confidence}
            for r in readings
        ],
    })


@assistant_bp.route("/sensor/aggregate", methods=["GET"])
def sensor_aggregate():
    """Weighted HRV aggregate за окно (время-decay × confidence).

    Query: ?window=180
    """
    from .sensors.stream import get_stream
    try:
        window = float(request.args.get("window", 180))
    except ValueError:
        window = 180.0
    agg = get_stream().latest_hrv_aggregate(window_s=window)
    activity = get_stream().recent_activity(window_s=60)
    return jsonify({
        "aggregate": agg,
        "activity_magnitude": activity,
        "active_sources": get_stream().active_sources(),
    })


# ── Debug: test harness для всех _check_* в cognitive_loop ────────────
# Прогоняет каждую `_check_*` функцию с force-сбросом throttle. Полезно
# чтобы видеть: какой alert реально emit'ится когда условия выполнены,
# какой молчит (условие/данные не дают), какой падает с ошибкой.

# Тяжёлые check'и (LLM-цикл, pump, REM) пропускаются по default чтобы
# один вызов не занимал минуту. ?include_heavy=1 — прогнать всё.
_HEAVY_CHECKS = {
    "_check_night_cycle",           # REM + Scout + Consolidation
    "_check_dmn_deep_research",     # full execute_deep pipeline
    "_check_dmn_converge",          # autorun loop
    "_check_dmn_continuous",        # pump-bridge LLM
}


@assistant_bp.route("/debug/alerts/trigger-all", methods=["POST", "GET"])
def debug_alerts_trigger_all():
    """Прогоняет все `_check_*` методы cognitive_loop с force-сбросом
    throttle, возвращает отчёт: что emitted alert / silent / error.

    Query: ?include_heavy=1 — включить pump/night/dmn_converge (долго!).
    """
    from .process.cognitive_loop import get_cognitive_loop
    include_heavy = request.args.get("include_heavy") in ("1", "true", "yes")

    cl = get_cognitive_loop()

    # Monkey-patch _throttled чтобы всегда пропускал (и обнулял timer'ы)
    original_throttled = cl._throttled
    def force_throttled(attr, interval_s):
        try:
            setattr(cl, attr, 0.0)
        except Exception:
            pass
        return original_throttled(attr, interval_s)
    cl._throttled = force_throttled

    # Находим все _check_* методы
    check_names = sorted(
        m for m in dir(cl)
        if m.startswith("_check_") and callable(getattr(cl, m, None))
    )

    results = []
    try:
        for name in check_names:
            entry = {"name": name, "heavy": name in _HEAVY_CHECKS}
            if name in _HEAVY_CHECKS and not include_heavy:
                entry["status"] = "skipped_heavy"
                results.append(entry)
                continue
            before = list(cl._alerts_queue)
            before_ids = {id(a) for a in before}
            try:
                fn = getattr(cl, name)
                t0 = time.time()
                fn()
                entry["elapsed_s"] = round(time.time() - t0, 3)
                new_alerts = [a for a in cl._alerts_queue if id(a) not in before_ids]
                if new_alerts:
                    entry["status"] = "alert_emitted"
                    entry["alerts"] = [
                        {"type": a.get("type"),
                         "severity": a.get("severity"),
                         "text": (a.get("text") or "")[:140]}
                        for a in new_alerts
                    ]
                else:
                    entry["status"] = "silent_ok"
            except Exception as e:
                entry["status"] = "error"
                entry["error"] = str(e)[:200]
            results.append(entry)
    finally:
        cl._throttled = original_throttled

    summary = {
        "total": len(results),
        "alert_emitted": sum(1 for r in results if r["status"] == "alert_emitted"),
        "silent_ok":     sum(1 for r in results if r["status"] == "silent_ok"),
        "error":         sum(1 for r in results if r["status"] == "error"),
        "skipped_heavy": sum(1 for r in results if r["status"] == "skipped_heavy"),
        "include_heavy": include_heavy,
    }
    return jsonify({"summary": summary, "results": results})


# ── Chat history (ранее жил в browser localStorage) ───────────────────
# GET /assist/chat/history — возвращает весь сохранённый список entries.
# POST /assist/chat/append — добавить одну entry (fire-and-forget из JS).
# POST /assist/chat/clear — очистить историю (кнопка «Очистить чат»).

@assistant_bp.route("/assist/chat/history", methods=["GET"])
def assist_chat_history():
    from .chat_history import load_history
    return jsonify({"entries": load_history()})


@assistant_bp.route("/assist/chat/append", methods=["POST"])
def assist_chat_append():
    from .chat_history import append_entry
    entry = request.get_json(force=True, silent=True)
    if not isinstance(entry, dict):
        return jsonify({"error": "entry must be object"}), 400
    try:
        append_entry(entry)
        # Adaptive idle + Action Memory: user-сообщение будит циклы +
        # записывается как user_chat action со sentiment. Учитываем role=user.
        if (entry.get("role") or "").lower() == "user":
            try:
                from .process.cognitive_loop import get_cognitive_loop
                get_cognitive_loop().signal_user_input()
            except Exception:
                pass
            msg_text = str(entry.get("text") or entry.get("content") or "")[:500]
            if msg_text:
                # 1. Sentiment classify (light LLM, cached)
                sentiment = 0.0
                try:
                    from .sentiment import classify_message_sentiment
                    sentiment = classify_message_sentiment(msg_text)
                except Exception as e:
                    log.debug(f"[sentiment] classify failed: {e}")
                # 2. EMA feeders в UserState: valence от sentiment, dopamine
                # от самого факта вовлечённости. Вместе дают движение метрик
                # при каждом сообщении, чтобы sync_error был живой.
                try:
                    from .substrate.rgk import get_global_rgk
                    r = get_global_rgk()
                    r.u_chat(sentiment)
                    r.u_engage()
                except Exception as e:
                    log.debug(f"[sentiment] ema update failed: {e}")
                # 3. user_chat через workspace (W14.2): add(accumulate=False)
                # → immediate commit. Один path для всего что попадает в граф+chat.
                try:
                    from .memory import workspace
                    from .graph_logic import _current_snapshot, link_chat_continuation
                    ctx = _current_snapshot()
                    ctx["sentiment"] = round(float(sentiment), 3)
                    uc_idx = workspace.add(
                        actor="user",
                        action_kind="user_chat",
                        text=msg_text[:200],
                        urgency=1.0,
                        accumulate=False,
                        context=ctx,
                    )
                    workspace.commit([uc_idx])
                    link_chat_continuation(uc_idx)
                except Exception as e:
                    log.debug(f"[action-memory] user_chat record failed: {e}")
        return jsonify({"ok": True})
    except Exception as e:
        log.warning(f"[/assist/chat/append] failed: {e}")
        return jsonify({"error": str(e)}), 500


@assistant_bp.route("/assist/chat/clear", methods=["POST"])
def assist_chat_clear():
    from .chat_history import clear_history
    removed = clear_history()
    return jsonify({"ok": True, "removed": removed})


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
        from .process.cognitive_loop import get_cognitive_loop
        cl = get_cognitive_loop()
        if hasattr(cl, "_build_morning_briefing_sections"):
            sections = cl._build_morning_briefing_sections() or []
    except Exception as e:
        log.debug(f"[/assist/morning] sections builder failed: {e}")

    # Action timeline (W14.4): brief_morning через workspace. accumulate=False
    # + immediate commit — briefing запрашивается user'ом explicit, не накапливается.
    # TTL 24ч на случай если не committed (для archive_expired post-hoc analysis).
    try:
        from .memory import workspace
        bm_idx = workspace.add(
            actor="baddle",
            action_kind="brief_morning",
            text=greeting,
            urgency=0.6,
            accumulate=False,
            ttl_seconds=24 * 3600,
            extras={"sections_count": len(sections),
                     "recovery_pct": recovery_pct,
                     "lang": lang},
        )
        workspace.commit([bm_idx])
    except Exception as e:
        log.debug(f"[workspace] brief_morning record failed: {e}")

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
        from .activity_log import _replay as _replay_act
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
        from .plans import _replay as _plans_replay
        week_start = time.time() - 7 * 86400
        import datetime as _dt
        today_d = _dt.date.today()
        from .plans import _matches_recurring as _mr
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
        from .activity_log import _replay as _act_replay
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

    # Scout bridges за неделю (читаем из alerts_queue + _recent_bridges cognitive_loop)
    try:
        from .process.cognitive_loop import get_cognitive_loop
        loop = get_cognitive_loop()
        now_ts = time.time()
        week_ago = now_ts - 7 * 86400
        bridges = [b for b in (getattr(loop, "_recent_bridges", []) or [])
                   if (b.get("ts") or 0) >= week_ago]
        digest["scout_bridges"] = [{
            "text": (b.get("text") or "")[:120],
            "source": b.get("source"),
            "ts": b.get("ts"),
        } for b in bridges[:10]]
    except Exception:
        digest["scout_bridges"] = []

    # Check-in averages (7-day)
    try:
        from .checkins import rolling_averages
        digest["checkin"] = rolling_averages(days=7)
    except Exception as e:
        digest["checkin"] = {"error": str(e)}

    # Patterns detected recently
    try:
        from .patterns import read_recent_patterns
        digest["patterns"] = read_recent_patterns(hours=7 * 24)[:5]
    except Exception:
        digest["patterns"] = []

    # Action timeline (W14.4): brief_weekly через workspace.
    try:
        from .memory import workspace
        bw_idx = workspace.add(
            actor="baddle",
            action_kind="brief_weekly",
            text=text,
            urgency=0.6,
            accumulate=False,
            ttl_seconds=7 * 24 * 3600,
            extras={"decisions_this_week": len(recent),
                     "mode_counts": mode_counts,
                     "lang": lang},
        )
        workspace.commit([bw_idx])
    except Exception as e:
        log.debug(f"[workspace] brief_weekly record failed: {e}")

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

    Alerts теперь выводятся из sync_regime (FLOW/REST/PROTECT/CONFESS) плюс
    watchdog Scout/DMN. Жёсткие пороги остаются fallback'ом на случай когда
    UserState ещё не набрал сигналов.
    """
    from .substrate.horizon import get_global_state
    ctx = _get_context()
    hrv_state = ctx["hrv"] or {}
    capacity = ctx.get("capacity") or {}
    alerts = []

    cs = get_global_state()
    regime = cs.sync_regime
    sync_err = cs.sync_error

    # Sync-regime driven advice (prime-directive слой)
    if regime == "rest":
        alerts.append({
            "type": "regime_rest",
            "severity": "info",
            "text": "Оба устали. Предлагаю сделать паузу.",
            "text_en": "We're both low. Let's take a pause.",
            "regime": regime, "sync_error": round(sync_err, 2),
        })
    elif regime == "protect":
        alerts.append({
            "type": "regime_protect",
            "severity": "info",
            "text": "Ты устал — возьму на себя. Отвечаю короче, сложное отложим.",
            "text_en": "You're tired — I'll handle it. Short answers, heavy stuff later.",
            "regime": regime, "sync_error": round(sync_err, 2),
        })
    elif regime == "confess":
        alerts.append({
            "type": "regime_confess",
            "severity": "info",
            "text": "Мне нужно подумать — дай минуту.",
            "text_en": "I need a moment to think.",
            "regime": regime, "sync_error": round(sync_err, 2),
        })

    # Hard floors (Phase C: capacity_zone red — critical signal независимо от regime)
    if capacity.get("zone") == "red":
        reason_ru = _capacity_reason_text(capacity.get("reason"), "ru")
        reason_en = _capacity_reason_text(capacity.get("reason"), "en")
        alerts.append({
            "type": "capacity_red",
            "severity": "warning",
            "text": f"Capacity red — {reason_ru}. Отложи сложные решения.",
            "text_en": f"Capacity red — {reason_en}. Postpone heavy decisions.",
            "zone": "red",
            "reason": capacity.get("reason"),
        })
    if hrv_state:
        coh = hrv_state.get("coherence")
        if coh is not None and coh < 0.25:
            alerts.append({
                "type": "low_coherence",
                "severity": "warning",
                "text": f"Coherence {coh:.2f}. Минутку подыши.",
                "text_en": f"Coherence {coh:.2f}. Take a breath.",
            })

    # Activity-zone alerts (4-зонная классификация HRV × движение)
    try:
        from .substrate.rgk import get_global_rgk
        az = get_global_rgk().activity_zone()
        if az.get("key") == "overload":
            alerts.append({
                "type": "zone_overload",
                "severity": "warning",
                "text": f"🔴 {az['label']}. {az['advice']}",
                "text_en": "Overload detected: high activity + low HRV. Ease off.",
                "zone": "overload",
            })
        elif az.get("key") == "stress_rest":
            alerts.append({
                "type": "zone_stress_rest",
                "severity": "info",
                "text": f"🟡 {az['label']}. {az['advice']}",
                "text_en": "Stress at rest: low HRV without movement. Breathe.",
                "zone": "stress_rest",
            })
    except Exception:
        pass

    # Background cognitive-loop alerts (Scout, DMN)
    loop = get_cognitive_loop()
    loop_alerts = loop.get_alerts(clear=True)
    alerts.extend(loop_alerts)

    return jsonify({
        "alerts": alerts,
        "count": len(alerts),
        "capacity": capacity,
        "hrv": hrv_state,
        "sync_regime": regime,
        "sync_error": round(sync_err, 3),
        "loop": loop.get_status(),
    })


# ── Cognitive loop control ──────────────────────────────────────────────

def _loop_start():
    loop = get_cognitive_loop()
    loop.start()
    return jsonify({"ok": True, "status": loop.get_status()})

def _loop_stop():
    get_cognitive_loop().stop()
    return jsonify({"ok": True})

def _loop_status():
    return jsonify(get_cognitive_loop().get_status())


assistant_bp.add_url_rule("/loop/start",  "loop_start",  _loop_start,  methods=["POST"])
assistant_bp.add_url_rule("/loop/stop",   "loop_stop",   _loop_stop,   methods=["POST"])
assistant_bp.add_url_rule("/loop/status", "loop_status", _loop_status, methods=["GET"])
