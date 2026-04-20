"""Sentiment classification — light LLM-based scoring в [-1, 1].

Используется как высокочастотный feeder в `UserState.valence`:
- Каждое user-сообщение → classify → EMA вклад
- Плюс сохраняется в `action.context.sentiment` для user_chat action-нод
  (Action Memory — см. docs/action-memory-design.md)

Philosophy: объективный observer tool, не «настроение». Просто
эмоциональный окрас текста который юзер написал. Агрессивное → -0.8,
нейтральное "сделай X" → 0, радостное → +0.7.

LLM-вызов минимальный (max_tokens=5, temp=0) — не тратим токены на
объяснения. Cache по hash текста — повторные сообщения не бьют LLM.
"""
import hashlib
import logging
from typing import Optional

log = logging.getLogger(__name__)


# Cache: hash(text) → score. Очищается при /reset. Rotating — max 500.
_cache: dict[str, float] = {}
_CACHE_MAX = 500


def _text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _trim_cache():
    """Простая rotating очистка — при превышении _CACHE_MAX режем пополам."""
    if len(_cache) > _CACHE_MAX:
        # Берём последние _CACHE_MAX//2 insertions (dict сохраняет insertion order)
        items = list(_cache.items())[-(_CACHE_MAX // 2):]
        _cache.clear()
        _cache.update(items)


def classify_message_sentiment(text: str) -> float:
    """Вернуть sentiment score в [-1, 1] для текста.

    -1.0 = frustration/anger/sadness
     0.0 = neutral / factual / inquiry
    +1.0 = joy/excitement/gratitude

    Кэшируется по hash. При LLM-ошибке возвращаем 0.0 (neutral fallback).
    Пустой/короткий текст (< 3 символов) — 0.0 без LLM.
    """
    if not text or len(text.strip()) < 3:
        return 0.0
    h = _text_hash(text)
    if h in _cache:
        return _cache[h]

    try:
        from .graph_logic import _graph_generate
        system = (
            "/no_think\n"
            "Return ONE number between -1.0 and 1.0 — sentiment of the user message.\n"
            "-1.0 = frustration / anger / sadness / despair\n"
            " 0.0 = neutral / factual / inquiry / planning\n"
            "+1.0 = joy / excitement / gratitude / relief\n"
            "NO explanation. NO prefix. JUST the number."
        )
        res, _ent = _graph_generate(
            [{"role": "system", "content": system},
             {"role": "user", "content": text[:500]}],
            max_tokens=8, temp=0.0, top_k=1,
        )
        if res:
            # Ищем первое number-like в ответе
            cleaned = res.strip().strip(' ".,\n`')
            # Частые случаи: "0.5", "-0.3", "  +0.7  "
            try:
                score = float(cleaned)
            except ValueError:
                # Fallback: попробовать первое слово
                parts = cleaned.split()
                score = 0.0
                for p in parts:
                    p2 = p.strip(' .,;:"`')
                    try:
                        score = float(p2)
                        break
                    except ValueError:
                        continue
            score = max(-1.0, min(1.0, score))
            _cache[h] = score
            _trim_cache()
            return score
    except Exception as e:
        log.debug(f"[sentiment] LLM classify failed for {text[:30]!r}: {e}")

    _cache[h] = 0.0
    return 0.0


def clear_cache():
    """Очистка cache — вызывается при reset-эндпоинтах."""
    _cache.clear()
