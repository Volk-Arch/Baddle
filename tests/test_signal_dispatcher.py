"""Unit tests для src/signals.py.

Проверяем contract Dispatcher: filter expired → dedup → sort → budget gate
с critical bypass + drop logging в JSONL формате backward-compatible со
старым `_log_throttle_drop`.
"""
import json
import pytest

from src.process.signals import Signal, Dispatcher


# ── Helpers ────────────────────────────────────────────────────────────────

def _sig(type_: str, urgency: float = 0.5, *,
         expires_at: float = 1_000_000.0,
         dedup_key=None, source=None,
         accumulating: bool = False) -> Signal:
    """Build a Signal with sane defaults для test."""
    return Signal(
        type=type_,
        urgency=urgency,
        content={"type": type_, "severity": "info", "text": f"hello {type_}"},
        expires_at=expires_at,
        dedup_key=dedup_key,
        source=source,
        accumulating=accumulating,
    )


@pytest.fixture
def disp(tmp_path):
    """Dispatcher с budget=3, window=1h, drops в tmp."""
    return Dispatcher(
        budget_per_window=3,
        window_s=3600.0,
        critical_threshold=0.9,
        drops_file=tmp_path / "throttle_drops.jsonl",
    )


def _read_drops(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# ── Signal dataclass ───────────────────────────────────────────────────────

def test_signal_construction_defaults():
    s = Signal(type="x", urgency=0.5, content={}, expires_at=100.0)
    assert s.dedup_key is None
    assert s.source is None
    assert s.type == "x"
    assert s.urgency == 0.5


def test_signal_full_construction():
    s = Signal(type="dmn_bridge", urgency=0.7,
               content={"text": "hi"}, expires_at=200.0,
               dedup_key="dmn_bridge:42", source="detect_dmn_bridge")
    assert s.dedup_key == "dmn_bridge:42"
    assert s.source == "detect_dmn_bridge"


# ── Dispatcher: empty / single ─────────────────────────────────────────────

def test_empty_candidates_returns_empty(disp):
    assert disp.dispatch([], now=100.0) == []


def test_single_candidate_emitted(disp):
    s = _sig("morning_briefing", urgency=0.5)
    out = disp.dispatch([s], now=100.0)
    assert out == [s]


# ── Filter expired ─────────────────────────────────────────────────────────

def test_expired_signal_dropped(disp, tmp_path):
    s = _sig("dmn_bridge", urgency=0.8, expires_at=99.0, source="detect_dmn_bridge")
    out = disp.dispatch([s], now=100.0)
    assert out == []
    drops = _read_drops(tmp_path / "throttle_drops.jsonl")
    assert len(drops) == 1
    assert drops[0]["check"] == "dmn_bridge"
    assert drops[0]["ctx"]["reason"] == "expired"
    assert drops[0]["ctx"]["urgency"] == 0.8
    assert drops[0]["ctx"]["source"] == "detect_dmn_bridge"
    assert drops[0]["ctx"]["expires_in_s"] == -1.0


def test_expired_at_now_dropped(disp):
    """expires_at == now → expired (≤, не <)"""
    s = _sig("x", expires_at=100.0)
    assert disp.dispatch([s], now=100.0) == []


def test_alive_signal_passes_filter(disp):
    s = _sig("x", expires_at=101.0)
    assert disp.dispatch([s], now=100.0) == [s]


# ── Dedup ──────────────────────────────────────────────────────────────────

def test_same_dedup_key_in_window_blocked(disp, tmp_path):
    s1 = _sig("recurring_lag", urgency=0.4, dedup_key="recurring_lag:42")
    s2 = _sig("recurring_lag", urgency=0.4, dedup_key="recurring_lag:42")

    out1 = disp.dispatch([s1], now=100.0)
    out2 = disp.dispatch([s2], now=200.0)   # тот же key, 100s позже < 3600s window

    assert out1 == [s1]
    assert out2 == []
    drops = _read_drops(tmp_path / "throttle_drops.jsonl")
    assert any(d["ctx"]["reason"] == "dedup" for d in drops)


def test_dedup_expires_after_window(disp):
    s1 = _sig("recurring_lag", dedup_key="recurring_lag:42")
    s2 = _sig("recurring_lag", dedup_key="recurring_lag:42")

    disp.dispatch([s1], now=100.0)
    out = disp.dispatch([s2], now=100.0 + 3601.0)   # > window_s → key forgotten

    assert out == [s2]


def test_different_dedup_keys_pass_independently(disp):
    s1 = _sig("recurring_lag", dedup_key="recurring_lag:1")
    s2 = _sig("recurring_lag", dedup_key="recurring_lag:2")
    s3 = _sig("recurring_lag", dedup_key="recurring_lag:3")
    out = disp.dispatch([s1, s2, s3], now=100.0)
    assert out == [s1, s2, s3]


def test_no_dedup_key_no_dedup(disp):
    """dedup_key=None → дедупликация выключена."""
    s1 = _sig("dmn_bridge")
    s2 = _sig("dmn_bridge")
    out = disp.dispatch([s1, s2], now=100.0)
    # Оба пройдут (urgency=0.5 default, budget=3)
    assert len(out) == 2


# ── Sort by urgency desc ───────────────────────────────────────────────────

def test_sort_by_urgency_desc(disp):
    """При budget=3, 3 сигнала проходят но в порядке urgency."""
    a = _sig("a", urgency=0.3)
    b = _sig("b", urgency=0.8)
    c = _sig("c", urgency=0.5)
    out = disp.dispatch([a, b, c], now=100.0)
    assert [s.type for s in out] == ["b", "c", "a"]


def test_sort_stable_by_type_secondary(disp):
    """Same urgency → sort by type для повторяемости."""
    a = _sig("zzz", urgency=0.5)
    b = _sig("aaa", urgency=0.5)
    out = disp.dispatch([a, b], now=100.0)
    assert [s.type for s in out] == ["aaa", "zzz"]


# ── Budget gate ────────────────────────────────────────────────────────────

def test_budget_limits_non_critical(disp, tmp_path):
    """Budget=3 → только top-3 non-critical эмитятся, остальное в drops."""
    sigs = [_sig(f"t{i}", urgency=0.5) for i in range(5)]
    out = disp.dispatch(sigs, now=100.0)
    assert len(out) == 3
    drops = _read_drops(tmp_path / "throttle_drops.jsonl")
    budget_drops = [d for d in drops if d["ctx"]["reason"] == "budget"]
    assert len(budget_drops) == 2


def test_critical_bypass_budget(disp):
    """urgency≥0.9 эмитится даже при пустом budget."""
    # Заполняем budget non-critical'ами
    sigs = [_sig(f"t{i}", urgency=0.5) for i in range(3)]
    disp.dispatch(sigs, now=100.0)
    # Теперь budget=0, но critical всё равно проходит
    crit = _sig("coherence_crit", urgency=0.95)
    out = disp.dispatch([crit], now=100.0)
    assert out == [crit]


def test_critical_does_not_consume_budget(disp):
    """Critical signal не уменьшает budget для последующих non-critical."""
    crit = _sig("crit", urgency=0.95)
    disp.dispatch([crit], now=100.0)
    # Critical не съедает budget
    sigs = [_sig(f"t{i}", urgency=0.5) for i in range(3)]
    out = disp.dispatch(sigs, now=100.0)
    assert len(out) == 3   # все 3 прошли несмотря на предыдущий critical


def test_budget_resets_after_window(disp):
    sigs1 = [_sig(f"a{i}", urgency=0.5) for i in range(3)]
    disp.dispatch(sigs1, now=100.0)
    # Budget exhausted
    sigs2 = [_sig("late", urgency=0.5)]
    assert disp.dispatch(sigs2, now=100.0) == []  # budget=0
    # После window — budget восстанавливается
    out = disp.dispatch([_sig("after_window", urgency=0.5)], now=100.0 + 3601.0)
    assert len(out) == 1


def test_critical_at_threshold_exactly(disp):
    """urgency = critical_threshold (0.9) → bypass'ит budget."""
    sigs = [_sig(f"t{i}", urgency=0.5) for i in range(3)]
    disp.dispatch(sigs, now=100.0)   # budget exhausted
    boundary = _sig("boundary", urgency=0.9)
    assert disp.dispatch([boundary], now=100.0) == [boundary]


# ── Drop log format ────────────────────────────────────────────────────────

def test_drop_log_format_compatible_with_legacy(disp, tmp_path):
    """Drops пишутся в backward-compat формате со старым `_log_throttle_drop`:
    `{"ts", "check", "ctx": {reason, ...}}`. Старые анализаторы работают.
    """
    sigs = [_sig(f"t{i}", urgency=0.5,
                 dedup_key=f"k{i}",
                 source=f"detect_t{i}")
            for i in range(5)]
    disp.dispatch(sigs, now=100.0)

    drops = _read_drops(tmp_path / "throttle_drops.jsonl")
    assert len(drops) == 2  # budget=3 → 2 dropped

    for d in drops:
        assert "ts" in d
        assert "check" in d
        assert "ctx" in d
        assert "reason" in d["ctx"]
        # Новые поля (доступны для аналитики)
        assert "urgency" in d["ctx"]
        assert "dedup_key" in d["ctx"]
        assert "expires_in_s" in d["ctx"]
        assert "source" in d["ctx"]


def test_drop_log_no_file_when_none(tmp_path):
    """drops_file=None → ничего не пишется (graceful)."""
    disp = Dispatcher(budget_per_window=0, drops_file=None)
    # No drops_file и нет paths.THROTTLE_DROPS_FILE → silent skip
    s = _sig("x", urgency=0.5)
    out = disp.dispatch([s], now=100.0)   # budget=0, всё в drops
    assert out == []
    # Главное что не упало — ничего не записалось куда-то ещё


# ── Status / introspection ────────────────────────────────────────────────

def test_status_after_emissions(disp):
    sigs = [_sig(f"t{i}", urgency=0.5, dedup_key=f"k{i}") for i in range(2)]
    disp.dispatch(sigs, now=100.0)
    s = disp.status(now=100.0)
    assert s["emitted_in_window"] == 2
    assert s["budget_remaining"] == 1
    assert set(s["dedup_keys_active"]) == {"k0", "k1"}


def test_status_empty(disp):
    s = disp.status(now=100.0)
    assert s["emitted_in_window"] == 0
    assert s["budget_remaining"] == 3
    assert s["dedup_keys_active"] == []


# ── Combined scenarios ─────────────────────────────────────────────────────

def test_mixed_scenario_critical_dedup_budget(disp, tmp_path):
    """Realistic scenario: critical + budget-fitted + dedup'd + expired.
    Все 4 ветки drop работают параллельно."""
    sigs = [
        _sig("coherence_crit", urgency=0.95),                    # critical → emit
        _sig("morning_briefing", urgency=0.8),                    # budget → emit
        _sig("recurring_lag", urgency=0.4, dedup_key="rec:1"),    # budget → emit
        _sig("dmn_bridge", urgency=0.6),                          # budget → emit (3rd)
        _sig("evening_retro", urgency=0.7),                       # budget exceeded → drop
        _sig("expired_x", urgency=0.5, expires_at=99.0),          # expired → drop
    ]
    out = disp.dispatch(sigs, now=100.0)
    types = {s.type for s in out}
    assert "coherence_crit" in types
    assert "morning_briefing" in types
    assert "expired_x" not in types
    assert len(out) == 4   # 1 critical + 3 budget

    drops = _read_drops(tmp_path / "throttle_drops.jsonl")
    reasons = {d["ctx"]["reason"] for d in drops}
    assert "expired" in reasons
    assert "budget" in reasons


def test_recurring_lag_per_goal_dedup(disp):
    """Per-goal dedup: разные goals одного типа alert проходят, тот же goal
    дважды — нет."""
    g1_first = _sig("recurring_lag", dedup_key="recurring_lag:goal_1")
    g2_first = _sig("recurring_lag", dedup_key="recurring_lag:goal_2")
    g1_second = _sig("recurring_lag", dedup_key="recurring_lag:goal_1")

    out1 = disp.dispatch([g1_first, g2_first], now=100.0)
    out2 = disp.dispatch([g1_second], now=200.0)

    assert len(out1) == 2   # both goals first time
    assert out2 == []       # goal_1 already seen


# ── Counter-wave (Правило 7) ───────────────────────────────────────────────

def test_counter_wave_reduces_push_urgency(disp):
    """user_mode='C' понижает urgency push-style сигналов на 0.3."""
    sync_seeking = _sig("sync_seeking", urgency=0.95)   # critical → emit normally
    out = disp.dispatch([sync_seeking], now=100.0, user_mode="C")
    # After −0.3: 0.65, не critical, попадает в budget
    assert len(out) == 1
    assert out[0].urgency == pytest.approx(0.65, abs=1e-6)


def test_counter_wave_does_not_affect_non_push(disp):
    """user_mode='C' не трогает urgency не-push сигналов (dmn_bridge etc)."""
    bridge = _sig("dmn_bridge", urgency=0.6)
    out = disp.dispatch([bridge], now=100.0, user_mode="C")
    assert len(out) == 1
    assert out[0].urgency == pytest.approx(0.6, abs=1e-6)


def test_counter_wave_default_mode_R_no_effect(disp):
    """Default user_mode='R' (resonance) — push-сигналы проходят без изменений."""
    sync = _sig("sync_seeking", urgency=0.7)
    out = disp.dispatch([sync], now=100.0)   # default user_mode="R"
    assert len(out) == 1
    assert out[0].urgency == pytest.approx(0.7, abs=1e-6)


def test_counter_wave_critical_loses_bypass(disp):
    """При user_mode='C' даже critical sync_seeking (0.95→0.65) теряет bypass
    и попадает в budget gate. Это сознательно: при desync push'ить нельзя
    даже критическим тоном — нужна counter-wave (пауза/смена несущей)."""
    # Заполняем budget=3 не-push сигналами
    fillers = [_sig(f"dmn_bridge", urgency=0.5, dedup_key=f"f{i}") for i in range(3)]
    disp.dispatch(fillers, now=100.0)
    # Теперь critical sync_seeking при mode='C' попадает в budget (full)
    sync = _sig("sync_seeking", urgency=0.95)
    out = disp.dispatch([sync], now=101.0, user_mode="C")
    assert out == []   # budget full + reduced urgency не bypass'ит


# ── W14.5c: accumulating bypass ────────────────────────────────────────────


def test_accumulating_bypasses_counter_wave(disp):
    """W14.5c: accumulating Signals не получают counter-wave penalty
    в Dispatcher (workspace.select применяет её позже). Urgency сохраняется."""
    sig = _sig("observation_suggestion", urgency=0.7, accumulating=True)
    out = disp.dispatch([sig], now=100.0, user_mode="C")
    assert len(out) == 1
    assert out[0].urgency == 0.7  # без −0.3 penalty


def test_accumulating_bypasses_budget(disp):
    """W14.5c: accumulating не считается в budget. 5 ноды → все 5 emitted
    (несмотря на budget=3 у non-accumulating)."""
    sigs = [_sig("observation_suggestion", urgency=0.5, dedup_key=f"o{i}",
                  accumulating=True) for i in range(5)]
    out = disp.dispatch(sigs, now=100.0)
    assert len(out) == 5


def test_accumulating_keeps_dedup(disp):
    """W14.5c: accumulating всё ещё dedup'ятся в Dispatcher (window dedup
    защищает от рапид-фаер дубликатов)."""
    sig1 = _sig("observation_suggestion", urgency=0.5, dedup_key="same",
                 accumulating=True)
    sig2 = _sig("observation_suggestion", urgency=0.5, dedup_key="same",
                 accumulating=True)
    out1 = disp.dispatch([sig1], now=100.0)
    out2 = disp.dispatch([sig2], now=101.0)  # within window_s
    assert len(out1) == 1
    assert len(out2) == 0


def test_accumulating_drops_expired(disp):
    """W14.5c: expired filter работает для accumulating тоже."""
    sig = _sig("observation_suggestion", urgency=0.5,
                expires_at=99.0, accumulating=True)
    out = disp.dispatch([sig], now=100.0)
    assert out == []
