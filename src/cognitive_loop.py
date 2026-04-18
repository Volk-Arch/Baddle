"""Baddle Cognitive Loop — один когнитивный контур с NE-бюджетом.

Объединяет бывший Watchdog + точку входа /graph/tick в одну структуру.

Фоновая активность разнесена по временным шкалам:
  • DMN 10 min       — continuous pump (не сохраняет, только предлагает)
  • State-walk 20 min — эпизодическая память через state_graph similarity
  • Night cycle 24 h — единый ночной проход:
      1. Scout pump+save (persistent bridge)
      2. REM emotional (эпизоды с высоким |rpe| → pump их content)
      3. REM creative (close-in-embedding + far-in-path → manual_link)
      4. Consolidation (прунинг + архив state_graph)
Foreground вход:
  • tick_foreground() — /graph/tick ping, координация через shared timestamp

NE-бюджет:
  norepinephrine > 0.55          → юзер активен, фон на паузе
  Последний foreground < 30s     → недавно была работа, фон не лезет
  PROTECTIVE_FREEZE              → только decay, никаких новых действий

Design: poll-based, non-blocking. UI дёргает /assist/alerts чтобы увидеть
накопленные инсайты.
"""
import threading
import time
import logging
import random
from typing import Optional, Tuple

from .graph_logic import _graph
from .hrv_manager import get_manager as get_hrv_manager

log = logging.getLogger(__name__)


def _find_distant_pair(nodes: list) -> Optional[Tuple[int, int]]:
    """Intrinsic pull — dopamine-modulated curiosity вместо случайного pivot.

    score(a, b) = novelty(a, b) · relevance(a) · relevance(b)

        novelty(a, b)  = distinct(emb_a, emb_b) — дистанция между идеями
        relevance(n)   = recency(n) · uncertainty(n) — недавно тронутое +
                         непроверенное (confidence около 0.5)

    Выбор пары: softmax по score с температурой T = 1.1 − dopamine.
    Высокий dopamine → резкий argmax (любопытство ведёт в самую новую связь).
    Низкий dopamine → мягкое распределение (ангедония, выбор ближе к рандому).

    Ограничение O(K²): берём top-K по relevance (K=20), пары только среди них.
    """
    from .main import distinct
    from .horizon import get_global_state
    from datetime import datetime, timezone
    import math
    import numpy as np

    # Filter candidates: active hypothesis/thought nodes with embeddings
    candidates = []
    for i, n in enumerate(nodes):
        if n.get("depth", 0) < 0:
            continue
        if n.get("type") not in ("hypothesis", "thought"):
            continue
        if not n.get("embedding"):
            continue
        candidates.append(i)

    if len(candidates) < 2:
        return None

    def _recency(ts_iso) -> float:
        if not ts_iso:
            return 0.5
        try:
            ts = datetime.fromisoformat(str(ts_iso).replace("Z", "+00:00"))
            hours = (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
            return float(math.exp(-max(0.0, hours) / 24.0))   # e-decay, half-life ≈ 17ч
        except Exception:
            return 0.5

    def _relevance(node) -> float:
        # Недавно тронутая нода + неочевидная (confidence около 0.5 = макс любопытство)
        r = _recency(node.get("last_accessed") or node.get("created_at"))
        conf = node.get("confidence", 0.5)
        uncertainty = 1.0 - abs(conf - 0.5) * 2.0   # пик 1.0 при conf=0.5
        return 0.5 * r + 0.5 * uncertainty

    relevance = {i: _relevance(nodes[i]) for i in candidates}

    # Top-K по relevance — O(n log n) вместо полного O(n²) перебора
    K = min(20, len(candidates))
    top_k = sorted(candidates, key=lambda i: relevance[i], reverse=True)[:K]

    # Compute pair scores (O(K²))
    scores = []
    for ii in range(len(top_k)):
        for jj in range(ii + 1, len(top_k)):
            i, j = top_k[ii], top_k[jj]
            emb_a = np.array(nodes[i]["embedding"], dtype=np.float32)
            emb_b = np.array(nodes[j]["embedding"], dtype=np.float32)
            if emb_a.size == 0 or emb_b.size == 0:
                continue
            novelty = float(distinct(emb_a, emb_b))
            score = novelty * relevance[i] * relevance[j]
            scores.append((i, j, score))

    if not scores:
        return None

    # Dopamine → sampling temperature
    try:
        dopamine = float(get_global_state().neuro.dopamine)
    except Exception:
        dopamine = 0.5
    T = max(0.1, 1.1 - dopamine)   # DA=0 → T=1.1 (flat); DA=1 → T=0.1 (sharp)

    score_vec = np.asarray([s[2] for s in scores], dtype=np.float64)
    score_vec = score_vec / T
    score_vec -= score_vec.max()                 # числовая стабильность softmax
    probs = np.exp(score_vec)
    total = float(probs.sum())
    if total <= 0 or not np.isfinite(total):
        pick = random.randrange(len(scores))
    else:
        probs /= total
        pick = int(np.random.choice(len(scores), p=probs))
    return (scores[pick][0], scores[pick][1])


class CognitiveLoop:
    """Singleton: один фоновый контур + foreground tick entry."""

    # Интервалы в секундах
    DMN_INTERVAL = 600                # 10 минут между DMN continuous (content pump)
    STATE_WALK_INTERVAL = 20 * 60     # 20 минут между эпизодическими запросами к state_graph
    NIGHT_CYCLE_INTERVAL = 24 * 3600  # раз в сутки: Scout + REM + Consolidation единым блоком
    TICK_INTERVAL = 60                # частота бэкграунд-проверок
    FOREGROUND_COOLDOWN = 30          # после юзер-тика DMN ждёт столько секунд

    # NE gating
    NE_BASELINE = 0.3            # baseline к которому дрейфует NE
    NE_HIGH_GATE = 0.55          # выше — юзер активен, DMN не лезет
    NE_DECAY_PER_TICK = 0.05     # EMA decay в сторону baseline

    # REM параметры
    REM_RPE_THRESHOLD = 0.15          # |rpe| выше → эпизод эмоционально насыщен
    REM_EMO_MAX_PUMPS = 3             # максимум пампов эмоциональной фазы за ночь
    REM_CREATIVE_DIST_MAX = 0.2       # embedding близость для creative-merge
    REM_CREATIVE_PATH_MIN = 3         # BFS-дистанция чтобы считаться «далёкими»
    REM_CREATIVE_MAX_MERGES = 3       # сколько парадоксальных пар линковать за ночь

    def __init__(self):
        self.is_running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_dmn = 0.0
        self._last_state_walk = 0.0
        self._last_night_cycle = 0.0
        self._last_foreground_tick = 0.0
        self._alerts_queue: list = []
        self._lock = threading.Lock()

    # ── Lifecycle ──────────────────────────────────────────────────────

    def start(self):
        if self.is_running:
            return
        self.is_running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="cognitive_loop")
        self._thread.start()
        log.info("[cognitive_loop] started")

    def stop(self):
        self.is_running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)

    # ── Foreground entry (юзер-инициированный тик) ─────────────────────

    def tick_foreground(self,
                        threshold: float = 0.91,
                        sim_mode: str = "embedding",
                        stable_threshold: float = 0.8,
                        force_collapse: bool = False,
                        max_meta: int = 2,
                        min_hyp: int = 5) -> dict:
        """Юзер дёрнул /graph/tick → единый tick_emergent на текущем графе.

        Записываем timestamp в shared state чтобы background DMN не полез
        следующие FOREGROUND_COOLDOWN секунд.
        """
        from .tick_nand import tick_emergent
        from .graph_logic import _compute_edges

        self._last_foreground_tick = time.time()
        nodes = _graph["nodes"]
        edges = _compute_edges(nodes, threshold, sim_mode)
        return tick_emergent(
            nodes, edges, _graph,
            threshold=threshold,
            stable_threshold=stable_threshold,
            force_collapse=force_collapse,
            max_meta=max_meta,
            min_hyp=min_hyp,
            user_initiated=True,
        )

    # ── Background loop ────────────────────────────────────────────────

    def _loop(self):
        """Main loop body. Каждая итерация:

        1. NE decay в сторону baseline (бездействие успокаивает)
        2. Гейт: не FREEZE + NE < 0.55 + последний foreground > cooldown
        3. Если прошёл интервал Scout / DMN — запустить pump
        4. HRV alerts всегда
        5. Сон tick_interval (масштабируется NE)
        """
        from .horizon import get_global_state, PROTECTIVE_FREEZE

        while self.is_running and not self._stop_event.is_set():
            try:
                state = get_global_state()

                # 1. NE homeostasis
                ne = state.neuro.norepinephrine
                state.neuro.norepinephrine = (
                    ne * (1 - self.NE_DECAY_PER_TICK)
                    + self.NE_BASELINE * self.NE_DECAY_PER_TICK
                )

                # 2. Gate
                idle_enough = time.time() - self._last_foreground_tick >= self.FOREGROUND_COOLDOWN
                ne_quiet = state.neuro.norepinephrine < self.NE_HIGH_GATE
                not_frozen = state.state != PROTECTIVE_FREEZE

                if not_frozen and ne_quiet and idle_enough:
                    self._check_dmn_continuous()
                    self._check_state_walk()
                    self._check_night_cycle()

                # 3. HRV alerts всегда
                self._check_hrv_alerts()
            except Exception as e:
                log.warning(f"[cognitive_loop] error: {e}")

            # 4. Adaptive sleep
            try:
                ne = get_global_state().neuro.norepinephrine
                scaled = self.TICK_INTERVAL * max(0.5, 1.2 - ne)
            except Exception:
                scaled = self.TICK_INTERVAL
            self._stop_event.wait(scaled)

    # ── Night cycle: Scout + REM emotional + REM creative + Consolidation ──

    def _check_night_cycle(self):
        """Единый 24ч ночной цикл. Заменяет три параллельных механизма.

        Последовательность (slow-wave → REM → cleanup):
          1. Scout pump+save (был SCOUT_INTERVAL=3h, теперь раз в сутки)
          2. REM emotional — state_nodes с |recent_rpe| > threshold
             прогоняются через Pump между парами content_touched
          3. REM creative — content-пары близкие в embedding + далёкие
             в path-графе получают manual_link (парадоксальные связи)
          4. Consolidation — прунинг слабых + архив state_graph (было 24h)
        """
        now = time.time()
        if now - self._last_night_cycle < self.NIGHT_CYCLE_INTERVAL:
            return
        self._last_night_cycle = now
        log.info("[cognitive_loop] night cycle starting")

        summary: dict = {}

        # Phase 1: Scout pump+save
        if len(_graph.get("nodes", [])) >= 5:
            bridge = self._run_pump_bridge(max_iterations=2, save=True)
            summary["scout"] = {
                "bridge_saved": bridge is not None,
                "bridge_text": (bridge.get("text", "") if bridge else "")[:60],
            }
        else:
            summary["scout"] = {"skipped": "graph_too_small"}

        # Phase 2: REM emotional
        summary["rem_emotional"] = self._rem_emotional()

        # Phase 3: REM creative
        summary["rem_creative"] = self._rem_creative()

        # Phase 4: Consolidation
        try:
            from .consolidation import consolidate_all
            res = consolidate_all()
            summary["consolidation"] = {
                "pruned": res.get("content", {}).get("removed", 0),
                "archived": res.get("state", {}).get("archived", 0),
            }
        except Exception as e:
            summary["consolidation"] = {"error": str(e)}

        s = summary
        text = (
            f"Ночной цикл: "
            f"Scout {'+мост' if s['scout'].get('bridge_saved') else 'пропуск'} · "
            f"REM эмо pump {s['rem_emotional'].get('pumped', 0)} · "
            f"REM merge {s['rem_creative'].get('merged', 0)} · "
            f"прунинг {s['consolidation'].get('pruned', 0)} / "
            f"архив {s['consolidation'].get('archived', 0)}"
        )
        self._add_alert({
            "type": "night_cycle", "severity": "info",
            "text": text, "text_en": text,
            "summary": summary,
        }, dedupe=True)
        log.info(f"[cognitive_loop] night cycle done: {text}")

    # ── REM emotional: прогон эпизодов с высоким |rpe| через Pump ──

    def _rem_emotional(self) -> dict:
        """Находит state_nodes с |recent_rpe| > REM_RPE_THRESHOLD за последние 100
        записей, берёт их content_touched, запускает Pump между парой.

        Эффект: эмоционально-насыщенные эпизоды получают новую переработку —
        рождаются новые связи именно поверх тех нод которые удивили.
        """
        from .state_graph import get_state_graph
        from .pump_logic import pump

        try:
            entries = get_state_graph().read_all()
        except Exception as e:
            return {"pumped": 0, "error": f"read_failed: {e}"}

        candidates: list[tuple[float, list]] = []
        seen_pair: set = set()
        for entry in entries[-100:]:
            snap = entry.get("state_snapshot") or {}
            neuro = snap.get("neurochem") or {}
            rpe = neuro.get("recent_rpe")
            if not isinstance(rpe, (int, float)):
                continue
            if abs(rpe) < self.REM_RPE_THRESHOLD:
                continue
            touched = entry.get("content_touched") or []
            if len(touched) < 2:
                continue
            sig = tuple(sorted(touched[:2]))
            if sig in seen_pair:
                continue
            seen_pair.add(sig)
            candidates.append((abs(float(rpe)), list(touched)))

        if not candidates:
            return {"pumped": 0, "candidates": 0}

        # Самые неожиданные эпизоды сначала
        candidates.sort(key=lambda x: -x[0])
        nodes = _graph.get("nodes", [])
        pumped = 0
        for _, touched in candidates[:self.REM_EMO_MAX_PUMPS]:
            valid = [
                t for t in touched
                if 0 <= t < len(nodes)
                and nodes[t].get("embedding")
                and nodes[t].get("type") in ("hypothesis", "thought")
            ]
            if len(valid) < 2:
                continue
            try:
                result = pump(valid[0], valid[1], max_iterations=1, lang="ru")
                if result and not result.get("error") and result.get("all_bridges"):
                    pumped += 1
            except Exception as e:
                log.debug(f"[rem_emotional] pump failed: {e}")
        return {"pumped": pumped, "candidates": len(candidates)}

    # ── REM creative: пары близкие в embedding + далёкие в пути графа ──

    def _rem_creative(self) -> dict:
        """Находит «далёких но близких» — content ноды с distinct(emb) < 0.2
        при BFS-расстоянии по графу ≥ 3, ставит manual_link между ними.

        Это **парадоксальные связи**: ноды думают похожее но не связаны
        путём. Creative merge — ночной мостик между ними. Без LLM синтеза
        (дорого) — просто manual_link + alert; collapse юзер делает явно.
        """
        from .main import distinct
        from collections import defaultdict, deque
        import numpy as np

        nodes = _graph.get("nodes", [])
        if len(nodes) < 6:
            return {"merged": 0, "reason": "graph_too_small"}

        # Adjacency из similarity-edges + directed
        adj = defaultdict(set)
        from .graph_logic import _compute_edges
        try:
            sim_edges = _compute_edges(nodes, threshold=0.91, sim_mode="embedding")
        except Exception as e:
            return {"merged": 0, "error": f"edges_failed: {e}"}
        for e in sim_edges:
            adj[e["from"]].add(e["to"])
            adj[e["to"]].add(e["from"])
        for pair in _graph.get("edges", {}).get("directed", []) or []:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                a, b = pair
                adj[a].add(b); adj[b].add(a)

        def path_dist(start: int, goal: int, cap: int = 6) -> int:
            if start == goal:
                return 0
            visited = {start}
            queue = deque([(start, 0)])
            while queue:
                node, d = queue.popleft()
                if d >= cap:
                    continue
                for n in adj[node]:
                    if n in visited:
                        continue
                    if n == goal:
                        return d + 1
                    visited.add(n)
                    queue.append((n, d + 1))
            return cap

        active = [
            (i, n) for i, n in enumerate(nodes)
            if n.get("depth", 0) >= 0
            and n.get("type") in ("hypothesis", "thought")
            and n.get("embedding")
        ]

        candidates: list[tuple[float, int, int, int]] = []
        for ii in range(len(active)):
            for jj in range(ii + 1, len(active)):
                i, ni = active[ii]
                j, nj = active[jj]
                va = np.asarray(ni["embedding"], dtype=np.float32)
                vb = np.asarray(nj["embedding"], dtype=np.float32)
                d_emb = float(distinct(va, vb))
                if d_emb > self.REM_CREATIVE_DIST_MAX:
                    continue
                pd = path_dist(i, j)
                if pd < self.REM_CREATIVE_PATH_MIN:
                    continue
                candidates.append((d_emb, pd, i, j))

        if not candidates:
            return {"merged": 0, "candidates": 0}

        # Самые парадоксальные сначала: близкие в emb, далёкие в path
        candidates.sort(key=lambda x: (x[0], -x[1]))

        merged = 0
        insights: list[dict] = []
        manual_links = _graph["edges"].setdefault("manual_links", [])
        for d_emb, pd, i, j in candidates[:self.REM_CREATIVE_MAX_MERGES]:
            pair = [min(i, j), max(i, j)]
            if pair in manual_links:
                continue
            manual_links.append(pair)
            merged += 1
            insights.append({
                "node_a": i, "text_a": nodes[i].get("text", "")[:60],
                "node_b": j, "text_b": nodes[j].get("text", "")[:60],
                "d_emb": round(d_emb, 3), "path_dist": pd,
            })

        return {"merged": merged, "candidates": len(candidates),
                "insights": insights}

    # ── DMN continuous (10 min: pump attempt, don't save) ───────────────

    def _check_dmn_continuous(self):
        now = time.time()
        if now - self._last_dmn < self.DMN_INTERVAL:
            return
        if len(_graph.get("nodes", [])) < 4:
            return
        self._last_dmn = now

        bridge = self._run_pump_bridge(max_iterations=1, save=False)
        if bridge and bridge.get("quality", 0) > 0.5:
            self._add_alert({
                "type": "dmn_bridge",
                "severity": "info",
                "text": f"DMN-инсайт: {bridge['text'][:80]} (quality {bridge.get('quality', 0):.0%})",
                "text_en": f"DMN insight: {bridge['text'][:80]} (quality {bridge.get('quality', 0):.0%})",
                "bridge": bridge,
            }, dedupe=True)

    def _run_pump_bridge(self, max_iterations: int = 2, save: bool = False) -> Optional[dict]:
        """Call pump between two most distant nodes. Optionally persist bridge.

        save=True → новый node + связи с обоими источниками (Scout path).
        save=False → только возвращаем bridge-дикт (DMN suggest).
        """
        from .graph_logic import _add_node, _ensure_embeddings
        from .pump_logic import pump

        nodes = _graph.get("nodes", [])
        if len(nodes) < 4:
            return None

        try:
            texts = [n.get("text", "") for n in nodes]
            _ensure_embeddings(texts)
        except Exception as e:
            log.warning(f"[cognitive_loop] embeddings failed: {e}")
            return None

        pair = _find_distant_pair(nodes)
        if pair is None:
            return None

        idx_a, idx_b = pair
        log.info(f"[cognitive_loop] Pump #{idx_a} <-> #{idx_b}")

        try:
            result = pump(idx_a, idx_b, max_iterations=max_iterations, lang="ru")
        except Exception as e:
            log.warning(f"[cognitive_loop] pump failed: {e}")
            return None

        if result.get("error"):
            log.info(f"[cognitive_loop] pump error: {result['error']}")
            return None

        bridges = result.get("all_bridges", [])
        if not bridges:
            return None
        best = bridges[0]

        # Feed back to neurochem: хороший мост = низкое d (новизна подтверждена)
        try:
            from .horizon import get_global_state
            quality = best.get("quality", 0.0)
            get_global_state().update_neurochem(d=(1.0 - quality))
        except Exception as e:
            log.debug(f"[cognitive_loop] neurochem feedback failed: {e}")

        if save:
            try:
                new_idx = _add_node(
                    best["text"],
                    depth=0, topic="",
                    node_type="hypothesis",
                    confidence=min(0.9, max(0.3, best.get("quality", 0.5))),
                )
                directed = _graph["edges"].setdefault("directed", [])
                directed.append([idx_a, new_idx])
                directed.append([idx_b, new_idx])
                manual_links = _graph["edges"].setdefault("manual_links", [])
                for other in (idx_a, idx_b):
                    pair_link = [min(new_idx, other), max(new_idx, other)]
                    if pair_link not in manual_links:
                        manual_links.append(pair_link)
                best["saved_idx"] = new_idx
                best["source_a"] = idx_a
                best["source_b"] = idx_b
            except Exception as e:
                log.warning(f"[cognitive_loop] bridge save failed: {e}")

        return best

    # ── State walk (DMN на state-графе: ищем похожие моменты из прошлого) ──

    def _build_current_state_signature(self) -> str:
        """Текст-сигнатура текущего момента для embedding запроса.

        Формат зеркалит `StateGraph._compute_embedding_text` — чтобы
        сравнение current vs past было эквивалентным.
        """
        from .horizon import get_global_state
        from .graph_logic import _graph
        cs = get_global_state()
        neuro = cs.neuro
        bits = [f"state:{cs.state}"]
        bits.append(f"S={neuro.serotonin:.2f} NE={neuro.norepinephrine:.2f} "
                    f"DA={neuro.dopamine:.2f}")
        bits.append(cs.state_origin_hint or "1_rest")
        # Topic / goal text если есть
        topic = (_graph.get("meta") or {}).get("topic", "")
        if topic:
            bits.append(f"topic: {topic[:80]}")
        for n in _graph.get("nodes", []):
            if n.get("type") == "goal" and n.get("depth", 0) >= 0:
                bits.append(f"goal: {n.get('text', '')[:80]}")
                break
        return " | ".join(bits)

    def _check_state_walk(self):
        """DMN по state-графу: похожие моменты из прошлого → эпизодический alert.

        1. Прогреваем embedding-кэш для хвоста (≤30 entries) — амортизация.
        2. Берём embedding текущей сигнатуры.
        3. query_similar(k=3), фильтруем < 1 час (тривиально-свежие).
        4. Если топ-match достаточно близкий — surface as alert.
        """
        now = time.time()
        if now - self._last_state_walk < self.STATE_WALK_INTERVAL:
            return

        from .state_graph import get_state_graph
        sg = get_state_graph()
        if sg.count() < 10:
            return  # слишком мало истории
        self._last_state_walk = now

        # Прогрев embedding-кэша для tail (<=30 последних)
        try:
            for entry in sg.tail(30):
                sg.ensure_embedding(entry)
        except Exception as e:
            log.debug(f"[state_walk] warm embeddings failed: {e}")

        # Embedding текущего момента
        try:
            from .api_backend import api_get_embedding
            sig = self._build_current_state_signature()
            query_emb = api_get_embedding(sig)
            if not query_emb:
                return
        except Exception as e:
            log.warning(f"[state_walk] query embedding failed: {e}")
            return

        try:
            similar = sg.query_similar(query_emb, k=3, exclude_recent=3)
        except Exception as e:
            log.warning(f"[state_walk] query_similar failed: {e}")
            return
        if not similar:
            return

        # Фильтр: не всплывать если лучший match моложе часа (тривиально близко)
        from datetime import datetime, timezone
        now_utc = datetime.now(timezone.utc)
        best = None
        for entry in similar:
            ts_iso = entry.get("timestamp")
            if not ts_iso:
                continue
            try:
                ts = datetime.fromisoformat(str(ts_iso).replace("Z", "+00:00"))
                if (now_utc - ts).total_seconds() < 3600:
                    continue
            except Exception:
                pass
            best = entry
            break
        if best is None:
            return

        ts_disp = str(best.get("timestamp", "?"))[:10]
        action = best.get("action", "?")
        reason = (best.get("reason") or "")[:100]
        self._add_alert({
            "type": "state_walk",
            "severity": "info",
            "text": f"Похожий момент в прошлом ({ts_disp}): {action} — {reason}",
            "text_en": f"Similar past moment ({ts_disp}): {action} — {reason}",
            "match": {
                "hash": best.get("hash"),
                "action": action,
                "reason": reason,
                "timestamp": best.get("timestamp"),
            },
        }, dedupe=True)
        log.info(f"[state_walk] episodic recall: {ts_disp} {action} — {reason[:60]}")

    # ── HRV alerts ─────────────────────────────────────────────────────

    def _check_hrv_alerts(self):
        mgr = get_hrv_manager()
        if not mgr.is_running:
            return
        state = mgr.get_baddle_state()
        coh = state.get("coherence")
        if coh is None:
            return
        if coh < 0.25:
            self._add_alert({
                "type": "coherence_crit",
                "severity": "warning",
                "text": "Coherence очень низкая. Сделай паузу.",
                "text_en": "Coherence very low. Take a break.",
            }, dedupe=True)

    # ── Alerts queue ───────────────────────────────────────────────────

    def _add_alert(self, alert: dict, dedupe: bool = False):
        with self._lock:
            if dedupe:
                for a in self._alerts_queue:
                    if a.get("type") == alert.get("type"):
                        return
            alert["ts"] = time.time()
            self._alerts_queue.append(alert)
            if len(self._alerts_queue) > 20:
                self._alerts_queue = self._alerts_queue[-20:]

    def get_alerts(self, clear: bool = False) -> list:
        with self._lock:
            alerts = list(self._alerts_queue)
            if clear:
                self._alerts_queue.clear()
            return alerts

    def get_status(self) -> dict:
        return {
            "running": self.is_running,
            "alerts_pending": len(self._alerts_queue),
            "last_dmn": self._last_dmn,
            "last_state_walk": self._last_state_walk,
            "last_night_cycle": self._last_night_cycle,
            "last_foreground_tick": self._last_foreground_tick,
        }


# ── Singleton ─────────────────────────────────────────────────────────

_loop: Optional[CognitiveLoop] = None


def get_cognitive_loop() -> CognitiveLoop:
    global _loop
    if _loop is None:
        _loop = CognitiveLoop()
    return _loop
