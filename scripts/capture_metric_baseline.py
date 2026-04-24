"""One-off: прогоняет фиксированный event-sequence через UserState /
Neurochem / ProtectiveFreeze и печатает JSON-snapshot. Snapshot ручно
переносится в `tests/test_metric_identity.py` как EXPECTED.

Запуск: python scripts/capture_metric_baseline.py

После миграции registry test_metric_identity должен давать тот же snapshot.
"""
import json
import sys
import os

# Ensure src importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np

from src.user_state import UserState
from src.neurochem import Neurochem, ProtectiveFreeze


def _round(v, n=6):
    if isinstance(v, (list, tuple)):
        return [_round(x, n) for x in v]
    if hasattr(v, "tolist"):
        return _round(v.tolist(), n)
    try:
        return round(float(v), n)
    except Exception:
        return v


def run_sequence():
    # Deterministic TOD: pin to "day" so TOD-scoped EMA updates one bucket
    UserState._current_tod = staticmethod(lambda: "day")

    us = UserState()
    nc = Neurochem()
    pf = ProtectiveFreeze()

    # ── UserState events ───────────────────────────────────────────────
    for _ in range(5):
        us.update_from_hrv(coherence=0.6, stress=0.3, rmssd=40.0, activity=0.2)

    for _ in range(10):
        us.update_from_engagement(signal=0.65)

    for _ in range(3):
        us.update_from_feedback("accepted")

    for _ in range(2):
        us.update_from_feedback("rejected")

    for s in (0.4, 0.2, -0.1, 0.5, 0.3):
        us.update_from_chat_sentiment(s)

    us.update_from_plan_completion(completed=3, planned=5)
    us.update_from_energy(decisions_today=20)

    for _ in range(10):
        us.tick_expectation()

    # ── Neurochem events ───────────────────────────────────────────────
    for d, wc, w in [
        (0.4, [0.1, -0.05, 0.2], [0.3, 0.4, 0.3]),
        (0.3, [0.05, -0.02, 0.1], [0.35, 0.3, 0.35]),
        (0.5, [0.0, 0.0, 0.1], [0.25, 0.45, 0.3]),
        (0.2, [-0.05, 0.1, -0.02], [0.4, 0.3, 0.3]),
        (0.45, [0.1, 0.05, -0.05], [0.3, 0.3, 0.4]),
    ]:
        nc.update(d=d, w_change=wc, weights=w)

    for _ in range(3):
        nc.tick_expectation()

    nc.record_outcome(prior=0.5, posterior=0.7)
    nc.record_outcome(prior=0.6, posterior=0.55)

    # ── ProtectiveFreeze events ────────────────────────────────────────
    pf.update(d=0.7, serotonin=0.4)
    pf.update(d=0.65, serotonin=0.45)

    for _ in range(20):
        pf.feed_tick(dt=60.0, sync_err=0.5, imbalance=0.3)

    return {
        "user_state": {
            "dopamine": _round(us.dopamine),
            "serotonin": _round(us.serotonin),
            "norepinephrine": _round(us.norepinephrine),
            "valence": _round(us.valence),
            "burnout": _round(us.burnout),
            "agency": _round(us.agency),
            "expectation": _round(us.expectation),
            "expectation_by_tod": {k: _round(v) if v is not None else None
                                     for k, v in us.expectation_by_tod.items()},
            "expectation_vec": _round(us.expectation_vec),
            "hrv_baseline_by_tod": {k: _round(v) if v is not None else None
                                      for k, v in us.hrv_baseline_by_tod.items()},
            "vector": _round(us.vector()),
            "surprise": _round(us.surprise),
            "imbalance": _round(us.imbalance),
        },
        "neurochem": {
            "dopamine": _round(nc.dopamine),
            "serotonin": _round(nc.serotonin),
            "norepinephrine": _round(nc.norepinephrine),
            "expectation_vec": _round(nc.expectation_vec),
            "gamma": _round(nc.gamma),
            "recent_rpe": _round(nc.recent_rpe),
            "self_imbalance": _round(nc.self_imbalance),
            "vector": _round(nc.vector()),
        },
        "freeze": {
            "conflict_accumulator": _round(pf.conflict_accumulator),
            "silence_pressure": _round(pf.silence_pressure),
            "imbalance_pressure": _round(pf.imbalance_pressure),
            "sync_error_ema_fast": _round(pf.sync_error_ema_fast),
            "sync_error_ema_slow": _round(pf.sync_error_ema_slow),
            "display_burnout": _round(pf.display_burnout),
            "active": pf.active,
        },
    }


if __name__ == "__main__":
    snap = run_sequence()
    print(json.dumps(snap, indent=2, ensure_ascii=False))
