"""Baddle Assistant — chat-first interface.

One endpoint turns user messages into graph operations.
User sees conversation. Baddle runs the graph underneath.

W14.6a: state helpers extracted в src/io/state.py. Public API re-exports
ниже для backward-compat (cognitive_loop / detectors / assistant_exec /
tests импортируют через `from .assistant import _load_state` etc).
"""
import logging
import time
from datetime import datetime, timedelta
from typing import Optional, Dict

from flask import request, jsonify

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


# State helpers extracted в src/io/state.py (W14.6a). Re-exports — public API
# для cognitive_loop / detectors / assistant_exec / tests.
from .io.state import (
    _detect_category,
    _load_state,
    _save_state,
    _today_date,
    _capacity_reason_text,
    _ensure_daily_reset,
    _log_decision,
    _get_context,
    _response_for_mode,
    _STATE_FILE,
)

# Blueprint moved в src/io/routes/__init__.py (W14.6b0). Re-export для
# `from src.assistant import assistant_bp` (used by ui.py).
from .io.routes import assistant_bp

__all__ = [
    "assistant_bp",
    "_detect_category", "_load_state", "_save_state", "_today_date",
    "_capacity_reason_text", "_ensure_daily_reset", "_log_decision",
    "_get_context", "_response_for_mode", "_STATE_FILE",
    "get_hrv_manager", "get_mode",
]


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
    if response_text:
        from .memory import workspace
        from .graph_logic import link_chat_continuation
        br_idx = workspace.record_committed(
            actor="baddle", action_kind="baddle_reply",
            text=response_text[:200], urgency=1.0, accumulate=False,
            extras={"mode": mode_id, "intent": intent,
                     "cards_count": len(cards) if cards else 0},
        )
        if br_idx is not None:
            link_chat_continuation(br_idx)

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


# Profile routes extracted в src/io/routes/profile.py (W14.6b2).


# Goals routes extracted в src/io/routes/goals.py (W14.6b1).
# /goals/* + /goals/solved/* + _push_event_to_chat helper (используется
# activity/checkin/plans для chat events — W14.6b2 продолжит мигрировать
# через `from .goals import _push_event_to_chat`).


# ── Activity log endpoints (ручной ground-truth трекер) ──────────────

# Activity routes extracted в src/io/routes/activity.py (W14.6b3)
# (включая _sync_activity_to_graph helper).
# Plans routes — src/io/routes/plans.py
# Check-ins routes — src/io/routes/checkins.py




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
            try:
                from .memory import workspace
                before_ts = time.time()
                fn = getattr(cl, name)
                t0 = time.time()
                fn()
                entry["elapsed_s"] = round(time.time() - t0, 3)
                # W14.5c-2: alerts читаются из графа (since_ts cursor) вместо
                # in-memory queue.
                new_alerts = workspace.list_recent_alerts(since_ts=before_ts)
                if new_alerts:
                    entry["status"] = "alert_emitted"
                    entry["alerts"] = [
                        {"type": n.get("action_kind"),
                         "severity": n.get("severity"),
                         "text": (n.get("text") or "")[:140]}
                        for n in new_alerts
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
                # 3. user_chat через workspace (W14.2): один path для всего
                # что попадает в граф+chat.
                from .memory import workspace
                from .graph_logic import _current_snapshot, link_chat_continuation
                ctx = _current_snapshot()
                ctx["sentiment"] = round(float(sentiment), 3)
                uc_idx = workspace.record_committed(
                    actor="user", action_kind="user_chat",
                    text=msg_text[:200], urgency=1.0, accumulate=False,
                    context=ctx,
                )
                if uc_idx is not None:
                    link_chat_continuation(uc_idx)
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

    # Action timeline (W14.4): brief_morning. accumulate=False + immediate
    # commit — briefing explicit user request.
    from .memory import workspace
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

    # Scout bridges за неделю — graph query через workspace.list_recent_bridges
    # (W14.5c-3: _recent_bridges deque удалена, bridges теперь committed actions).
    try:
        from .memory import workspace
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
    from .memory import workspace
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
    from .substrate.horizon import get_global_state
    ctx = _get_context()
    hrv_state = ctx["hrv"] or {}
    capacity = ctx.get("capacity") or {}
    alerts = []

    cs = get_global_state()
    regime = cs.sync_regime
    sync_err = cs.sync_error

    loop = get_cognitive_loop()
    try:
        from .memory import workspace
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
