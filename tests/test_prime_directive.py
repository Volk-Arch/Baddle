"""Tests для расширения prime_directive на Outcome dashboard fields.

Wave 3 + Outcome dashboard (2026-04-26): record_tick принимает balance_user/
balance_system/capacity_zone/frequency_regime/mode_user/mode_system.
aggregate() добавляет capacity_zone_counts/frequency_regime_counts/mode_*_counts
distributions + mean_balance_user/mean_balance_system.
"""
from __future__ import annotations

import json


def _isolate_log(monkeypatch, tmp_path):
    """Перенаправить _LOG_PATH на tmp файл, чистый для каждого теста."""
    from src import prime_directive
    log_file = tmp_path / "prime_directive.jsonl"
    monkeypatch.setattr(prime_directive, "_LOG_PATH", str(log_file))
    return log_file


def test_record_tick_writes_outcome_fields(monkeypatch, tmp_path):
    """Новые kwargs (balance/capacity_zone/frequency_regime/mode) пишутся в jsonl."""
    log_file = _isolate_log(monkeypatch, tmp_path)
    from src.prime_directive import record_tick

    ok = record_tick(
        sync_error=0.2, sync_error_ema_fast=0.18, sync_error_ema_slow=0.15,
        imbalance_pressure=0.05, silence_pressure=0.0, conflict_accumulator=0.0,
        balance_user=0.85, balance_system=1.15,
        capacity_zone="green", frequency_regime="long_wave",
        mode_user="R", mode_system="C",
    )
    assert ok is True

    line = log_file.read_text(encoding="utf-8").strip().splitlines()[-1]
    entry = json.loads(line)
    assert entry["balance_user"] == 0.85
    assert entry["balance_system"] == 1.15
    assert entry["capacity_zone"] == "green"
    assert entry["frequency_regime"] == "long_wave"
    assert entry["mode_user"] == "R"
    assert entry["mode_system"] == "C"


def test_record_tick_omits_outcome_fields_when_none(monkeypatch, tmp_path):
    """Backward-compat: без kwargs Outcome поля НЕ пишутся (старые logs совместимы)."""
    log_file = _isolate_log(monkeypatch, tmp_path)
    from src.prime_directive import record_tick

    record_tick(
        sync_error=0.2, sync_error_ema_fast=0.18, sync_error_ema_slow=0.15,
        imbalance_pressure=0.05, silence_pressure=0.0, conflict_accumulator=0.0,
    )
    line = log_file.read_text(encoding="utf-8").strip().splitlines()[-1]
    entry = json.loads(line)
    assert "balance_user" not in entry
    assert "capacity_zone" not in entry
    assert "mode_user" not in entry


def test_aggregate_returns_distributions(monkeypatch, tmp_path):
    """capacity_zone_counts / frequency_regime_counts / mode_*_counts
    собирают категориальные распределения."""
    _isolate_log(monkeypatch, tmp_path)
    from src.prime_directive import record_tick, aggregate

    # 3 green, 1 yellow, 1 red zones
    zones = ["green", "green", "green", "yellow", "red"]
    regimes = ["long_wave", "flat", "flat", "short_wave", "short_wave"]
    user_modes = ["R", "R", "R", "R", "C"]
    for z, r, m in zip(zones, regimes, user_modes):
        record_tick(
            sync_error=0.1, sync_error_ema_fast=0.1, sync_error_ema_slow=0.1,
            imbalance_pressure=0.0, silence_pressure=0.0, conflict_accumulator=0.0,
            capacity_zone=z, frequency_regime=r, mode_user=m, mode_system="R",
        )

    agg = aggregate()
    assert agg["count"] == 5
    assert agg["capacity_zone_counts"] == {"green": 3, "yellow": 1, "red": 1}
    assert agg["frequency_regime_counts"] == {"long_wave": 1, "flat": 2, "short_wave": 2}
    assert agg["mode_user_counts"] == {"R": 4, "C": 1}
    assert agg["mode_system_counts"] == {"R": 5}


def test_aggregate_balance_means_skip_none(monkeypatch, tmp_path):
    """mean_balance_user/_system считают только entries с явным balance."""
    _isolate_log(monkeypatch, tmp_path)
    from src.prime_directive import record_tick, aggregate

    # 2 entries без balance, 3 с balance (среднее 1.0)
    for _ in range(2):
        record_tick(
            sync_error=0.1, sync_error_ema_fast=0.1, sync_error_ema_slow=0.1,
            imbalance_pressure=0.0, silence_pressure=0.0, conflict_accumulator=0.0,
        )
    for bu in (0.8, 1.0, 1.2):
        record_tick(
            sync_error=0.1, sync_error_ema_fast=0.1, sync_error_ema_slow=0.1,
            imbalance_pressure=0.0, silence_pressure=0.0, conflict_accumulator=0.0,
            balance_user=bu, balance_system=bu * 0.9,
        )

    agg = aggregate()
    assert agg["count"] == 5
    assert agg["mean_balance_user"] == 1.0  # (0.8+1.0+1.2)/3
    assert agg["mean_balance_system"] == 0.9
