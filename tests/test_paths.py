"""Тесты paths.py — single path, без легаси/миграций."""
import pytest
from pathlib import Path


def test_data_dir_structure():
    from src import paths

    # Базовые поля должны существовать
    assert paths.PROJECT_ROOT.exists()
    assert paths.DATA_DIR.parent == paths.PROJECT_ROOT
    # Все path-constants заданы
    for attr in ("SETTINGS_FILE", "USER_STATE_FILE", "USER_PROFILE_FILE",
                 "GOALS_FILE", "ACTIVITY_FILE", "CHECKINS_FILE",
                 "PATTERNS_FILE", "PLANS_FILE", "ROLES_FILE",
                 "TEMPLATES_FILE", "STATE_GRAPH_FILE",
                 "STATE_EMBEDDINGS_FILE", "STATE_GRAPH_ARCHIVE"):
        val = getattr(paths, attr)
        assert isinstance(val, Path)
        # Все под DATA_DIR
        assert paths.DATA_DIR in val.parents


def test_no_legacy_migration_function():
    """migrate_to_data_dir() должен быть удалён (single path)."""
    from src import paths
    assert not hasattr(paths, "migrate_to_data_dir")
    assert not hasattr(paths, "_MIGRATION_MAP")


def test_ensure_data_dir_idempotent(tmp_path, monkeypatch):
    from src import paths
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "newdata")
    d = paths.ensure_data_dir()
    assert d.exists()
    d2 = paths.ensure_data_dir()
    assert d == d2


def test_resettable_list_excludes_settings(temp_data_dir):
    from src.paths import get_resettable_files, SETTINGS_FILE, USER_STATE_FILE

    rf = get_resettable_files()
    assert SETTINGS_FILE not in rf   # settings сохраняется
    assert USER_STATE_FILE in rf     # state удаляется


def test_no_migrations_module():
    """migrations.py удалён (single-path, нет legacy)."""
    with pytest.raises(ImportError):
        import src.migrations  # noqa
