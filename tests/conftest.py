"""Pytest fixtures — изоляция данных каждого теста.

По умолчанию Baddle пишет в `data/goals.jsonl` и друзей. Тесты не должны
загрязнять реальные данные юзера. Здесь:
- `temp_data_dir` — создаёт tmp-папку + monkey-patch paths module
- `clean_goals` — сбрасывает goals_store для каждого теста
"""
import pytest
import shutil
import tempfile
from pathlib import Path


@pytest.fixture
def temp_data_dir(monkeypatch, tmp_path):
    """Перенаправляет все пути paths-модуля в tmp_path."""
    from src import paths

    tmp_data = tmp_path / "data"
    tmp_data.mkdir(parents=True, exist_ok=True)

    # Патчим все константы
    monkeypatch.setattr(paths, "DATA_DIR", tmp_data)
    monkeypatch.setattr(paths, "SETTINGS_FILE",       tmp_data / "settings.json")
    monkeypatch.setattr(paths, "USER_STATE_FILE",     tmp_data / "user_state.json")
    monkeypatch.setattr(paths, "USER_PROFILE_FILE",   tmp_data / "user_profile.json")
    monkeypatch.setattr(paths, "GOALS_FILE",          tmp_data / "goals.jsonl")
    monkeypatch.setattr(paths, "ACTIVITY_FILE",       tmp_data / "activity.jsonl")
    monkeypatch.setattr(paths, "CHECKINS_FILE",       tmp_data / "checkins.jsonl")
    monkeypatch.setattr(paths, "PATTERNS_FILE",       tmp_data / "patterns.jsonl")
    monkeypatch.setattr(paths, "PLANS_FILE",          tmp_data / "plans.jsonl")
    monkeypatch.setattr(paths, "STATE_GRAPH_FILE",    tmp_data / "state_graph.jsonl")

    # goals_store уже импортирован и держит ссылку на _GOALS_FILE — патчим его
    from src import goals_store
    monkeypatch.setattr(goals_store, "_GOALS_FILE", tmp_data / "goals.jsonl")
    monkeypatch.setattr(goals_store, "_GOALS_ARCHIVE_DIR", tmp_data / "archives")

    from src import activity_log
    monkeypatch.setattr(activity_log, "_ACTIVITY_FILE", tmp_data / "activity.jsonl")

    from src import plans
    monkeypatch.setattr(plans, "_PLANS_FILE", tmp_data / "plans.jsonl")

    # checkins/patterns/user_profile тоже делают `from .paths import X as Y`
    # — monkey-patch на paths.Y не меняет module.Y. Патчим явно.
    from src import checkins
    monkeypatch.setattr(checkins, "_CHECKIN_FILE", tmp_data / "checkins.jsonl")

    from src import patterns
    monkeypatch.setattr(patterns, "_PATTERNS_FILE", tmp_data / "patterns.jsonl")
    monkeypatch.setattr(patterns, "_ACTIVITY_FILE", tmp_data / "activity.jsonl")

    try:
        from src import user_profile
        monkeypatch.setattr(user_profile, "_PROFILE_FILE",
                            tmp_data / "user_profile.json")
    except ImportError:
        pass

    try:
        from src import api_backend
        monkeypatch.setattr(api_backend, "_SETTINGS_FILE",
                            tmp_data / "settings.json")
    except ImportError:
        pass

    try:
        from src import assistant
        monkeypatch.setattr(assistant, "_STATE_FILE",
                            tmp_data / "user_state.json")
    except ImportError:
        pass

    yield tmp_data


@pytest.fixture
def clean_router_cache():
    """Сбросить LRU-кэш intent_router."""
    from src import intent_router
    intent_router._cache.clear()
    yield
    intent_router._cache.clear()
