"""Activity log — ручной трекер «что я сейчас делаю».

Цикл как в прототипе Time Player: **Начать → (ввод названия) → активная
задача → «Следующая» (переключение) → «Стоп»**. Каждое переключение —
явный контекст-свитч, создаёт новый event. Дневная активность восстанавливается
replay'ем событий.

Зачем это Baddle
----------------
Baddle знает скаляры (HRV, DA/S/NE, daily_remaining), но не знает
*что юзер делал* последние часы. Activity-лог — ground truth слой:
- реальный energy accounting (совещания/код/пауза имеют разную цену);
- substrate для pattern detector («3 четверга пропустил завтрак → crash»);
- sleep duration (sleep/wake как частные случаи activity);
- morning briefing с субстанцией («вчера: 4h кода, 2 митинга, 30 мин пауз»).

Формат `activity.jsonl` (append-only, как state_graph/goals):

    {"action":"start",  "id","ts","name","category","node_index"}
    {"action":"stop",   "id","ts","reason"}                   # reason="manual"|"switch"|"auto"
    {"action":"update", "id","fields":{name,category,...}}

Активная задача — последний `start` без последующего `stop` для того же id.
"""
import json
import logging
import time
import uuid
from typing import Optional

log = logging.getLogger(__name__)

from .paths import ACTIVITY_FILE as _ACTIVITY_FILE


# ── Keyword → category mapping (быстрый фолбэк для шаблонов) ──────────────
# Переиспользует ту же 5-категорийную модель, что и user_profile.

_CATEGORY_HINTS = {
    "food":     ("обед", "ужин", "завтрак", "перекус", "еда", "кушать",
                 "meal", "lunch", "dinner", "breakfast"),
    "work":     ("код", "разработ", "программ", "ревью", "review",
                 "совещан", "митинг", "meeting", "встреча",
                 "задач", "таск", "проект", "пр ", "pr ",
                 "ответ", "вопрос", "call", "созвон", "работ"),
    "health":   ("пауза", "отдых", "перерыв", "бег", "зарядк", "тренировк",
                 "спорт", "сон", "прогулк", "медитац",
                 "pause", "rest", "break", "sleep", "walk", "run", "gym"),
    "social":   ("друг", "семь", "родител", "дети", "подруг",
                 "friend", "family"),
    "learning": ("учит", "курс", "книг", "статью", "изучить",
                 "study", "book", "course", "learn"),
}


def detect_category(name: str) -> Optional[str]:
    """Keyword-first autodetect. Возвращает None если ничего не подошло."""
    if not name:
        return None
    low = name.lower()
    for cat, kws in _CATEGORY_HINTS.items():
        for kw in kws:
            if kw in low:
                return cat
    return None


# ── Storage primitives ────────────────────────────────────────────────────

def _append(entry: dict):
    entry.setdefault("ts", time.time())
    try:
        with _ACTIVITY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning(f"[activity_log] append failed: {e}")


def _read_all() -> list[dict]:
    if not _ACTIVITY_FILE.exists():
        return []
    out: list[dict] = []
    try:
        with _ACTIVITY_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        log.warning(f"[activity_log] read failed: {e}")
    return out


# ── Replay: восстановление состояния задач ───────────────────────────────

def _replay() -> dict[str, dict]:
    """Построить {activity_id: {...}} по event log.

    Поля: id, name, category, node_index, started_at, stopped_at,
          duration_s, status ("active" | "done").
    """
    state: dict[str, dict] = {}
    for e in _read_all():
        aid = e.get("id")
        if not aid:
            continue
        act = e.get("action")
        if act == "delete":
            state.pop(aid, None)
            continue
        if act == "start":
            state[aid] = {
                "id": aid,
                "name": e.get("name", ""),
                "category": e.get("category"),
                "node_index": e.get("node_index"),
                "started_at": e.get("ts"),
                "stopped_at": None,
                "duration_s": None,
                "status": "active",
                "stop_reason": None,
                # Phase C: surprise tracking для cognitive_load complexity
                "surprise_at_start": float(e.get("surprise_at_start") or 0.0),
                "surprise_at_stop": None,
                "surprise_delta": None,
            }
        elif aid in state:
            rec = state[aid]
            if act == "stop":
                rec["status"] = "done"
                rec["stopped_at"] = e.get("ts")
                rec["stop_reason"] = e.get("reason") or "manual"
                if rec.get("started_at"):
                    rec["duration_s"] = float(rec["stopped_at"]) - float(rec["started_at"])
                # Phase C: surprise_delta = stop - start
                if "surprise_at_stop" in e:
                    try:
                        stop_v = float(e.get("surprise_at_stop") or 0.0)
                        rec["surprise_at_stop"] = stop_v
                        rec["surprise_delta"] = round(
                            stop_v - rec.get("surprise_at_start", 0.0), 4)
                    except (TypeError, ValueError):
                        pass
            elif act == "update":
                for k, v in (e.get("fields") or {}).items():
                    if k in {"name", "category", "node_index"}:
                        rec[k] = v
                    elif k in {"started_at", "stopped_at"}:
                        try:
                            rec[k] = float(v)
                        except (TypeError, ValueError):
                            continue
                # Пересчёт duration при изменении времени
                if rec.get("started_at") and rec.get("stopped_at"):
                    rec["duration_s"] = float(rec["stopped_at"]) - float(rec["started_at"])
    return state


def get_active() -> Optional[dict]:
    """Текущая активная задача (status='active'). None если нет."""
    for rec in _replay().values():
        if rec.get("status") == "active":
            return rec
    return None


# ── Public mutators ──────────────────────────────────────────────────────

def start_activity(name: str,
                   category: Optional[str] = None,
                   node_index: Optional[int] = None,
                   _reason: str = "manual") -> str:
    """Начать новую задачу. Если уже есть активная — автоматически стопает её
    со `stop_reason='switch'` (поведение кнопки «Следующая»).

    Side effect (Phase C): фиксирует `surprise_at_start` — модуль вектора
    UserState.surprise на момент запуска. Используется для `complexity_sum`
    в cognitive_load формуле (см. docs/capacity-design.md §Дневная метрика).

    Stop'ает auto-switch предыдущей задачи также с `surprise_at_stop` —
    чтобы посчитать `surprise_delta` (как изменилась модель за время
    задачи).

    Возвращает id новой активной задачи.
    """
    name = (name or "").strip()[:200]
    if not name:
        raise ValueError("name_required")

    # Snapshot surprise (РГК.project("user_state")["imbalance"]) — complexity tracking
    surprise_at_start = 0.0
    try:
        from .substrate.rgk import get_global_rgk
        surprise_at_start = float(get_global_rgk().project("user_state")["imbalance"])
    except Exception:
        pass

    # Автостоп текущей со snapshot surprise_delta
    cur = get_active()
    if cur:
        stop_event = {"action": "stop", "id": cur["id"], "reason": "switch",
                       "surprise_at_stop": surprise_at_start}
        _append(stop_event)

    cat = category or detect_category(name)
    aid = uuid.uuid4().hex[:12]
    _append({
        "action": "start",
        "id": aid,
        "name": name,
        "category": cat,
        "node_index": node_index,
        "surprise_at_start": round(surprise_at_start, 4),
    })
    return aid


# ── Связка activity ↔ recurring goals ────────────────────────────────────
# Когда юзер пишет «Начать: Обед» в taskplayer — логично автоматически
# засчитать это как instance recurring-цели «покушать 3 раза в день».
# Матчинг через intent_router'овский классификатор (fact → instance).
# Вызывается из /activity/start после записи события.

def try_match_recurring_instance(activity_name: str,
                                  activity_category: Optional[str] = None,
                                  lang: str = "ru") -> Optional[dict]:
    """Если activity-имя похоже на одну из recurring целей, записать
    instance и вернуть {goal_id, goal_text, progress}. Иначе None.

    Использует `intent_router._classify_subtype_fact` который через LLM
    выбирает подходящую recurring цель из списка (или говорит что не
    подходит). Быстрый — max_tokens=10.
    """
    try:
        from .recurring import list_recurring, get_progress
        from .goals_store import record_instance
        from .intent_router import _classify_subtype_fact
    except Exception as e:
        log.debug(f"[activity→recurring] import failed: {e}")
        return None

    recs = list_recurring(active_only=True)
    # Сужаем по категории если известна (меньше LLM токенов + меньше FP)
    if activity_category:
        recs = [r for r in recs if r.get("category") == activity_category] or recs
    if not recs:
        return None

    try:
        sub, conf, gid = _classify_subtype_fact(
            activity_name, lang=lang, recurring_list=recs,
        )
    except Exception as e:
        log.debug(f"[activity→recurring] match failed: {e}")
        return None

    if sub != "instance" or not gid or conf < 0.7:
        return None

    try:
        record_instance(gid, note=f"activity: {activity_name}")
        progress = get_progress(gid)
        goal_text = next((r["text"] for r in recs if r["id"] == gid), "")
        log.info(f"[activity→recurring] auto-matched '{activity_name}' → "
                 f"recurring '{goal_text[:40]}' (conf={conf:.2f})")
        return {
            "goal_id": gid,
            "goal_text": goal_text,
            "progress": progress,
        }
    except Exception as e:
        log.warning(f"[activity→recurring] record failed: {e}")
        return None


def try_detect_constraint_violation(activity_name: str,
                                     lang: str = "ru") -> list[dict]:
    """Activity → constraint: если имя активности нарушает один из активных
    constraint'ов юзера («Пиво в баре» при constraint «не пью»), пишем
    violation через LLM-скан.

    Возвращает список записанных violations — симметрично с
    `scan_message_for_violations` для /assist.
    """
    try:
        from .recurring import scan_message_for_violations
    except Exception:
        return []
    try:
        return scan_message_for_violations(activity_name, lang=lang)
    except Exception as e:
        log.debug(f"[activity→constraint] scan failed: {e}")
        return []


def stop_activity(reason: str = "manual") -> Optional[dict]:
    """Остановить текущую активную задачу. Возвращает завершённую запись или None.

    Side effect (Phase C): фиксирует `surprise_at_stop` для расчёта
    `surprise_delta = stop - start` в `_replay()`. Sign:
        delta > 0 — surprise вырос за время задачи (перемоделирование)
        delta < 0 — surprise упал (модель стабилизировалась)
    """
    cur = get_active()
    if not cur:
        return None
    surprise_at_stop = 0.0
    try:
        from .substrate.rgk import get_global_rgk
        surprise_at_stop = float(get_global_rgk().project("user_state")["imbalance"])
    except Exception:
        pass
    _append({
        "action": "stop", "id": cur["id"],
        "reason": reason or "manual",
        "surprise_at_stop": round(surprise_at_stop, 4),
    })
    # Перечитаем — получим вычисленный duration_s + surprise_delta
    return _replay().get(cur["id"])


def delete_activity(activity_id: str):
    """Мягкое удаление через event `delete` — replay пропускает запись.

    Оригинальные события остаются в логе (для аудита), но текущий state
    их не видит.
    """
    _append({"action": "delete", "id": activity_id})


def update_activity(activity_id: str, fields: dict):
    """Обновить name / category / node_index / started_at / stopped_at.

    started_at/stopped_at — опциональные корректировки времени («я забыл
    выключить Код когда пошёл на митинг → подрежь до 15:30»). Validation:
    stopped_at > started_at и duration <= 24ч.
    """
    allowed = {"name", "category", "node_index", "started_at", "stopped_at"}
    clean = {k: v for k, v in (fields or {}).items() if k in allowed}
    if not clean:
        return
    # Validation для time fields
    if "started_at" in clean or "stopped_at" in clean:
        cur = _replay().get(activity_id)
        if cur is None:
            raise ValueError("activity_not_found")
        new_start = float(clean.get("started_at", cur.get("started_at") or 0))
        new_stop = clean.get("stopped_at")
        if new_stop is not None:
            new_stop = float(new_stop)
            if new_stop <= new_start:
                raise ValueError("stopped_at must be > started_at")
            if (new_stop - new_start) > 24 * 3600:
                raise ValueError("duration > 24h")
    _append({"action": "update", "id": activity_id, "fields": clean})


# ── Queries ──────────────────────────────────────────────────────────────

def list_activities(since_ts: Optional[float] = None,
                    until_ts: Optional[float] = None,
                    limit: int = 200) -> list[dict]:
    """Вернуть завершённые + активные задачи, новейшие сверху."""
    items = list(_replay().values())
    items.sort(key=lambda a: a.get("started_at") or 0, reverse=True)
    if since_ts is not None:
        items = [a for a in items if (a.get("started_at") or 0) >= since_ts]
    if until_ts is not None:
        items = [a for a in items if (a.get("started_at") or 0) <= until_ts]
    return items[:limit]


def _day_bounds(ts: Optional[float] = None) -> tuple[float, float]:
    """Границы локального дня для timestamp ts (по умолчанию — сегодня)."""
    import datetime as _dt
    t = ts if ts is not None else time.time()
    d = _dt.datetime.fromtimestamp(t)
    start = _dt.datetime(d.year, d.month, d.day).timestamp()
    end = start + 86400.0
    return start, end


def day_summary(ts: Optional[float] = None) -> dict:
    """Агрегат за локальный день: total_tracked_s, by_category, by_name, switches.

    Активная задача включается — duration = now - started_at.
    """
    start, end = _day_bounds(ts)
    now = time.time()

    # Только задачи которые пересеклись с днём
    acts = []
    for a in _replay().values():
        s = a.get("started_at") or 0
        e = a.get("stopped_at") or now
        if e < start or s > end:
            continue
        # клип по границам дня
        ss = max(s, start)
        ee = min(e, end)
        dur = max(0.0, ee - ss)
        acts.append({**a, "day_duration_s": dur})

    total = sum(a["day_duration_s"] for a in acts)
    by_cat: dict[str, float] = {}
    by_name: dict[str, float] = {}
    for a in acts:
        c = a.get("category") or "uncategorized"
        by_cat[c] = by_cat.get(c, 0.0) + a["day_duration_s"]
        n = a.get("name") or "(no name)"
        by_name[n] = by_name.get(n, 0.0) + a["day_duration_s"]

    # Сколько раз юзер переключался (по switch-stop'ам, а не по count задач)
    switches = 0
    day_entries = [e for e in _read_all()
                   if e.get("ts", 0) >= start and e.get("ts", 0) < end
                   and e.get("action") == "stop" and e.get("reason") == "switch"]
    switches = len(day_entries)

    # Топ-3 по длительности
    top = sorted(by_name.items(), key=lambda kv: kv[1], reverse=True)[:3]

    return {
        "day_start": start,
        "day_end": end,
        "total_tracked_s": round(total),
        "total_tracked_h": round(total / 3600.0, 2),
        "activity_count": len(acts),
        "switches": switches,
        "by_category_s": {k: round(v) for k, v in by_cat.items()},
        "by_category_h": {k: round(v / 3600.0, 2) for k, v in by_cat.items()},
        "top_names": [(n, round(s / 60.0)) for n, s in top],  # minutes
    }


# ── Energy cost per category (points/min) ─────────────────────────────────
# Положительный = тратит daily_remaining. Отрицательный = восстанавливает
# (лёгкий bump обратно). Meeting > simple work (больше переключений + социальная
# нагрузка), pause/food → recovery. Калибровка под ~100/day budget: 7 часов
# активной работы ≈ 420 мин × 0.25 = 105 points → выгорание к вечеру.

CATEGORY_ENERGY_COST_PER_MIN = {
    "work":          0.25,
    "social":        0.20,
    "learning":      0.22,
    "food":          -0.05,   # лёгкое восстановление во время еды
    "health":        -0.15,   # пауза, тренировка = recovery
    "uncategorized": 0.15,
}

_NAME_COST_OVERRIDES = (
    # (keyword in name → rate_per_min). Overrides categoryrate if matched.
    (("совещан", "митинг", "meeting", "созвон"),       0.40),
    (("пауза", "перерыв", "pause", "break"),          -0.25),
    (("сон", "sleep"),                                 -0.50),
    (("тренировк", "зарядк", "бег", "gym", "run"),    -0.10),
)


def cost_per_min(name: str, category: Optional[str]) -> float:
    """Сколько points/min списывается/восстанавливается за activity.

    Name-override'ы (совещание / пауза / сон / тренировка) важнее category
    потому что в пределах одной category разная нагрузка.
    """
    low = (name or "").lower()
    for kws, rate in _NAME_COST_OVERRIDES:
        for kw in kws:
            if kw in low:
                return rate
    return CATEGORY_ENERGY_COST_PER_MIN.get(category or "uncategorized",
                                            CATEGORY_ENERGY_COST_PER_MIN["uncategorized"])


# ── Sleep estimation из activity log ──────────────────────────────────────

_SLEEP_KEYWORDS = ("сон", "спал", "спала", "sleep", "slept")


def estimate_last_sleep_hours(now_ts: Optional[float] = None) -> Optional[dict]:
    """Оценить продолжительность последнего сна.

    Две эвристики в порядке приоритета:
      1. **Явная задача «Сон»** (или keyword match в name) завершённая
         в последние 24 часа — берём её duration.
      2. **Idle-gap** между последним `stop` до полуночи и первым `start`
         после полуночи (или до `now`). Валидный диапазон: 4-12 часов.

    Возвращает {hours, source: "explicit"|"idle_gap", started_at, ended_at}
    или None если не удалось оценить.
    """
    now = now_ts if now_ts is not None else time.time()
    acts = list(_replay().values())
    if not acts:
        return None

    # 1. Явная задача Сон
    cutoff = now - 36 * 3600  # 36h чтобы поймать даже поздний вчерашний Сон
    for a in sorted(acts, key=lambda x: x.get("stopped_at") or 0, reverse=True):
        name = (a.get("name") or "").lower()
        if any(kw in name for kw in _SLEEP_KEYWORDS):
            stopped = a.get("stopped_at")
            dur = a.get("duration_s")
            if stopped and dur and stopped >= cutoff and 4 * 3600 <= dur <= 14 * 3600:
                return {
                    "hours": round(dur / 3600.0, 1),
                    "source": "explicit",
                    "started_at": a.get("started_at"),
                    "ended_at": stopped,
                }
            # Если это последняя «Сон»-задача — дальше не ищем
            break

    # 2. Idle-gap
    # Берём все события sorted by ts, ищем самый большой gap end→start в
    # последние 24h, валидный по диапазону длительности.
    events: list[tuple[float, str]] = []
    for a in acts:
        if a.get("started_at"):
            events.append((float(a["started_at"]), "start"))
        if a.get("stopped_at"):
            events.append((float(a["stopped_at"]), "stop"))
    events.sort()
    if len(events) < 2:
        return None

    best_gap = None
    for i in range(len(events) - 1):
        t1, k1 = events[i]
        t2, k2 = events[i + 1]
        # Интересует только gap ПОСЛЕ stop и ДО start (юзер ничем не занят)
        if k1 != "stop" or k2 != "start":
            continue
        gap = t2 - t1
        if not (4 * 3600 <= gap <= 14 * 3600):
            continue
        if t2 < now - 20 * 3600:  # ушёл в историю > 20ч назад — не интересен
            continue
        if best_gap is None or gap > best_gap["duration_s"]:
            best_gap = {"duration_s": gap, "ended_at": t1, "started_at": t2}

    if best_gap:
        return {
            "hours": round(best_gap["duration_s"] / 3600.0, 1),
            "source": "idle_gap",
            "started_at": best_gap["ended_at"],  # когда ушёл от дел = лёг спать
            "ended_at": best_gap["started_at"],  # когда вернулся = проснулся
        }
    return None


# ── Templates: default quick-tasks ────────────────────────────────────────

DEFAULT_TEMPLATES = [
    {"name": "Код",         "category": "work",   "emoji": "💻"},
    {"name": "Совещание",   "category": "work",   "emoji": "👥"},
    {"name": "Ответ",       "category": "work",   "emoji": "✉"},
    {"name": "Обед",        "category": "food",   "emoji": "🍽"},
    {"name": "Пауза",       "category": "health", "emoji": "🧘"},
]


def get_templates() -> list[dict]:
    """Шаблоны quick-tasks. В будущем — из profile.context.activity_templates."""
    try:
        from .user_profile import load_profile
        p = load_profile()
        custom = p.get("context", {}).get("activity_templates")
        if isinstance(custom, list) and custom:
            return custom
    except Exception:
        pass
    return list(DEFAULT_TEMPLATES)
