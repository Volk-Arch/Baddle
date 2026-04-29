"""State helpers — load/save user state + context + capacity (W14.6a extract).

Public API (used by src/process/* + src/assistant_exec.py + tests):
- `_load_state` / `_save_state` — atomic JSON persistence для daily counters/
   history + UserState dump через РГК serialize/load.
- `_get_context` — single source for {state, hrv, capacity} в decision-gate.
- `_capacity_reason_text` — i18n перевод reason tags.

Internal к routes:
- `_detect_category` — keyword-first category detection для profile injection
- `_today_date`, `_ensure_daily_reset`, `_log_decision` — daily housekeeping
- `_response_for_mode` — UI confirmation envelope из modes.py
"""
import json
import logging
import threading as _threading
import time
from datetime import datetime
from typing import Optional, Dict

from ..modes import get_mode
from ..paths import USER_STATE_FILE as _STATE_FILE
from ..sensors.manager import get_manager as get_hrv_manager

log = logging.getLogger(__name__)


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


# ── Energy / decisions store ────────────────────────────────────────────

# In-process lock сериализует load↔save между параллельными Flask-threads.
# Устраняет race: thread A читает dump, thread B читает тот же dump,
# оба пишут обратно свои версии → чекмарк теряется. Atomic write через
# temp + replace даёт файловую консистентность; lock — семантическую.
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
                from ..substrate.rgk import get_global_rgk
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
            from ..substrate.rgk import get_global_rgk
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
                from ..substrate.rgk import get_global_rgk
                from ..user_dynamics import rollover_day
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
    from ..substrate.rgk import get_global_rgk
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
