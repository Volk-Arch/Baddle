"""Baddle Assistant — chat-first interface.

One endpoint turns user messages into graph operations.
User sees conversation. Baddle runs the graph underneath.
"""
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict

from flask import Blueprint, request, jsonify

log = logging.getLogger(__name__)

from .modes import get_mode
from .hrv_manager import get_manager as get_hrv_manager
from .cognitive_loop import get_cognitive_loop
from .assistant_exec import execute as execute_mode


# ── Decision cost by mode complexity (MindBalance intuition) ────────────────

# Сложность режима определяет энергоёмкость решения. Разные моды тратят
# разные объёмы — простой brainstorm ≠ tournament с LLM-судейством.
_MODE_COST = {
    # simple — быстрые, почти free flow
    "free": 3, "scout": 3, "fan": 3,
    # moderate — направленные, один LLM-путь
    "vector": 6, "horizon": 6, "rhythm": 4, "bayes": 7,
    # complex — multi-step, AND/OR cluster
    "builder": 10, "pipeline": 10, "cascade": 10, "scales": 8,
    # critical — XOR с LLM-judge + смысловая ответственность
    "tournament": 12, "dispute": 12,
    # race — быстрее XOR
    "race": 6,
}
_DEFAULT_COST = 6


def _decision_cost(mode_id: str) -> int:
    """Стоимость решения в daily energy единицах по mode_id."""
    return _MODE_COST.get(mode_id, _DEFAULT_COST)


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

_STATE_FILE = Path(__file__).parent.parent / "user_state.json"


def _load_state() -> dict:
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
            "daily_spent": 0.0,   # сумма энергии потраченной за сегодня (дробная)
            "last_reset_date": None,
            "last_interaction": None,
            "total_decisions": 0,
            "streaks": {},       # habit_name → consecutive_days
            "history": [],       # last 100 interactions (trimmed)
        }
    # Восстановим UserState из блока в файле (persistence между сессиями)
    try:
        from .user_state import UserState, set_user_state
        us_dump = data.get("user_state_dump")
        if isinstance(us_dump, dict):
            set_user_state(UserState.from_dict(us_dump))
    except Exception as e:
        print(f"[assistant] user_state restore error: {e}")
    return data


def _save_state(state: dict):
    # Сериализуем текущий UserState вместе с остальным для continuity
    try:
        from .user_state import get_user_state
        state["user_state_dump"] = get_user_state().to_dict()
    except Exception:
        pass
    try:
        _STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"[assistant] state save error: {e}")


def _today_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _ensure_daily_reset(state: dict) -> dict:
    """Reset daily counters if date changed. Полуночный хук восстанавливает
    long_reserve (ночное восстановление из MindBalance v2)."""
    today = _today_date()
    if state.get("last_reset_date") != today:
        # Ночь прошла — восстановим long reserve через HRV (если был)
        prev_date = state.get("last_reset_date")
        state["decisions_today"] = 0
        state["daily_spent"] = 0.0
        state["last_reset_date"] = today
        if prev_date:
            try:
                from .user_state import get_user_state
                hrv_mgr = get_hrv_manager()
                rec = None
                if hrv_mgr.is_running:
                    rec = (hrv_mgr.get_baddle_state() or {}).get("energy_recovery")
                get_user_state().recover_long_reserve(hrv_recovery=rec)
            except Exception as e:
                print(f"[assistant] overnight recovery error: {e}")
    return state


def _compute_energy(state: dict, hrv_recovery: Optional[float] = None) -> dict:
    """Compute current energy level. Dual-pool: daily + long_reserve.

    daily_spent — фактически потраченная сегодня энергия (модально-взвешенная,
    см. _decision_cost). Ceiling модулируется HRV recovery.
    long_reserve — медленный пул, тратится при daily<20 как подстраховка.
    """
    daily_spent = float(state.get("daily_spent", 0.0))
    base_max = 100.0
    if hrv_recovery is not None:
        base_max = 40 + 60 * hrv_recovery
    daily_remaining = max(0.0, base_max - daily_spent)

    from .user_state import get_user_state
    user = get_user_state()
    pool = user.energy_snapshot(state.get("decisions_today", 0))
    return {
        "energy": round(daily_remaining, 0),
        "max": round(base_max, 0),
        "decisions_today": state.get("decisions_today", 0),
        "daily_spent": round(daily_spent, 1),
        "recovery": hrv_recovery,
        "long_reserve": pool["long_reserve"],
        "long_reserve_max": pool["long_reserve_max"],
        "long_reserve_pct": pool["long_reserve_pct"],
        "burnout_risk": pool["burnout_risk"],
    }


def _log_decision(state: dict, kind: str, meta: dict = None, mode_id: str = None,
                  hrv_recovery: Optional[float] = None):
    """Record decision. Debits energy по сложности mode_id (MindBalance intuition).

    Dual-pool: если daily<20 → cascade в long_reserve (см. UserState.debit_energy).
    """
    cost = _decision_cost(mode_id) if mode_id else _DEFAULT_COST
    state["decisions_today"] = state.get("decisions_today", 0) + 1
    state["total_decisions"] = state.get("total_decisions", 0) + 1
    state["last_interaction"] = time.time()

    # Dual-pool debit
    base_max = 40 + 60 * hrv_recovery if hrv_recovery is not None else 100.0
    daily_remaining = max(0.0, base_max - float(state.get("daily_spent", 0.0)))
    debit = {"daily_used": cost, "long_used": 0.0}
    try:
        from .user_state import get_user_state
        debit = get_user_state().debit_energy(cost, daily_remaining)
    except Exception as e:
        print(f"[assistant] debit error: {e}")
    state["daily_spent"] = float(state.get("daily_spent", 0.0)) + debit["daily_used"]

    entry = {
        "ts": time.time(), "kind": kind,
        "cost": cost, "daily_used": round(debit["daily_used"], 1),
        "long_used": round(debit["long_used"], 1),
    }
    if meta:
        entry.update(meta)
    state.setdefault("history", []).append(entry)
    if len(state["history"]) > 200:
        state["history"] = state["history"][-200:]


# ── Shared context helper (state + HRV + energy) ──────────────────────

def _get_context(reset_daily: bool = True) -> Dict:
    """Load user state + HRV snapshot + computed energy.

    Returns:
      {
        "state": dict (loaded user_state.json, daily-reset applied),
        "hrv": dict | None (baddle_state or None if HRV off),
        "energy": dict (computed from state + hrv.energy_recovery),
      }
    """
    state = _load_state()
    if reset_daily:
        state = _ensure_daily_reset(state)

    hrv_mgr = get_hrv_manager()
    hrv_state = hrv_mgr.get_baddle_state() if hrv_mgr.is_running else None
    recovery = hrv_state.get("energy_recovery") if hrv_state else None
    energy = _compute_energy(state, recovery)

    return {"state": state, "hrv": hrv_state, "energy": energy}


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

    # Inject NE spike — user engagement = Horizon takes budget from DMN
    from .horizon import get_global_state
    from .user_state import get_user_state
    cs = get_global_state()
    cs.inject_ne(0.4)

    # User signals: timing + message length (before state logging so EMA updated)
    user = get_user_state()
    user.update_from_timing()
    user.update_from_message(message)

    ctx = _get_context()
    state, hrv_state, energy = ctx["state"], ctx["hrv"], ctx["energy"]
    user.update_from_energy(state.get("decisions_today", 0))

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
    profile_hint = (profile_summary_for_prompt([detected_category], lang=lang,
                                                profile=_user_profile)
                    if detected_category else "")

    # Recent context from state_graph (last 3 user-initiated actions)
    context_parts = []
    try:
        from .state_graph import get_state_graph
        sg = get_state_graph()
        recent = [e for e in sg.tail(10) if e.get("user_initiated")][-3:]
        for e in recent:
            context_parts.append(e.get("reason", "")[:60])
    except Exception:
        pass
    context = " | ".join(context_parts)

    # ── ONE LLM call: mode + intent + confidence ──
    classification = classify_intent_llm(message, context=context, state_hint=state_hint,
                                         profile_hint=profile_hint, lang=lang)
    mode_id = classification.get("mode", "free")
    intent = classification.get("intent", "direct")
    confidence = classification.get("confidence", 0.5)
    response = _response_for_mode(mode_id, message, lang)

    # Check energy — warn if low
    warnings = []
    if energy["energy"] < 20:
        warnings.append({
            "type": "low_energy",
            "text": "Энергия низкая. Сложные решения лучше оставить на утро." if lang == "ru"
                    else "Energy low. Heavy decisions are better left for morning.",
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
            "energy": energy, "hrv": hrv_state, "warnings": warnings,
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
            "energy": energy, "hrv": hrv_state, "warnings": warnings,
            "awaiting_input": True, "graph_updated": False,
            "lang": lang, "intent": intent, "confidence": confidence,
            "classify_source": classification.get("source"),
        })

    # ── Actually execute the mode (profile_hint injects constraints) ──
    exec_result = execute_mode(mode_id, message, lang, profile_hint=profile_hint)
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

    # Log this interaction
    _log_decision(state, kind="assist",
                  meta={"mode": mode_id, "message": message[:200],
                        "intent": intent, "confidence": confidence},
                  mode_id=mode_id,
                  hrv_recovery=(hrv_state or {}).get("energy_recovery"))
    _save_state(state)

    return jsonify({
        "text": response_text,
        "intro": response["intro"],
        "mode": mode_id,
        "mode_name": response["mode_name"],
        "message_echo": message,
        "cards": cards,
        "steps": steps,
        "energy": energy,
        "hrv": hrv_state,
        "warnings": warnings,
        "awaiting_input": exec_result.get("awaiting_input", False),
        "graph_updated": len(cards) > 0,
        "lang": lang,
        "intent": intent,
        "confidence": confidence,
        "classify_source": classification.get("source"),
        "error": exec_result.get("error"),
    })


# ── Status / energy ────────────────────────────────────────────────────

@assistant_bp.route("/assist/status", methods=["GET"])
def assist_status():
    """Current user state — energy, HRV, recent activity."""
    ctx = _get_context()
    state, hrv_state, energy = ctx["state"], ctx["hrv"], ctx["energy"]
    return jsonify({
        "energy": energy,
        "hrv": hrv_state,
        "total_decisions": state.get("total_decisions", 0),
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
    from .horizon import get_global_state
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
    # Mirror signal into UserState (accept ↑ dopamine, reject ↑ burnout)
    from .user_state import get_user_state
    get_user_state().update_from_feedback(kind)
    return jsonify({"ok": True, "neurochem": cs.get_metrics().get("neurochem", {})})


@assistant_bp.route("/assist/camera", methods=["POST"])
def assist_camera():
    """Toggle Camera mode (v8c) — sensory deprivation.

    Body: { "enabled": true/false }
    When enabled, llm_disabled=True: tick works only on existing embeddings,
    no new LLM calls. Useful for reflection + finding hidden patterns in
    what's already there.
    """
    from .horizon import get_global_state
    d = request.get_json(force=True) or {}
    enabled = bool(d.get("enabled", False))
    cs = get_global_state()
    cs.llm_disabled = enabled
    return jsonify({"ok": True, "camera": enabled})


@assistant_bp.route("/assist/state", methods=["GET"])
def assist_state():
    """Return full CognitiveState metrics (for UI panel, diagnostics).

    UserState через `user_state` ключ уже включает: dopamine/serotonin/
    norepinephrine/burnout, expectation/reality/surprise/imbalance (signed
    prediction error), named_state (Voronoi region), long_reserve (dual-pool).
    """
    from .horizon import get_global_state
    return jsonify(get_global_state().get_metrics())


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
    from datetime import datetime, timezone
    import time as _t

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
            "dopamine": neuro.get("dopamine"),
            "serotonin": neuro.get("serotonin"),
            "norepinephrine": neuro.get("norepinephrine"),
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


@assistant_bp.route("/assist/simulate-day", methods=["POST"])
def assist_simulate_day():
    """Day planning simulator — предсказать end-of-day state от плана решений.

    Body: {
      "plan": [{"mode": "tournament"}, {"mode": "fan"}, ...]
      "hrv_recovery": 0.7   (optional, 0..1; если не задан — текущий HRV)
    }

    Симулирует по порядку: списывает cost из daily/long через UserState.debit_energy,
    прокачивает (decisions_today, daily_spent). Возвращает прогноз:
    end-of-day energy, long_reserve после, burnout_risk, predicted named_state.
    """
    from .user_state import UserState, get_user_state

    d = request.get_json(force=True) or {}
    plan = d.get("plan") or []
    hrv_mgr = get_hrv_manager()
    live_recovery = None
    if hrv_mgr.is_running:
        live_recovery = (hrv_mgr.get_baddle_state() or {}).get("energy_recovery")
    hrv_recovery = d.get("hrv_recovery", live_recovery)

    # Клонируем UserState чтобы симуляция не изменила живой
    current = get_user_state()
    sim = UserState.from_dict(current.to_dict())

    # Стартовое daily_remaining — текущий reseted ceiling минус реально потраченное
    state = _load_state()
    state = _ensure_daily_reset(state)
    base_max = 40 + 60 * hrv_recovery if hrv_recovery is not None else 100.0
    daily_spent = float(state.get("daily_spent", 0.0))

    steps = []
    for step in plan:
        mode = step.get("mode") or "free"
        cost = _decision_cost(mode)
        daily_rem = max(0.0, base_max - daily_spent)
        debit = sim.debit_energy(cost, daily_rem)
        daily_spent += debit["daily_used"]
        steps.append({
            "mode": mode, "cost": cost,
            "daily_used": round(debit["daily_used"], 1),
            "long_used": round(debit["long_used"], 1),
            "daily_remaining_after": round(max(0.0, base_max - daily_spent), 1),
            "long_reserve_after": round(sim.long_reserve, 1),
        })
        # Burnout каждое решение накапливает — приблизим update_from_energy
        sim.burnout = min(1.0, sim.burnout + 0.005)

    sim._clamp()
    ns = sim.named_state
    long_pct = sim.long_reserve / 2000.0
    total_cost = sum(s["cost"] for s in steps)
    total_daily = sum(s["daily_used"] for s in steps)
    total_long = sum(s["long_used"] for s in steps)

    return jsonify({
        "plan_size": len(plan),
        "total_cost": total_cost,
        "total_daily_used": round(total_daily, 1),
        "total_long_used": round(total_long, 1),
        "steps": steps,
        "end_of_day": {
            "daily_remaining": round(max(0.0, base_max - daily_spent), 1),
            "daily_max": round(base_max, 1),
            "long_reserve": round(sim.long_reserve, 1),
            "long_reserve_pct": round(long_pct, 3),
            "burnout_risk": round(1.0 - long_pct, 3),
            "predicted_named_state": {
                "key": ns["key"], "label": ns["label"], "advice": ns["advice"],
            },
            "dopamine": round(sim.dopamine, 3),
            "serotonin": round(sim.serotonin, 3),
            "norepinephrine": round(sim.norepinephrine, 3),
            "burnout": round(sim.burnout, 3),
        },
    })


@assistant_bp.route("/assist/named-states", methods=["GET"])
def assist_named_states():
    """UI map: 10 регионов из MindBalance-Voronoi с координатами и advice."""
    from .user_state_map import list_named_states
    return jsonify({"states": list_named_states()})


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
    """Query: ?status=open|done|abandoned &workspace=X &category=Y &limit=N"""
    from .goals_store import list_goals
    status = request.args.get("status")
    ws = request.args.get("workspace")
    cat = request.args.get("category")
    try:
        limit = int(request.args.get("limit", 100))
    except ValueError:
        limit = 100
    return jsonify({"goals": list_goals(status=status, workspace=ws,
                                        category=cat, limit=limit)})


@assistant_bp.route("/goals/stats", methods=["GET"])
def goals_stats():
    from .goals_store import goal_stats
    return jsonify(goal_stats())


@assistant_bp.route("/goals/add", methods=["POST"])
def goals_add():
    """Manual add (obычно создаётся автоматом из /graph/add node_type=goal).

    Body: {text, mode, workspace, priority, deadline, category}
    """
    from .goals_store import add_goal
    d = request.get_json(force=True) or {}
    gid = add_goal(
        text=d.get("text", ""),
        mode=d.get("mode", "horizon"),
        workspace=d.get("workspace", "main"),
        priority=d.get("priority"),
        deadline=d.get("deadline"),
        category=d.get("category"),
    )
    return jsonify({"ok": True, "id": gid})


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


@assistant_bp.route("/goals/update", methods=["POST"])
def goals_update():
    """Body: {id, fields: {priority, deadline, category, ...}}"""
    from .goals_store import update_goal
    d = request.get_json(force=True) or {}
    update_goal(d.get("id", ""), d.get("fields") or {})
    return jsonify({"ok": True})


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
    question = (d.get("question") or "").strip()
    requested_mode = d.get("mode")

    nodes = _graph["nodes"]
    goal_nodes = [(i, n) for i, n in enumerate(nodes)
                  if n.get("type") == "goal" and n.get("depth", 0) >= 0]
    goal_idx, goal_node = goal_nodes[0] if goal_nodes else (None, None)
    mode_id = requested_mode or (goal_node.get("mode") if goal_node else None) or \
              _graph.get("meta", {}).get("mode", "horizon")

    # NE spike on any /graph/assist activity (dialogical loop is engagement too)
    from .horizon import get_global_state
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
    unverified = [n for n in nodes if n.get("type") in ("hypothesis", "thought")
                  and n.get("confidence", 0.5) < 0.6][:3]

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

@assistant_bp.route("/assist/morning", methods=["POST"])
def assist_morning():
    """Generate a morning briefing based on HRV recovery + pending tasks."""
    lang = request.get_json(force=True).get("lang", "ru") if request.is_json else "ru"

    ctx = _get_context()
    state, hrv_state, energy = ctx["state"], ctx["hrv"] or {}, ctx["energy"]
    recovery = (hrv_state or {}).get("energy_recovery") if hrv_state else None

    # Compose greeting
    recovery_pct = round((recovery or 0.7) * 100)
    energy_val = energy["energy"]

    if lang == "ru":
        if recovery_pct >= 80:
            greeting = f"Доброе утро. Восстановление {recovery_pct}%. Отличный день для сложных задач."
        elif recovery_pct >= 60:
            greeting = f"Доброе утро. Восстановление {recovery_pct}%. Средний день — начни с важного."
        else:
            greeting = f"Доброе утро. Восстановление {recovery_pct}%. Береги энергию, лёгкие задачи первыми."
        greeting += f" Бюджет: {int(energy_val)}/100."
    else:
        if recovery_pct >= 80:
            greeting = f"Good morning. Recovery {recovery_pct}%. Great day for complex tasks."
        elif recovery_pct >= 60:
            greeting = f"Good morning. Recovery {recovery_pct}%. Medium day — start with priorities."
        else:
            greeting = f"Good morning. Recovery {recovery_pct}%. Save energy, light tasks first."
        greeting += f" Budget: {int(energy_val)}/100."

    _log_decision(state, kind="morning_briefing")
    _save_state(state)

    return jsonify({
        "text": greeting,
        "energy": energy,
        "hrv": hrv_state,
        "recovery_pct": recovery_pct,
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

    return jsonify({
        "text": text,
        "decisions_this_week": len(recent),
        "mode_counts": mode_counts,
        "streaks": streaks,
        "daily_series": daily_series,
        "hrv_trend": hrv_trend,
    })


# ── Proactive alerts (polled by UI) ────────────────────────────────────

@assistant_bp.route("/assist/alerts", methods=["GET"])
def assist_alerts():
    """Return pending proactive alerts. UI polls this periodically.

    Alerts теперь выводятся из sync_regime (FLOW/REST/PROTECT/CONFESS) плюс
    watchdog Scout/DMN. Жёсткие пороги остаются fallback'ом на случай когда
    UserState ещё не набрал сигналов.
    """
    from .horizon import get_global_state
    ctx = _get_context()
    state, hrv_state, energy = ctx["state"], ctx["hrv"] or {}, ctx["energy"]
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

    # Hard floors (независимо от regime — критические пороги должны звенеть)
    if energy["energy"] < 20 and state.get("decisions_today", 0) > 5:
        alerts.append({
            "type": "energy_critical",
            "severity": "warning",
            "text": "Энергия <20. Отложи сложные решения до утра.",
            "text_en": "Energy <20. Postpone heavy decisions until morning.",
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

    # Background cognitive-loop alerts (Scout, DMN)
    loop = get_cognitive_loop()
    loop_alerts = loop.get_alerts(clear=True)
    alerts.extend(loop_alerts)

    return jsonify({
        "alerts": alerts,
        "count": len(alerts),
        "energy": energy,
        "hrv": hrv_state,
        "sync_regime": regime,
        "sync_error": round(sync_err, 3),
        "loop": loop.get_status(),
    })


# ── Cognitive loop control (/loop/* — canonical; /watchdog/* alias for compat) ─

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
# Legacy URL aliases — существующие клиенты (docs/TODO примеры) дёргают /watchdog/*
assistant_bp.add_url_rule("/watchdog/start",  "watchdog_start",  _loop_start,  methods=["POST"])
assistant_bp.add_url_rule("/watchdog/stop",   "watchdog_stop",   _loop_stop,   methods=["POST"])
assistant_bp.add_url_rule("/watchdog/status", "watchdog_status", _loop_status, methods=["GET"])
