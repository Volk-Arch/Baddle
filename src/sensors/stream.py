"""Sensor stream — универсальный поток сигналов тела.

Было: `HRVManager` держал один источник (simulator или Polar-stub) и
in-memory deque RR-интервалов. Другие источники (Apple Watch,
manual check-in) не было куда подключить, данные не персистились.

Стало: любой источник пушит `SensorReading` в единый `SensorStream`.
Stream персистит append-only в `data/sensor_readings.jsonl` и отдаёт
rolling window + weighted aggregate. UserState читает отсюда,
не из конкретного manager'а.

Типы источников (`source` поле):
  polar_h10    — реальный Polar H10 BLE (high-freq RR + accelerometer)
  apple_watch  — Apple Watch HR samples (sparse, ~раз в N мин)
  manual       — check-in форма (energy/focus/stress — юзер ввёл)
  simulator    — sim, dev/demo

Типы сигналов (`kind` поле на уровне reading):
  rr            — один RR interval (ms), для high-freq источников
  hrv_snapshot  — агрегат RMSSD/coherence/heart_rate/lf_hf (Polar/Apple)
  activity      — акселерометр magnitude
  subjective    — manual check-in {energy, focus, stress, surprise, valence}

UserState читает `latest_aggregate(kinds={hrv_snapshot, subjective}, window=300s)`
→ weighted avg с decay по давности.
"""
from __future__ import annotations
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Iterable

from ..paths import SENSOR_READINGS_FILE

log = logging.getLogger(__name__)


SENSOR_FILE = SENSOR_READINGS_FILE

# Источники — enum-like, но строкой для backward compat
SOURCE_POLAR       = "polar_h10"
SOURCE_APPLE       = "apple_watch"
SOURCE_MANUAL      = "manual"
SOURCE_SIMULATOR   = "simulator"

# Kinds
KIND_RR            = "rr"
KIND_HRV_SNAPSHOT  = "hrv_snapshot"
KIND_ACTIVITY      = "activity"
KIND_SUBJECTIVE    = "subjective"


# ── Data model ───────────────────────────────────────────────────────────

@dataclass
class SensorReading:
    """Один замер одного датчика в один момент времени.

    metrics — словарь сигналов конкретной природы:
      rr                   → {"rr_ms": 867}
      hrv_snapshot         → {"rmssd": 42, "coherence": 0.65, "heart_rate": 72,
                               "lf_hf_ratio": 1.3, "stress": 0.2}
      activity             → {"magnitude": 0.4}
      subjective           → {"energy": 70, "focus": 65, "stress": 30,
                               "surprise": -0.2, "valence": 0.5}

    confidence ∈ [0,1] — насколько надёжна выборка:
      - polar_h10/simulator hrv_snapshot: 1.0
      - apple_watch sparse (редко): 0.8
      - manual checkin: 0.7 (subjective, но user-reported)
    """
    ts: float
    source: str
    kind: str
    metrics: dict = field(default_factory=dict)
    confidence: float = 1.0

    def to_json(self) -> str:
        return json.dumps({
            "ts": round(self.ts, 3),
            "source": self.source,
            "kind": self.kind,
            "metrics": self.metrics,
            "confidence": round(self.confidence, 2),
        }, ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict) -> Optional["SensorReading"]:
        try:
            return cls(
                ts=float(d.get("ts", 0)),
                source=str(d.get("source", "unknown")),
                kind=str(d.get("kind", "unknown")),
                metrics=d.get("metrics") or {},
                confidence=float(d.get("confidence", 1.0)),
            )
        except Exception:
            return None


# ── Stream ───────────────────────────────────────────────────────────────

class SensorStream:
    """Thread-safe singleton. Пишет в jsonl + держит rolling window.

    Rolling window — последние MAX_MEMORY readings в памяти (для быстрого
    query без чтения файла). Старше — живут только на диске (можно поднять
    через replay если нужно).
    """

    MAX_MEMORY = 2000     # ≈ 5-10 минут при 200ms/beat от Polar
    RR_DOWNSAMPLE = 10    # пишем на диск каждый 10-й RR (иначе файл раздуется)

    def __init__(self):
        self._lock = threading.Lock()
        self._buf: list[SensorReading] = []
        self._rr_counter = 0
        # Активные источники — кто сейчас пушит. UI показывает статус.
        self._active_sources: dict[str, float] = {}  # source → last push ts

    def push(self, reading: SensorReading, persist: bool = True):
        """Добавить замер. High-freq RR — downsample на диск; остальное — всё."""
        with self._lock:
            self._buf.append(reading)
            if len(self._buf) > self.MAX_MEMORY:
                self._buf = self._buf[-self.MAX_MEMORY:]
            self._active_sources[reading.source] = reading.ts

            should_persist = persist
            if reading.kind == KIND_RR:
                self._rr_counter += 1
                if self._rr_counter % self.RR_DOWNSAMPLE != 0:
                    should_persist = False
            if should_persist:
                try:
                    SENSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
                    with SENSOR_FILE.open("a", encoding="utf-8") as f:
                        f.write(reading.to_json() + "\n")
                except OSError as e:
                    log.debug(f"[sensor_stream] persist failed: {e}")

    def recent(self, kinds: Optional[Iterable[str]] = None,
                sources: Optional[Iterable[str]] = None,
                since_seconds: float = 300) -> list[SensorReading]:
        """Последние readings из memory-буфера за окно."""
        cutoff = time.time() - since_seconds
        kinds_set = set(kinds) if kinds else None
        sources_set = set(sources) if sources else None
        with self._lock:
            out = []
            for r in self._buf:
                if r.ts < cutoff:
                    continue
                if kinds_set and r.kind not in kinds_set:
                    continue
                if sources_set and r.source not in sources_set:
                    continue
                out.append(r)
            return out

    def latest_hrv_aggregate(self, window_s: float = 180) -> Optional[dict]:
        """Weighted average HRV-метрик за окно.

        Вес = confidence × exp(-age/τ), τ = window_s/2.
        Если источников нет — None. Если есть только subjective (manual
        checkin) — возвращаем его как fallback (stress/energy как proxy).
        """
        import math
        readings = self.recent(
            kinds=[KIND_HRV_SNAPSHOT, KIND_SUBJECTIVE],
            since_seconds=window_s,
        )
        if not readings:
            return None
        tau = max(30.0, window_s / 2.0)
        now = time.time()

        # Собираем агрегат по каждой метрике
        sums = {}
        weights = {}
        for r in readings:
            age = now - r.ts
            w = max(0.0, r.confidence) * math.exp(-age / tau)
            if w <= 0:
                continue
            for k, v in (r.metrics or {}).items():
                if v is None:
                    continue
                sums[k] = sums.get(k, 0.0) + w * float(v)
                weights[k] = weights.get(k, 0.0) + w
        agg = {k: round(sums[k] / weights[k], 3) for k in sums if weights[k] > 0}
        agg["_sources"] = sorted({r.source for r in readings})
        agg["_window_s"] = window_s
        agg["_sample_count"] = len(readings)
        return agg

    def recent_activity(self, window_s: float = 60) -> Optional[float]:
        """Latest activity magnitude (acc L2 от Polar или slider симулятора)."""
        readings = self.recent(kinds=[KIND_ACTIVITY], since_seconds=window_s)
        if not readings:
            return None
        # Берём latest (не среднее — магнитуда быстрая)
        latest = readings[-1]
        return float(latest.metrics.get("magnitude", 0.0))

    def active_sources(self, stale_after_s: float = 60) -> list[str]:
        """Источники которые пушили за последние N сек — для UI status."""
        cutoff = time.time() - stale_after_s
        with self._lock:
            return sorted(s for s, ts in self._active_sources.items() if ts >= cutoff)

    def clear_memory(self):
        """Сбросить in-memory буфер (диск не трогаем). Для /data/reset."""
        with self._lock:
            self._buf.clear()
            self._active_sources.clear()
            self._rr_counter = 0


# ── Singleton ────────────────────────────────────────────────────────────

_stream: Optional[SensorStream] = None


def get_stream() -> SensorStream:
    global _stream
    if _stream is None:
        _stream = SensorStream()
    return _stream


# ── Convenience push helpers ────────────────────────────────────────────

def push_hrv_snapshot(source: str, rmssd: Optional[float] = None,
                       coherence: Optional[float] = None,
                       heart_rate: Optional[float] = None,
                       lf_hf_ratio: Optional[float] = None,
                       stress: Optional[float] = None,
                       confidence: float = 1.0):
    """Толкнуть HRV-снимок. None-поля не записываются."""
    metrics = {}
    for k, v in [("rmssd", rmssd), ("coherence", coherence),
                 ("heart_rate", heart_rate), ("lf_hf_ratio", lf_hf_ratio),
                 ("stress", stress)]:
        if v is not None:
            metrics[k] = round(float(v), 3)
    if not metrics:
        return
    get_stream().push(SensorReading(
        ts=time.time(), source=source, kind=KIND_HRV_SNAPSHOT,
        metrics=metrics, confidence=confidence,
    ))


def push_rr(source: str, rr_ms: float):
    """Один RR-interval (high-freq sources: Polar, simulator)."""
    get_stream().push(SensorReading(
        ts=time.time(), source=source, kind=KIND_RR,
        metrics={"rr_ms": round(float(rr_ms), 1)}, confidence=1.0,
    ))


def push_activity(source: str, magnitude: float):
    """Акселерометр magnitude — 0 покой, 1 ходьба, 2+ бег."""
    get_stream().push(SensorReading(
        ts=time.time(), source=source, kind=KIND_ACTIVITY,
        metrics={"magnitude": round(max(0.0, float(magnitude)), 3)},
        confidence=1.0,
    ))


def push_subjective(energy: Optional[float] = None, focus: Optional[float] = None,
                     stress: Optional[float] = None, surprise: Optional[float] = None,
                     valence: Optional[float] = None, note: Optional[str] = None):
    """Manual check-in — субъективные оценки юзера. Confidence 0.7 (самоотчёт)."""
    metrics = {}
    for k, v in [("energy", energy), ("focus", focus), ("stress", stress),
                 ("surprise", surprise), ("valence", valence)]:
        if v is not None:
            metrics[k] = round(float(v), 3)
    if not metrics:
        return
    if note:
        metrics["_note"] = str(note)[:200]
    get_stream().push(SensorReading(
        ts=time.time(), source=SOURCE_MANUAL, kind=KIND_SUBJECTIVE,
        metrics=metrics, confidence=0.7,
    ))
