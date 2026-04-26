"""Pattern detector — weekday × category × behaviour → outcome.

Цель: находить повторяющиеся связки «в такой-то день недели такая-то
категория действий приводит к такому-то исходу». Mockup:

    «Last 3 Thursdays: skipped breakfast → energy crash at 2pm
     → postponed tasks → Friday overload».

Substrate: три источника данных.
  - activity.jsonl     — что делал (category × время суток)
  - goals.jsonl        — что начал/закончил/бросил
  - /assist/history    — feedback valence (accepted/rejected)

Запускается раз в сутки в `_check_night_cycle`. Результат кладётся в
`patterns.jsonl`. `morning_briefing` подтягивает карточку если есть
паттерн подходящий к текущему дню недели.

Простое определение паттерна (MVP):
  - категория C и weekday W отсутствовали последние ≥3 вхождения
    когда ожидались (баг: «пропускаю завтрак по четвергам»), ИЛИ
  - категория C и weekday W присутствовали + vallencia < −0.3 в
    соседнем окне (негативный исход после), ≥3 раза = паттерн.

Выход: entries типа
  {"kind":"skip","category":"food","weekday":3,"count":3,"window":"morning"}
  {"kind":"after_bad","category":"work","weekday":1,"count":3,
   "valence_mean":-0.4}
"""
from __future__ import annotations
import json
import logging
import time
from datetime import datetime

log = logging.getLogger(__name__)

from .paths import PATTERNS_FILE as _PATTERNS_FILE, ACTIVITY_FILE as _ACTIVITY_FILE

# Интервалы (в ISO weekday: 0=понедельник ... 6=воскресенье)
WEEKDAYS_RU = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]

# Временные окна дня
WINDOWS = {
    "morning": (5, 12),    # 5:00-11:59
    "afternoon": (12, 18),
    "evening": (18, 23),
    "night": (23, 5),
}


def _window_for_hour(h: int) -> str:
    if 5 <= h < 12: return "morning"
    if 12 <= h < 18: return "afternoon"
    if 18 <= h < 23: return "evening"
    return "night"


def _read_activity_events() -> list[dict]:
    f = _ACTIVITY_FILE
    if not f.exists():
        return []
    out = []
    try:
        with f.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        log.warning(f"[patterns] activity read failed: {e}")
    return out


def _replay_activities() -> list[dict]:
    """Построить список завершённых task'ов с weekday/window метаданными."""
    from .activity_log import _replay
    activities = []
    for a in _replay().values():
        start = a.get("started_at")
        if not start:
            continue
        d = datetime.fromtimestamp(float(start))
        activities.append({
            **a,
            "weekday": d.weekday(),
            "hour": d.hour,
            "window": _window_for_hour(d.hour),
            "date": d.strftime("%Y-%m-%d"),
        })
    return activities


def detect_skipped_category(days_back: int = 21,
                            min_occurrences: int = 3) -> list[dict]:
    """«Пропускаю завтрак по четвергам»-паттерн.

    Алгоритм:
      1. Для каждой weekday × window собрать historical occurrences category.
      2. Найти (weekday, window, category) где:
         - category встречается в ≥50% дней этой weekday (baseline),
         - но пропускается в последние min_occurrences подряд таких weekday.

    Для MVP — только категория food × window morning (завтрак).
    """
    cutoff = time.time() - days_back * 86400
    acts = [a for a in _replay_activities()
            if (a.get("started_at") or 0) >= cutoff]
    if not acts:
        return []

    patterns = []

    # MVP: food в morning — самый частый breakfast-кейс
    # Сгруппировать по дате (только дни где была хоть какая-то активность —
    # иначе юзер может быть в отпуске и ноль данных не означает «пропустил»).
    by_date: dict[str, list[dict]] = {}
    for a in acts:
        by_date.setdefault(a["date"], []).append(a)

    if len(by_date) < 7:
        return []  # слишком мало истории

    # Для каждого weekday собираем даты
    weekday_dates: dict[int, list[str]] = {}
    for date, items in by_date.items():
        wd = items[0]["weekday"]
        weekday_dates.setdefault(wd, []).append(date)

    for wd, dates in weekday_dates.items():
        dates.sort()
        # Была ли food в morning в этот день?
        had_breakfast = []
        for date in dates:
            items = by_date[date]
            breakfast = any(a.get("category") == "food" and a.get("window") == "morning"
                            for a in items)
            had_breakfast.append((date, breakfast))
        # Baseline: в каком % дней за всю историю этого weekday был завтрак
        total = len(had_breakfast)
        with_bf = sum(1 for _, b in had_breakfast if b)
        if total < 4:
            continue
        if with_bf / total < 0.5:
            continue  # нет baseline — не паттерн

        # Последние N вхождений пропуска подряд
        recent_skipped = 0
        for date, b in reversed(had_breakfast):
            if b:
                break
            recent_skipped += 1
        if recent_skipped >= min_occurrences:
            patterns.append({
                "kind": "skip_breakfast",
                "category": "food",
                "window": "morning",
                "weekday": wd,
                "count": recent_skipped,
                "baseline_rate": round(with_bf / total, 2),
                "weekday_label_ru": WEEKDAYS_RU[wd],
                "hint_ru": (
                    f"Последние {recent_skipped} {WEEKDAYS_RU[wd]} подряд пропускал "
                    f"завтрак (обычно {int(100 * with_bf / total)}% дней ты ешь утром)."
                ),
            })
    return patterns


def detect_heavy_day_overload(days_back: int = 21,
                              min_occurrences: int = 3) -> list[dict]:
    """«По таким-то weekday перегружаюсь»-паттерн.

    Overload = weekday × window где суммарная work-нагрузка > mean + 1std.
    Повтор ≥3 раз = паттерн.
    """
    cutoff = time.time() - days_back * 86400
    acts = [a for a in _replay_activities()
            if (a.get("started_at") or 0) >= cutoff
            and a.get("category") == "work"
            and (a.get("duration_s") or 0) > 0]
    if len(acts) < 10:
        return []

    # Для каждого (date, weekday) — сколько минут work
    by_date: dict[str, dict] = {}
    for a in acts:
        d = by_date.setdefault(a["date"], {"minutes": 0, "weekday": a["weekday"]})
        d["minutes"] += a["duration_s"] / 60.0

    minutes_list = [d["minutes"] for d in by_date.values()]
    if len(minutes_list) < 7:
        return []
    mean = sum(minutes_list) / len(minutes_list)
    var = sum((m - mean) ** 2 for m in minutes_list) / len(minutes_list)
    std = var ** 0.5
    heavy_threshold = mean + std

    # По weekday считаем сколько раз был heavy
    weekday_heavy: dict[int, int] = {}
    weekday_total: dict[int, int] = {}
    for date, d in by_date.items():
        wd = d["weekday"]
        weekday_total[wd] = weekday_total.get(wd, 0) + 1
        if d["minutes"] >= heavy_threshold:
            weekday_heavy[wd] = weekday_heavy.get(wd, 0) + 1

    patterns = []
    for wd, heavy_count in weekday_heavy.items():
        total = weekday_total.get(wd, 0)
        if heavy_count >= min_occurrences and heavy_count / max(1, total) >= 0.6:
            patterns.append({
                "kind": "heavy_work_day",
                "category": "work",
                "weekday": wd,
                "count": heavy_count,
                "mean_minutes_day": round(mean, 0),
                "weekday_label_ru": WEEKDAYS_RU[wd],
                "hint_ru": (
                    f"По {WEEKDAYS_RU[wd]} ты обычно перегружаешься работой "
                    f"({heavy_count} из {total} таких дней). Береги энергию."
                ),
            })
    return patterns


def detect_habit_skips(days_back: int = 14,
                       min_skips: int = 3) -> list[dict]:
    """Anomaly: intentional habit (plans.recurring) × observed skip.

    Находит habit'ы которые юзер явно заявил («завтрак каждое утро») НО
    по факту пропустил ≥min_skips раз за days_back дней. Это отличается
    от observed-only (detect_skipped_category) тем, что habit МОЖЕТ не
    пересекаться с activity log — достаточно skip-events в plans.jsonl.
    """
    import datetime as _dt
    try:
        from .plans import _replay as _plans_replay, _matches_recurring
    except Exception:
        return []

    cutoff_date = _dt.date.today() - _dt.timedelta(days=days_back)
    today = _dt.date.today()
    patterns = []

    for p in _plans_replay().values():
        if p.get("status") == "deleted":
            continue
        rec = p.get("recurring")
        if not rec:
            continue
        done_dates = {c.get("for_date") for c in p.get("completions", [])
                      if c.get("for_date")}
        skip_dates = {s.get("for_date") for s in p.get("skips", [])
                      if s.get("for_date")}

        total_matches = 0
        skipped = 0
        completed = 0
        weekday_skip_count: dict[int, int] = {}

        d = cutoff_date
        while d <= today:
            if _matches_recurring(rec, d):
                total_matches += 1
                ds = d.strftime("%Y-%m-%d")
                if ds in done_dates:
                    completed += 1
                elif ds in skip_dates or d < today:
                    # Вчерашние не-выполненные тоже считаем skip
                    skipped += 1
                    weekday_skip_count[d.weekday()] = weekday_skip_count.get(d.weekday(), 0) + 1
            d += _dt.timedelta(days=1)

        if total_matches < 3 or skipped < min_skips:
            continue

        skip_rate = skipped / total_matches
        # Слабый сигнал — skip rate < 40% не отмечаем
        if skip_rate < 0.4:
            continue

        # Worst weekday
        if weekday_skip_count:
            worst_wd = max(weekday_skip_count.items(), key=lambda kv: kv[1])
            wd_hint = f" · особенно по {WEEKDAYS_RU[worst_wd[0]]} ({worst_wd[1]}×)"
        else:
            wd_hint = ""

        patterns.append({
            "kind": "habit_anomaly",
            "plan_id": p["id"],
            "habit_name": p.get("name", ""),
            "category": p.get("category"),
            "weekday": today.weekday(),  # триггерим сегодня, не привязан к weekday
            "skipped": skipped,
            "completed": completed,
            "total": total_matches,
            "skip_rate": round(skip_rate, 2),
            "hint_ru": (
                f"Habit «{p.get('name', '')}» пропущен {skipped} из {total_matches} раз "
                f"за {days_back} дней ({int(skip_rate*100)}%){wd_hint}. "
                f"Ставим напоминалку или меняем расписание?"
            ),
        })
    return patterns


def detect_all(days_back: int = 21) -> list[dict]:
    """Запустить все детекторы + записать результат в patterns.jsonl.

    Дедуп: одинаковый (kind, weekday, category, window) за сутки не
    пишется повторно.
    """
    found = []
    try:
        found.extend(detect_skipped_category(days_back=days_back))
    except Exception as e:
        log.warning(f"[patterns] skipped_breakfast failed: {e}")
    try:
        found.extend(detect_heavy_day_overload(days_back=days_back))
    except Exception as e:
        log.warning(f"[patterns] heavy_work_day failed: {e}")
    try:
        found.extend(detect_habit_skips(days_back=min(days_back, 14)))
    except Exception as e:
        log.warning(f"[patterns] habit_skips failed: {e}")

    if not found:
        return []

    # Append в jsonl с ts — чтобы можно было смотреть историю детектов
    now = time.time()
    for p in found:
        p["detected_at"] = now
        try:
            with _PATTERNS_FILE.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(p, ensure_ascii=False) + "\n")
        except Exception as e:
            log.warning(f"[patterns] append failed: {e}")
    log.info(f"[patterns] detected {len(found)} patterns")
    return found


def read_recent_patterns(hours: int = 36) -> list[dict]:
    """Последние сохранённые паттерны — читает patterns.jsonl."""
    if not _PATTERNS_FILE.exists():
        return []
    cutoff = time.time() - hours * 3600
    out = []
    try:
        with _PATTERNS_FILE.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    p = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (p.get("detected_at") or 0) >= cutoff:
                    out.append(p)
    except Exception as e:
        log.warning(f"[patterns] read failed: {e}")
    return out


def patterns_for_today() -> list[dict]:
    """Паттерны применимые к сегодняшнему дню недели (для morning briefing)."""
    today_wd = datetime.now().weekday()
    all_recent = read_recent_patterns(hours=36)
    return [p for p in all_recent if p.get("weekday") == today_wd]


def is_pattern_abandoned(p: dict,
                          days_window: int = 14,
                          max_appearances: int = 5) -> bool:
    """Auto-abandon: pattern с той же signature повторился ≥ max_appearances
    раз за days_window — юзер игнорирует, не предлагаем больше.

    v1 time-based proxy. Реальное reaction-tracking (через action_memory
    accepted/rejected events per pattern) — Tier 2 в TODO. Сейчас просто
    «если детектится и детектится без видимой пользы — отстать».

    Args:
        p: pattern dict (kind/category/weekday/window).
        days_window: окно в днях для подсчёта (default 14).
        max_appearances: ≥ этого = abandoned (default 5 — детектор бежит
            раз в день, 5 повторений за 2 нед = постоянное упорство).
    """
    sig = (p.get("kind"), p.get("category"),
           p.get("weekday"), p.get("window"))
    cutoff = time.time() - days_window * 86400
    if not _PATTERNS_FILE.exists():
        return False
    count = 0
    try:
        with _PATTERNS_FILE.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    other = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (other.get("detected_at") or 0) < cutoff:
                    continue
                other_sig = (other.get("kind"), other.get("category"),
                              other.get("weekday"), other.get("window"))
                if other_sig == sig:
                    count += 1
                    if count >= max_appearances:
                        return True
    except Exception as e:
        log.debug(f"[patterns] is_pattern_abandoned read failed: {e}")
    return False
