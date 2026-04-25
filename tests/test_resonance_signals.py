"""Unit tests для frequency_regime + focus_residue (cheap items session
после Phase A+B). Spec: planning/resonance-code-changes.md §2-3.
"""
import time
from unittest.mock import patch

import pytest

from src.user_state import UserState


# ── frequency_regime ───────────────────────────────────────────────────────

def test_frequency_regime_flat_when_no_hrv():
    us = UserState()
    assert us.frequency_regime == "flat"


def test_frequency_regime_long_wave():
    us = UserState()
    us.hrv_coherence = 0.7
    us.hrv_rmssd = 40
    us.norepinephrine = 0.3
    assert us.frequency_regime == "long_wave"


def test_frequency_regime_short_wave_low_coherence():
    us = UserState()
    us.hrv_coherence = 0.3
    assert us.frequency_regime == "short_wave"


def test_frequency_regime_short_wave_high_ne():
    us = UserState()
    us.hrv_coherence = 0.5
    us.norepinephrine = 0.8
    assert us.frequency_regime == "short_wave"


def test_frequency_regime_mixed():
    us = UserState()
    us.hrv_coherence = 0.5
    us.hrv_rmssd = 25
    us.norepinephrine = 0.55
    assert us.frequency_regime == "mixed"


def test_frequency_regime_long_wave_requires_all_three():
    """coherence>0.6 alone не достаточно — нужно ещё RMSSD>30 и NE<0.5."""
    us = UserState()
    us.hrv_coherence = 0.7
    us.hrv_rmssd = 20   # low
    us.norepinephrine = 0.3
    assert us.frequency_regime == "mixed"


# ── focus_residue ──────────────────────────────────────────────────────────

def test_focus_residue_starts_zero():
    us = UserState()
    assert us.focus_residue == 0.0


def test_focus_residue_first_bump_no_change():
    """Первый bump без previous input/mode — без приращения."""
    us = UserState()
    us.bump_focus_residue(mode_id="free", now=1000.0)
    assert us.focus_residue == 0.0


def test_focus_residue_rapid_input():
    """Два bump'а подряд (< 30 сек) → +0.05."""
    us = UserState()
    us.bump_focus_residue(mode_id="free", now=1000.0)
    us.bump_focus_residue(mode_id="free", now=1010.0)   # 10 сек спустя
    assert us.focus_residue == pytest.approx(0.05, abs=1e-6)


def test_focus_residue_mode_switch():
    """Mode change → +0.15."""
    us = UserState()
    us.bump_focus_residue(mode_id="free", now=1000.0)
    us.bump_focus_residue(mode_id="horizon", now=1100.0)   # 100 сек, не rapid
    assert us.focus_residue == pytest.approx(0.15, abs=1e-6)


def test_focus_residue_rapid_plus_switch():
    """Rapid + mode switch вместе → 0.05 + 0.15 = 0.20."""
    us = UserState()
    us.bump_focus_residue(mode_id="free", now=1000.0)
    us.bump_focus_residue(mode_id="horizon", now=1010.0)   # rapid + switch
    assert us.focus_residue == pytest.approx(0.20, abs=1e-6)


def test_focus_residue_clamps_at_one():
    us = UserState()
    us.bump_focus_residue(mode_id="free", now=1000.0)
    for i in range(20):   # 20 mode switches → would be 3.0+ unclamped
        next_mode = "horizon" if i % 2 else "free"
        us.bump_focus_residue(mode_id=next_mode, now=1000.0 + (i + 1) * 60)
    assert us.focus_residue == 1.0


def test_focus_residue_decay():
    us = UserState()
    us.focus_residue = 0.5
    us.decay_focus_residue(dt_seconds=120)   # 2 min → -0.10
    assert us.focus_residue == pytest.approx(0.40, abs=1e-6)


def test_focus_residue_decay_floors_at_zero():
    us = UserState()
    us.focus_residue = 0.05
    us.decay_focus_residue(dt_seconds=600)   # 10 min would be -0.5
    assert us.focus_residue == 0.0


def test_focus_residue_decay_zero_dt_noop():
    us = UserState()
    us.focus_residue = 0.3
    us.decay_focus_residue(dt_seconds=0)
    assert us.focus_residue == 0.3


# ── Persistence ────────────────────────────────────────────────────────────

def test_focus_residue_in_to_dict():
    us = UserState()
    us.focus_residue = 0.42
    d = us.to_dict()
    assert d["focus_residue"] == pytest.approx(0.42, abs=1e-3)


def test_frequency_regime_in_to_dict():
    us = UserState()
    us.hrv_coherence = 0.7
    us.hrv_rmssd = 40
    us.norepinephrine = 0.3
    d = us.to_dict()
    assert d["frequency_regime"] == "long_wave"


def test_focus_residue_roundtrip_through_from_dict():
    us = UserState()
    us.focus_residue = 0.33
    d = us.to_dict()
    us2 = UserState.from_dict(d)
    assert us2.focus_residue == pytest.approx(0.33, abs=1e-3)


# ── Integration: detect_observation_suggestions skips on high residue ───────

def test_observation_suggestions_skipped_on_high_residue():
    """focus_residue > 0.5 → silent skip без update throttle."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from src.detectors import DetectorContext, detect_observation_suggestions

    user = SimpleNamespace(_last_input_ts=None, focus_residue=0.7,
                            hrv_surprise=0.0)
    loop = SimpleNamespace(
        SUGGESTIONS_CHECK_INTERVAL=86400.0,
        SUGGESTIONS_MAX_PER_DAY=2,
        _throttled=MagicMock(return_value=True),
    )
    ctx = DetectorContext(now=1_000_000.0, user=user,
                           neuro=SimpleNamespace(),
                           freeze=SimpleNamespace(silence_pressure=0.0),
                           loop=loop)
    with patch("src.suggestions.collect_suggestions") as mock_collect:
        result = list(detect_observation_suggestions(ctx))
    assert result == []
    # Throttle не trigger'ится — collect не вызвана
    mock_collect.assert_not_called()
    # И _throttled не вызван — silent skip до compute
    loop._throttled.assert_not_called()
