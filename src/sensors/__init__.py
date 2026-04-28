"""Sensors package — все источники сигналов тела + хранилище.

Sub-modules:
  - `manager`  — `HRVManager` singleton, simulator/Polar mode lifecycle
  - `metrics`  — pure HRV functions (RMSSD/SDNN/coherence/LF-HF, simulator)
  - `stream`   — `SensorStream` (jsonl), source/kind constants, push_* helpers
  - `adapters` — `BaseSensorAdapter`, Polar/Apple stubs, registry

История: до W11 #4 жил как 4 plain-файла в `src/`. Package позволяет
расти: новые адаптеры (real Polar BLE / EEG / Apple Watch HealthKit)
добавляются как отдельные модули без раздувания.

Используйте прямые sub-module импорты (`from .sensors.manager import
get_manager`), не re-exports — чтобы test patches и трейсы указывали
на конкретный файл.
"""
