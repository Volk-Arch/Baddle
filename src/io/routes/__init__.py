"""Flask routes split по доменам (W14.6 — extracts из assistant.py).

Blueprint объявляется здесь, child modules регистрируют routes через
`@assistant_bp.route(...)`. Каждый child file — один домен:

  chat.py       — /assist + /assist/state + /assist/feedback + chat/*
  goals.py      — /goals/* (list/add/complete/recurring/constraints/...)
  activity.py   — /activity/* (start/stop/history/...)
  plans.py      — /plan/* (today/add/complete/skip/delete)
  checkins.py   — /checkin/* (add/latest/history)
  profile.py    — /profile/* (get/add/remove/context/learn)
  briefings.py  — /assist/morning + /assist/weekly + /assist/alerts
  misc.py       — /sensor/* + /patterns/* + /debug/* + /assist/decompose

Naming `assistant_bp` сохранено backward-compat (ui.py делает
`from src.assistant import assistant_bp`). assistant.py re-export'ит.
"""
from flask import Blueprint

assistant_bp = Blueprint("assistant", __name__)

# Child modules регистрируют routes (импорт triggers @assistant_bp.route).
from . import goals, profile, activity, plans, checkins
__all__ = ["assistant_bp", "goals", "profile", "activity", "plans", "checkins"]
