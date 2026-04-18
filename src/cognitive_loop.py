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
    BRIEFING_INTERVAL = 20 * 3600     # раз в ~сутки: утренний briefing (< чем night чтобы не совпадать)
    HRV_PUSH_INTERVAL = 15            # каждые 15с синхронизируем HRV → UserState
    TICK_INTERVAL = 60                # частота бэкграунд-проверок
    FOREGROUND_COOLDOWN = 30          # после юзер-тика DMN ждёт столько секунд
    DEFAULT_WAKE_HOUR = 7             # если profile.context.wake_hour не задан
    WS_FLUSH_INTERVAL = 120           # каждые 2 мин — auto-save активного workspace
                                      # (nodes + embeddings) чтобы рестарт не терял данные
    LOW_ENERGY_THRESHOLD = 30         # ниже этого — тяжёлые решения предлагаем отложить
    LOW_ENERGY_CHECK_INTERVAL = 30 * 60  # раз в 30 мин — не спамить
    HEAVY_MODES = ("dispute", "tournament", "bayes", "race", "builder", "cascade", "scales")

    # Plan reminders: push-alert за N min до planned events
    PLAN_REMINDER_MINUTES = 10        # за сколько минут до события пушить
    PLAN_REMINDER_CHECK_INTERVAL = 60 # раз в минуту проверяем upcoming

    # Evening retrospective: раз в сутки поздним вечером
    EVENING_RETRO_HOUR_OFFSET = 14    # wake_hour + 14h = typical 21:00

    # Heartbeat: сводный снапшот в state_graph для DMN/scout substrate
    HEARTBEAT_INTERVAL = 300          # раз в 5 мин — пишет single state_node со стримами

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
        self._last_briefing = 0.0
        self._last_hrv_push = 0.0
        self._last_foreground_tick = 0.0
        self._last_ws_flush = 0.0
        self._last_activity_tick = 0.0  # для activity → energy cost
        self._activity_cost_carry = 0.0  # остаток < 0.1 между тиками (не терять копейки)
        self._last_low_energy_check = 0.0  # дроссель low_energy_heavy alerts
        self._last_plan_reminder_check = 0.0
        self._reminded_plan_keys: set = set()  # "plan_id:YYYY-MM-DD" dedup
        self._last_evening_retro_date: str = ""  # YYYY-MM-DD последнего ретро
        self._last_heartbeat = 0.0
        # Persist overnight findings отдельно от alerts_queue — UI drain'ит очередь
        # быстрее чем briefing её читает. Briefing читает recent_bridges напрямую.
        self._recent_bridges: list = []  # [{ts, text, source: "dmn"|"scout"}], max 10
        self._last_night_summary: Optional[dict] = None
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

                # 3. HRV alerts + morning briefing + hrv→UserState push всегда
                # (briefing проактивный, срабатывает когда юзер проснулся;
                # hrv_push гарантирует что UserState.hrv_* не устаревает
                # между вызовами /hrv/metrics endpoint — критично для
                # activity_zone, sync_regime, named_state).
                self._check_hrv_alerts()
                self._check_daily_briefing()
                self._check_hrv_push()
                self._check_ws_flush()
                self._check_activity_cost()
                self._check_low_energy_heavy()
                self._check_plan_reminders()
                self._check_evening_retro()
                self._check_heartbeat()
            except Exception as e:
                log.warning(f"[cognitive_loop] error: {e}")

            # 4. Adaptive sleep. Верхний bound = TICK_INTERVAL (60s) для
            # scout/dmn/night проверок; но HRV push хочет каждые 15с —
            # cap на HRV_PUSH_INTERVAL чтобы physical state не устаревал.
            try:
                ne = get_global_state().neuro.norepinephrine
                scaled = self.TICK_INTERVAL * max(0.5, 1.2 - ne)
            except Exception:
                scaled = self.TICK_INTERVAL
            scaled = min(scaled, float(self.HRV_PUSH_INTERVAL))
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

        # Phase 5: Patterns detector (weekday × activity → исход)
        try:
            from .patterns import detect_all
            detected = detect_all(days_back=21)
            summary["patterns"] = {"detected": len(detected)}
        except Exception as e:
            summary["patterns"] = {"error": str(e)}

        # Phase 6: Rotation goals.jsonl (gzip старых завершённых событий)
        try:
            from .goals_store import rotate_if_needed
            rotated = rotate_if_needed()
            summary["rotation"] = {"archived_file": rotated}
        except Exception as e:
            summary["rotation"] = {"error": str(e)}

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
        # Persist за пределами очереди alerts — briefing читает напрямую
        self._last_night_summary = dict(summary)
        bt = (s.get("scout") or {}).get("bridge_text")
        if bt:
            self._recent_bridges.append({
                "ts": time.time(),
                "text": bt,
                "source": "scout",
            })
            self._recent_bridges = self._recent_bridges[-10:]
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
            self._recent_bridges.append({
                "ts": time.time(),
                "text": (bridge.get("text") or "")[:100],
                "quality": bridge.get("quality", 0),
                "source": "dmn",
            })
            self._recent_bridges = self._recent_bridges[-10:]

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

    # ── Morning briefing push (once per day, after wake_hour) ───────────

    def _check_daily_briefing(self):
        """Push morning-briefing alert в очередь раз в сутки после wake_hour.

        Условия:
          • прошло >= BRIEFING_INTERVAL с прошлого briefing
          • текущий локальный час >= wake_hour (из profile.context, default 7)
        `_last_briefing` персистится в user_state.json — чтобы рестарт
        процесса не приводил к повторному брифингу в тот же день.
        """
        import datetime as _dt
        now = time.time()

        # Lazy-load last_briefing_ts из state (первый вызов после рестарта)
        if getattr(self, "_briefing_loaded_from_disk", False) is False:
            try:
                from .assistant import _load_state
                persisted = float((_load_state().get("last_briefing_ts") or 0.0))
                if persisted > self._last_briefing:
                    self._last_briefing = persisted
            except Exception:
                pass
            self._briefing_loaded_from_disk = True

        if now - self._last_briefing < self.BRIEFING_INTERVAL:
            return

        try:
            from .user_profile import load_profile
            ctx = (load_profile().get("context") or {})
            wake_hour = int(ctx.get("wake_hour", self.DEFAULT_WAKE_HOUR))
        except Exception:
            wake_hour = self.DEFAULT_WAKE_HOUR

        local_hour = _dt.datetime.now().hour
        if local_hour < wake_hour:
            return

        self._last_briefing = now
        # Persist сразу — даже если briefing text упадёт, интервал уже
        # зачитан и повторы не сработают.
        try:
            from .assistant import _load_state, _save_state
            st = _load_state()
            st["last_briefing_ts"] = now
            _save_state(st)
        except Exception as e:
            log.debug(f"[cognitive_loop] briefing persist failed: {e}")
        try:
            text = self._build_morning_briefing_text()
        except Exception as e:
            log.warning(f"[cognitive_loop] briefing text failed: {e}")
            return
        # Structured sections — для rich-card рендеринга в UI (как в mockup).
        # text остаётся как fallback / для logs.
        try:
            sections = self._build_morning_briefing_sections()
        except Exception as e:
            log.debug(f"[cognitive_loop] briefing sections failed: {e}")
            sections = []

        self._add_alert({
            "type": "morning_briefing",
            "severity": "info",
            "text": text,
            "text_en": text,
            "hour": local_hour,
            "sections": sections,
        }, dedupe=True)
        log.info(f"[cognitive_loop] morning briefing pushed @ {local_hour}:00")

    def _build_morning_briefing_sections(self) -> list:
        """Структурированный briefing — список карточек {emoji, title, subtitle, kind}.

        UI рендерит как набор секций (см. mockup Thursday briefing). Порядок:
          1. Sleep      (из activity log)
          2. Recovery   (HRV energy_recovery + named_state)
          3. Energy     (long_reserve %)
          4. Overnight  (Scout bridges найденные ночью)
          5. Activity   (вчера: N часов по категориям)
          6. Goals      (открытые + первая)
          7. Pattern    (weekday hint если есть)

        kind ∈ {info, warn, highlight, neutral} → CSS-класс акцента.
        """
        from .horizon import get_global_state
        from .hrv_manager import get_manager as get_hrv_mgr
        from .goals_store import list_goals
        sections: list = []

        # 1. Sleep
        try:
            from .activity_log import estimate_last_sleep_hours
            sleep = estimate_last_sleep_hours()
            if sleep and sleep.get("hours"):
                hrs = sleep["hours"]
                src = "из трекера" if sleep.get("source") == "explicit" else "из пауз активности"
                if hrs >= 7:
                    sub, kind = f"Полноценный сон · {src}", "info"
                elif hrs >= 5:
                    sub, kind = f"Короткий сон · береги ресурс · {src}", "warn"
                else:
                    sub, kind = f"Сильно недоспал · сложные задачи позже · {src}", "warn"
                sections.append({"emoji": "💤", "title": f"Сон {hrs}ч",
                                 "subtitle": sub, "kind": kind})
        except Exception:
            pass

        # 1b. Last check-in (если есть) — subjective сигнал юзера
        try:
            from .checkins import latest_checkin
            ci = latest_checkin(hours=36)
            if ci:
                parts = []
                if ci.get("energy") is not None:
                    parts.append(f"E {int(ci['energy'])}")
                if ci.get("focus") is not None:
                    parts.append(f"F {int(ci['focus'])}")
                if ci.get("stress") is not None:
                    parts.append(f"S {int(ci['stress'])}")
                surprise_part = None
                if ci.get("expected") is not None and ci.get("reality") is not None:
                    s = ci["reality"] - ci["expected"]
                    surprise_part = f"Δ{'+' if s >= 0 else ''}{int(s)}"
                subtitle_bits = []
                if parts:
                    subtitle_bits.append(" · ".join(parts))
                if surprise_part:
                    subtitle_bits.append(f"вчера ожидание vs реальность: {surprise_part}")
                if ci.get("note"):
                    subtitle_bits.append(f"«{ci['note'][:50]}»")
                if subtitle_bits:
                    kind = "info"
                    # Если stress высокий — warn
                    if (ci.get("stress") or 0) > 70:
                        kind = "warn"
                    sections.append({
                        "emoji": "📝",
                        "title": "Последний check-in",
                        "subtitle": " · ".join(subtitle_bits),
                        "kind": kind,
                    })
        except Exception:
            pass

        # 2. Recovery + named_state
        recovery_pct = None
        named_label = None
        try:
            mgr = get_hrv_mgr()
            if mgr.is_running:
                hrv_state = mgr.get_baddle_state() or {}
                rec = hrv_state.get("energy_recovery")
                if rec is not None:
                    recovery_pct = int(rec * 100)
            metrics = get_global_state().get_metrics()
            ns = (metrics.get("user_state") or {}).get("named_state") or {}
            named_label = ns.get("label") or ns.get("key")
        except Exception:
            pass
        if recovery_pct is not None or named_label:
            title = "Восстановление"
            if recovery_pct is not None:
                title += f" {recovery_pct}%"
            kind = "neutral"
            subtitle = f"Состояние: {named_label.lower()}" if named_label else ""
            if recovery_pct is not None:
                if recovery_pct >= 80:
                    subtitle = (subtitle + " · хороший день для сложного") if subtitle else "Хороший день для сложного"
                    kind = "info"
                elif recovery_pct >= 60:
                    subtitle = (subtitle + " · начни с важного") if subtitle else "Начни с важного"
                else:
                    subtitle = (subtitle + " · лёгкие задачи первыми") if subtitle else "Лёгкие задачи первыми"
                    kind = "warn"
            sections.append({"emoji": "⚡", "title": title, "subtitle": subtitle or "—", "kind": kind})

        # 3. Energy pool (long_reserve)
        try:
            metrics = get_global_state().get_metrics()
            user = metrics.get("user_state") or {}
            lr = user.get("long_reserve")
            if isinstance(lr, (int, float)):
                pct = int(lr / 2000.0 * 100)
                kind = "info" if pct >= 70 else "warn" if pct < 30 else "neutral"
                sub = "полный" if pct >= 90 else ("в норме" if pct >= 50
                                                   else "нужна пауза" if pct < 30 else "средний")
                sections.append({"emoji": "🔋", "title": f"Резерв {pct}%",
                                 "subtitle": f"{int(lr)}/2000 · {sub}", "kind": kind})
        except Exception:
            pass

        # 4. Overnight Scout / DMN bridges
        try:
            now_ts = time.time()
            cutoff = now_ts - 10 * 3600
            recent = [b for b in (self._recent_bridges or [])
                      if (b.get("ts") or 0) >= cutoff]
            if recent:
                recent.sort(key=lambda b: b.get("ts", 0), reverse=True)
                first = recent[0].get("text", "")[:80]
                if len(recent) == 1:
                    sections.append({
                        "emoji": "🌙", "title": "Scout нашёл 1 мост",
                        "subtitle": f"«{first}»", "kind": "highlight"
                    })
                else:
                    sections.append({
                        "emoji": "🌙", "title": f"Scout нашёл {len(recent)} мостов",
                        "subtitle": f"Первый: «{first}»", "kind": "highlight"
                    })
        except Exception:
            pass

        # 5. Yesterday activity summary
        try:
            from .activity_log import day_summary
            yday = day_summary(ts=time.time() - 86400)
            if (yday.get("activity_count") or 0) > 0:
                cat_h = yday.get("by_category_h") or {}
                top = sorted(cat_h.items(), key=lambda kv: kv[1], reverse=True)[:2]
                by_cat = ", ".join(f"{c} {h}ч" for c, h in top if h > 0.1)
                sections.append({
                    "emoji": "📊", "title": f"Вчера: {yday['total_tracked_h']}ч",
                    "subtitle": f"{by_cat or '—'} · {yday.get('switches', 0)} переключений",
                    "kind": "neutral"
                })
        except Exception:
            pass

        # 6. Open goals
        try:
            open_goals = list_goals(status="open", limit=3)
            if open_goals:
                first = (open_goals[0].get("text") or "")[:70]
                sections.append({
                    "emoji": "🎯",
                    "title": f"Открытых целей: {len(open_goals)}",
                    "subtitle": f"Первая: «{first}»",
                    "kind": "neutral"
                })
        except Exception:
            pass

        # 7. Pattern hint for today
        try:
            from .patterns import patterns_for_today
            today_patterns = patterns_for_today()
            if today_patterns:
                today_patterns.sort(key=lambda p: p.get("detected_at", 0), reverse=True)
                hint = today_patterns[0].get("hint_ru") or ""
                if hint:
                    sections.append({
                        "emoji": "💡", "title": "Паттерн на сегодня",
                        "subtitle": hint, "kind": "highlight"
                    })
        except Exception:
            pass

        # 8. Today's schedule (plans + recurring habits)
        try:
            from .plans import schedule_for_day
            sched = schedule_for_day()
            if sched:
                # Неотмеченные + неотпропущенные
                todo = [s for s in sched if not s.get("done") and not s.get("skipped")]
                recurring = [s for s in sched if s.get("kind") == "recurring"]
                n_todo = len(todo)
                n_total = len(sched)
                n_rec = len(recurring)
                # Краткая строка первых 2 событий по времени
                preview_parts = []
                for it in sorted(todo, key=lambda x: x.get("planned_ts") or 0)[:3]:
                    import datetime as _dt
                    t = _dt.datetime.fromtimestamp(it.get("planned_ts") or 0).strftime("%H:%M")
                    preview_parts.append(f"{t} {it.get('name', '')[:30]}")
                preview = "; ".join(preview_parts) if preview_parts else "все выполнено"
                kind = "highlight" if n_todo > 0 else "info"
                title = f"План: {n_todo}/{n_total}"
                if n_rec > 0:
                    title += f" · {n_rec} привычек"
                sections.append({
                    "emoji": "📋", "title": title,
                    "subtitle": preview, "kind": kind,
                })
        except Exception:
            pass

        # 9. Food suggestion если нет завтрака в плане и profile.food непустой
        try:
            from .user_profile import load_profile, get_category
            from .plans import schedule_for_day
            import datetime as _dt
            prof = load_profile()
            food_cat = get_category("food", prof)
            has_prefs = bool(food_cat.get("preferences") or food_cat.get("constraints"))
            sched = schedule_for_day()
            # Уже есть запланированная еда на утро (до 11:00)?
            has_morning_food = any(
                s for s in sched
                if s.get("category") == "food"
                and (s.get("planned_ts") or 0)
                    and _dt.datetime.fromtimestamp(s["planned_ts"]).hour < 11
            )
            if has_prefs and not has_morning_food:
                prefs = food_cat.get("preferences") or []
                cons = food_cat.get("constraints") or []
                constr_str = ""
                if cons:
                    constr_str = " · избегай: " + ", ".join(cons[:2])
                pref_str = ""
                if prefs:
                    pref_str = " · любишь: " + ", ".join(prefs[:2])
                sections.append({
                    "emoji": "🍳", "title": "Завтрак?",
                    "subtitle": f"Нет плана на утро{pref_str}{constr_str}.",
                    "kind": "info",
                    "actions": [
                        {"label": "Выбери для меня", "action": "food_suggest"},
                    ],
                })
        except Exception:
            pass

        return sections

    def _build_morning_briefing_text(self) -> str:
        """Собрать короткий morning-briefing из HRV + energy + open goals + profile.

        Не вызывает LLM — быстрая агрегация из state. UI показывает как alert;
        если юзер откроет /assist/morning — получит расширенную LLM-версию.
        """
        from .horizon import get_global_state
        from .hrv_manager import get_manager as get_hrv_manager
        from .goals_store import list_goals

        bits = ["Доброе утро."]

        # Sleep duration (из activity idle-gap или явной задачи «Сон»)
        try:
            from .activity_log import estimate_last_sleep_hours
            sleep = estimate_last_sleep_hours()
            if sleep and sleep.get("hours"):
                hrs = sleep["hours"]
                suffix = " (из трекера)" if sleep.get("source") == "explicit" else ""
                bits.append(f"Сон {hrs}ч{suffix}.")
                # Зеркалим в UserState чтобы simulate-day и другие могли читать
                try:
                    from .user_state import get_user_state
                    get_user_state().last_sleep_duration_h = float(hrs)
                except Exception:
                    pass
        except Exception:
            pass

        # HRV recovery
        mgr = get_hrv_manager()
        recovery_pct = None
        if mgr.is_running:
            state = mgr.get_baddle_state() or {}
            rec = state.get("energy_recovery")
            if rec is not None:
                recovery_pct = int(rec * 100)
                bits.append(f"Восстановление {recovery_pct}%.")

        # User state (named region)
        try:
            cs = get_global_state()
            metrics = cs.get_metrics()
            user = metrics.get("user_state") or {}
            named = user.get("named_state") or {}
            if named.get("label"):
                bits.append(f"Состояние: {named['label'].lower()}.")
            long_reserve = user.get("long_reserve")
            if isinstance(long_reserve, (int, float)):
                pct = long_reserve / 2000.0 * 100
                bits.append(f"Долгий резерв {int(pct)}%.")
        except Exception:
            pass

        # Open goals
        try:
            open_goals = list_goals(status="open", limit=3)
            if open_goals:
                bits.append(f"Открытых целей: {len(open_goals)}. "
                            f"Первая: «{open_goals[0].get('text', '')[:60]}».")
        except Exception:
            pass

        # Advice by recovery
        if recovery_pct is not None:
            if recovery_pct >= 80:
                bits.append("Хороший день для сложных задач.")
            elif recovery_pct >= 60:
                bits.append("Средний день — начни с важного.")
            else:
                bits.append("Береги энергию, лёгкие задачи первыми.")

        # Overnight Scout / DMN findings — что нашёл пока юзер спал.
        # Читаем из self._recent_bridges (персистентно, не через alerts-queue
        # которую UI быстро drain'ит). Порог ~10ч — покрывает ночь.
        try:
            now_ts = time.time()
            cutoff = now_ts - 10 * 3600
            recent = [b for b in (self._recent_bridges or [])
                      if (b.get("ts") or 0) >= cutoff]
            if recent:
                # Топ 2 — от новых к старым
                recent.sort(key=lambda b: b.get("ts", 0), reverse=True)
                top = recent[:2]
                if len(recent) == 1:
                    bits.append(f"Пока спал, Scout нашёл мост: «{top[0]['text'][:80]}».")
                else:
                    bits.append(f"Пока спал, Scout нашёл {len(recent)} мостов. "
                                f"Первый: «{top[0]['text'][:80]}».")
            elif self._last_night_summary is not None:
                # Scout пробежал но мостов нет — отметим хотя бы консолидацию
                cs = self._last_night_summary.get("consolidation") or {}
                pr = cs.get("pruned", 0)
                ar = cs.get("archived", 0)
                if pr or ar:
                    bits.append(f"Ночная консолидация: прунинг {pr}, архив {ar}.")
        except Exception:
            pass

        # Pattern hint для сегодняшнего weekday (если ночью что-то нашли)
        try:
            from .patterns import patterns_for_today
            todays = patterns_for_today()
            if todays:
                # Один самый свежий — не заваливаем briefing
                todays.sort(key=lambda p: p.get("detected_at", 0), reverse=True)
                hint = todays[0].get("hint_ru") or ""
                if hint:
                    bits.append(f"💡 {hint}")
        except Exception:
            pass

        # Вчерашний activity summary — ground truth прошедшего дня
        try:
            from .activity_log import day_summary
            import time as _time
            yday = day_summary(ts=_time.time() - 86400)
            if (yday.get("activity_count") or 0) > 0:
                parts = [f"Вчера: {yday['total_tracked_h']}ч"]
                # Топ-2 по категориям
                cat_h = yday.get("by_category_h") or {}
                top_cats = sorted(cat_h.items(), key=lambda kv: kv[1], reverse=True)[:2]
                if top_cats:
                    parts.append("(" + ", ".join(f"{c} {h}ч" for c, h in top_cats if h > 0.1) + ")")
                sw = yday.get("switches") or 0
                if sw > 0:
                    parts.append(f"· {sw} переключ.")
                bits.append(" ".join(parts) + ".")
        except Exception:
            pass

        return " ".join(bits)

    # ── HRV → UserState periodic push (15s) ─────────────────────────────

    def _check_hrv_push(self):
        """Периодически синхронизирует hrv_manager → UserState.

        До этого UserState.hrv_* обновлялся только при **явном вызове**
        `/hrv/metrics` endpoint'а (pull-модель). Если UI не поллит — UserState
        устаревает, `activity_zone` / `named_state` / `sync_regime` считаются
        на старом coherence. Этот push гарантирует свежесть каждые 15с.
        """
        now = time.time()
        if now - self._last_hrv_push < self.HRV_PUSH_INTERVAL:
            return
        mgr = get_hrv_manager()
        if not mgr.is_running:
            return
        self._last_hrv_push = now
        try:
            state = mgr.get_baddle_state() or {}
            from .user_state import get_user_state
            get_user_state().update_from_hrv(
                coherence=state.get("coherence"),
                rmssd=state.get("rmssd"),
                stress=state.get("stress"),
                activity=state.get("activity_magnitude"),
            )
        except Exception as e:
            log.debug(f"[cognitive_loop] hrv push failed: {e}")

    # ── Low-energy heavy-decision guard ─────────────────────────────────

    def _check_low_energy_heavy(self):
        """Проактивная защита: если daily_remaining < THRESHOLD И в open_goals
        есть цель с тяжёлым mode — предлагаем перенести на утро.

        Mockup: «Heavy decision 'change tech stack?' — move to tomorrow
        morning?». Дроссель раз в 30 минут чтобы не спамить.
        """
        now = time.time()
        if now - self._last_low_energy_check < self.LOW_ENERGY_CHECK_INTERVAL:
            return
        try:
            from .assistant import _get_context
            from .goals_store import list_goals
            ctx = _get_context(reset_daily=False)
            energy = ctx.get("energy") or {}
            daily = energy.get("energy", 100)
            if daily >= self.LOW_ENERGY_THRESHOLD:
                return
            open_goals = list_goals(status="open", limit=20)
            heavy = [g for g in open_goals if g.get("mode") in self.HEAVY_MODES]
            if not heavy:
                return
            g0 = heavy[0]
            txt = (g0.get("text") or "")[:80]
            self._last_low_energy_check = now
            self._add_alert({
                "type": "low_energy_heavy",
                "severity": "warning",
                "text": f"Энергия {int(daily)}/100. Тяжёлое решение «{txt}» — "
                        f"перенести на утро?",
                "text_en": f"Energy {int(daily)}/100. Heavy decision '{txt}' — "
                           f"move to tomorrow morning?",
                "goal_id": g0.get("id"),
                "goal_text": txt,
                "goal_mode": g0.get("mode"),
                "energy": int(daily),
                "actions": [
                    {"label": "Перенести", "label_en": "Postpone",
                     "action": "postpone_goal_tomorrow", "goal_id": g0.get("id")},
                    {"label": "Нет, сейчас", "label_en": "No, now",
                     "action": "dismiss"},
                ],
            }, dedupe=True)
            log.info(f"[cognitive_loop] low_energy_heavy alert: energy={daily} goal={g0.get('id')}")
        except Exception as e:
            log.debug(f"[cognitive_loop] low_energy check failed: {e}")

    # ── Heartbeat: сводный снапшот всех стримов в state_graph ───────────

    def _check_heartbeat(self):
        """Раз в 5 мин пишем в state_graph «pulse»-запись — свёрнутый снапшот
        всего что система знает в этот момент: активная задача, pending plans,
        last check-in, recent surprises, open goals, live HRV.

        Зачем: DMN, state_walk и meta-tick читают tail state_graph'a как
        substrate. Если юзер idle — без heartbeat'a tail статичен и DMN
        варится только на content_graph. С heartbeat'ом поток живёт 24/7:
        система всегда видит СВОЁ текущее состояние во времени.

        Не эмитит alert — это наблюдательная запись, не сигнал.
        """
        now = time.time()
        if now - self._last_heartbeat < self.HEARTBEAT_INTERVAL:
            return
        self._last_heartbeat = now

        snapshot: dict = {"ts": now}
        # 1. Active activity
        try:
            from .activity_log import get_active, day_summary
            active = get_active()
            if active:
                snapshot["active_activity"] = {
                    "name": active.get("name"),
                    "category": active.get("category"),
                    "elapsed_s": int(now - float(active.get("started_at") or now)),
                }
            today = day_summary()
            snapshot["today_activity"] = {
                "count": today.get("activity_count", 0),
                "total_h": today.get("total_tracked_h", 0),
                "switches": today.get("switches", 0),
            }
        except Exception:
            pass

        # 2. Plans today (pending + completed ratio)
        try:
            from .plans import schedule_for_day
            sched = schedule_for_day()
            snapshot["plans_today"] = {
                "total": len(sched),
                "done": sum(1 for s in sched if s.get("done")),
                "skipped": sum(1 for s in sched if s.get("skipped")),
                "pending": sum(1 for s in sched
                               if not s.get("done") and not s.get("skipped")),
            }
            # Ближайшее событие
            pending = [s for s in sched if not s.get("done") and not s.get("skipped")
                       and (s.get("planned_ts") or 0) >= now]
            if pending:
                nx = min(pending, key=lambda s: s.get("planned_ts") or 0)
                snapshot["next_plan_in_s"] = int((nx.get("planned_ts") or now) - now)
                snapshot["next_plan_name"] = nx.get("name")
        except Exception:
            pass

        # 3. Latest check-in
        try:
            from .checkins import latest_checkin
            ci = latest_checkin(hours=48)
            if ci:
                snapshot["last_checkin"] = {
                    "age_h": round((now - float(ci.get("ts") or now)) / 3600.0, 1),
                    "energy": ci.get("energy"),
                    "focus": ci.get("focus"),
                    "stress": ci.get("stress"),
                    "surprise": ci.get("surprise"),
                }
        except Exception:
            pass

        # 4. Open goals
        try:
            from .goals_store import list_goals
            open_count = len(list_goals(status="open", limit=50))
            snapshot["open_goals"] = open_count
        except Exception:
            pass

        # 5. Neurochem + UserState scalars
        try:
            from .horizon import get_global_state
            m = get_global_state().get_metrics()
            neuro = m.get("neurochem", {})
            us = m.get("user_state", {})
            snapshot["neuro"] = {
                "da": round(neuro.get("dopamine", 0), 2),
                "s":  round(neuro.get("serotonin", 0), 2),
                "ne": round(neuro.get("norepinephrine", 0), 2),
                "burnout": round(neuro.get("burnout", 0), 2),
            }
            snapshot["user"] = {
                "long_reserve_pct": us.get("long_reserve_pct"),
                "named": (us.get("named_state") or {}).get("key"),
                "sync_regime": m.get("sync_regime"),
            }
        except Exception:
            pass

        # 6. Recent bridge (если был DMN хит)
        if self._recent_bridges:
            last_br = self._recent_bridges[-1]
            if (now - (last_br.get("ts") or 0)) < 3600:
                snapshot["recent_bridge"] = {
                    "text": (last_br.get("text") or "")[:80],
                    "source": last_br.get("source"),
                    "age_s": int(now - (last_br.get("ts") or now)),
                }

        # Составляем короткую reason-строку (для читаемого tail)
        bits = []
        if snapshot.get("active_activity"):
            a = snapshot["active_activity"]
            bits.append(f"act:{a['name']}({a['elapsed_s']//60}m)")
        if snapshot.get("plans_today"):
            p = snapshot["plans_today"]
            bits.append(f"plans:{p['done']}/{p['total']}")
        if snapshot.get("next_plan_in_s") is not None:
            mins = snapshot["next_plan_in_s"] // 60
            bits.append(f"next:{snapshot.get('next_plan_name', '')}→{mins}m")
        if snapshot.get("open_goals"):
            bits.append(f"goals:{snapshot['open_goals']}")
        neuro = snapshot.get("neuro") or {}
        if neuro:
            bits.append(f"NE:{neuro.get('ne')}")
        reason = "heartbeat · " + (" ".join(bits) if bits else "idle")

        try:
            from .state_graph import get_state_graph
            from .horizon import get_global_state
            st = get_global_state()
            sg = get_state_graph()
            # state_origin: 1_held если есть active activity, иначе 1_rest
            origin = "1_held" if snapshot.get("active_activity") else "1_rest"
            sg.append(
                action="heartbeat",
                phase="background",
                user_initiated=False,
                state_snapshot=snapshot,
                reason=reason,
                state_origin=origin,
            )
            log.debug(f"[cognitive_loop] heartbeat: {reason}")
        except Exception as e:
            log.debug(f"[cognitive_loop] heartbeat write failed: {e}")

    # ── Plan reminders & evening retrospective ──────────────────────────

    def _check_plan_reminders(self):
        """За 10 минут до запланированного события пушим alert.

        Dedup по (plan_id, for_date) чтобы не повторять. На новый день набор
        сбрасывается.
        """
        now = time.time()
        if now - self._last_plan_reminder_check < self.PLAN_REMINDER_CHECK_INTERVAL:
            return
        self._last_plan_reminder_check = now

        import datetime as _dt
        today_str = _dt.date.today().strftime("%Y-%m-%d")
        # Reset set на новый день
        if not any(k.endswith(today_str) for k in self._reminded_plan_keys):
            # Отсеиваем старые записи — храним только сегодняшние
            self._reminded_plan_keys = {k for k in self._reminded_plan_keys
                                        if k.endswith(today_str)}

        try:
            from .plans import schedule_for_day
            sched = schedule_for_day()
            window_s = self.PLAN_REMINDER_MINUTES * 60
            for it in sched:
                if it.get("done") or it.get("skipped"):
                    continue
                planned = it.get("planned_ts")
                if not planned:
                    continue
                delta = planned - now
                if not (0 < delta <= window_s):
                    continue
                key = f"{it['id']}:{it.get('for_date') or today_str}"
                if key in self._reminded_plan_keys:
                    continue
                self._reminded_plan_keys.add(key)
                mins_left = max(1, int(delta / 60))
                self._add_alert({
                    "type": "plan_reminder",
                    "severity": "info",
                    "text": f"Через {mins_left} мин: {it.get('name', '')}"
                            + (f" ({it.get('category')})" if it.get("category") else ""),
                    "text_en": f"In {mins_left} min: {it.get('name', '')}",
                    "plan_id": it["id"],
                    "plan_name": it.get("name", ""),
                    "plan_category": it.get("category"),
                    "for_date": it.get("for_date"),
                    "planned_ts": planned,
                    "minutes_before": mins_left,
                })
                log.info(f"[cognitive_loop] plan_reminder: {it.get('name')} in {mins_left}min")
        except Exception as e:
            log.debug(f"[cognitive_loop] plan reminder failed: {e}")

    def _check_evening_retro(self):
        """Вечернее ретро — раз в день, после wake_hour + 14h.

        Alert содержит list невыполненных plans + hint на check-in модал.
        """
        import datetime as _dt
        today_str = _dt.date.today().strftime("%Y-%m-%d")
        if self._last_evening_retro_date == today_str:
            return
        # Считаем время наступления ретро: wake_hour + offset (14h)
        try:
            from .user_profile import load_profile
            wake = int((load_profile().get("context") or {}).get("wake_hour",
                                                                  self.DEFAULT_WAKE_HOUR))
        except Exception:
            wake = self.DEFAULT_WAKE_HOUR
        retro_hour = min(23, wake + self.EVENING_RETRO_HOUR_OFFSET)
        local_hour = _dt.datetime.now().hour
        if local_hour < retro_hour:
            return

        # Собираем unfinished сегодняшние plans
        try:
            from .plans import schedule_for_day
            sched = schedule_for_day()
            unfinished = [
                {"id": s["id"], "name": s.get("name", ""),
                 "category": s.get("category"),
                 "planned_ts": s.get("planned_ts"),
                 "kind": s.get("kind")}
                for s in sched
                if not s.get("done") and not s.get("skipped")
            ]
        except Exception:
            unfinished = []

        self._last_evening_retro_date = today_str
        n_un = len(unfinished)
        text = (f"Ретро дня: {n_un} невыполнен{'о' if n_un == 1 else 'ы'}. "
                f"Откроем check-in?") if n_un else "Ретро дня: всё по плану. Сделаем check-in?"
        self._add_alert({
            "type": "evening_retro",
            "severity": "info",
            "text": text,
            "text_en": text,
            "unfinished": unfinished,
            "hour": local_hour,
        })
        log.info(f"[cognitive_loop] evening retro pushed @ {local_hour}:00 ({n_un} unfinished)")

    # ── Activity → energy cost (category-based) ──────────────────────────

    def _check_activity_cost(self):
        """Списывает daily energy по категории текущей активной задачи.

        До этого `decision_cost` применялся только при `/assist/feedback`
        (разговор с Baddle) — реальный 2h митинг без Baddle не тратил
        энергию в модели. Теперь:
          - work      → 0.25/мин  (≈15/час)
          - meeting   → 0.40/мин  (override по name)
          - pause/sleep → отрицательное (лёгкое восстановление)

        Source of truth — daily_spent в assistant state (тот же что и
        /assist тратит, единый счётчик).
        """
        now = time.time()
        if self._last_activity_tick == 0.0:
            self._last_activity_tick = now
            return
        delta_s = now - self._last_activity_tick
        if delta_s < 10:  # слишком частые тики — пропускаем
            return

        try:
            from .activity_log import get_active, cost_per_min
            act = get_active()
            if not act:
                self._last_activity_tick = now
                return

            rate = cost_per_min(act.get("name", ""), act.get("category"))
            if rate == 0:
                self._last_activity_tick = now
                return

            minutes = delta_s / 60.0
            cost = rate * minutes + self._activity_cost_carry
            # Накапливаем под 0.1 чтобы не терять мелкие delta
            whole = round(cost, 2)
            self._activity_cost_carry = cost - whole

            if whole != 0:
                # Импорт assistant state helpers (единый источник daily_spent)
                from .assistant import _get_context, _save_state
                ctx = _get_context()
                state = ctx["state"]
                prev = float(state.get("daily_spent", 0.0))
                new = max(0.0, prev + whole)  # clamp в ноль при recovery
                state["daily_spent"] = new
                _save_state(state)
        except Exception as e:
            log.debug(f"[cognitive_loop] activity cost failed: {e}")
        finally:
            self._last_activity_tick = now

    # ── Workspace auto-save (embeddings + nodes persistence) ─────────────

    def _check_ws_flush(self):
        """Каждые ~2 мин сбрасываем активный workspace на диск.

        До этого save происходил только на /workspace/switch или явный
        /workspace/save. При крэше/рестарте терялись новые ноды +
        embeddings с момента последнего switch. Теперь auto-flush раз
        в 2 минуты делает persistence надёжным.
        """
        now = time.time()
        if now - self._last_ws_flush < self.WS_FLUSH_INTERVAL:
            return
        self._last_ws_flush = now
        try:
            from .workspace import get_workspace_manager
            get_workspace_manager().save_active()
        except Exception as e:
            log.debug(f"[cognitive_loop] ws flush failed: {e}")

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
        now = time.time()
        # Gate diagnostics: почему/может ли DMN сейчас сработать
        gate = self._dmn_gate_diagnostics(now)
        return {
            "running": self.is_running,
            "alerts_pending": len(self._alerts_queue),
            "last_dmn": self._last_dmn,
            "last_state_walk": self._last_state_walk,
            "last_night_cycle": self._last_night_cycle,
            "last_briefing": self._last_briefing,
            "last_foreground_tick": self._last_foreground_tick,
            "last_heartbeat": self._last_heartbeat,
            "heartbeat_interval_s": self.HEARTBEAT_INTERVAL,
            "recent_bridges": list(self._recent_bridges or [])[-5:],
            "dmn": gate,
        }

    def _dmn_gate_diagnostics(self, now: float) -> dict:
        """Детальный статус DMN-гейта — почему может / не может сработать.

        Для проверки: работает ли DMN автономно когда юзер idle.
        Гейт: not_frozen AND ne < NE_HIGH_GATE AND idle >= FOREGROUND_COOLDOWN
        Плюс DMN_INTERVAL между запусками.
        """
        try:
            from .horizon import get_global_state, PROTECTIVE_FREEZE
            st = get_global_state()
            ne = st.neuro.norepinephrine
            state = st.state
            not_frozen = state != PROTECTIVE_FREEZE
        except Exception:
            ne = None
            state = "?"
            not_frozen = True
        idle_s = (now - self._last_foreground_tick) if self._last_foreground_tick else None
        since_dmn = now - self._last_dmn if self._last_dmn else None
        ne_quiet = (ne is not None and ne < self.NE_HIGH_GATE)
        # idle_enough: никогда не было foreground ИЛИ прошло больше cooldown
        idle_enough = (idle_s is None) or (idle_s >= self.FOREGROUND_COOLDOWN)
        interval_ok = since_dmn is None or since_dmn >= self.DMN_INTERVAL

        eligible = not_frozen and ne_quiet and idle_enough and interval_ok
        reason = None
        if not not_frozen:
            reason = f"PROTECTIVE_FREEZE (state={state})"
        elif not ne_quiet:
            reason = f"NE too high ({ne:.2f} >= {self.NE_HIGH_GATE})"
        elif not idle_enough:
            reason = f"user active recently ({idle_s:.0f}s < cooldown {self.FOREGROUND_COOLDOWN})"
        elif not interval_ok:
            reason = f"DMN_INTERVAL not elapsed ({since_dmn:.0f}s < {self.DMN_INTERVAL})"

        return {
            "eligible_now": eligible,
            "blocked_by": reason,
            "ne": round(ne, 3) if ne is not None else None,
            "ne_gate": self.NE_HIGH_GATE,
            "state": state,
            "idle_seconds": round(idle_s, 1) if idle_s is not None else None,
            "cooldown_s": self.FOREGROUND_COOLDOWN,
            "since_last_dmn_s": round(since_dmn, 1) if since_dmn is not None else None,
            "dmn_interval_s": self.DMN_INTERVAL,
            "last_bridge": (self._recent_bridges[-1] if self._recent_bridges else None),
        }


# ── Singleton ─────────────────────────────────────────────────────────

_loop: Optional[CognitiveLoop] = None


def get_cognitive_loop() -> CognitiveLoop:
    global _loop
    if _loop is None:
        _loop = CognitiveLoop()
    return _loop
