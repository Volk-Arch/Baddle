"""Единый источник путей ко всем runtime-файлам.

Все runtime-данные живут под `data/` (кроме `graphs/<ws>/` и `workspaces/`,
у них своя логика). Код ship-with-defaults там где нужно — см. ui.py.
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

# ── UI defaults (персистентные, но перезаписываемые; см. ui.py fallback) ──
ROLES_FILE            = DATA_DIR / "roles.json"
TEMPLATES_FILE        = DATA_DIR / "templates.json"

# ── Workspace=main fallback для StateGraph ─────────────────────────────
# Когда StateGraph создаётся без base_dir (тесты / единичные вызовы),
# пишет сюда. В production workspace.load_active_graph() прокидывает
# base_dir = graphs/<active_ws>/.
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
        STATE_GRAPH_FILE,
        STATE_EMBEDDINGS_FILE,
        STATE_GRAPH_ARCHIVE,
    ]
    # Per-workspace runtime (graph.json + state_graph.jsonl + solved/)
    graphs_root = PROJECT_ROOT / "graphs"
    if graphs_root.exists():
        for ws in graphs_root.iterdir():
            if ws.is_dir():
                paths.append(ws)
    # Workspace index
    ws_idx = PROJECT_ROOT / "workspaces" / "index.json"
    if ws_idx.exists():
        paths.append(ws_idx)
    return paths
