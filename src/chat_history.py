"""Chat history — persistent на сервере (раньше жил в browser localStorage).

Формат: JSONL, одна entry на строку. Форматы entry совпадают с теми что
писал JS `_chatStorePush`:
  {"kind":"msg", "role":"user"|"assistant"|"system", "content":"...", "meta":{}}
  {"kind":"card", "card":{...}}

Чат глобальный (не per-workspace) — один человек, один разговор с Baddle.
Workspace-скоуп — это про цели и граф, не про чат.
"""
from __future__ import annotations
import json
import logging
import threading
from typing import Optional

from .paths import CHAT_HISTORY_FILE

log = logging.getLogger(__name__)

# Сколько последних entries хранить. Старше — урезаем при append.
# Совпадает с JS CHAT_STORE_MAX (было 200).
_MAX_ENTRIES = 200

_lock = threading.Lock()


# Intro-строки для observation / Scout / DMN alert'ов. Если следом нет
# card — intro висит в чате мусором («Я заметил паттерн — предлагаю:»
# без тела). Было из-за бага persistence (card не push'илась). Теперь
# фикс есть, но старые записи в jsonl остались — чистим read-through.
_ORPHAN_INTRO_PREFIXES = (
    "💡 Я заметил паттерн",
    "💡 Пока ты не смотрел",
    "💡 While you were away",
    "🔗 DMN-инсайт",
    "🔗 DMN insight",
)


def _is_orphan_intro(entry: dict, next_entry: Optional[dict]) -> bool:
    if entry.get("kind") != "msg" or entry.get("role") != "assistant":
        return False
    content = entry.get("content") or ""
    if not any(content.startswith(p) for p in _ORPHAN_INTRO_PREFIXES):
        return False
    return not (next_entry and next_entry.get("kind") == "card")


_CARDS_WITH_INTRO = ("intent_confirm", "bridge")


def _migrate_intro_into_card(entries: list[dict]) -> list[dict]:
    """Legacy: intro был отдельным msg перед card. Новый формат — встроен
    в card.intro (один атомарный persist). Если видим pattern
    `(intro msg) + (intent_confirm|bridge card)` — сливаем intro в card
    и выкидываем msg.
    """
    out: list[dict] = []
    i = 0
    while i < len(entries):
        e = entries[i]
        nxt = entries[i + 1] if i + 1 < len(entries) else None
        content = (e.get("content") or "") if e.get("kind") == "msg" else ""
        is_intro = (e.get("kind") == "msg"
                    and e.get("role") == "assistant"
                    and any(content.startswith(p) for p in _ORPHAN_INTRO_PREFIXES))
        if is_intro and nxt and nxt.get("kind") == "card":
            card = (nxt.get("card") or {})
            if card.get("type") in _CARDS_WITH_INTRO:
                merged_card = dict(card)
                merged_card.setdefault("intro", content)
                out.append({"kind": "card", "card": merged_card})
                i += 2
                continue
        out.append(e)
        i += 1
    return out


def load_history() -> list[dict]:
    """Читает всю историю. Пропускает битые строки.

    Cleanup на чтении:
      1) `(intro msg) + (card)` → merge intro в card.intro (новый формат)
      2) orphan intros (intro без следующей card) — остаток старого бага
    Если что-то изменилось — переписываем файл.
    """
    path = CHAT_HISTORY_FILE
    if not path.exists():
        return []
    raw: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        log.warning(f"[chat_history] load failed: {e}")
        return []

    merged = _migrate_intro_into_card(raw)
    filtered = [e for i, e in enumerate(merged)
                if not _is_orphan_intro(e, merged[i + 1] if i + 1 < len(merged) else None)]
    if len(filtered) != len(raw):
        with _lock:
            try:
                _write_all(filtered)
                log.info(f"[chat_history] cleanup: "
                         f"{len(raw) - len(merged)} intros merged, "
                         f"{len(merged) - len(filtered)} orphans pruned")
            except Exception as e:
                log.warning(f"[chat_history] rewrite failed: {e}")
    return filtered


def _write_all(entries: list[dict]) -> None:
    """Атомарный перезапис файла (через .tmp)."""
    path = CHAT_HISTORY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    tmp.replace(path)


def append_entry(entry: dict) -> None:
    """Добавить одну entry. Усечь до _MAX_ENTRIES если переросло.

    Для msg entries с meta.mode_name='Утро' (morning briefing) — убираем
    прошлый briefing если в той же UTC-дате сегодня. UI не должен
    видеть дубли если сервер перезапустится и пушнёт briefing повторно.
    """
    with _lock:
        entries = load_history()
        entries.append(entry)
        # Dedup morning briefings: оставляем только самый свежий
        _dedup_morning(entries)
        if len(entries) > _MAX_ENTRIES:
            entries = entries[-_MAX_ENTRIES:]
        _write_all(entries)


def _dedup_morning(entries: list[dict]) -> None:
    """In-place: убрать дубли morning_briefing, оставить последний."""
    last_idx = -1
    for i, e in enumerate(entries):
        if e.get("kind") == "msg" and ((e.get("meta") or {}).get("mode_name") == "Утро"
                                        or (e.get("meta") or {}).get("mode_name") == "утренний брифинг"):
            last_idx = i
        elif e.get("kind") == "card" and (e.get("card") or {}).get("type") == "morning_briefing":
            last_idx = i
    if last_idx < 0:
        return
    filtered = [
        e for i, e in enumerate(entries)
        if i == last_idx or not _is_morning(e)
    ]
    entries.clear()
    entries.extend(filtered)


def _is_morning(e: dict) -> bool:
    if e.get("kind") == "msg":
        mn = (e.get("meta") or {}).get("mode_name")
        return mn in ("Утро", "утренний брифинг")
    if e.get("kind") == "card":
        return (e.get("card") or {}).get("type") == "morning_briefing"
    return False


def clear_history() -> int:
    """Удалить файл. Возвращает 0 если не было, 1 если удалили."""
    with _lock:
        path = CHAT_HISTORY_FILE
        if not path.exists():
            return 0
        try:
            path.unlink()
            return 1
        except OSError as e:
            log.warning(f"[chat_history] clear failed: {e}")
            return 0
