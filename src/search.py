"""Cross-workspace semantic search.

Один вход для поиска по всем workspace'ам сразу. Используется:
  1. Поисковое окошко в Baddle UI — пользователь пишет текст, видит
     top-K совпадений по графам всех workspace'ов.
  2. Semantic navigation — открыта нода в Lab, показываем «эта идея
     встречается ещё в: work/personal/research» через близкие embeddings
     + явно сохранённые cross_edges (из `workspaces/index.json`).

DMN-автоматический путь уже пишет cross_edges фоново (cognitive_loop
`_check_dmn_cross_graph` раз в 60 мин). Этот модуль — **синхронный путь**
по запросу, для live-поиска.
"""
from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_ROOT = Path(__file__).parent.parent
_GRAPHS_DIR = _ROOT / "graphs"
_WS_INDEX = _ROOT / "workspaces" / "index.json"


# ── Helpers ──────────────────────────────────────────────────────────────

def _cosine(a, b) -> float:
    if not a or not b:
        return 0.0
    try:
        import math
        num = 0.0
        na = 0.0
        nb = 0.0
        for x, y in zip(a, b):
            num += x * y
            na += x * x
            nb += y * y
        denom = (math.sqrt(na) * math.sqrt(nb)) + 1e-9
        return float(num / denom)
    except Exception:
        return 0.0


def _load_graph(ws_id: str) -> Optional[dict]:
    gf = _GRAPHS_DIR / ws_id / "graph.json"
    if not gf.exists():
        return None
    try:
        return json.loads(gf.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning(f"[search] load {ws_id} failed: {e}")
        return None


def _list_workspaces() -> list[str]:
    """Все workspace'ы из index.json (или из graphs/ если index не существует)."""
    if _WS_INDEX.exists():
        try:
            idx = json.loads(_WS_INDEX.read_text(encoding="utf-8"))
            return list((idx.get("workspaces") or {}).keys())
        except Exception:
            pass
    if _GRAPHS_DIR.exists():
        return [d.name for d in _GRAPHS_DIR.iterdir() if d.is_dir()]
    return []


def _node_has_embedding(node: dict) -> bool:
    emb = node.get("embedding")
    return bool(emb) and isinstance(emb, list) and len(emb) > 0


def _ensure_embeddings_for_graph(ws_id: str, texts_idx: list[tuple[int, str]]):
    """Сгенерить embeddings для нод в ws_id у которых их нет.
    Сохраняет обратно в graph.json. Вызывается лениво из search'а —
    первый поиск после seed может занять 5-15 секунд, далее мгновенно.
    """
    if not texts_idx:
        return
    try:
        from .api_backend import api_get_embedding
    except Exception as e:
        log.warning(f"[search] api_backend import failed: {e}")
        return
    g = _load_graph(ws_id)
    if not g:
        return
    nodes_by_id = {n.get("id"): n for n in g.get("nodes", [])}
    changed = False
    for idx, text in texts_idx:
        node = nodes_by_id.get(idx)
        if not node or _node_has_embedding(node):
            continue
        try:
            emb = api_get_embedding(text)
            if emb:
                node["embedding"] = emb
                changed = True
        except Exception as e:
            log.debug(f"[search] embed {ws_id}#{idx} failed: {e}")
    if changed:
        try:
            gf = _GRAPHS_DIR / ws_id / "graph.json"
            gf.write_text(json.dumps(g, ensure_ascii=False, indent=2),
                          encoding="utf-8")
            log.info(f"[search] embeddings cached for {ws_id}")
        except Exception as e:
            log.warning(f"[search] save {ws_id} failed: {e}")


# ── Public API ───────────────────────────────────────────────────────────

def search_across_workspaces(query: str, top_k: int = 12,
                              min_similarity: float = 0.35,
                              ensure_embeddings: bool = True) -> dict:
    """Синхронный поиск: text → embedding → cosine по всем workspace'ам.

    Возвращает:
      {
        "query": str,
        "hits": [{ws_id, ws_title, node_idx, text, type, similarity}, ...]
                 отсортировано по убыванию similarity,
        "stats": {workspaces_scanned, nodes_total, nodes_with_embedding}
      }

    `ensure_embeddings=True` — если у нод нет embeddings, сгенерит их
    на лету (может быть долго при первом поиске). После — кеш в graph.json.
    """
    from .api_backend import api_get_embedding

    query = (query or "").strip()
    if not query:
        return {"query": "", "hits": [], "stats": {}}

    try:
        query_emb = api_get_embedding(query)
    except Exception as e:
        log.warning(f"[search] query embedding failed: {e}")
        return {"query": query, "hits": [], "error": str(e), "stats": {}}
    if not query_emb:
        return {"query": query, "hits": [],
                "error": "embedding model unavailable", "stats": {}}

    ws_ids = _list_workspaces()
    ws_titles = {}
    if _WS_INDEX.exists():
        try:
            idx = json.loads(_WS_INDEX.read_text(encoding="utf-8"))
            for wid, info in (idx.get("workspaces") or {}).items():
                ws_titles[wid] = info.get("title") or wid
        except Exception:
            pass

    hits: list[dict] = []
    total_nodes = 0
    embedded_nodes = 0

    for ws_id in ws_ids:
        g = _load_graph(ws_id)
        if not g:
            continue
        nodes = g.get("nodes") or []
        total_nodes += len(nodes)

        # Ленивая генерация embeddings (первый поиск после seed)
        if ensure_embeddings:
            missing = [(n.get("id"), n.get("text") or "")
                       for n in nodes
                       if not _node_has_embedding(n) and (n.get("text") or "").strip()]
            if missing:
                _ensure_embeddings_for_graph(ws_id, missing)
                g = _load_graph(ws_id)  # перечитать после save
                nodes = (g or {}).get("nodes") or []

        for n in nodes:
            if not _node_has_embedding(n):
                continue
            embedded_nodes += 1
            sim = _cosine(query_emb, n["embedding"])
            if sim < min_similarity:
                continue
            hits.append({
                "ws_id": ws_id,
                "ws_title": ws_titles.get(ws_id, ws_id),
                "node_idx": n.get("id"),
                "text": (n.get("text") or "")[:200],
                "type": n.get("type") or "thought",
                "similarity": round(sim, 3),
                "confidence": n.get("confidence"),
            })

    hits.sort(key=lambda h: -h["similarity"])
    return {
        "query": query,
        "hits": hits[:top_k],
        "stats": {
            "workspaces_scanned": len(ws_ids),
            "nodes_total": total_nodes,
            "nodes_with_embedding": embedded_nodes,
            "hits_above_threshold": len(hits),
        },
    }


def node_related_across_workspaces(ws_id: str, node_idx: int,
                                     top_k: int = 6,
                                     min_similarity: float = 0.6) -> dict:
    """Для открытой ноды в Lab — показать «связано в других workspace'ах».

    Два источника:
      1. Явные `cross_edges` из `workspaces/index.json` — DMN уже нашёл.
      2. Semantic similarity — cosine embedding этой ноды vs все другие ws.

    Возвращает:
      {
        "source": {"ws_id", "node_idx", "text"},
        "explicit": [cross_edge'ы где участвует эта нода],
        "semantic": [top-K других нод с высоким cosine]
      }
    """
    g = _load_graph(ws_id)
    if not g:
        return {"error": f"workspace '{ws_id}' not found"}
    src_node = next((n for n in g.get("nodes") or []
                     if n.get("id") == node_idx), None)
    if not src_node:
        return {"error": f"node #{node_idx} not found in '{ws_id}'"}

    result = {
        "source": {"ws_id": ws_id, "node_idx": node_idx,
                    "text": (src_node.get("text") or "")[:200]},
        "explicit": [],
        "semantic": [],
    }

    # 1. Explicit cross_edges
    if _WS_INDEX.exists():
        try:
            idx = json.loads(_WS_INDEX.read_text(encoding="utf-8"))
            for e in (idx.get("cross_edges") or []):
                if (e.get("from_graph") == ws_id and e.get("from_node") == node_idx) \
                   or (e.get("to_graph") == ws_id and e.get("to_node") == node_idx):
                    # Получить текст с другой стороны
                    other_ws = e["to_graph"] if e["from_graph"] == ws_id else e["from_graph"]
                    other_node = e["to_node"] if e["from_graph"] == ws_id else e["from_node"]
                    og = _load_graph(other_ws)
                    other_text = ""
                    if og:
                        on = next((n for n in og.get("nodes") or []
                                    if n.get("id") == other_node), None)
                        if on:
                            other_text = (on.get("text") or "")[:160]
                    result["explicit"].append({
                        "ws_id": other_ws,
                        "node_idx": other_node,
                        "text": other_text,
                        "d": e.get("d"),
                        "ts": e.get("ts"),
                    })
        except Exception as e:
            log.debug(f"[search] cross_edges read failed: {e}")

    # 2. Semantic — cosine vs ноды из ДРУГИХ workspace'ов
    if _node_has_embedding(src_node):
        src_emb = src_node["embedding"]
        for other_ws in _list_workspaces():
            if other_ws == ws_id:
                continue
            og = _load_graph(other_ws)
            if not og:
                continue
            for n in (og.get("nodes") or []):
                if not _node_has_embedding(n):
                    continue
                sim = _cosine(src_emb, n["embedding"])
                if sim < min_similarity:
                    continue
                result["semantic"].append({
                    "ws_id": other_ws,
                    "node_idx": n.get("id"),
                    "text": (n.get("text") or "")[:160],
                    "type": n.get("type"),
                    "similarity": round(sim, 3),
                })
        result["semantic"].sort(key=lambda x: -x["similarity"])
        result["semantic"] = result["semantic"][:top_k]

    return result
