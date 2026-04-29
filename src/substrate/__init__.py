"""Substrate — резонатор + горизонт + state shim.

Реализует ветвь H.1 онтологии (см. examples/ontology-v3-baddle-branch.md).
Public API ниже — convenience re-exports. Полные пути работают тоже:
`from .substrate.rgk import X` ≡ `from .substrate import X`.
"""

from .rgk import РГК, get_global_rgk, reset_global_rgk
from .horizon import (
    CognitiveState,
    create_horizon,
    get_global_state,
    set_global_state,
    PROTECTIVE_FREEZE,
    INTEGRATION,
)
from .user_state import (
    UserState,
    get_user_state,
    set_user_state,
    compute_sync_error,
    compute_sync_error_wave,
    compute_sync_regime,
)

__all__ = [
    "РГК",
    "get_global_rgk",
    "reset_global_rgk",
    "CognitiveState",
    "create_horizon",
    "get_global_state",
    "set_global_state",
    "PROTECTIVE_FREEZE",
    "INTEGRATION",
    "UserState",
    "get_user_state",
    "set_user_state",
    "compute_sync_error",
    "compute_sync_error_wave",
    "compute_sync_regime",
]
