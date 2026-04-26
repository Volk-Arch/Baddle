"""Unit tests для capacity helpers и UserState properties (Phase C).

Spec: docs/capacity-design.md (3-zone модель). Phase C завершена 2026-04-25.
"""
import datetime as _dt

import pytest

from src.user_state import (
    UserState,
    compute_cognitive_load,
    compute_capacity_indicators,
)


# ── compute_cognitive_load ─────────────────────────────────────────────────

def test_cognitive_load_empty_day():
    assert compute_cognitive_load({}, 0.0) == 0.0


def test_cognitive_load_max_rough_day():
    """Все 3 положительных фактора на cap → 0.20+0.30+0.30 = 0.80."""
    s = {"tasks_started": 8, "context_switches": 10, "complexity_sum": 3.0}
    assert compute_cognitive_load(s, 0.0) == pytest.approx(0.80, abs=1e-6)


def test_cognitive_load_completions_reduce():
    """5 completions (cap=5) → −0.25, итого 0.80 − 0.25 = 0.55."""
    s = {"tasks_started": 8, "context_switches": 10, "complexity_sum": 3.0,
         "tasks_completed": 5}
    assert compute_cognitive_load(s, 0.0) == pytest.approx(0.55, abs=1e-6)


def test_cognitive_load_improving_progress_reduces():
    """progress_delta=−1.0 (sync улучшился на 1.0) → −0.25, итого 0.55."""
    s = {"tasks_started": 8, "context_switches": 10, "complexity_sum": 3.0}
    assert compute_cognitive_load(s, -1.0) == pytest.approx(0.55, abs=1e-6)


def test_cognitive_load_worsening_progress_no_reduction():
    """progress_delta=+0.5 (sync ухудшился) → max(0, −0.5)=0, без вычета."""
    s = {"tasks_started": 8, "context_switches": 10, "complexity_sum": 3.0}
    assert compute_cognitive_load(s, 0.5) == pytest.approx(0.80, abs=1e-6)


def test_cognitive_load_above_cap_clamps():
    """Над cap'ом нормализация даёт 1.0 — не больше."""
    s = {"tasks_started": 100, "context_switches": 100, "complexity_sum": 100.0}
    assert compute_cognitive_load(s, 0.0) == pytest.approx(0.80, abs=1e-6)


def test_cognitive_load_clamps_at_zero():
    """Огромный progress + completions → load не уходит ниже 0."""
    s = {"tasks_completed": 5}
    result = compute_cognitive_load(s, -10.0)
    assert result == 0.0


def test_cognitive_load_handles_missing_keys():
    """Все ключи опциональны (default 0)."""
    assert compute_cognitive_load({"tasks_started": 4}, 0.0) == \
        pytest.approx(0.10, abs=1e-6)


# ── compute_capacity_indicators ────────────────────────────────────────────

def _fresh_user(coherence=0.7, serotonin=0.5, dopamine=0.5,
                burnout=0.0, cogload=0.0):
    """UserState с заданными полями для тестов indicators."""
    us = UserState(serotonin=serotonin, dopamine=dopamine, burnout=burnout)
    us.hrv_coherence = coherence
    us.cognitive_load_today = cogload
    return us


def test_indicators_all_ok_green():
    us = _fresh_user(coherence=0.7, serotonin=0.5, dopamine=0.5,
                      burnout=0.0, cogload=0.2)
    ind = compute_capacity_indicators(us)
    assert ind == {"phys_ok": True, "affect_ok": True, "cogload_ok": True,
                    "reasons": [], "zone": "green"}


def test_indicators_low_coherence_phys_fail():
    us = _fresh_user(coherence=0.3)
    ind = compute_capacity_indicators(us)
    assert ind["phys_ok"] is False
    assert "hrv_coherence_low" in ind["reasons"]


def test_indicators_high_burnout_phys_fail():
    us = _fresh_user(burnout=0.5)
    ind = compute_capacity_indicators(us)
    assert ind["phys_ok"] is False
    assert "burnout_high" in ind["reasons"]


def test_indicators_no_hrv_uses_burnout_only():
    """coherence None → phys_ok зависит только от burnout."""
    us = _fresh_user(burnout=0.0)
    us.hrv_coherence = None
    ind = compute_capacity_indicators(us)
    assert ind["phys_ok"] is True
    assert "hrv_coherence_low" not in ind["reasons"]


def test_indicators_low_serotonin_affect_fail():
    us = _fresh_user(serotonin=0.3)
    ind = compute_capacity_indicators(us)
    assert ind["affect_ok"] is False
    assert "serotonin_low" in ind["reasons"]


def test_indicators_low_dopamine_affect_fail():
    us = _fresh_user(dopamine=0.3)
    ind = compute_capacity_indicators(us)
    assert ind["affect_ok"] is False
    assert "dopamine_low" in ind["reasons"]


def test_indicators_high_cogload_fail():
    us = _fresh_user(cogload=0.7)
    ind = compute_capacity_indicators(us)
    assert ind["cogload_ok"] is False
    assert "cogload_high" in ind["reasons"]


# ── UserState properties ──────────────────────────────────────────────────
# zone derivation 3-bool→{green,yellow,red} тестируется неявно через
# UserState.capacity_zone ниже + полный матрикс в test_rgk_properties.

def test_userstate_capacity_zone_green_default():
    us = UserState()
    us.hrv_coherence = 0.7
    # default serotonin=0.5, dopamine=0.5, burnout=0.0, cogload=0.0 → green
    assert us.capacity_zone == "green"
    assert us.capacity_reason == []


def test_userstate_capacity_zone_red_multiple_fails():
    us = UserState(serotonin=0.3, dopamine=0.3, burnout=0.5)
    us.cognitive_load_today = 0.8
    assert us.capacity_zone == "red"
    reasons = set(us.capacity_reason)
    assert "burnout_high" in reasons
    assert "serotonin_low" in reasons
    assert "dopamine_low" in reasons
    assert "cogload_high" in reasons


# ── rollover_day ──────────────────────────────────────────────────────────

def test_rollover_resets_cognitive_load():
    us = UserState()
    us.cognitive_load_today = 0.5
    us.rollover_day()
    assert us.cognitive_load_today == 0.0


def test_rollover_persists_yesterday(monkeypatch):
    """Yesterday's day_summary получает finalized cognitive_load."""
    today = _dt.date(2026, 4, 25)
    yesterday_str = "2026-04-24"
    today_str = "2026-04-25"

    class _FakeDate:
        @classmethod
        def today(cls):
            return today
    monkeypatch.setattr(_dt, "date", _FakeDate)

    us = UserState()
    # Pre-existing yesterday entry from earlier in the day_summary
    us.day_summary[yesterday_str] = {"tasks_started": 5,
                                       "context_switches": 3}
    us.cognitive_load_today = 0.42

    us.rollover_day()

    # yesterday was updated with cognitive_load
    assert us.day_summary[yesterday_str]["cognitive_load"] == pytest.approx(
        0.42, abs=1e-3)
    # today was created with sync_error_at_dawn snapshot
    assert today_str in us.day_summary
    assert "sync_error_at_dawn" in us.day_summary[today_str]
    # cognitive_load_today reset
    assert us.cognitive_load_today == 0.0


def test_rollover_trims_history():
    """day_summary хранит максимум 30 дней."""
    us = UserState()
    today = _dt.date.today()
    for i in range(40):
        d = (today - _dt.timedelta(days=i)).strftime("%Y-%m-%d")
        us.day_summary[d] = {"cognitive_load": 0.5}
    us.rollover_day()
    assert len(us.day_summary) <= 30


# ── update_cognitive_load — pull из activity_log + sync_error ─────────────

def test_update_cognitive_load_empty_activity_log(tmp_path, monkeypatch):
    """Пустой activity_log → tasks_started=0, complexity_sum=0 → load=0."""
    from src import paths, activity_log
    monkeypatch.setattr(paths, "ACTIVITY_FILE", tmp_path / "act.jsonl")
    monkeypatch.setattr(activity_log, "_ACTIVITY_FILE", tmp_path / "act.jsonl")
    # Mock global state freeze.sync_error_ema_slow to be stable
    from unittest.mock import MagicMock
    fake_state = MagicMock()
    fake_state.rgk.sync_slow.value = 0.0
    monkeypatch.setattr("src.horizon.get_global_state", lambda: fake_state)

    us = UserState()
    us.update_cognitive_load()
    assert us.cognitive_load_today == 0.0


def test_update_cognitive_load_aggregates_from_activity_log(tmp_path, monkeypatch):
    """Реальные activity_log events → правильные aggregates → правильный load."""
    from src import paths, activity_log
    monkeypatch.setattr(paths, "ACTIVITY_FILE", tmp_path / "act.jsonl")
    monkeypatch.setattr(activity_log, "_ACTIVITY_FILE", tmp_path / "act.jsonl")

    # Фиксированные surprise значения через mock UserState.imbalance
    from unittest.mock import MagicMock
    mock_rgk = MagicMock(); mock_rgk.project = lambda dom: {"imbalance": 0.5} if dom == "user_state" else {}
    monkeypatch.setattr("src.rgk.get_global_rgk",
                         lambda: mock_rgk)

    fake_state = MagicMock()
    fake_state.rgk.sync_slow.value = 0.1
    monkeypatch.setattr("src.horizon.get_global_state", lambda: fake_state)

    # Добавим 3 активности (одна — switch на следующую → context_switch)
    aid1 = activity_log.start_activity("Task 1", category="work")
    aid2 = activity_log.start_activity("Task 2", category="meeting")  # auto-switch
    activity_log.stop_activity(reason="manual")  # task 2 done

    # Теперь update_cognitive_load
    us = UserState()
    us.update_cognitive_load()

    today_str = _dt.date.today().strftime("%Y-%m-%d")
    summary = us.day_summary.get(today_str) or {}
    assert summary["tasks_started"] == 2
    assert summary["tasks_completed"] == 2   # обе завершены (switch + manual)
    assert summary["context_switches"] == 1   # только task 1 → switch
    assert summary["complexity_sum"] == pytest.approx(1.0, abs=1e-3)  # 0.5 + 0.5
    # Load > 0 (есть активность)
    assert us.cognitive_load_today > 0


def test_update_cognitive_load_progress_delta_from_dawn(tmp_path, monkeypatch):
    """progress_delta вычисляется как (sync_now − sync_at_dawn).
    Первый update в день фиксирует sync_at_dawn."""
    from src import paths, activity_log
    monkeypatch.setattr(paths, "ACTIVITY_FILE", tmp_path / "act.jsonl")
    monkeypatch.setattr(activity_log, "_ACTIVITY_FILE", tmp_path / "act.jsonl")

    from unittest.mock import MagicMock
    fake_state = MagicMock()
    fake_state.rgk.sync_slow.value = 0.5    # initial
    monkeypatch.setattr("src.horizon.get_global_state", lambda: fake_state)

    us = UserState()
    us.update_cognitive_load()
    today_str = _dt.date.today().strftime("%Y-%m-%d")
    # Первый раз: sync_at_dawn = sync_now = 0.5, delta = 0
    assert us.day_summary[today_str]["sync_error_at_dawn"] == pytest.approx(0.5)
    assert us.day_summary[today_str]["progress_delta"] == pytest.approx(0.0)

    # Час спустя sync улучшился (упал)
    fake_state.rgk.sync_slow.value = 0.3
    us.update_cognitive_load()
    # at_dawn остался 0.5 (зафиксирован), delta = 0.3 - 0.5 = -0.2
    assert us.day_summary[today_str]["sync_error_at_dawn"] == pytest.approx(0.5)
    assert us.day_summary[today_str]["progress_delta"] == pytest.approx(-0.2)


# ── Persistence to_dict / from_dict ───────────────────────────────────────

def test_capacity_in_to_dict():
    us = UserState()
    us.cognitive_load_today = 0.33
    us.day_summary = {"2026-04-25": {"tasks_started": 3}}
    d = us.to_dict()
    assert d["cognitive_load_today"] == pytest.approx(0.33, abs=1e-3)
    assert d["capacity_zone"] in ("green", "yellow", "red")
    assert isinstance(d["capacity_reason"], list)
    assert d["day_summary"] == {"2026-04-25": {"tasks_started": 3}}


def test_capacity_roundtrip_from_dict():
    us = UserState()
    us.cognitive_load_today = 0.42
    us.day_summary = {"2026-04-25": {"tasks_started": 5,
                                       "cognitive_load": 0.4}}
    d = us.to_dict()
    us2 = UserState.from_dict(d)
    assert us2.cognitive_load_today == pytest.approx(0.42, abs=1e-3)
    assert "2026-04-25" in us2.day_summary
    assert us2.day_summary["2026-04-25"]["tasks_started"] == 5


# ── activity_log surprise tracking (Phase C Шаг 2) ────────────────────────

def test_activity_log_records_surprise_at_start(tmp_path, monkeypatch):
    """start_activity снимает UserState.imbalance в surprise_at_start."""
    from src import paths, activity_log
    monkeypatch.setattr(paths, "ACTIVITY_FILE", tmp_path / "act.jsonl")
    monkeypatch.setattr(activity_log, "_ACTIVITY_FILE", tmp_path / "act.jsonl")

    from unittest.mock import MagicMock
    mock_rgk = MagicMock(); mock_rgk.project = lambda dom: {"imbalance": 0.42} if dom == "user_state" else {}
    monkeypatch.setattr("src.rgk.get_global_rgk",
                         lambda: mock_rgk)

    aid = activity_log.start_activity("Test task", category="work")
    rec = activity_log.get_active()
    assert rec is not None
    assert rec["surprise_at_start"] == pytest.approx(0.42, abs=1e-3)


def test_activity_log_computes_surprise_delta(tmp_path, monkeypatch):
    """stop_activity записывает surprise_at_stop, replay считает delta."""
    from src import paths, activity_log
    monkeypatch.setattr(paths, "ACTIVITY_FILE", tmp_path / "act.jsonl")
    monkeypatch.setattr(activity_log, "_ACTIVITY_FILE", tmp_path / "act.jsonl")

    from unittest.mock import MagicMock
    mock_rgk = MagicMock(); mock_rgk.project = lambda dom: {"imbalance": 0.3} if dom == "user_state" else {}
    monkeypatch.setattr("src.rgk.get_global_rgk",
                         lambda: mock_rgk)

    activity_log.start_activity("Test", category="work")
    mock_rgk.project = lambda dom: {"imbalance": 0.8} if dom == "user_state" else {}   # surprise grew
    done = activity_log.stop_activity()
    assert done["surprise_at_start"] == pytest.approx(0.3, abs=1e-3)
    assert done["surprise_at_stop"] == pytest.approx(0.8, abs=1e-3)
    assert done["surprise_delta"] == pytest.approx(0.5, abs=1e-3)


# ── assistant.py: capacity helpers + _get_context dict shape ──────────────

def test_capacity_reason_text_ru():
    from src.assistant import _capacity_reason_text
    txt = _capacity_reason_text(["hrv_coherence_low", "cogload_high"], "ru")
    assert "когерентность HRV" in txt
    assert "когнитивная нагрузка" in txt


def test_capacity_reason_text_en():
    from src.assistant import _capacity_reason_text
    txt = _capacity_reason_text(["burnout_high"], "en")
    assert txt == "burnout high"


def test_capacity_reason_text_empty():
    from src.assistant import _capacity_reason_text
    txt_ru = _capacity_reason_text([], "ru")
    assert "общая нагрузка" in txt_ru
    txt_en = _capacity_reason_text([], "en")
    assert "load is high" in txt_en


def test_capacity_reason_unknown_tag_passthrough():
    """Unknown reason tag должен сохраняться raw (для forward compat)."""
    from src.assistant import _capacity_reason_text
    txt = _capacity_reason_text(["new_unknown_reason"], "ru")
    assert "new_unknown_reason" in txt


def test_get_context_returns_capacity(tmp_path, monkeypatch):
    """_get_context добавляет capacity dict рядом с energy/hrv/state."""
    from unittest.mock import patch, MagicMock
    monkeypatch.setattr("src.assistant._load_state",
                         lambda: {"last_reset_date": None})
    monkeypatch.setattr("src.assistant._save_state", lambda *a, **k: None)

    fake_hrv = MagicMock()
    fake_hrv.is_running = False
    monkeypatch.setattr("src.assistant.get_hrv_manager",
                         lambda: fake_hrv)

    from src.assistant import _get_context
    ctx = _get_context(reset_daily=False)
    assert "capacity" in ctx
    cap = ctx["capacity"]
    assert "zone" in cap
    assert cap["zone"] in ("green", "yellow", "red")
    assert isinstance(cap.get("reason"), list)
    assert "phys_ok" in cap and "affect_ok" in cap and "cogload_ok" in cap
    assert "cognitive_load_today" in cap


def test_activity_log_auto_switch_records_surprise(tmp_path, monkeypatch):
    """При auto-switch (start второй задачи) — первая получает surprise_delta."""
    from src import paths, activity_log
    monkeypatch.setattr(paths, "ACTIVITY_FILE", tmp_path / "act.jsonl")
    monkeypatch.setattr(activity_log, "_ACTIVITY_FILE", tmp_path / "act.jsonl")

    from unittest.mock import MagicMock
    mock_rgk = MagicMock(); mock_rgk.project = lambda dom: {"imbalance": 0.2} if dom == "user_state" else {}
    monkeypatch.setattr("src.rgk.get_global_rgk",
                         lambda: mock_rgk)

    aid1 = activity_log.start_activity("First", category="work")
    mock_rgk.project = lambda dom: {"imbalance": 0.7} if dom == "user_state" else {}
    aid2 = activity_log.start_activity("Second", category="meeting")

    # Replay — first task должна быть done с surprise_delta
    state = activity_log._replay()
    assert state[aid1]["status"] == "done"
    assert state[aid1]["surprise_delta"] == pytest.approx(0.5, abs=1e-3)
    assert state[aid2]["status"] == "active"
    assert state[aid2]["surprise_at_start"] == pytest.approx(0.7, abs=1e-3)
