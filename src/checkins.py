"""Daily check-in — ручной ввод subjective-сигналов.

Закрывает дыру: без HRV-трекера система не видит реальное состояние
юзера. Check-in — ручной аналог.

Юзер раз в день (или чаще) отвечает:
  • energy (0-100)        — сколько сил сейчас
  • focus (0-100)         — ясность головы
  • stress (0-100)        — напряжение
  • expected (−2..+2)     — как ожидал что день пойдёт
  • reality  (−2..+2)     — как пошло на самом деле
  • note (optional)       — короткий комментарий

Вычисляемое:
  • surprise = reality - expected    → feed в UserState.surprise
  • valence_hint = reality / 2       → feed в valence
  • energy_est = energy / 100        → хинт для long_reserve scaling

Файл: `checkins.jsonl` append-only.
"""
import json
import logging
import time
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

from .paths import CHECKINS_FILE as _CHECKIN_FILE


def _append(entry: dict):
    entry.setdefault("ts", time.time())
    try:
        with _CHECKIN_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning(f"[checkins] append failed: {e}")


def _read_all() -> list[dict]:
    if not _CHECKIN_FILE.exists():
        return []
    out = []
    try:
        with _CHECKIN_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        log.warning(f"[checkins] read failed: {e}")
    return out


def add_checkin(energy: Optional[int] = None,
                focus: Optional[int] = None,
                stress: Optional[int] = None,
                expected: Optional[float] = None,
                reality: Optional[float] = None,
                note: str = "") -> dict:
    """Записать новый check-in. Все поля опциональны (можно записать только
    energy если больше нечего сказать).

    energy/focus/stress ∈ [0, 100]; expected/reality ∈ [−2, +2].
    """
    def _clamp(v, lo, hi):
        if v is None:
            return None
        try:
            return max(lo, min(hi, float(v)))
        except (TypeError, ValueError):
            return None

    entry = {
        "action": "checkin",
        "energy": _clamp(energy, 0, 100),
        "focus":  _clamp(focus, 0, 100),
        "stress": _clamp(stress, 0, 100),
        "expected": _clamp(expected, -2, 2),
        "reality":  _clamp(reality, -2, 2),
        "note": (note or "")[:300],
    }
    _append(entry)

    # Derived fields
    if entry["expected"] is not None and entry["reality"] is not None:
        entry["surprise"] = entry["reality"] - entry["expected"]

    # Дублируем в sensor_stream — UserState читает и HRV и manual как единый
    # полиморфный поток. Источник = 'manual', confidence = 0.7 (субъективно).
    try:
        from .sensor_stream import push_subjective
        # Приводим к [0,1] нормализованные копии для HRV-совместимости
        energy_norm = (entry["energy"] / 100.0) if entry.get("energy") is not None else None
        focus_norm = (entry["focus"] / 100.0) if entry.get("focus") is not None else None
        stress_norm = (entry["stress"] / 100.0) if entry.get("stress") is not None else None
        push_subjective(
            energy=energy_norm, focus=focus_norm, stress=stress_norm,
            surprise=entry.get("surprise"),
            note=entry.get("note"),
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug(f"[checkin] sensor_stream push failed: {e}")

    return entry


def latest_checkin(hours: float = 24) -> Optional[dict]:
    """Последний check-in за N часов. None если нет."""
    cutoff = time.time() - hours * 3600
    items = [e for e in _read_all() if e.get("ts", 0) >= cutoff]
    if not items:
        return None
    items.sort(key=lambda e: e.get("ts", 0), reverse=True)
    top = dict(items[0])
    if top.get("expected") is not None and top.get("reality") is not None:
        top["surprise"] = top["reality"] - top["expected"]
    return top


def list_checkins(days: int = 14, limit: int = 100) -> list[dict]:
    cutoff = time.time() - days * 86400
    items = [e for e in _read_all() if e.get("ts", 0) >= cutoff]
    items.sort(key=lambda e: e.get("ts", 0), reverse=True)
    return items[:limit]


def rolling_averages(days: int = 7) -> dict:
    """Средние значения за последние N дней — хинт для long-term тренда."""
    cutoff = time.time() - days * 86400
    items = [e for e in _read_all() if e.get("ts", 0) >= cutoff]
    if not items:
        return {"n": 0}
    keys = ("energy", "focus", "stress", "expected", "reality")
    totals = {k: [] for k in keys}
    for e in items:
        for k in keys:
            v = e.get(k)
            if v is not None:
                totals[k].append(float(v))
    out = {"n": len(items)}
    for k, vs in totals.items():
        out[f"{k}_mean"] = round(sum(vs) / len(vs), 2) if vs else None
    # Surprise mean: avg(reality - expected) по тем entries где оба есть
    surprises = [float(e["reality"]) - float(e["expected"])
                 for e in items
                 if e.get("reality") is not None and e.get("expected") is not None]
    out["surprise_mean"] = round(sum(surprises) / len(surprises), 2) if surprises else None
    return out


# ── Feed into UserState ────────────────────────────────────────────────────

def apply_to_user_state(entry: dict):
    """Спроецировать check-in в UserState — заменяет роль HRV когда HRV off.

    - stress (0-100) → NE EMA bump
    - focus  (0-100) → serotonin EMA bump
    - reality (-2..+2) → valence + subjective_surprise (если есть expected)
    - expected (-2..+2) → используется вместе с reality для nudge expectation

    Phase C: `energy` поле checkin'а больше не пишется (long_reserve удалён в
    Шаге 6). Если юзер пишет energy — игнорируется. Когнитивная нагрузка
    теперь считается через `cognitive_load_today` из activity_log.

    Decay constants — см. `src/ema.py::Decays.CHECKIN_*`. Checkin decays
    агрессивнее обычных (0.6-0.85 vs 0.9-0.98) — явный user input должен
    корректировать модель сильнее чем автоматические feeders.
    """
    try:
        from .user_state import get_user_state
        user = get_user_state()

        # Stress/focus/reality → NE/serotonin/valence через explicit apply_checkin
        # (Phase D Step 3c). Каждая метрика получает per-event decay_override
        # из Decays.CHECKIN_* — реализация в UserState.apply_checkin.
        user.apply_checkin(
            stress=entry.get("stress"),
            focus=entry.get("focus"),
            reality=entry.get("reality"),
        )

        # Subjective surprise → nudge expectation (bridge от старого сломанного
        # `user.surprise = ...` к правильному baseline-nudge).
        if entry.get("expected") is not None and entry.get("reality") is not None:
            subjective_surprise = (float(entry["reality"]) - float(entry["expected"])) / 4.0
            user.apply_subjective_surprise(subjective_surprise, blend=0.4)

        user._clamp()
    except Exception as e:
        log.warning(f"[checkins] apply_to_user_state failed: {e}")
