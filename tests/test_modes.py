"""Smoke tests для 14 mode пресетов в src/modes.py.

Проверяет что весь registry загружается, каждый preset имеет нужную
структуру (precision/policy/target), и список consistent (14 modes).
"""
import pytest

from src.modes import _MODES, get_mode, list_modes, DEFAULT_MODE


# Канонические 14 modes (из src/modes.py _MODES dict).
EXPECTED_MODES = [
    "free", "scout", "vector", "rhythm", "horizon",
    "builder", "pipeline", "cascade", "scales",
    "race", "fan", "tournament", "dispute", "bayes",
]


def test_modes_registry_size():
    """14 modes ровно — не меняем без обновления docs/horizon-design.md table."""
    assert len(_MODES) == 14
    assert set(_MODES.keys()) == set(EXPECTED_MODES)


def test_list_modes_returns_14():
    modes = list_modes()
    assert len(modes) == 14
    assert {m["id"] for m in modes} == set(EXPECTED_MODES)


def test_default_mode_exists():
    assert DEFAULT_MODE in _MODES
    assert get_mode(DEFAULT_MODE)["id"] == DEFAULT_MODE


def test_unknown_mode_falls_back_to_default():
    """get_mode(invalid) → возвращает дефолтный, не падает."""
    m = get_mode("nonexistent_mode_xyz")
    assert m["id"] == DEFAULT_MODE


@pytest.mark.parametrize("mode_id", EXPECTED_MODES)
def test_mode_preset_structure(mode_id):
    """Каждый mode имеет valid preset: precision ∈ [0,1], policy 4 keys, target ∈ [0,1]."""
    m = get_mode(mode_id)
    assert m["id"] == mode_id

    preset = m["preset"]
    assert "precision" in preset
    assert "policy" in preset
    assert "target" in preset

    # precision ∈ [0, 1]
    assert 0.0 <= preset["precision"] <= 1.0

    # policy — 4 фазы, веса нормированы (sum ≈ 1.0)
    policy = preset["policy"]
    assert set(policy.keys()) == {"generate", "merge", "elaborate", "doubt"}
    weights_sum = sum(policy.values())
    assert abs(weights_sum - 1.0) < 1e-6, f"{mode_id}: policy sum {weights_sum} != 1.0"

    # target_surprise ∈ [0, 1] (целевое удивление в зоне потока)
    assert 0.0 <= preset["target"] <= 1.0


@pytest.mark.parametrize("mode_id", EXPECTED_MODES)
def test_mode_metadata_complete(mode_id):
    """Метаданные UI: name/name_en/goals_count/fields/placeholder/intro/renderer."""
    m = get_mode(mode_id)
    for key in ("name", "name_en", "goals_count", "fields",
                "placeholder", "placeholder_en", "intro", "intro_en",
                "renderer_style"):
        assert key in m, f"{mode_id} missing {key}"
        assert m[key] is not None, f"{mode_id}: {key} is None"

    # goals_count: 0 / 1 / "2+"
    assert m["goals_count"] in (0, 1, "2+"), f"{mode_id}: goals_count {m['goals_count']} invalid"


def test_renderer_styles_known():
    """renderer_style определяет UI card type — должен быть из known set."""
    KNOWN_RENDERERS = {"ideas", "habit", "cluster", "comparative", "dialectical", "bayesian"}
    for mode_id in EXPECTED_MODES:
        m = get_mode(mode_id)
        assert m["renderer_style"] in KNOWN_RENDERERS, \
            f"{mode_id}: unknown renderer {m['renderer_style']}"
