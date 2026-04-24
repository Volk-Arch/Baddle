"""Unit tests for src/metrics.py MetricRegistry.

Тонкие тесты контракта: register, fire_event (с None / scalar / tuple
extractors), TOD-filtering, time-constant путь, decay_override, to_dict/load.
"""
import math
import numpy as np
import pytest

from src.ema import EMA, VectorEMA
from src.metrics import MetricRegistry


def test_register_and_value():
    reg = MetricRegistry()
    reg.register("x", EMA(0.5, decay=0.9))
    assert reg.value("x") == pytest.approx(0.5)
    assert "x" in reg


def test_register_duplicate_raises():
    reg = MetricRegistry()
    reg.register("x", EMA(0.5, decay=0.9))
    with pytest.raises(ValueError, match="already registered"):
        reg.register("x", EMA(0.5, decay=0.9))


def test_fire_event_scalar_extractor():
    reg = MetricRegistry()
    reg.register(
        "dopamine",
        EMA(0.5, decay=0.9),
        listens=[("engagement", lambda p: p.get("signal"))],
    )
    reg.fire_event("engagement", signal=0.7)
    # 0.9*0.5 + 0.1*0.7 = 0.52
    assert reg.value("dopamine") == pytest.approx(0.52, abs=1e-6)


def test_fire_event_extractor_returns_none_skips():
    reg = MetricRegistry()
    reg.register(
        "x",
        EMA(0.5, decay=0.9),
        listens=[("e", lambda p: p.get("missing"))],
    )
    reg.fire_event("e", other=0.7)
    assert reg.value("x") == 0.5


def test_fire_event_unknown_event_noop():
    reg = MetricRegistry()
    reg.register("x", EMA(0.5, decay=0.9))
    reg.fire_event("never", whatever=1.0)
    assert reg.value("x") == 0.5


def test_decay_override_applied():
    reg = MetricRegistry()
    reg.register(
        "x",
        EMA(0.5, decay=0.99),  # usually very slow
        listens=[("e", lambda p: p.get("s"))],
    )
    reg.fire_event("e", s=1.0, _decay_override=0.5)
    # 0.5*0.5 + 0.5*1.0 = 0.75 (fast decay)
    assert reg.value("x") == pytest.approx(0.75, abs=1e-6)


def test_time_constant_tuple_extractor():
    reg = MetricRegistry()
    reg.register(
        "x",
        EMA(0.0, time_const=3600.0),
        listens=[("tick", lambda p: (p.get("s"), p.get("dt")))],
    )
    reg.fire_event("tick", s=1.0, dt=3600.0)
    # alpha = 1 - exp(-1) ≈ 0.632; d = 0.368
    # new = 0.368*0 + 0.632*1 = 0.632
    expected = 1.0 - math.exp(-1.0)
    assert reg.value("x") == pytest.approx(expected, abs=1e-5)


def test_tod_filter_extractor():
    """TOD-scoped: 4 metrics, only matching tod получает update."""
    reg = MetricRegistry()

    def _for_tod(tod_name):
        def _fn(p):
            return p.get("signal") if p.get("tod") == tod_name else None
        return _fn

    for tod in ("morning", "day", "evening", "night"):
        reg.register(
            f"exp_{tod}",
            EMA(0.5, decay=0.9),
            listens=[("tick", _for_tod(tod))],
        )
    reg.fire_event("tick", tod="day", signal=0.8)
    assert reg.value("exp_morning") == 0.5
    assert reg.value("exp_day") == pytest.approx(0.53, abs=1e-6)
    assert reg.value("exp_evening") == 0.5
    assert reg.value("exp_night") == 0.5


def test_vector_order_preserved():
    reg = MetricRegistry()
    reg.register("a", EMA(0.1, decay=0.9))
    reg.register("b", EMA(0.2, decay=0.9))
    reg.register("c", EMA(0.3, decay=0.9))
    v = reg.vector(["a", "b", "c"])
    assert v.dtype == np.float32
    assert v.tolist() == pytest.approx([0.1, 0.2, 0.3])
    # Different order works
    v2 = reg.vector(["c", "a"])
    assert v2.tolist() == pytest.approx([0.3, 0.1])


def test_vector_ema_registers_and_fires():
    reg = MetricRegistry()
    reg.register(
        "vec",
        VectorEMA([0.5, 0.5, 0.5], decay=0.9),
        listens=[("tick", lambda p: p.get("v"))],
    )
    reg.fire_event("tick", v=np.array([1.0, 0.0, 0.5], dtype=np.float32))
    out = reg.value("vec")
    assert out.tolist() == pytest.approx([0.55, 0.45, 0.5], abs=1e-6)


def test_to_dict_and_load_roundtrip():
    reg = MetricRegistry()
    reg.register("x", EMA(0.5, decay=0.9))
    reg.register("v", VectorEMA([0.1, 0.2, 0.3], decay=0.9))
    # Mutate
    reg.get("x").feed(1.0)
    reg.get("v").feed(np.array([1.0, 1.0, 1.0], dtype=np.float32))

    dump = reg.to_dict()
    assert "x" in dump and "v" in dump
    assert "value" in dump["x"]
    assert "seeded" in dump["x"]

    # Fresh registry, load — значения восстанавливаются
    reg2 = MetricRegistry()
    reg2.register("x", EMA(0.0, decay=0.9))
    reg2.register("v", VectorEMA([0.0, 0.0, 0.0], decay=0.9))
    reg2.load(dump)
    assert reg2.value("x") == pytest.approx(reg.value("x"), abs=1e-6)
    assert reg2.value("v").tolist() == pytest.approx(reg.value("v").tolist(),
                                                       abs=1e-6)


def test_load_missing_keys_noop():
    """Load с dict, не содержащим часть метрик — остальные не трогаются."""
    reg = MetricRegistry()
    reg.register("a", EMA(0.3, decay=0.9))
    reg.register("b", EMA(0.7, decay=0.9))
    reg.load({"a": {"value": 0.9, "seeded": True}})
    assert reg.value("a") == pytest.approx(0.9)
    assert reg.value("b") == pytest.approx(0.7)  # untouched


def test_seeded_on_first_flag_preserved_through_roundtrip():
    """seed_on_first EMA: to_dict включает seeded flag; load сохраняет."""
    reg = MetricRegistry()
    reg.register("h", EMA(0.0, decay=0.9, seed_on_first=True))
    dump = reg.to_dict()
    assert dump["h"]["seeded"] is False

    reg.get("h").feed(0.8)   # first feed seeds
    dump = reg.to_dict()
    assert dump["h"]["seeded"] is True
    assert dump["h"]["value"] == pytest.approx(0.8)

    reg2 = MetricRegistry()
    reg2.register("h", EMA(0.0, decay=0.9, seed_on_first=True))
    reg2.load(dump)
    # Now feed again — should apply EMA (not seed again)
    reg2.get("h").feed(0.0)
    # d=0.9: 0.9*0.8 + 0.1*0.0 = 0.72
    assert reg2.value("h") == pytest.approx(0.72, abs=1e-6)


def test_dict_return_with_decay_override():
    """Extractor returns dict with per-metric decay_override."""
    reg = MetricRegistry()
    reg.register(
        "x",
        EMA(0.5, decay=0.99),
        listens=[("e", lambda p: {"signal": 1.0, "decay_override": 0.5})],
    )
    reg.fire_event("e")
    # override 0.5: 0.5*0.5 + 0.5*1.0 = 0.75
    assert reg.value("x") == pytest.approx(0.75, abs=1e-6)


def test_dict_return_with_dt_for_time_constant():
    reg = MetricRegistry()
    reg.register(
        "x",
        EMA(0.0, time_const=3600.0),
        listens=[("e", lambda p: {"signal": p["s"], "dt": 3600.0})],
    )
    reg.fire_event("e", s=1.0)
    expected = 1.0 - math.exp(-1.0)
    assert reg.value("x") == pytest.approx(expected, abs=1e-5)


def test_dict_return_none_signal_skips():
    reg = MetricRegistry()
    reg.register(
        "x",
        EMA(0.5, decay=0.9),
        listens=[("e", lambda p: {"signal": None, "decay_override": 0.1})],
    )
    reg.fire_event("e")
    assert reg.value("x") == 0.5


def test_multiple_subscribers_single_event():
    """Одно событие → несколько метрик одновременно обновляются."""
    reg = MetricRegistry()
    reg.register("a", EMA(0.5, decay=0.9),
                 listens=[("e", lambda p: p.get("sa"))])
    reg.register("b", EMA(0.5, decay=0.8),
                 listens=[("e", lambda p: p.get("sb"))])
    reg.fire_event("e", sa=1.0, sb=0.0)
    assert reg.value("a") == pytest.approx(0.9*0.5 + 0.1*1.0, abs=1e-6)
    assert reg.value("b") == pytest.approx(0.8*0.5 + 0.2*0.0, abs=1e-6)
