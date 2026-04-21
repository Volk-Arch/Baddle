"""User-side surprise detection (OQ #7).

Момент когда **юзер** встретил неожиданное — не когда Baddle ошибся про
юзера. Три источника сигнала:

  A. HRV-based — RMSSD dropped significantly from rolling baseline.
     Читает `sensor_stream.recent(KIND_HRV_SNAPSHOT)`. Требует реального
     источника (Polar / симулятор должен быть запущен).

  B. Text markers (regex) — «воу», «не ожидал», «??», многоточие, капс.
     Работает всегда, но шумит на нетипичных формулировках.

  C. LLM classify — light 1-number prompt в borderline-диапазоне (когда
     regex score ∈ [0.15, 0.45]). Ловит иронию, сарказм, длинные фразы
     без явных маркеров. С кэшем по SHA1 текста (как в sentiment.py).

Combined check в `detect_user_surprise(text, activity, use_llm=True)` —
OR всех сигналов. Caller (cognitive_loop._check_user_surprise) throttle'ит
+ записывает event + вызывает `user_state.apply_surprise_boost()`.

Для подхода см. [docs/friston-loop.md](../docs/friston-loop.md) и
[OQ #7](../planning/TODO.md).
"""
from __future__ import annotations

import hashlib
import math
import re
import time
import logging
from typing import Optional

log = logging.getLogger(__name__)


# ── HRV-based detector ──────────────────────────────────────────────────────

HRV_SHORT_WINDOW_S = 30.0      # текущее состояние (последние 30 сек)
HRV_BASELINE_WINDOW_S = 300.0  # baseline (последние 5 мин)
HRV_MIN_BASELINE_SAMPLES = 5   # без этого — невозможно std оценить
HRV_THRESHOLD_SIGMA = 1.5      # отклонение больше 1.5σ считается surprise
HRV_MIN_DROP_MS = 5.0          # минимальная абс разница (ignore шум при низком std)
HRV_ACTIVITY_THRESHOLD = 0.5   # выше — физнагрузка, игнорим (не surprise)


def detect_hrv_surprise(activity_magnitude: Optional[float] = None) -> dict:
    """RMSSD drop detection по rolling window.

    Алгоритм:
      1. Baseline: RMSSD readings за последние 5 мин → mean + std
      2. Current: latest RMSSD reading (≤30 сек от now)
      3. surprise = |current − baseline_mean| > max(1.5σ, 5ms)

    Args:
        activity_magnitude: если > HRV_ACTIVITY_THRESHOLD (физнагрузка) —
            forced no-surprise (HRV drop от бега не считается surprise).
            None → проверка пропускается.

    Returns:
        {
            "event": bool,
            "score": float,                # |Δ| / baseline_std (z-score-like)
            "latest_rmssd": float | None,
            "baseline_mean": float | None,
            "baseline_std": float | None,
            "reason": str,                 # "no_data" | "insufficient_baseline" |
                                            # "activity_filter" | "stable" |
                                            # "surprise_detected"
        }
    """
    try:
        from .sensor_stream import get_stream, KIND_HRV_SNAPSHOT
    except Exception as e:
        return {"event": False, "reason": f"import_failed:{e}", "score": 0.0}

    # Activity gate
    if (activity_magnitude is not None
            and float(activity_magnitude) > HRV_ACTIVITY_THRESHOLD):
        return {
            "event": False, "reason": "activity_filter",
            "score": 0.0, "activity": float(activity_magnitude),
        }

    stream = get_stream()
    baseline_readings = stream.recent(
        kinds=[KIND_HRV_SNAPSHOT],
        since_seconds=HRV_BASELINE_WINDOW_S,
    )
    rmssds = [float(r.metrics["rmssd"])
              for r in baseline_readings
              if r.metrics and r.metrics.get("rmssd") is not None]
    if len(rmssds) < HRV_MIN_BASELINE_SAMPLES:
        return {
            "event": False, "reason": "insufficient_baseline",
            "samples": len(rmssds), "score": 0.0,
        }

    mean = sum(rmssds) / len(rmssds)
    variance = sum((x - mean) ** 2 for x in rmssds) / len(rmssds)
    std = math.sqrt(variance)

    # Latest — самое свежее чтение
    now = time.time()
    recent_short = [r for r in baseline_readings
                    if (now - r.ts) <= HRV_SHORT_WINDOW_S
                    and r.metrics
                    and r.metrics.get("rmssd") is not None]
    if not recent_short:
        return {
            "event": False, "reason": "no_recent_reading",
            "score": 0.0, "baseline_mean": round(mean, 2),
            "baseline_std": round(std, 2),
        }
    latest_rmssd = float(recent_short[-1].metrics["rmssd"])

    # Z-score-like (robust если std близка к 0 → защита)
    abs_delta = abs(latest_rmssd - mean)
    if std < 1.0:
        # Baseline почти плоский — можем ловить только большие абсолютные drop'ы
        # чтобы не триггерить на микро-шум.
        score = abs_delta / max(1.0, std)
        is_surprise = abs_delta >= HRV_MIN_DROP_MS * 2  # нужен более сильный сигнал
    else:
        score = abs_delta / std
        is_surprise = (score >= HRV_THRESHOLD_SIGMA
                       and abs_delta >= HRV_MIN_DROP_MS)

    return {
        "event": bool(is_surprise),
        "reason": "surprise_detected" if is_surprise else "stable",
        "score": round(score, 2),
        "latest_rmssd": round(latest_rmssd, 2),
        "baseline_mean": round(mean, 2),
        "baseline_std": round(std, 2),
        "delta_ms": round(latest_rmssd - mean, 2),  # signed
        "samples": len(rmssds),
    }


# ── Text-based detector ─────────────────────────────────────────────────────

# Lightweight regex markers. Покрывает ru + en + нейтральное.
# Score компоненты:
#   • сильные маркеры ("не ожидал", "wow") → +0.45
#   • средние маркеры ("странно", "really") → +0.30
#   • мягкие ("hmm", многоточие) → +0.15
#   • капс > 50% (min 4 букв) → +0.25
#   • '??' / '!!!' → +0.20
#   Сумма клампится в [0, 1].

STRONG_MARKERS = [
    r"\bне\s+ожидал",  r"\bне\s+ожидала",
    r"\bвот\s+это\s+да",
    r"\bохре",  r"\bохуе",
    r"\bнифига\s+себе",
    r"(?:^|\s)воу\b", r"(?:^|\s)вау\b",
    r"(?:^|\s)ого\b", r"(?:^|\s)ого-го",
    r"\bwow\b", r"\bwhoa\b",
    r"\bdidn'?t\s+expect", r"\bno\s+way\b",
    r"\bholy\s+(?:shit|moly|crap)",
]
MEDIUM_MARKERS = [
    r"\bстранно\b", r"\bинтересно\b", r"\bнеожид",
    r"\bсерь[её]зно\b",          # "серьёзно" | "серьезно" (обе формы)
    r"(?:^|\s)блин\b", r"(?:^|\s)хм+\b",
    r"\breally\?", r"\bseriously\?", r"\bwait\s+what",
    r"\bhuh\b", r"\bweird\b",
]
SOFT_MARKERS = [
    r"\.\.\.\.+",       # 4+ точек
    r"(?:^|\s)хм\b",    # hmm ru
    r"\bhmm\b",
    r"(?:^|\s)эм+\b",
]

_STRONG_RE = re.compile("|".join(STRONG_MARKERS), re.IGNORECASE)
_MEDIUM_RE = re.compile("|".join(MEDIUM_MARKERS), re.IGNORECASE)
_SOFT_RE = re.compile("|".join(SOFT_MARKERS), re.IGNORECASE)
_QUESTION_BURST = re.compile(r"\?{2,}")
_EXCLAIM_BURST = re.compile(r"!{3,}")

TEXT_SURPRISE_THRESHOLD = 0.35   # выше — event


def text_surprise_score(text: str) -> dict:
    """Regex + эвристики → score [0, 1] + breakdown.

    Без LLM на MVP — дёшево, работает оффлайн, предсказуемо. Если позже
    окажется что шумит — в `_check_user_surprise` добавим LLM classify на
    borderline (0.2–0.4) случаи.
    """
    if not text or not isinstance(text, str):
        return {"event": False, "score": 0.0, "markers": []}
    snippet = text.strip()
    if len(snippet) < 2:
        return {"event": False, "score": 0.0, "markers": []}

    score = 0.0
    markers: list = []

    strong_hits = _STRONG_RE.findall(snippet)
    if strong_hits:
        score += 0.45 + 0.05 * min(3, len(strong_hits) - 1)
        markers.append(f"strong({len(strong_hits)})")

    medium_hits = _MEDIUM_RE.findall(snippet)
    if medium_hits:
        score += 0.30 + 0.05 * min(2, len(medium_hits) - 1)
        markers.append(f"medium({len(medium_hits)})")

    soft_hits = _SOFT_RE.findall(snippet)
    if soft_hits:
        score += 0.15
        markers.append("soft")

    if _QUESTION_BURST.search(snippet):
        score += 0.25
        markers.append("??+")

    if _EXCLAIM_BURST.search(snippet):
        score += 0.25
        markers.append("!!!+")

    # Капс > 50% среди букв (min 4 буквы длинной)
    letters = [c for c in snippet if c.isalpha()]
    if len(letters) >= 4:
        upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
        if upper_ratio > 0.5:
            score += 0.25
            markers.append(f"caps({upper_ratio:.2f})")

    score = min(1.0, score)
    return {
        "event": score >= TEXT_SURPRISE_THRESHOLD,
        "score": round(score, 2),
        "markers": markers,
    }


# ── LLM-based detector (fallback для borderline regex scores) ──────────────

# LLM trigger: regex ≥ LLM_BORDERLINE_HIGH → уверенный regex, скипаем LLM.
# Regex ниже → LLM решает. Плюс min-length guard: очень короткие сообщения
# («ok», «да», «нет») — не зовём LLM даже если regex = 0.
LLM_BORDERLINE_HIGH = 0.45     # выше — regex уверенно triggered
LLM_MIN_TEXT_LEN = 15          # короче — только regex (не тратим LLM на «ok»)
LLM_SURPRISE_THRESHOLD = 0.5   # LLM score ≥ 0.5 → event

# Cache hash(text) → LLM score. Параллельный sentiment'у.
_llm_cache: dict[str, float] = {}
_LLM_CACHE_MAX = 500


def _text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _trim_llm_cache():
    if len(_llm_cache) > _LLM_CACHE_MAX:
        items = list(_llm_cache.items())[-(_LLM_CACHE_MAX // 2):]
        _llm_cache.clear()
        _llm_cache.update(items)


def llm_surprise_score(text: str) -> float:
    """LLM-classify: вернуть surprise score в [0, 1].

    Ловит случаи которые regex не покрывает:
      - Длинные описательные сообщения без ярких маркеров
      - Ирония / сарказм («ага, конечно, это я не ожидал»)
      - Нетипичные формулировки («зашквар», «я в шоке», «серьёзно что ли»)

    Light LLM call (max_tokens=6, temp=0). Cache по SHA1. При ошибке → 0.0.
    Пустой / короткий текст (<3 символов) → 0.0 без LLM.
    """
    if not text or len(text.strip()) < 3:
        return 0.0
    h = _text_hash(text)
    if h in _llm_cache:
        return _llm_cache[h]

    try:
        from .graph_logic import _graph_generate
        system = (
            "/no_think\n"
            "Return ONE number between 0.0 and 1.0 — how strongly the user "
            "expresses surprise / unexpectedness in this message.\n"
            "0.0 = neutral, factual, expected tone\n"
            "0.3 = mild surprise or curiosity (hmm, interesting)\n"
            "0.7 = clear surprise (wow, didn't expect, really?)\n"
            "1.0 = strong shock (no way, holy, что?!)\n"
            "NO explanation. NO prefix. JUST the number."
        )
        res, _ent = _graph_generate(
            [{"role": "system", "content": system},
             {"role": "user", "content": text[:400]}],
            max_tokens=6, temp=0.0, top_k=1,
        )
        if res:
            cleaned = res.strip().strip(' ".,\n`')
            try:
                score = float(cleaned)
            except ValueError:
                score = 0.0
                for p in cleaned.split():
                    p2 = p.strip(' .,;:"`')
                    try:
                        score = float(p2)
                        break
                    except ValueError:
                        continue
            score = max(0.0, min(1.0, score))
            _llm_cache[h] = score
            _trim_llm_cache()
            return score
    except Exception as e:
        log.debug(f"[surprise_detector] LLM classify failed for {text[:30]!r}: {e}")

    _llm_cache[h] = 0.0
    return 0.0


def clear_llm_cache():
    """Очистка cache — вызывается при /reset эндпоинтах."""
    _llm_cache.clear()


# ── Combined detector ──────────────────────────────────────────────────────

def detect_user_surprise(text: Optional[str] = None,
                          activity_magnitude: Optional[float] = None,
                          use_llm: bool = True) -> dict:
    """Combined HRV + text + (optional) LLM check. OR signals.

    Args:
        text: последнее user-сообщение (для B + C-каналов). None → только HRV.
        activity_magnitude: текущий activity (для HRV activity gate).
        use_llm: если True И regex score в borderline [0.15, 0.45] — зовём
            LLM classifier. LLM score ≥ 0.5 считается event. Ставим False
            для tests без LLM инфраструктуры.

    Returns:
        {
            "event": bool,
            "source": "hrv" | "text" | "llm" | "both" | "triple" | None,
            "confidence": float,     # оценка уверенности [0, 1]
            "hrv":  {...}  (из detect_hrv_surprise),
            "text": {...} (из text_surprise_score),
            "llm":  {score, used}  — если LLM вызывался
        }

    `source=None` если ни один не сработал. `confidence` = max из всех
    нормализованных каналов — не probability, просто relative strength.
    """
    hrv = detect_hrv_surprise(activity_magnitude=activity_magnitude)
    txt = (text_surprise_score(text or "") if text
           else {"event": False, "score": 0.0, "markers": []})

    # LLM fallback когда regex не уверен. Экономия: confident regex (≥0.45)
    # skip'аем; короткие сообщения (<15 симв.) тоже — регексы их покрывают.
    llm_info = {"used": False, "score": 0.0}
    txt_score = float(txt.get("score", 0.0))
    if (use_llm and text
            and len(text.strip()) >= LLM_MIN_TEXT_LEN
            and txt_score < LLM_BORDERLINE_HIGH):
        llm_score = llm_surprise_score(text)
        llm_info = {"used": True, "score": round(llm_score, 2)}

    hrv_event = bool(hrv.get("event"))
    txt_event = bool(txt.get("event"))
    llm_event = llm_info["used"] and llm_info["score"] >= LLM_SURPRISE_THRESHOLD

    if not (hrv_event or txt_event or llm_event):
        return {
            "event": False, "source": None, "confidence": 0.0,
            "hrv": hrv, "text": txt, "llm": llm_info,
        }

    # source label — учитываем сколько сигналов сработало
    active = []
    if hrv_event: active.append("hrv")
    if txt_event: active.append("text")
    if llm_event: active.append("llm")
    if len(active) == 3:
        source = "triple"
    elif len(active) == 2:
        source = "both"
    else:
        source = active[0]

    hrv_conf = min(1.0, float(hrv.get("score", 0.0)) / 3.0)
    txt_conf = txt_score
    llm_conf = float(llm_info.get("score", 0.0)) if llm_info["used"] else 0.0
    confidence = max(hrv_conf, txt_conf, llm_conf)

    return {
        "event": True,
        "source": source,
        "confidence": round(confidence, 2),
        "hrv": hrv, "text": txt, "llm": llm_info,
    }
