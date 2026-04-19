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


def load_history() -> list[dict]:
    """Читает всю историю. Пропускает битые строки."""
    path = CHAT_HISTORY_FILE
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        log.warning(f"[chat_history] load failed: {e}")
        return []
    return out


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
