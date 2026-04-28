"""HRV Manager — central access point for HRV data.

Two modes:
  - "simulator": synthetic RR intervals (demo, no hardware)
  - "polar": real Polar H10 via BLE (requires bleak + bleakheart)

Usage:
    from .sensors.manager import get_manager
    mgr = get_manager()
    mgr.start(mode="simulator")
    metrics = mgr.get_metrics()
"""

import threading
import time
import logging
from typing import Dict, Optional, Callable
from collections import deque

from .metrics import calculate_hrv_metrics, hrv_to_baddle_state, HRVSimulator
from .stream import (
    push_rr, push_hrv_snapshot, push_activity, SOURCE_SIMULATOR,
)

log = logging.getLogger(__name__)


class HRVManager:
    """Singleton HRV data manager. Thread-safe rolling buffer of RR intervals
    + activity_magnitude (L2 norm акселерометра в [0, ∞), нормализован ~0..3)."""

    def __init__(self):
        self.mode = "off"  # off, simulator, polar
        self.rr_buffer = deque(maxlen=240)  # ~2 min of beats at 70bpm
        self.is_running = False
        self._lock = threading.Lock()
        self._simulator: Optional[HRVSimulator] = None
        self._sim_thread: Optional[threading.Thread] = None
        self._polar_reader = None
        self._last_metrics: Dict = {}
        self._baseline: Optional[Dict] = None  # calibration
        self._listeners: list[Callable] = []
        # Activity — независимый от HR канал. Polar передаёт acc x/y/z,
        # считаем magnitude - g (1.0 в покое) чтобы получить чистую динамику.
        # Симулятор — плоский скаляр от слайдера.
        self._activity_magnitude: float = 0.0

    # ── Lifecycle ──────────────────────────────────────────────────────

    def start(self, mode: str = "simulator", **kwargs) -> bool:
        """Start HRV monitoring.

        mode='simulator': synthetic data
        mode='polar': real Polar H10 via BLE
        """
        if self.is_running:
            self.stop()

        self.mode = mode
        self.is_running = True

        if mode == "simulator":
            return self._start_simulator(**kwargs)
        elif mode == "polar":
            return self._start_polar(**kwargs)
        else:
            log.warning(f"Unknown mode: {mode}")
            self.is_running = False
            return False

    def stop(self):
        self.is_running = False
        if self._sim_thread:
            self._sim_thread.join(timeout=2.0)
            self._sim_thread = None
        # Polar cleanup happens in its own thread
        self.mode = "off"

    def _start_simulator(self, target_hr: float = 70.0, target_coherence: float = 0.7, **_):
        self._simulator = HRVSimulator(base_hr=target_hr, target_coherence=target_coherence)

        def run():
            snapshot_every = 20  # каждые ~20 beats (≈15с при 70bpm) — пушим snapshot
            counter = 0
            while self.is_running:
                rr = self._simulator.tick()
                with self._lock:
                    self.rr_buffer.append(rr)
                # Каждый RR идёт в SensorStream (stream сам downsample'ит на диск).
                # Благодаря этому любой consumer (UserState, UI) читает единый поток,
                # не зная про simulator vs Polar vs etc.
                push_rr(SOURCE_SIMULATOR, rr)
                counter += 1
                if counter >= snapshot_every and len(self.rr_buffer) >= 2:
                    # Агрегат HRV-метрик → snapshot в stream
                    try:
                        metrics = calculate_hrv_metrics(list(self.rr_buffer))
                        push_hrv_snapshot(
                            SOURCE_SIMULATOR,
                            rmssd=metrics.get("rmssd"),
                            coherence=metrics.get("coherence"),
                            heart_rate=metrics.get("heart_rate"),
                            lf_hf_ratio=metrics.get("lf_hf_ratio"),
                            stress=hrv_to_baddle_state(metrics).get("stress_level"),
                            confidence=1.0,
                        )
                    except Exception as e:
                        log.debug(f"[hrv] snapshot push failed: {e}")
                    counter = 0
                # Activity magnitude — постоянный канал (слайдер в UI или accelerometer)
                push_activity(SOURCE_SIMULATOR, self._activity_magnitude)
                # Wait approximately one beat
                time.sleep(rr / 1000.0)

        self._sim_thread = threading.Thread(target=run, daemon=True, name="hrv-simulator")
        self._sim_thread.start()
        log.info(f"[hrv] simulator started: hr={target_hr} coh={target_coherence}")
        return True

    def _start_polar(self, **kwargs):
        # Polar support stubbed for now — would import bleak + bleakheart here
        # For this implementation we focus on simulator; real Polar requires async BLE
        log.warning("[hrv] polar mode not yet implemented — falling back to simulator")
        return self._start_simulator(**kwargs)

    # ── Simulator control ──────────────────────────────────────────────

    def set_simulator_state(self, target_hr: float = None, target_coherence: float = None,
                            activity: float = None):
        """Adjust simulator parameters at runtime (for demo sliders).

        activity ∈ [0, 3] — magnitude движения (0 = лежишь, 1 = ходишь, 2+ = бег).
        Эмулирует acc L2 norm - gravity baseline.
        """
        if self._simulator is not None:
            self._simulator.set_state(target_hr=target_hr, target_coherence=target_coherence)
        if activity is not None:
            self._activity_magnitude = max(0.0, min(5.0, float(activity)))

    def update_activity(self, magnitude: float):
        """Called by Polar reader (real accelerometer): передаёт mag ≈ |accel|−g."""
        self._activity_magnitude = max(0.0, min(5.0, float(magnitude)))

    # ── Data access ────────────────────────────────────────────────────

    def get_rr_intervals(self) -> list:
        with self._lock:
            return list(self.rr_buffer)

    def get_metrics(self) -> Dict:
        """Compute HRV metrics from current buffer."""
        rr = self.get_rr_intervals()
        if len(rr) < 2:
            return {}
        metrics = calculate_hrv_metrics(rr)
        self._last_metrics = metrics
        return metrics

    def get_baddle_state(self) -> Dict:
        """HRV + activity → UserState signals. activity_magnitude проходит как
        отдельный канал; activity_zone deriveится в UserState."""
        state = hrv_to_baddle_state(self.get_metrics())
        state["activity_magnitude"] = round(float(self._activity_magnitude), 3)
        return state

    def get_status(self) -> Dict:
        return {
            "mode": self.mode,
            "running": self.is_running,
            "buffer_size": len(self.rr_buffer),
            "has_baseline": self._baseline is not None,
            "last_metrics": self._last_metrics,
        }

    # ── Calibration ────────────────────────────────────────────────────

    def calibrate(self) -> Dict:
        """Save current metrics as baseline."""
        metrics = self.get_metrics()
        if not metrics:
            return {}
        self._baseline = {
            "rmssd": metrics.get("rmssd"),
            "coherence": metrics.get("coherence"),
            "heart_rate": metrics.get("heart_rate"),
            "lf_hf_ratio": metrics.get("lf_hf_ratio"),
            "timestamp": time.time(),
        }
        log.info(f"[hrv] calibrated: {self._baseline}")
        return self._baseline

    def get_baseline(self) -> Optional[Dict]:
        return self._baseline


# ── Global singleton ────────────────────────────────────────────────────

_manager: Optional[HRVManager] = None


def get_manager() -> HRVManager:
    global _manager
    if _manager is None:
        _manager = HRVManager()
    return _manager
