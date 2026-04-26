"""Sensor adapters — как подключать реальные источники тела в SensorStream.

Все адаптеры имеют один интерфейс:
  .start() / .stop() / .is_running
Реализация push'ит данные в `sensor_stream.get_stream()` через хелперы
`push_rr / push_hrv_snapshot / push_activity / push_subjective`.

Polar H10 — готов по структуре, реальный BLE (bleak) не подключён.
Apple Watch — заготовка, HealthKit-импорт не реализован.

simulator уже работает — он внутри HRVManager._start_simulator, пушит
в stream автоматически (см. hrv_manager.py).

Manual — сразу работает: /checkin endpoint пушит subjective через
push_subjective. Не требует отдельного адаптера.
"""
from __future__ import annotations
import logging
from typing import Optional

from .sensor_stream import SOURCE_POLAR, SOURCE_APPLE

log = logging.getLogger(__name__)


class BaseSensorAdapter:
    source: str = "unknown"

    def __init__(self):
        self.is_running = False

    def start(self, **kwargs) -> bool:
        raise NotImplementedError

    def stop(self):
        self.is_running = False


# ── Polar H10 ────────────────────────────────────────────────────────────

class PolarH10Adapter(BaseSensorAdapter):
    """Polar H10 через BLE (bleak + bleakheart). High-freq RR + accelerometer.

    Подключение:
      pip install bleak bleakheart
      adapter = PolarH10Adapter()
      adapter.start(mac_address="XX:XX:...")
    Затем simulator останавливается, UserState читает реальный поток
    через sensor_stream.

    Статус: структура готова, реальный BLE не реализован (нет pip-пакетов,
    нет физического устройства для тестов).
    """
    source = SOURCE_POLAR

    def start(self, mac_address: Optional[str] = None, **_) -> bool:
        log.warning("[sensor:polar_h10] BLE integration not implemented — "
                    "install bleak+bleakheart and see docs/hrv-design.md")
        return False


# ── Apple Watch ──────────────────────────────────────────────────────────

class AppleWatchAdapter(BaseSensorAdapter):
    """Apple Watch через HealthKit export или локальный server.

    Apple Watch не даёт continuous RR — только HR samples раз в несколько
    минут + RMSSD-снимки (HealthKit). Подход:
      1. Юзер экспортирует здоровье из iPhone → парсим XML
      2. Или поднимаем локальный HealthKit-server (iOS shortcut)
      3. Или читаем из Apple Health iCloud API (если появится)

    Sparse push: каждый sample → push_hrv_snapshot с confidence=0.8,
    kind=hrv_snapshot (не RR — нет high-freq данных).
    Стресс derive'ится из HRV + HR deviation.
    """
    source = SOURCE_APPLE

    def start(self, export_path: Optional[str] = None, **_) -> bool:
        log.warning("[sensor:apple_watch] HealthKit import not implemented")
        return False


# ── Registry ─────────────────────────────────────────────────────────────

ADAPTERS = {
    SOURCE_POLAR:  PolarH10Adapter,
    SOURCE_APPLE:  AppleWatchAdapter,
}


def get_adapter(source: str) -> Optional[BaseSensorAdapter]:
    """Factory: вернуть instance адаптера по имени source."""
    cls = ADAPTERS.get(source)
    return cls() if cls else None
