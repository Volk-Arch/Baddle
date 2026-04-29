"""Baddle Assistant — chat-first interface (bootstrap shell).

After W14.6 split (54 routes → 8 io/routes/ modules):
- src/io/state.py             — state helpers (_load_state, _get_context, ...)
- src/io/routes/__init__.py   — assistant_bp Blueprint
- src/io/routes/chat.py       — /assist + /assist/* + /assist/chat/* + /loop/*
- src/io/routes/goals.py      — /goals/*
- src/io/routes/profile.py    — /profile/*
- src/io/routes/activity.py   — /activity/*
- src/io/routes/plans.py      — /plan/*
- src/io/routes/checkins.py   — /checkin/*
- src/io/routes/briefings.py  — /assist/morning + /assist/weekly + /assist/alerts
- src/io/routes/misc.py       — /patterns/* + /sensor/* + /debug/* + /assist/decompose
                                + /graph/assist

Этот модуль остаётся как **bootstrap-shell** для backward-compat:
- ui.py делает `from src.assistant import assistant_bp` — re-export ниже.
- cognitive_loop / detectors / assistant_exec / tests делают
  `from src.assistant import _load_state` etc — re-export из io.state.
- conftest fixtures monkeypatch'ят `assistant._STATE_FILE` — re-export.
- test_capacity.py monkeypatch'ит `assistant.get_hrv_manager` — re-export.

`from .io.routes import assistant_bp` triggers __init__.py which imports
все child modules — routes регистрируются как side-effect.
"""
import logging

log = logging.getLogger(__name__)

# Public API re-exports для cognitive_loop / detectors / assistant_exec / tests.
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
from .modes import get_mode
from .sensors.manager import get_manager as get_hrv_manager

# Trigger blueprint + route registration через io.routes/__init__.py
from .io.routes import assistant_bp

__all__ = [
    "assistant_bp",
    "_detect_category", "_load_state", "_save_state", "_today_date",
    "_capacity_reason_text", "_ensure_daily_reset", "_log_decision",
    "_get_context", "_response_for_mode", "_STATE_FILE",
    "get_hrv_manager", "get_mode",
]
