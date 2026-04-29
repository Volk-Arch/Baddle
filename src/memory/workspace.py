"""Workspace — рабочая память между генерацией и LTM (W14.1 Scope primitive).

Концепция: docs/workspace.md. Implementation план: planning/workspace-design.md.

Workspace это **scope-флаг на нодах графа**, не отдельный store. Источники
вызывают `add()` — создаётся нода через `record_action` с `scope="workspace"`
и `expires_at`. `select()` применяет convergence rule и возвращает idxs
к commit'у. `commit()` мутирует `scope="graph"`, убирает TTL — нода живёт
как обычная LTM.

В W14.1 — только primitive. Миграция callsites (W14.2-4), cross-processing
(W14.5), ночной integration cycle (W14.8+) — отдельные waves.
"""
import time
from typing import Optional

from ..graph_logic import _graph, graph_lock, record_action

# action_kind'ы которые считаются push-style — получают counter-wave penalty
# при r.user.mode == 'C' (counter-режим, юзер копит, не реактивен).
# user-action kinds (user_chat) и pull-инициируемые (assist_reply на user-msg) —
# не penalize.
PUSH_KINDS = {
    "alert",
    "observation_suggestion",
    "sync_seeking",
    "dmn_bridge",
    "scout",
    "brief_morning",
    "brief_weekly",
    "overnight_insight",
}

COUNTER_WAVE_PENALTY = 0.3

DEFAULT_TTL_SECONDS = 3600.0  # 1 час


def add(actor: str, action_kind: str, text: str,
        urgency: float = 0.5,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        accumulate: bool = True,
        dedup_key: Optional[str] = None,
        context: Optional[dict] = None,
        extras: Optional[dict] = None) -> int:
    """Добавить кандидата в workspace.

    Args:
        actor: 'baddle' | 'user'.
        action_kind: action taxonomy (sync_seeking / alert / brief_morning / ...).
        text: human-readable, для UI и LLM context.
        urgency: [0..1] приоритет в select(). Default 0.5.
        ttl_seconds: через сколько expire без commit. Default 3600.
        accumulate: True (default) — ждёт select cycle через budget.
                     False — preempt в select() (immediate broadcast).
        dedup_key: если задан, повторный add() с тем же key не создаёт
                    новую ноду — возвращает idx существующей pending workspace-ноды.
        context: snapshot состояния (см. record_action).
        extras: дополнительные top-level fields на ноде.

    Returns: node idx.
    """
    now = time.time()
    expires_at = now + float(ttl_seconds)

    if dedup_key:
        with graph_lock:
            for node in _graph["nodes"]:
                if (node.get("scope") == "workspace"
                        and node.get("dedup_key") == dedup_key
                        and (node.get("expires_at") or 0) > now):
                    return int(node["id"])

    ws_extras = dict(extras) if extras else {}
    ws_extras["urgency"] = float(urgency)
    ws_extras["accumulate"] = bool(accumulate)
    if dedup_key:
        ws_extras["dedup_key"] = dedup_key

    return record_action(
        actor=actor, action_kind=action_kind, text=text,
        context=context, extras=ws_extras,
        scope="workspace", expires_at=expires_at,
    )


def list_pending(now: Optional[float] = None) -> list[dict]:
    """Все workspace-ноды которые ещё не expired."""
    if now is None:
        now = time.time()
    with graph_lock:
        return [n for n in _graph["nodes"]
                if n.get("scope") == "workspace"
                and (n.get("expires_at") or 0) > now]


def _effective_urgency(node: dict, user_mode: str) -> float:
    """Urgency с counter-wave penalty при mode='C' для push-style kinds."""
    u = float(node.get("urgency", 0.5))
    if user_mode == "C" and node.get("action_kind") in PUSH_KINDS:
        u -= COUNTER_WAVE_PENALTY
    return u


def select(now: Optional[float] = None, max_emit: int = 1) -> list[int]:
    """Convergence rule → idxs к commit'у.

    1. Drop expired (now > expires_at).
    2. Immediate channel: accumulate=False всегда выходят (preempt budget).
    3. Accumulating channel: counter-wave penalty → urgency-sort → top-(max_emit).

    Не мутирует ноды — это работа commit().
    """
    if now is None:
        now = time.time()

    pending = list_pending(now)
    if not pending:
        return []

    try:
        from ..substrate.rgk import get_global_rgk
        user_mode = get_global_rgk().user.mode
    except Exception:
        user_mode = "R"

    immediate = [n for n in pending if not bool(n.get("accumulate", True))]
    accumulating = [n for n in pending if bool(n.get("accumulate", True))]

    accumulating.sort(
        key=lambda n: _effective_urgency(n, user_mode),
        reverse=True,
    )

    selected = [int(n["id"]) for n in immediate]
    selected.extend(int(n["id"]) for n in accumulating[:max_emit])
    return selected


def commit(node_indices: list[int]) -> int:
    """Промоут workspace-нод в LTM. Returns число действительно committed."""
    if not node_indices:
        return 0
    now = time.time()
    count = 0
    with graph_lock:
        nodes = _graph["nodes"]
        for idx in node_indices:
            if not (0 <= idx < len(nodes)):
                continue
            node = nodes[idx]
            if node.get("scope") != "workspace":
                continue
            node["scope"] = "graph"
            node["expires_at"] = None
            node["committed_at"] = now
            count += 1
    return count


def archive_expired(now: Optional[float] = None) -> int:
    """Workspace-ноды с истёкшим TTL → scope='archived'.

    Сами не удаляются — для post-hoc analysis «что не дошло до broadcast».
    """
    if now is None:
        now = time.time()
    count = 0
    with graph_lock:
        for node in _graph["nodes"]:
            if (node.get("scope") == "workspace"
                    and (node.get("expires_at") or 0) <= now):
                node["scope"] = "archived"
                count += 1
    return count
