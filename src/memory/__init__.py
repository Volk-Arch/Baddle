"""Memory — STM (workspace) + LTM (graph) substrate.

Реализует ветвь H.3 онтологии (см. examples/ontology-v3-baddle-branch.md).
Сейчас здесь только workspace (W14.1). graph_logic + state_graph + action
memory + consolidation мигрируют в эту директорию по мере wave-by-wave
впадания (W18 Phase 2).
"""
from . import workspace

__all__ = ["workspace"]
