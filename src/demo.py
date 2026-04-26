"""Demo seeder — наполняет единственный граф Baddle реалистичным контентом.

Пустой Baddle не демонстрирует ничего: morning briefing без данных пуст,
pump без графа ничего не находит, dashboard без checkins — нули. DEMO
заливает `graphs/main/` содержательными нодами, recurring-целями и
7 днями истории (activity + checkins), чтобы система сразу работала как
задумано.

Тематика: wellbeing + сторонние треки (сон / движение / кофе / вода /
работа / код) — отражает обычный микс юзера, а не чистый «work».

Использование:
  from src.demo import seed_demo, wipe_all_runtime
  wipe_all_runtime()   # удалить существующие данные
  seed_demo()          # заполнить graphs/main/

Timestamps относительные (now() − N часов/дней) — DEMO всегда выглядит
свежим независимо от того когда его загрузили.
"""
from __future__ import annotations
import json
import logging
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .paths import (
    GOALS_FILE, ACTIVITY_FILE, CHECKINS_FILE, PATTERNS_FILE,
    PLANS_FILE, CHAT_HISTORY_FILE, USER_STATE_FILE, USER_PROFILE_FILE,
    STATE_GRAPH_FILE, STATE_EMBEDDINGS_FILE, STATE_GRAPH_ARCHIVE,
)

log = logging.getLogger(__name__)

_ROOT = Path(__file__).parent.parent
_GRAPHS_DIR = _ROOT / "graphs"
_GRAPH_DIR = _GRAPHS_DIR / "main"


# ── Wipe ─────────────────────────────────────────────────────────────────

def wipe_all_runtime() -> dict:
    """Удалить все runtime-данные (то же что /data/reset endpoint).

    Не трогает: settings, roles, templates, source code.
    """
    removed: list[str] = []
    files = [
        GOALS_FILE, ACTIVITY_FILE, CHECKINS_FILE, PATTERNS_FILE, PLANS_FILE,
        CHAT_HISTORY_FILE, USER_STATE_FILE, USER_PROFILE_FILE,
        STATE_GRAPH_FILE, STATE_EMBEDDINGS_FILE, STATE_GRAPH_ARCHIVE,
    ]
    for f in files:
        if f.exists():
            try:
                f.unlink()
                removed.append(str(f.name))
            except OSError as e:
                log.warning(f"[demo.wipe] {f}: {e}")
    if _GRAPHS_DIR.exists():
        try:
            shutil.rmtree(_GRAPHS_DIR)
            removed.append("graphs/")
        except OSError as e:
            log.warning(f"[demo.wipe] graphs/: {e}")
    log.info(f"[demo.wipe] removed {len(removed)} items")
    return {"removed": removed, "count": len(removed)}


# ── Helpers ──────────────────────────────────────────────────────────────

def _now() -> float:
    return time.time()


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _mkid() -> str:
    return uuid.uuid4().hex[:12]


def _mknode(idx: int, text: str, ntype: str, topic: str = "",
            confidence: float = 0.5, mode: str = "free") -> dict:
    """Создать node в формате graph.json. Embedding пустой — сгенерится
    при первом pump/tick через _ensure_embeddings."""
    now = _iso_now()
    return {
        "id": idx,
        "text": text,
        "embedding": [],
        "entropy": {"avg": 0, "unc": 0},
        "depth": 0 if ntype == "goal" else 1,
        "topic": topic,
        "confidence": confidence,
        "type": ntype,
        "rendered": False,
        "created_at": now,
        "last_accessed": now,
        "mode": mode,
    }


def _write_graph(nodes: list, directed_edges: list,
                 topic: str, mode: str = "free") -> None:
    """Записать graph.json в `graphs/main/`."""
    _GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    graph = {
        "nodes": nodes,
        "edges": {
            "manual_links": [list(p) for p in directed_edges],
            "manual_unlinks": [],
            "directed": [list(p) for p in directed_edges],
            "caused_by": [],
            "followed_by": [],
        },
        "meta": {"topic": topic, "hub_nodes": [], "mode": mode},
        "embeddings": [],
        "tp_overrides": {},
    }
    (_GRAPH_DIR / "graph.json").write_text(
        json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # meta.json
    now = _iso_now()
    (_GRAPH_DIR / "meta.json").write_text(
        json.dumps({
            "id": "main", "title": "Main",
            "created": now, "last_active": now,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def _append_jsonl(path: Path, events: list[dict]) -> None:
    """Добавить events в jsonl (создаст файл если нужно)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


# ── Content ──────────────────────────────────────────────────────────────

def _seed_content() -> None:
    """Наполнение единственного графа. Смешивает темы wellbeing и code."""
    nodes_spec = [
        (0, "Снизить хронический стресс", "goal", "wellbeing"),
        (1, "Больше сна — 7+ часов в будни", "hypothesis", "sleep"),
        (2, "Регулярные прогулки по выходным", "hypothesis", "movement"),
        (3, "Меньше кофе после 16:00", "hypothesis", "caffeine"),
        (4, "7ч сна → energy следующего дня +20-30%", "evidence", "sleep"),
        (5, "Кофе после 16 — не мог уснуть до часу", "evidence", "caffeine"),
        (6, "В парке в субботу stress упал ощутимо", "evidence", "movement"),
        (7, "Ходить пешком вместо транспорта где можно", "hypothesis", "movement"),
        (8, "10000 шагов/день коррелирует с mood", "evidence", "movement"),
        (9, "Цель: 10к шагов ежедневно", "goal", "movement"),
        (10, "Что делать когда мало спал?", "question", "sleep"),
        (11, "Короткий дневной сон 20 минут", "hypothesis", "sleep"),
        (12, "Дневной сон >40 мин ломает ночной", "evidence", "sleep"),
        (13, "Медитация 5 минут утром", "hypothesis", "mental"),
        (14, "5-мин медитация снижает cortisol (исследования)", "evidence", "mental"),
        (15, "Пить 2 литра воды в день", "goal", "water"),
        (16, "Обезвоживание усиливает головную боль", "evidence", "water"),
        (17, "Завтрак — не пропускать", "hypothesis", "food"),
        (18, "Пропуск = crash к 11 утра, тяга к сладкому", "evidence", "food"),
        (19, "Снизить кофе до 2 чашек в день", "goal", "caffeine"),
        (20, "3-4 чашки = раздражительность вечером", "evidence", "caffeine"),
        (21, "Основа: сон + движение + вода", "synthesis", "wellbeing"),
        (22, "Чем меряем что стресс снизился?", "question", "wellbeing"),
        (23, "HRV coherence > 0.7 как индикатор", "hypothesis", "wellbeing"),
        (24, "План на неделю: recurring'и + ежедневный check-in", "synthesis", "wellbeing"),
    ]
    nodes = [_mknode(i, t, ty, tp) for (i, t, ty, tp) in nodes_spec]
    nodes[0]["confidence"] = 0.8
    nodes[4]["confidence"] = 0.85
    nodes[6]["confidence"] = 0.75
    nodes[11]["confidence"] = 0.4
    nodes[12]["confidence"] = 0.75
    nodes[21]["confidence"] = 0.8
    nodes[24]["confidence"] = 0.7

    edges = [
        (0, 1), (0, 2), (0, 3), (0, 13), (0, 21),
        (1, 4),
        (3, 5), (3, 20),
        (2, 6), (2, 7), (7, 8), (7, 9),
        (10, 11), (11, 12),
        (13, 14),
        (15, 16),
        (17, 18),
        (21, 1), (21, 7), (21, 15),
        (22, 23),
        (0, 9), (0, 15), (0, 19),
        (0, 24),
    ]
    _write_graph(nodes, edges, "Wellbeing", mode="free")

    # Goals + recurring
    ts = _now()
    events = [
        {"action": "create", "id": _mkid(),
         "text": "Снизить хронический стресс", "mode": "horizon",
         "priority": 1, "deadline": None, "category": "health",
         "ts": ts - 10 * 86400},
        {"action": "create", "id": _mkid(),
         "text": "10 000 шагов в день", "mode": "free",
         "priority": 2, "deadline": None, "category": "health",
         "ts": ts - 8 * 86400},
        {"action": "create", "id": _mkid(),
         "text": "Снизить кофе до 2 чашек", "mode": "free",
         "priority": 3, "deadline": None, "category": "health",
         "ts": ts - 5 * 86400},
        # Recurring
        {"action": "create", "id": _mkid(),
         "text": "Прогулка 30 минут", "kind": "recurring", "mode": "rhythm",
         "schedule": {"times_per_week": 3}, "category": "health",
         "ts": ts - 14 * 86400},
        {"action": "create", "id": _mkid(),
         "text": "Стакан воды", "kind": "recurring", "mode": "rhythm",
         "schedule": {"times_per_day": 5}, "category": "health",
         "ts": ts - 14 * 86400},
        {"action": "create", "id": _mkid(),
         "text": "Медитация утром", "kind": "recurring", "mode": "rhythm",
         "schedule": {"times_per_day": 1}, "category": "health",
         "ts": ts - 14 * 86400},
    ]
    _append_jsonl(GOALS_FILE, events)

    # Activity 7 дней
    act = []
    for d in range(7, 0, -1):
        day_start = ts - d * 86400
        aid = _mkid()
        act.append({"action": "start", "id": aid, "name": "Завтрак",
                    "category": "food", "ts": day_start + 8 * 3600})
        act.append({"action": "stop", "id": aid, "reason": "manual",
                    "ts": day_start + 8 * 3600 + 20 * 60})
        aid = _mkid()
        act.append({"action": "start", "id": aid, "name": "Deep work блок",
                    "category": "work", "ts": day_start + 9.5 * 3600})
        act.append({"action": "stop", "id": aid, "reason": "manual",
                    "ts": day_start + 13 * 3600})
        aid = _mkid()
        act.append({"action": "start", "id": aid, "name": "Обед",
                    "category": "food", "ts": day_start + 13 * 3600})
        act.append({"action": "stop", "id": aid, "reason": "manual",
                    "ts": day_start + 13 * 3600 + 40 * 60})
        if d in (6, 4, 1):
            aid = _mkid()
            act.append({"action": "start", "id": aid, "name": "Прогулка 30 минут",
                        "category": "health", "ts": day_start + 15 * 3600})
            act.append({"action": "stop", "id": aid, "reason": "manual",
                        "ts": day_start + 15 * 3600 + 30 * 60})
        aid = _mkid()
        act.append({"action": "start", "id": aid, "name": "Evening review",
                    "category": "work", "ts": day_start + 17 * 3600})
        act.append({"action": "stop", "id": aid, "reason": "manual",
                    "ts": day_start + 18.5 * 3600})
    _append_jsonl(ACTIVITY_FILE, act)

    # Checkins 7 дней
    checkins_data = [
        (6, 70, 75, 30, 0, 1, "хорошо отдохнул в выходные"),
        (5, 65, 70, 40, 1, 0, "затянуло в работу, забыл про перерыв"),
        (4, 50, 50, 60, 0, -1, "плохо спал, кофе после 18"),
        (3, 60, 65, 45, 0, 0, "средний день"),
        (2, 40, 45, 75, 0, -2, "сложный созвон, остаток дня тяжело"),
        (1, 75, 70, 30, 1, 1, "прогулка помогла, вернулся в фокус"),
        (0, 65, 60, 40, 0, 0, None),
    ]
    checkins = []
    for (days_ago, e, f, s, exp, real, note) in checkins_data:
        entry = {
            "action": "checkin",
            "energy": float(e), "focus": float(f), "stress": float(s),
            "expected": exp, "reality": real,
            "note": note or "",
            "ts": ts - days_ago * 86400 + 20 * 3600,
        }
        checkins.append(entry)
    _append_jsonl(CHECKINS_FILE, checkins)


# ── Public API ───────────────────────────────────────────────────────────

def seed_demo() -> dict:
    """Наполнить `graphs/main/` + goals + activity + checkins."""
    _GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    _seed_content()
    log.info("[demo.seed] populated graphs/main/")
    return {"graph": "main", "ts": _iso_now()}


def reset_and_seed() -> dict:
    """Полный цикл: wipe → seed. Атомарная операция для UI-кнопки."""
    wiped = wipe_all_runtime()
    seeded = seed_demo()
    return {"wiped": wiped, "seeded": seeded}


def should_auto_seed() -> bool:
    """True если runtime пустой (первый запуск)."""
    if (_GRAPH_DIR / "graph.json").exists():
        return False
    return True
