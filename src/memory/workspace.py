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

# W14.5 cross-processing: при N pending accumulate=True ноды одного
# action_kind — synthesize в 1 кандидата с references. 3 — мягкая граница
# (3 sync_seeking за час = паттерн «юзер молчит вечерами», не случайность).
THRESHOLD_SIMILAR_CANDIDATES = 3


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

    new_idx = record_action(
        actor=actor, action_kind=action_kind, text=text,
        context=context, extras=ws_extras,
        scope="workspace", expires_at=expires_at,
    )

    # W14.5 cross-processing trigger: только для accumulate=True источников.
    # accumulate=False (chat msgs/alerts/briefings) не triggers — они проходят
    # через record_committed → immediate commit, не накапливаются.
    if accumulate:
        _maybe_cross_process(action_kind)

    return new_idx


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


def record_committed(actor: str, action_kind: str, text: str,
                      urgency: float = 0.5,
                      accumulate: bool = False,
                      ttl_seconds: float = DEFAULT_TTL_SECONDS,
                      dedup_key: Optional[str] = None,
                      context: Optional[dict] = None,
                      extras: Optional[dict] = None) -> Optional[int]:
    """add() + immediate commit, swallow exceptions, log on failure.

    Один helper для всех explicit-emit источников (chat msgs, alerts,
    briefings) — pattern «нода живёт в workspace миллисекунды, потом
    сразу promote в LTM». Returns idx или None при failure.

    accumulate=False по умолчанию (chat/alert/brief — explicit publication).
    accumulate=True возможен — нода всё равно сразу commit'ится, флаг
    остаётся в metadata для downstream cross-processing (W14.5).
    """
    import logging
    try:
        idx = add(
            actor=actor, action_kind=action_kind, text=text,
            urgency=urgency, ttl_seconds=ttl_seconds,
            accumulate=accumulate, dedup_key=dedup_key,
            context=context, extras=extras,
        )
        commit([idx])
        return idx
    except Exception as e:
        logging.getLogger(__name__).debug(
            f"[workspace] record_committed {actor}/{action_kind} failed: {e}")
        return None


def synthesize_similar(node_idxs: list[int]) -> Optional[int]:
    """N pending workspace-нод → 1 синтезированный кандидат (W14.5).

    Создаёт committed `{action_kind}_synthesized` ноду с references на
    исходные через `synthesized_from`. Исходные mark'аются `superseded_by`
    — остаются в workspace до expire (action timeline trace), но
    исключены из дальнейшего cross-processing.

    NB: text-aggregation сейчас простая конкатенация. LLM-based synthesis
    (через pump.scout / consolidation.collapse / SmartDC) — improvement
    в W14.5+. Текущая версия даёт infrastructure без LLM hops в hot path.

    Returns: idx синтезированной ноды или None если input пуст.
    """
    if len(node_idxs) < 2:
        return None

    with graph_lock:
        nodes = [_graph["nodes"][i] for i in node_idxs
                 if 0 <= i < len(_graph["nodes"])]
        if len(nodes) < 2:
            return None

    aggregated = "; ".join((n.get("text") or "") for n in nodes)[:300]
    max_urgency = max(float(n.get("urgency") or 0.5) for n in nodes)
    action_kind = nodes[0].get("action_kind") or "unknown"

    # Synthesized publish напрямую в LTM (record_committed): synthesis = explicit
    # statement системы, не accumulating кандидат. Sources остаются в workspace
    # как trace.
    new_idx = record_committed(
        actor="baddle",
        action_kind=f"{action_kind}_synthesized",
        text=aggregated,
        urgency=min(1.0, max_urgency + 0.1),
        accumulate=False,
        extras={
            "synthesized_from": [int(n["id"]) for n in nodes],
            "synthesis_count": len(nodes),
        },
    )

    # Mark contributing sources
    if new_idx is not None:
        with graph_lock:
            for n in nodes:
                n["superseded_by"] = int(new_idx)

    return new_idx


def _maybe_cross_process(action_kind: str) -> Optional[int]:
    """Если N pending accumulate=True ноды одного action_kind ≥ threshold,
    запустить synthesize_similar. Loop protection через `synthesized_from`
    + `superseded_by` filter.
    """
    candidates = [
        n for n in list_pending()
        if n.get("action_kind") == action_kind
        and bool(n.get("accumulate", True))
        and "synthesized_from" not in n  # synthesized сами не triggers
        and "superseded_by" not in n     # уже включены в synthesis
    ]
    if len(candidates) < THRESHOLD_SIMILAR_CANDIDATES:
        return None
    return synthesize_similar([int(c["id"]) for c in candidates])


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
