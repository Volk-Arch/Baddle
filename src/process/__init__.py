"""Process — детекторы, dispatcher, NAND, pump, consolidation, cognitive loop.

Реализует ветвь H.2 онтологии (см. examples/ontology-v3-baddle-branch.md):
все периодические/реактивные операции над состоянием substrate. NAND
рядом с pump/consolidation — три mental operator'а в одном месте.

Public API ниже — convenience re-exports. Полные пути работают тоже.
"""

from .signals import Signal, Dispatcher
from .detectors import DETECTORS, build_detector_context
from .nand import tick_emergent
from .pump import pump
from .consolidation import consolidate_all
from .cognitive_loop import CognitiveLoop

__all__ = [
    "Signal",
    "Dispatcher",
    "DETECTORS",
    "build_detector_context",
    "tick_emergent",
    "pump",
    "consolidate_all",
    "CognitiveLoop",
]
