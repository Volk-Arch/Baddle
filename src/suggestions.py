"""Observation → Suggestion — замыкание «система заметила → предложила».

Три источника detect'а, один draft-шаблон:

1. **Patterns** (patterns.py) — «3 четверга подряд без завтрака»
2. **Checkins** (checkins.py) — 5 дней подряд «устал» или stress ≥ 70%
3. **State/activity** — юзер часто в stress-zone после определённой
   категории активности (например «совещания → overload»)

Все три возвращают draft карточку `intent_confirm` (тот же тип что
использует intent_router для new_recurring/new_constraint). Юзер жмёт
«Да, создать» — и suggestion превращается в реальную цель через
`/goals/confirm-draft`.

Принцип **detect → draft → confirm**: система не создаёт цели сама,
она предлагает. Последнее слово за юзером.
"""
import logging
import time
from typing import Optional

log = logging.getLogger(__name__)


# ── Helper: LLM draft генератор ──────────────────────────────────────────

def _llm_draft_from_trigger(trigger_description: str,
                             suggestion_hint: str = "",
                             lang: str = "ru") -> Optional[dict]:
    """Один LLM-вызов: «вот триггер и гипотеза, сгенерируй draft цели».

    Returns `{kind: "new_recurring"|"new_constraint", text, schedule?,
               polarity?}` или None если не получилось.
    """
    try:
        from .graph_logic import _graph_generate
    except Exception:
        return None

    if lang == "ru":
        system = (
            "/no_think\nТы помощник который на основе наблюдения за юзером "
            "предлагает ОДНУ простую цель. Отвечай СТРОГО в формате:\n"
            "KIND: recurring|constraint\n"
            "TEXT: <короткий текст цели, 1 строка>\n"
            "FREQ: <число> / <day|week>   (только для recurring)\n"
            "POLARITY: avoid|prefer       (только для constraint)\n\n"
            "recurring — если юзеру полезно делать что-то регулярно.\n"
            "constraint — если полезно чего-то избегать.\n"
            "FREQ: для recurring обязательно. '3/day' или '2/week'.\n"
            "Не добавляй пояснений, только 3-4 строки с ключами."
        )
        user = (f"Наблюдение: {trigger_description}\n"
                f"Подсказка для draft'а (опционально): {suggestion_hint}\n"
                f"Ответ:")
    else:
        system = (
            "/no_think\nBased on an observation, suggest ONE simple goal.\n"
            "Format:\nKIND: recurring|constraint\nTEXT: <goal text>\n"
            "FREQ: <n> / <day|week>\nPOLARITY: avoid|prefer\n"
            "recurring for habits to build, constraint for things to avoid."
        )
        user = f"Observation: {trigger_description}\nHint: {suggestion_hint}\nAnswer:"

    try:
        result, _ = _graph_generate(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            max_tokens=80, temp=0.3, top_k=20,
        )
    except Exception as e:
        log.debug(f"[suggestions] llm failed: {e}")
        return None

    return _parse_draft_response(result or "")


def _parse_draft_response(raw: str) -> Optional[dict]:
    """Распарсить KIND/TEXT/FREQ/POLARITY в draft dict.

    Толерантный к вариациям регистра + русским ключам.
    """
    kind_text = None
    text = None
    freq = None
    period = "day"
    polarity = None

    for line in raw.split("\n"):
        L = line.strip()
        if not L:
            continue
        low = L.lower()
        if low.startswith("kind:") or low.startswith("тип:"):
            val = L.split(":", 1)[1].strip().lower()
            if "recurring" in val or "привычка" in val:
                kind_text = "new_recurring"
            elif "constraint" in val or "ограничение" in val:
                kind_text = "new_constraint"
        elif low.startswith("text:") or low.startswith("текст:"):
            text = L.split(":", 1)[1].strip(' "«».')
        elif low.startswith("freq:") or low.startswith("частота:"):
            val = L.split(":", 1)[1].strip().lower()
            import re
            m = re.search(r"(\d+)\s*[/\\]\s*(day|week|день|недел)", val)
            if m:
                freq = int(m.group(1))
                p = m.group(2)
                period = "week" if p.startswith(("week", "недел")) else "day"
            else:
                m2 = re.search(r"\d+", val)
                if m2:
                    freq = int(m2.group(0))
        elif low.startswith("polarity:") or low.startswith("полярность:"):
            val = L.split(":", 1)[1].strip().lower()
            if "avoid" in val or "избег" in val:
                polarity = "avoid"
            elif "prefer" in val or "предпоч" in val:
                polarity = "prefer"

    if not kind_text or not text or len(text) < 3:
        return None

    draft = {"kind": kind_text, "text": text[:200], "mode": "rhythm"
             if kind_text == "new_recurring" else "free"}
    if kind_text == "new_recurring":
        if not freq:
            freq = 1
        if period == "week":
            draft["schedule"] = {"times_per_week": freq}
        else:
            draft["schedule"] = {"times_per_day": freq}
    elif kind_text == "new_constraint":
        draft["polarity"] = polarity or "avoid"

    return draft


# ── 1. Patterns → suggestion ─────────────────────────────────────────────

def suggest_from_pattern(pattern: dict, lang: str = "ru") -> Optional[dict]:
    """Сгенерировать draft из обнаруженного паттерна.

    Pattern format — см. patterns.py: {kind, weekday, category, count, ...}
    """
    kind = pattern.get("kind")
    hint_ru = pattern.get("hint_ru", "")
    if not hint_ru:
        return None

    # Hint для LLM: какой тип цели скорее всего подойдёт
    suggestion_hint = ""
    if kind == "skip_breakfast":
        suggestion_hint = "recurring: завтрак каждое утро"
    elif kind == "heavy_work_day":
        suggestion_hint = "constraint: ограничить работу в перегрузочные дни"
    elif kind == "habit_anomaly":
        suggestion_hint = ("constraint или recurring: помочь вернуть habit "
                           "или заменить на что-то проще")

    draft = _llm_draft_from_trigger(hint_ru, suggestion_hint, lang=lang)
    if not draft:
        return None
    return {
        "draft": draft,
        "trigger": {"type": "pattern", "kind": kind,
                     "description": hint_ru[:300]},
    }


# ── 2. Checkins → suggestion ─────────────────────────────────────────────

def suggest_from_checkins(days: int = 7,
                           lang: str = "ru") -> Optional[dict]:
    """Анализ check-in'ов за N дней. Если средние негативные — suggest.

    Критерии (any):
      - stress_mean ≥ 70 (хроническое напряжение)
      - energy_mean ≤ 30 (истощение)
      - focus_mean ≤ 30 (туман)
      - surprise_mean ≤ −0.5 (постоянное разочарование)
      - 3+ подряд notes с негативными ключевыми словами

    Если сработало — LLM генерит draft.
    """
    try:
        from .checkins import rolling_averages, list_checkins
    except Exception:
        return None

    avg = rolling_averages(days=days)
    n = avg.get("n") or 0
    if n < 3:
        return None

    reasons = []
    if (avg.get("stress_mean") or 0) >= 70:
        reasons.append(f"хронический стресс (avg {avg['stress_mean']:.0f}/100)")
    if avg.get("energy_mean") is not None and avg["energy_mean"] <= 30:
        reasons.append(f"низкая энергия (avg {avg['energy_mean']:.0f}/100)")
    if avg.get("focus_mean") is not None and avg["focus_mean"] <= 30:
        reasons.append(f"плохой фокус (avg {avg['focus_mean']:.0f}/100)")
    if avg.get("surprise_mean") is not None and avg["surprise_mean"] <= -0.5:
        reasons.append(f"реальность хуже ожиданий (surprise "
                       f"{avg['surprise_mean']:+.2f})")

    if not reasons:
        return None

    # Глобальная подсказка LLM на основе reasons
    desc = (f"За последние {days} дней у юзера: {', '.join(reasons)}. "
            f"Что могло бы помочь?")
    draft = _llm_draft_from_trigger(
        desc,
        suggestion_hint="recurring (восстановительная привычка) "
                        "или constraint (ограничение на стрессогенный триггер)",
        lang=lang,
    )
    if not draft:
        return None
    return {
        "draft": draft,
        "trigger": {"type": "checkin_streak", "days": days,
                     "reasons": reasons,
                     "averages": {k: v for k, v in avg.items() if k != "n"}},
    }


# ── 3. State / activity → suggestion ─────────────────────────────────────

def _weekly_aggregate(days_this: int = 7, days_prev: int = 7) -> Optional[dict]:
    """Собрать compact JSON сравнения этой недели и предыдущей.

    Источники:
      - rolling_averages(7) vs rolling_averages(14) минус первая неделя
      - recurring goals: средний lag, сумма instances
      - activity: часы по категории за 7 vs предыдущие 7
      - goals: closed/opened за неделю

    Возвращает dict или None если данных недостаточно.
    """
    import time as _time
    try:
        from .checkins import _read_all as _checkins_all
        from .activity_log import _replay as _acts_replay
        from .goals_store import _read_all as _goals_all
        from .recurring import list_recurring
    except Exception:
        return None

    now = _time.time()
    day = 86400.0
    w1_start = now - days_this * day        # начало этой недели
    w2_start = w1_start - days_prev * day   # начало прошлой недели

    def _bucket_checkins(entries):
        vals = {"energy": [], "focus": [], "stress": [], "surprise": []}
        for e in entries:
            if e.get("energy") is not None:
                vals["energy"].append(float(e["energy"]))
            if e.get("focus") is not None:
                vals["focus"].append(float(e["focus"]))
            if e.get("stress") is not None:
                vals["stress"].append(float(e["stress"]))
            if e.get("expected") is not None and e.get("reality") is not None:
                vals["surprise"].append(float(e["reality"]) - float(e["expected"]))
        return {k: (round(sum(v) / len(v), 2) if v else None)
                for k, v in vals.items()}

    checkins_all = _checkins_all()
    this_week_ch = [e for e in checkins_all
                    if w1_start <= (e.get("ts") or 0) < now]
    prev_week_ch = [e for e in checkins_all
                    if w2_start <= (e.get("ts") or 0) < w1_start]
    if not this_week_ch and not prev_week_ch:
        checkins_cmp = None
    else:
        checkins_cmp = {
            "this_week": _bucket_checkins(this_week_ch),
            "prev_week": _bucket_checkins(prev_week_ch),
            "this_n": len(this_week_ch),
            "prev_n": len(prev_week_ch),
        }

    # Activity hours by category week-vs-week
    acts = list(_acts_replay().values())
    def _cat_hours(start, end):
        by_cat: dict = {}
        for a in acts:
            s = a.get("started_at") or 0
            e = a.get("stopped_at") or now
            if e < start or s > end:
                continue
            dur = max(0, min(e, end) - max(s, start))
            cat = a.get("category") or "uncategorized"
            by_cat[cat] = by_cat.get(cat, 0.0) + dur / 3600.0
        return {k: round(v, 1) for k, v in by_cat.items()}
    activity_cmp = {
        "this_week": _cat_hours(w1_start, now),
        "prev_week": _cat_hours(w2_start, w1_start),
    }

    # Recurring adherence
    rec_stats = []
    for g in list_recurring(active_only=True):
        instances_this = [i for i in (g.get("instances") or [])
                          if w1_start <= (i.get("ts") or 0) < now]
        instances_prev = [i for i in (g.get("instances") or [])
                          if w2_start <= (i.get("ts") or 0) < w1_start]
        rec_stats.append({
            "text": (g.get("text") or "")[:60],
            "this_week": len(instances_this),
            "prev_week": len(instances_prev),
        })

    # Goals closed/opened counts
    goals_events = _goals_all()
    opened_this = sum(1 for e in goals_events
                      if e.get("action") == "create"
                      and w1_start <= (e.get("ts") or 0) < now)
    closed_this = sum(1 for e in goals_events
                      if e.get("action") in ("complete", "abandon")
                      and w1_start <= (e.get("ts") or 0) < now)

    # Достаточно данных?
    enough = (checkins_cmp and checkins_cmp["this_n"] >= 2) \
             or activity_cmp["this_week"] or rec_stats
    if not enough:
        return None

    return {
        "checkins": checkins_cmp,
        "activity": activity_cmp,
        "recurring": rec_stats,
        "goals_events": {"opened_this_week": opened_this,
                         "closed_this_week": closed_this},
    }


def suggest_from_weekly_review(lang: str = "ru") -> Optional[dict]:
    """Weekly review → next-week plan. Сравнивает эту vs прошлую неделю,
    LLM предлагает ОДНУ change (добавить recurring / ввести constraint /
    abandon overload).

    Throttle: имеет смысл вызывать раз в неделю. Без данных или при
    отсутствии значимых изменений возвращает None.
    """
    agg = _weekly_aggregate(days_this=7, days_prev=7)
    if not agg:
        return None

    # Форматируем в компактный текст для LLM
    import json as _json
    compact = _json.dumps(agg, ensure_ascii=False)[:1200]

    if lang == "ru":
        desc = (f"Сравнение недель (this vs prev):\n{compact}\n\n"
                f"Что изменилось значимо? Что ОДНО полезное предложить юзеру "
                f"на следующую неделю?")
        hint = ("Например: если stress вырос — constraint «меньше работы "
                "в пятницу». Если recurring пропущен — recurring-напоминалка "
                "с меньшей частотой. Если активность в health упала — "
                "recurring «прогулка 3 раза в неделю».")
    else:
        desc = f"Week comparison:\n{compact}\n\nWhat to change next week?"
        hint = "Suggest one actionable goal/habit/constraint."

    draft = _llm_draft_from_trigger(desc, hint, lang=lang)
    if not draft:
        return None
    return {
        "draft": draft,
        "trigger": {"type": "weekly_review", "aggregate": agg},
    }


def suggest_from_stress_activity(tail_n: int = 50,
                                  lang: str = "ru") -> Optional[dict]:
    """Юзер часто попадает в stress-zone после определённой активности.

    Эвристика:
      - Читаем последние activity'ы с категорией
      - По state_graph смотрим post-activity coherence/stress
      - Если >3 раза после activity X coherence упало → suggest constraint

    MVP: если в activity_log >3 раза подряд категория work длилась
    >2h, и state_graph показывает stress после — предлагаем constraint
    «ограничить сессии работы до 2ч».
    """
    try:
        from .activity_log import list_activities
    except Exception:
        return None

    acts = list_activities(limit=tail_n)
    if len(acts) < 5:
        return None

    # Долгие work-сессии (>2h)
    long_work = [a for a in acts
                 if a.get("category") == "work"
                 and (a.get("duration_s") or 0) > 7200]
    if len(long_work) < 3:
        return None

    # Получаем средний stress после (через UserState snapshot — упрощённо)
    desc = (f"Юзер {len(long_work)} раз за последнее время работал блоками "
            f"больше 2 часов без перерыва. Это типично вызывает перегрузку.")
    draft = _llm_draft_from_trigger(
        desc,
        suggestion_hint="recurring или constraint: ограничить блок работы "
                        "или добавить паузы каждые 90 минут",
        lang=lang,
    )
    if not draft:
        return None
    return {
        "draft": draft,
        "trigger": {"type": "state_stress",
                     "long_work_sessions": len(long_work)},
    }


# ── Unified: собрать все suggestions ─────────────────────────────────────

def collect_suggestions(lang: str = "ru",
                        include_patterns: bool = True,
                        include_checkins: bool = True,
                        include_stress: bool = True,
                        include_weekly: bool = True) -> list[dict]:
    """Прогнать все источники, вернуть список draft-suggestions.

    Cheap guard — пропускаем если нет данных. Не дублирует одинаковые
    тексты draft'ов (dedup by draft.text).
    """
    results: list[dict] = []
    seen_texts: set[str] = set()

    def _add(item: Optional[dict]):
        if not item:
            return
        key = (item.get("draft") or {}).get("text", "").lower().strip()
        if not key or key in seen_texts:
            return
        seen_texts.add(key)
        results.append(item)

    if include_patterns:
        try:
            from .patterns import read_recent_patterns
            for p in read_recent_patterns(hours=48)[:3]:
                _add(suggest_from_pattern(p, lang=lang))
        except Exception as e:
            log.debug(f"[suggestions] pattern source failed: {e}")

    if include_checkins:
        try:
            _add(suggest_from_checkins(days=7, lang=lang))
        except Exception as e:
            log.debug(f"[suggestions] checkin source failed: {e}")

    if include_stress:
        try:
            _add(suggest_from_stress_activity(lang=lang))
        except Exception as e:
            log.debug(f"[suggestions] stress source failed: {e}")

    if include_weekly:
        try:
            _add(suggest_from_weekly_review(lang=lang))
        except Exception as e:
            log.debug(f"[suggestions] weekly source failed: {e}")

    return results


def make_suggestion_card(item: dict, lang: str = "ru") -> dict:
    """Превратить suggestion в UI-карточку `intent_confirm` (тот же тип что
    router draft) + trigger description для объяснения «почему».
    """
    draft = item.get("draft") or {}
    trigger = item.get("trigger") or {}
    sub = draft.get("kind", "new_goal")
    labels = {
        "new_recurring":  ("Система предложила привычку",
                            "System suggests a habit"),
        "new_constraint": ("Система предложила ограничение",
                            "System suggests a constraint"),
    }
    ru, en = labels.get(sub, ("Предложение", "Suggestion"))
    trig_desc = trigger.get("description") or ""
    if not trig_desc and trigger.get("reasons"):
        trig_desc = "; ".join(trigger["reasons"])
    return {
        "type": "intent_confirm",
        "kind": sub,
        "draft": draft,
        "title": ru if lang == "ru" else en,
        "description_ru": f"💡 Потому что: {trig_desc}",
        "description_en": f"💡 Because: {trig_desc}",
        "trigger": trigger,
        "source": "observation",
        "prompt_user": True,
    }
