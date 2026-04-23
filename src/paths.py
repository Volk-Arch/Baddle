"""Единый источник путей ко всем runtime-файлам.

Все runtime-данные живут под `data/` (кроме `graphs/main/` — граф мыслей,
state_graph, archive). Код ship-with-defaults там где нужно — см. ui.py.
"""
import logging
from pathlib import Path

log = logging.getLogger(__name__)


PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"

# ── Runtime data (per-person, не shared между юзерами) ─────────────────
SETTINGS_FILE         = DATA_DIR / "settings.json"
USER_STATE_FILE       = DATA_DIR / "user_state.json"
USER_PROFILE_FILE     = DATA_DIR / "user_profile.json"
GOALS_FILE            = DATA_DIR / "goals.jsonl"
ACTIVITY_FILE         = DATA_DIR / "activity.jsonl"
CHECKINS_FILE         = DATA_DIR / "checkins.jsonl"
PATTERNS_FILE         = DATA_DIR / "patterns.jsonl"
PLANS_FILE            = DATA_DIR / "plans.jsonl"
CHAT_HISTORY_FILE     = DATA_DIR / "chat_history.jsonl"
SENSOR_READINGS_FILE  = DATA_DIR / "sensor_readings.jsonl"
THROTTLE_DROPS_FILE   = DATA_DIR / "throttle_drops.jsonl"  # когда proactive check
                                                           # детектил сигнал но
                                                           # throttle заблокировал

# ── UI defaults (персистентные, но перезаписываемые; см. ui.py fallback) ──
ROLES_FILE            = DATA_DIR / "roles.json"
TEMPLATES_FILE        = DATA_DIR / "templates.json"

# ── Legacy state-graph fallbacks (используется `/data/reset` для очистки
# старых расположений; текущий state_graph живёт в `graphs/main/`) ─────
STATE_GRAPH_FILE           = DATA_DIR / "state_graph.jsonl"
STATE_EMBEDDINGS_FILE      = DATA_DIR / "state_embeddings.jsonl"
STATE_GRAPH_ARCHIVE        = DATA_DIR / "state_graph.archive.jsonl"


def ensure_data_dir() -> Path:
    """Создаёт `data/` если не существует. Возвращает путь."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def get_resettable_files() -> list[Path]:
    """Список файлов/папок которые очищаются через /data/reset endpoint.

    Исключает: settings (API config), roles, templates (ship-defaults copy).
    """
    paths = [
        USER_STATE_FILE,
        USER_PROFILE_FILE,
        GOALS_FILE,
        ACTIVITY_FILE,
        CHECKINS_FILE,
        PATTERNS_FILE,
        PLANS_FILE,
        CHAT_HISTORY_FILE,
        SENSOR_READINGS_FILE,
        STATE_GRAPH_FILE,
        STATE_EMBEDDINGS_FILE,
        STATE_GRAPH_ARCHIVE,
    ]
    # Граф + state_graph + solved/
    graph_dir = PROJECT_ROOT / "graphs" / "main"
    if graph_dir.exists():
        paths.append(graph_dir)
    return paths
