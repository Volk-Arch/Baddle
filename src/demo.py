"""Demo seeder — создаёт 2 workspace'а с готовым наполнением.

Зачем: пустой Baddle не демонстрирует ничего. Morning briefing без данных =
plain text, pump без графа = ничего, dashboard без checkins = нули. DEMO
заполняет оба workspace'а содержательными нодами/целями/активностями,
чтобы система сразу работала как задумано.

Два workspace'а:
  work-demo       — «релиз MVP»: goals, hypothesis, evidence, synthesis;
                    recurring (daily standup, code review); 3 дня activity.
  personal-demo   — «wellbeing»: сон/прогулки/кофе/вода; recurring
                    (прогулка 3/нед, вода 5/день, медитация 1/день);
                    7 дней activity + 7 дней checkins.

Использование:
  from src.demo import seed_demo, wipe_all_runtime
  wipe_all_runtime()   # удалить существующие данные
  seed_demo()          # создать оба demo-workspace'а

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
    DATA_DIR, GOALS_FILE, ACTIVITY_FILE, CHECKINS_FILE, PATTERNS_FILE,
    PLANS_FILE, CHAT_HISTORY_FILE, USER_STATE_FILE, USER_PROFILE_FILE,
    STATE_GRAPH_FILE, STATE_EMBEDDINGS_FILE, STATE_GRAPH_ARCHIVE,
)

log = logging.getLogger(__name__)

_ROOT = Path(__file__).parent.parent
_GRAPHS_DIR = _ROOT / "graphs"
_WORKSPACES_DIR = _ROOT / "workspaces"
_WS_INDEX = _WORKSPACES_DIR / "index.json"


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
        for ws in _GRAPHS_DIR.iterdir():
            if ws.is_dir():
                try:
                    shutil.rmtree(ws)
                    removed.append(f"graphs/{ws.name}")
                except OSError as e:
                    log.warning(f"[demo.wipe] graphs/{ws.name}: {e}")
    if _WS_INDEX.exists():
        try:
            _WS_INDEX.unlink()
            removed.append("workspaces/index.json")
        except OSError as e:
            log.warning(f"[demo.wipe] index.json: {e}")
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


def _write_graph(ws_id: str, nodes: list, directed_edges: list,
                  topic: str, mode: str = "free"):
    """Записать graph.json для workspace'а."""
    ws_dir = _GRAPHS_DIR / ws_id
    ws_dir.mkdir(parents=True, exist_ok=True)
    graph = {
        "nodes": nodes,
        "edges": {
            "manual_links": [list(p) for p in directed_edges],  # для undirected-проекций
            "manual_unlinks": [],
            "directed": [list(p) for p in directed_edges],
        },
        "meta": {"topic": topic, "hub_nodes": [], "mode": mode},
        "embeddings": [],
        "tp_overrides": {},
    }
    (ws_dir / "graph.json").write_text(
        json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _append_jsonl(path: Path, events: list[dict]):
    """Добавить events в jsonl (создаст файл если нужно)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def _register_workspace(ws_id: str, title: str, tags: list[str]):
    """Добавить запись в workspaces/index.json."""
    _WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
    if _WS_INDEX.exists():
        idx = json.loads(_WS_INDEX.read_text(encoding="utf-8"))
    else:
        idx = {"active_id": ws_id, "workspaces": {}, "cross_edges": []}
    now = _iso_now()
    info = {
        "id": ws_id,
        "title": title,
        "tags": tags,
        "created": now,
        "last_active": now,
    }
    idx.setdefault("workspaces", {})[ws_id] = info
    # meta.json per workspace
    ws_dir = _GRAPHS_DIR / ws_id
    ws_dir.mkdir(parents=True, exist_ok=True)
    (ws_dir / "meta.json").write_text(
        json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return idx


def _set_active(ws_id: str):
    """Поставить активный workspace в index.json."""
    if not _WS_INDEX.exists():
        return
    idx = json.loads(_WS_INDEX.read_text(encoding="utf-8"))
    idx["active_id"] = ws_id
    _WS_INDEX.write_text(json.dumps(idx, indent=2, ensure_ascii=False),
                          encoding="utf-8")


# ── work-demo content ────────────────────────────────────────────────────

def _seed_work_demo():
    """Проект релиза: ~25 нод, 4 goal, 2 recurring, 3 дня activity."""
    ws = "work-demo"
    idx = _register_workspace(ws, "Work: release v1", ["demo", "work"])
    _WS_INDEX.write_text(json.dumps(idx, indent=2, ensure_ascii=False),
                          encoding="utf-8")

    # Граф — связная дискуссия про релиз
    nodes_spec = [
        # id, text, type, topic
        (0, "Зарелизить v1 до пятницы", "goal", "release"),
        (1, "Главная фича: AI-чат с памятью", "hypothesis", "release"),
        (2, "Главная фича: визуальный граф мышления", "hypothesis", "release"),
        (3, "Юзеры сразу понимают чат — low-friction onboarding", "evidence", "release"),
        (4, "Чат без контекста = ещё один WhatsApp, не unique", "evidence", "release"),
        (5, "Граф выделяет продукт среди AI-ассистентов", "evidence", "release"),
        (6, "Нужен onboarding — первый экран пустой пугает", "hypothesis", "release"),
        (7, "Seed-demo решает проблему пустого экрана", "hypothesis", "release"),
        (8, "Починить LM Studio интеграцию", "goal", "llm"),
        (9, "qwen3 игнорит /no_think — сливаем токены в reasoning", "hypothesis", "llm"),
        (10, "max_tokens=120 обрезал TEXT: строку, карточки пустые", "evidence", "llm"),
        (11, "Увеличили до 400 — парсер не ломается", "fact", "llm"),
        (12, "Написать статью на Хабр с живым примером", "goal", "habr"),
        (13, "Начать с конкретного: pump нашёл связь процесс↔кабачки", "hypothesis", "habr"),
        (14, "Объяснить архитектуру через distinct() как ядро", "hypothesis", "habr"),
        (15, "Distinct — редкое решение, большинство строят на generate", "evidence", "habr"),
        (16, "Добавить HRV через Polar H10 (real sensor)", "hypothesis", "future"),
        (17, "Bluetooth теряется — нужен fallback на симулятор", "evidence", "future"),
        (18, "MVP без Polar — симулятор достаточен для демо", "synthesis", "release"),
        (19, "Сколько self-test перед релизом?", "question", "release"),
        (20, "1 неделя ежедневного использования достаточно", "hypothesis", "release"),
        (21, "UI split + dark-theme модалки готовы (сегодня)", "fact", "release"),
        (22, "DEMO seed готов (сегодня же)", "fact", "release"),
        (23, "Остался smoke-тест LM + 3-минутное демо-видео", "synthesis", "release"),
        (24, "Релиз-готовность: 85%, осталась полировка", "synthesis", "release"),
    ]
    nodes = [_mknode(i, t, ty, tp) for (i, t, ty, tp) in nodes_spec]
    # Confidence tweaks — сделать граф живым
    nodes[0]["confidence"] = 0.8  # главная цель почти уверена
    nodes[4]["confidence"] = 0.3  # слабый argument against
    nodes[5]["confidence"] = 0.75
    nodes[11]["confidence"] = 0.9  # fact
    nodes[21]["confidence"] = 0.95
    nodes[22]["confidence"] = 0.95
    nodes[24]["confidence"] = 0.7

    edges = [
        # goal#0 → hypotheses
        (0, 1), (0, 2), (0, 6), (0, 7), (0, 18),
        # #1 pro/con
        (1, 3), (1, 4),
        # #2 evidence
        (2, 5),
        # #6 solved by #7
        (6, 7),
        # #8 LLM goal
        (8, 9), (8, 10), (8, 11),
        (9, 10), (10, 11),
        # #12 Habr
        (12, 13), (12, 14),
        (14, 15),
        # #16 future
        (16, 17),
        # synthesis связи
        (18, 16), (18, 17),
        (19, 20),
        # release progress
        (0, 21), (0, 22), (0, 23), (0, 24),
        (23, 24),
    ]
    _write_graph(ws, nodes, edges, "Release v1", mode="free")

    # Goals + recurring
    ts = _now()
    events = [
        {"action": "create", "id": _mkid(), "workspace": ws,
         "text": "Зарелизить v1 до пятницы", "mode": "horizon",
         "priority": 1, "deadline": None, "category": "work",
         "ts": ts - 5 * 86400},
        {"action": "create", "id": _mkid(), "workspace": ws,
         "text": "Починить LM Studio интеграцию", "mode": "free",
         "priority": 2, "deadline": None, "category": "work",
         "ts": ts - 3 * 86400},
        {"action": "create", "id": _mkid(), "workspace": ws,
         "text": "Написать статью на Хабр", "mode": "free",
         "priority": 3, "deadline": None, "category": "work",
         "ts": ts - 2 * 86400},
        # Recurring
        {"action": "create", "id": _mkid(), "workspace": ws,
         "text": "Daily standup", "kind": "recurring", "mode": "rhythm",
         "schedule": {"times_per_day": 1}, "category": "work",
         "ts": ts - 7 * 86400},
        {"action": "create", "id": _mkid(), "workspace": ws,
         "text": "Code review сессия", "kind": "recurring", "mode": "rhythm",
         "schedule": {"times_per_week": 3}, "category": "work",
         "ts": ts - 7 * 86400},
    ]
    _append_jsonl(GOALS_FILE, events)

    # Activity 3 дня
    act = []
    for d in range(3, 0, -1):
        day_start = ts - d * 86400
        # 10:00 — stand-up (короткий)
        aid = _mkid()
        act.append({"action": "start", "id": aid, "name": "Daily standup",
                    "category": "work", "workspace": ws, "ts": day_start + 10 * 3600})
        act.append({"action": "stop", "id": aid, "reason": "manual",
                    "ts": day_start + 10 * 3600 + 15 * 60})
        # 10:30 — coding
        aid = _mkid()
        act.append({"action": "start", "id": aid, "name": "UI split implementation",
                    "category": "work", "workspace": ws, "ts": day_start + 10.5 * 3600})
        act.append({"action": "stop", "id": aid, "reason": "switch",
                    "ts": day_start + 13 * 3600})
        # 14:00 — code review
        if d % 2 == 0:
            aid = _mkid()
            act.append({"action": "start", "id": aid, "name": "Code review сессия",
                        "category": "work", "workspace": ws, "ts": day_start + 14 * 3600})
            act.append({"action": "stop", "id": aid, "reason": "manual",
                        "ts": day_start + 14 * 3600 + 45 * 60})
        # 16:00 — debug
        aid = _mkid()
        act.append({"action": "start", "id": aid, "name": "Debug LM integration",
                    "category": "work", "workspace": ws, "ts": day_start + 16 * 3600})
        act.append({"action": "stop", "id": aid, "reason": "manual",
                    "ts": day_start + 18 * 3600})
    _append_jsonl(ACTIVITY_FILE, act)


# ── personal-demo content ────────────────────────────────────────────────

def _seed_personal_demo():
    """Wellbeing: сон/прогулки/кофе/вода, recurring, 7 дней истории."""
    ws = "personal-demo"
    idx = _register_workspace(ws, "Personal: wellbeing", ["demo", "personal"])
    _WS_INDEX.write_text(json.dumps(idx, indent=2, ensure_ascii=False),
                          encoding="utf-8")

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
        # goal#0 → hypotheses
        (0, 1), (0, 2), (0, 3), (0, 13), (0, 21),
        # #1 evidence
        (1, 4),
        # #3 evidence
        (3, 5), (3, 20),
        # #2 → #7 → #9
        (2, 6), (2, 7), (7, 8), (7, 9),
        # #10 dilemma
        (10, 11), (11, 12),
        # #13 meditation
        (13, 14),
        # #15 water
        (15, 16),
        # food
        (17, 18),
        # synthesis
        (21, 1), (21, 7), (21, 15),
        (22, 23),
        (0, 9), (0, 15), (0, 19),
        (0, 24),
    ]
    _write_graph(ws, nodes, edges, "Wellbeing", mode="free")

    # Goals + recurring
    ts = _now()
    events = [
        {"action": "create", "id": _mkid(), "workspace": ws,
         "text": "Снизить хронический стресс", "mode": "horizon",
         "priority": 1, "deadline": None, "category": "health",
         "ts": ts - 10 * 86400},
        {"action": "create", "id": _mkid(), "workspace": ws,
         "text": "10 000 шагов в день", "mode": "free",
         "priority": 2, "deadline": None, "category": "health",
         "ts": ts - 8 * 86400},
        {"action": "create", "id": _mkid(), "workspace": ws,
         "text": "Снизить кофе до 2 чашек", "mode": "free",
         "priority": 3, "deadline": None, "category": "health",
         "ts": ts - 5 * 86400},
        # Recurring
        {"action": "create", "id": _mkid(), "workspace": ws,
         "text": "Прогулка 30 минут", "kind": "recurring", "mode": "rhythm",
         "schedule": {"times_per_week": 3}, "category": "health",
         "ts": ts - 14 * 86400},
        {"action": "create", "id": _mkid(), "workspace": ws,
         "text": "Стакан воды", "kind": "recurring", "mode": "rhythm",
         "schedule": {"times_per_day": 5}, "category": "health",
         "ts": ts - 14 * 86400},
        {"action": "create", "id": _mkid(), "workspace": ws,
         "text": "Медитация утром", "kind": "recurring", "mode": "rhythm",
         "schedule": {"times_per_day": 1}, "category": "health",
         "ts": ts - 14 * 86400},
    ]
    _append_jsonl(GOALS_FILE, events)

    # Activity 7 дней (ниже — паттерны для Scout/pattern detector)
    act = []
    for d in range(7, 0, -1):
        day_start = ts - d * 86400
        # Завтрак 8:00
        aid = _mkid()
        act.append({"action": "start", "id": aid, "name": "Завтрак",
                    "category": "food", "workspace": ws, "ts": day_start + 8 * 3600})
        act.append({"action": "stop", "id": aid, "reason": "manual",
                    "ts": day_start + 8 * 3600 + 20 * 60})
        # Работа 9:30-13:00
        aid = _mkid()
        act.append({"action": "start", "id": aid, "name": "Deep work блок",
                    "category": "work", "workspace": ws, "ts": day_start + 9.5 * 3600})
        act.append({"action": "stop", "id": aid, "reason": "manual",
                    "ts": day_start + 13 * 3600})
        # Обед 13:00-13:40
        aid = _mkid()
        act.append({"action": "start", "id": aid, "name": "Обед",
                    "category": "food", "workspace": ws, "ts": day_start + 13 * 3600})
        act.append({"action": "stop", "id": aid, "reason": "manual",
                    "ts": day_start + 13 * 3600 + 40 * 60})
        # Прогулка — понедельник/среда/суббота (day 6, 4, 1 considering loop from 7 down)
        if d in (6, 4, 1):
            aid = _mkid()
            act.append({"action": "start", "id": aid, "name": "Прогулка 30 минут",
                        "category": "health", "workspace": ws, "ts": day_start + 15 * 3600})
            act.append({"action": "stop", "id": aid, "reason": "manual",
                        "ts": day_start + 15 * 3600 + 30 * 60})
        # Вечерняя работа
        aid = _mkid()
        act.append({"action": "start", "id": aid, "name": "Evening review",
                    "category": "work", "workspace": ws, "ts": day_start + 17 * 3600})
        act.append({"action": "stop", "id": aid, "reason": "manual",
                    "ts": day_start + 18.5 * 3600})
    _append_jsonl(ACTIVITY_FILE, act)

    # Checkins 7 дней
    checkins_data = [
        # (days_ago, energy, focus, stress, expected, reality, note)
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
            "ts": ts - days_ago * 86400 + 20 * 3600,  # 20:00 каждого дня
        }
        checkins.append(entry)
    _append_jsonl(CHECKINS_FILE, checkins)


# ── Public API ───────────────────────────────────────────────────────────

def seed_demo(active: str = "personal-demo") -> dict:
    """Создать оба demo-workspace'а + выставить active.

    Идемпотентна: если workspaces уже существуют — перетирает.
    Возвращает статистику для UI.
    """
    _GRAPHS_DIR.mkdir(parents=True, exist_ok=True)
    _WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)

    _seed_work_demo()
    _seed_personal_demo()
    _set_active(active)
    log.info(f"[demo.seed] created work-demo + personal-demo, active={active}")
    return {
        "workspaces": ["work-demo", "personal-demo"],
        "active": active,
        "ts": _iso_now(),
    }


def reset_and_seed(active: str = "personal-demo") -> dict:
    """Полный цикл: wipe → seed. Атомарная операция для UI-кнопки."""
    wiped = wipe_all_runtime()
    seeded = seed_demo(active=active)
    return {"wiped": wiped, "seeded": seeded}


def should_auto_seed() -> bool:
    """True если runtime пустой (первый запуск) — seed-on-empty путь."""
    if _WS_INDEX.exists():
        return False
    if _GRAPHS_DIR.exists() and any(_GRAPHS_DIR.iterdir()):
        return False
    return True
