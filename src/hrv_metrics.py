"""HRV metrics — adapted from working HRV Reader project.

Source: C:\\Users\\Volk\\Desktop\\Projects\\hrv\\hrv_calculator.py
This is a self-contained copy — no external dependencies beyond numpy.

Provides:
  - calculate_rmssd/sdnn/pnn50
  - calculate_frequency_domain (LF/HF via FFT)
  - calculate_coherence (derived metric for Baddle)
  - calculate_hrv_metrics (all-in-one)
"""
import math
import numpy as np
from typing import List, Optional, Dict


def calculate_rmssd(rr_intervals: List[float]) -> Optional[float]:
    """RMSSD — parasympathetic tone. Higher = more relaxed."""
    if len(rr_intervals) < 2:
        return None
    differences = []
    for i in range(len(rr_intervals) - 1):
        diff = rr_intervals[i + 1] - rr_intervals[i]
        differences.append(diff * diff)
    if not differences:
        return None
    return math.sqrt(sum(differences) / len(differences))


def calculate_sdnn(rr_intervals: List[float]) -> Optional[float]:
    """SDNN — overall HRV."""
    if len(rr_intervals) < 2:
        return None
    mean = sum(rr_intervals) / len(rr_intervals)
    variance = sum((x - mean) ** 2 for x in rr_intervals) / (len(rr_intervals) - 1)
    return math.sqrt(variance)


def calculate_pnn50(rr_intervals: List[float]) -> Optional[float]:
    """pNN50 — percent of RR diffs > 50ms."""
    if len(rr_intervals) < 2:
        return None
    count = 0
    for i in range(len(rr_intervals) - 1):
        if abs(rr_intervals[i + 1] - rr_intervals[i]) > 50:
            count += 1
    return (count / (len(rr_intervals) - 1)) * 100


def calculate_frequency_domain(rr_intervals: List[float], sampling_rate: float = 4.0) -> Dict[str, Optional[float]]:
    """LF/HF via FFT. LF=sympathetic, HF=parasympathetic."""
    if len(rr_intervals) < 10:
        return {'lf': None, 'hf': None, 'lf_hf_ratio': None}

    try:
        time_points = np.cumsum([0] + rr_intervals[:-1])
        total_time = time_points[-1] + rr_intervals[-1]
        num_samples = int(total_time / (1000.0 / sampling_rate))
        if num_samples < 10:
            return {'lf': None, 'hf': None, 'lf_hf_ratio': None}

        uniform_time = np.linspace(0, total_time, num_samples)
        interpolated_rr = np.interp(uniform_time, time_points, rr_intervals)

        # Detrend to remove DC/drift
        mean_val = np.mean(interpolated_rr)
        detrended = interpolated_rr - mean_val

        fft_values = np.fft.fft(detrended)
        fft_freq = np.fft.fftfreq(len(detrended), 1.0 / sampling_rate)
        power = np.abs(fft_values) ** 2

        lf_mask = (fft_freq >= 0.04) & (fft_freq <= 0.15)
        hf_mask = (fft_freq >= 0.15) & (fft_freq <= 0.4)

        lf_power = float(np.sum(power[lf_mask])) if np.any(lf_mask) else 0.0
        hf_power = float(np.sum(power[hf_mask])) if np.any(hf_mask) else 0.0

        lf_hf_sum = lf_power + hf_power
        if lf_hf_sum > 0:
            lf_nu = (lf_power / lf_hf_sum) * 100
            hf_nu = (hf_power / lf_hf_sum) * 100
        else:
            lf_nu = None
            hf_nu = None

        lf_hf_ratio = lf_power / hf_power if hf_power > 0 else None

        return {'lf': lf_nu, 'hf': hf_nu, 'lf_hf_ratio': lf_hf_ratio}
    except Exception:
        return {'lf': None, 'hf': None, 'lf_hf_ratio': None}


def calculate_coherence(rr_intervals: List[float]) -> Optional[float]:
    """HRV coherence [0-1].

    High coherence = HRV has a clear dominant frequency (around 0.1 Hz, breathing-heart sync).
    Low coherence = noisy spectrum, no dominant peak.

    Method: peak power in 0.04-0.26 Hz / total power in same band.
    Classical HeartMath approach.
    """
    if len(rr_intervals) < 10:
        return None

    try:
        time_points = np.cumsum([0] + rr_intervals[:-1])
        total_time = time_points[-1] + rr_intervals[-1]
        num_samples = int(total_time / (1000.0 / 4.0))
        if num_samples < 10:
            return None

        uniform_time = np.linspace(0, total_time, num_samples)
        interpolated_rr = np.interp(uniform_time, time_points, rr_intervals)
        detrended = interpolated_rr - np.mean(interpolated_rr)

        fft_values = np.fft.fft(detrended)
        fft_freq = np.fft.fftfreq(len(detrended), 1.0 / 4.0)
        power = np.abs(fft_values) ** 2

        # Coherence band 0.04-0.26 Hz
        coh_mask = (fft_freq >= 0.04) & (fft_freq <= 0.26)
        if not np.any(coh_mask):
            return None

        coh_power = power[coh_mask]
        total_in_band = float(np.sum(coh_power))
        if total_in_band <= 0:
            return 0.0

        peak_power = float(np.max(coh_power))
        # Normalize: peak/total with smoothing for stability
        # Classic HeartMath uses slightly different weighting but this approximates well
        coherence = peak_power / total_in_band
        # Typical values are 0-0.5; rescale to 0-1 for UI
        return min(1.0, coherence * 2.0)
    except Exception:
        return None


def calculate_hrv_metrics(rr_intervals: List[float]) -> Dict[str, Optional[float]]:
    """All-in-one HRV summary."""
    if not rr_intervals or len(rr_intervals) < 2:
        return {
            'rmssd': None, 'sdnn': None, 'pnn50': None,
            'lf': None, 'hf': None, 'lf_hf_ratio': None,
            'coherence': None, 'heart_rate': None,
        }

    mean_rr = sum(rr_intervals) / len(rr_intervals)
    heart_rate = 60000.0 / mean_rr if mean_rr > 0 else None

    metrics = {
        'rmssd': calculate_rmssd(rr_intervals),
        'sdnn': calculate_sdnn(rr_intervals),
        'pnn50': calculate_pnn50(rr_intervals),
        'heart_rate': heart_rate,
    }

    if len(rr_intervals) >= 10:
        freq = calculate_frequency_domain(rr_intervals)
        metrics.update(freq)
        metrics['coherence'] = calculate_coherence(rr_intervals)
    else:
        metrics.update({'lf': None, 'hf': None, 'lf_hf_ratio': None, 'coherence': None})

    return metrics


# ── Stress/Coherence normalization for Baddle ──

def hrv_to_baddle_state(metrics: Dict) -> Dict:
    """Convert HRV metrics to Baddle state params (0-1 range).

    Returns:
      stress ∈ [0,1] — 0 calm, 1 stressed (from 1/RMSSD)
      coherence ∈ [0,1] — already normalized
      energy_recovery ∈ [0,1] — suggests daily energy level

    Used by Horizon.update_from_hrv()
    """
    rmssd = metrics.get('rmssd')
    coherence = metrics.get('coherence')

    # Stress from RMSSD: ~20ms = high stress, ~80ms = low stress
    if rmssd is not None:
        stress = max(0.0, min(1.0, 1.0 - (rmssd / 80.0)))
    else:
        stress = None

    # Energy recovery estimate from RMSSD + coherence
    if rmssd is not None and coherence is not None:
        # Typical healthy: RMSSD 30-80ms
        rmssd_score = max(0.0, min(1.0, (rmssd - 15.0) / 65.0))
        energy_recovery = 0.5 * rmssd_score + 0.5 * coherence
    elif rmssd is not None:
        energy_recovery = max(0.0, min(1.0, (rmssd - 15.0) / 65.0))
    else:
        energy_recovery = 0.5  # unknown

    return {
        'stress': stress,
        'coherence': coherence,
        'energy_recovery': energy_recovery,
        'rmssd': rmssd,
        'heart_rate': metrics.get('heart_rate'),
        'lf_hf_ratio': metrics.get('lf_hf_ratio'),
    }


# ── Simulator for demo without Polar H10 ──

import random
import time


class HRVSimulator:
    """Simulates RR intervals without a real sensor.

    Useful for demo, testing, and when Polar H10 isn't available.
    Generates realistic RR patterns based on target state.
    """

    def __init__(self, base_hr: float = 70.0, target_coherence: float = 0.7):
        self.base_hr = base_hr
        self.target_coherence = target_coherence
        self.rr_buffer: List[float] = []
        self._breath_phase = 0.0

    def set_state(self, target_hr: float = None, target_coherence: float = None):
        if target_hr is not None:
            self.base_hr = max(50.0, min(120.0, target_hr))
        if target_coherence is not None:
            self.target_coherence = max(0.0, min(1.0, target_coherence))

    def tick(self) -> float:
        """Generate one new RR interval (milliseconds).

        Adds breathing modulation (RSA) proportional to target_coherence.
        """
        base_rr = 60000.0 / self.base_hr

        # Breathing modulation (0.2 Hz ≈ 12 breaths/min)
        self._breath_phase += 0.2 * (base_rr / 1000.0)
        rsa_amplitude = 30.0 * self.target_coherence  # stronger when coherent
        rsa = rsa_amplitude * math.sin(self._breath_phase * 2 * math.pi)

        # Random noise inversely proportional to coherence
        noise_amplitude = 50.0 * (1.0 - self.target_coherence)
        noise = random.gauss(0, noise_amplitude)

        rr = base_rr + rsa + noise
        rr = max(400.0, min(1500.0, rr))

        self.rr_buffer.append(rr)
        if len(self.rr_buffer) > 240:  # ~2 min at 70 bpm
            self.rr_buffer.pop(0)

        return rr

    def get_metrics(self) -> Dict:
        return calculate_hrv_metrics(self.rr_buffer)

    def get_baddle_state(self) -> Dict:
        return hrv_to_baddle_state(self.get_metrics())


# Global singleton simulator (for demo mode)
_simulator = None


def get_simulator() -> HRVSimulator:
    global _simulator
    if _simulator is None:
        _simulator = HRVSimulator()
    return _simulator
