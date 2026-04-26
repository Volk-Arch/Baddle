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
import json
import threading
import time
import logging
import random
from typing import Optional, Tuple

from .graph_logic import _graph
from .hrv_manager import get_manager as get_hrv_manager
from .horizon import get_global_state, PROTECTIVE_FREEZE
from .user_state import get_user_state
from .signals import Signal, Dispatcher

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
    GRAPH_FLUSH_INTERVAL = 120        # каждые 2 мин — auto-save графа на диск
                                      # (nodes + embeddings) чтобы рестарт не терял данные
    # Phase C: LOW_ENERGY_THRESHOLD удалена — gate теперь через capacity_zone.
    HEAVY_MODES = ("dispute", "tournament", "bayes", "race", "builder", "cascade", "scales")

    # Plan reminders: push-alert за N min до planned events
    PLAN_REMINDER_MINUTES = 10        # за сколько минут до события пушить
    RECURRING_LAG_MIN = 1                    # alert когда отстаём ≥1 instance

    # Observation suggestions: раз в сутки собираем draft-карточки из
    # patterns / checkins / stress-зон. Юзер видит в alerts, может
    # подтвердить создание recurring/constraint или отклонить.
    SUGGESTIONS_CHECK_INTERVAL = 24 * 3600   # раз в сутки
    SUGGESTIONS_MAX_PER_DAY = 2              # не спамить карточками

    # Evening retrospective: раз в сутки поздним вечером
    EVENING_RETRO_HOUR_OFFSET = 14    # wake_hour + 14h = typical 21:00

    # Heartbeat: сводный снапшот в state_graph для DMN/scout substrate
    HEARTBEAT_INTERVAL = 300          # раз в 5 мин — пишет single state_node со стримами

    # DMN autonomous deep-research: когда юзер idle давно и есть open-goal,
    # запускаем реальный 3-step pipeline (brainstorm → elaborate → smartdc)
    # на одной цели. Реальная работа мозга в фоне, не просто pump-bridge.
    DMN_DEEP_INTERVAL = 30 * 60       # раз в 30 мин (реже обычного DMN)
    DMN_CONVERGE_INTERVAL = 60 * 60   # раз в час — полный autorun loop до STABLE
    DMN_CONVERGE_MAX_STEPS = 100      # глубокое исследование, не вечно
    DMN_CONVERGE_MAX_WALL_S = 15 * 60 # но не дольше 15 минут wall-time
    DMN_CONVERGE_STALL_WINDOW = 12    # если за 12 тиков ноды не выросли — стоп

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

    # Agency update (OQ #2 — 5-я ось):
    AGENCY_UPDATE_INTERVAL = 60 * 60  # раз в час обновляем agency EMA

    # Action Memory closing (этап 3):
    ACTION_OUTCOMES_CHECK_INTERVAL = 5 * 60  # раз в 5 мин проходим open actions
    # Per-kind timeout (сек): через сколько после emit считаем outcome closed.
    # До timeout — ждём user-reaction. После — forced close с текущим state.
    ACTION_TIMEOUTS = {
        "sync_seeking":            30 * 60,       # 30 мин
        "dmn_bridge":              24 * 3600,     # 24 часа (увидит утром?)
        "scout_bridge":            24 * 3600,
    }
    ACTION_TIMEOUT_DEFAULT = 60 * 60              # 1 час если kind не в словаре

    # Active sync-seeking (resonance protocol механика #3):
    # Sync-seeking, low-energy, plan reminders, recurring lag — Phase B
    # вынесены в детекторы (см. src/detectors.py). Throttle/dedup делает
    # Dispatcher; константы тут больше не нужны.

    # Adaptive idle (resonance protocol механики #2 + #4 together):
    # Multiplier = 1 + combined_burnout × (IDLE_MULTIPLIER_MAX - 1)
    # применяется ко всем investigation-интервалам. `combined_burnout` это
    # max(display_burnout, user.burnout) из ProtectiveFreeze (см. neurochem.py):
    #   • conflict_accumulator — графовые конфликты (single feeder активирует Bayes-freeze)
    #   • silence_pressure — хроническое молчание юзера (таймер)
    #   • imbalance_pressure — EMA |UserState.surprise| (predictive error)
    #   • user.burnout — usage fatigue (decisions_today + rejects)
    # Семантика UI: «Усталость Baddle» = display_burnout (без user).
    #
    # Параметры silence_pressure живут в `ProtectiveFreeze.SILENCE_RAMP_SECONDS`:
    #   • 7 суток без user-event → +1.0 (линейно)
    #   • 1 user event → -SILENCE_EVENT_DROP (~20 событий для восстановления)
    IDLE_MULTIPLIER_MAX = 10.0
    SILENCE_EVENT_DROP = 0.05                  # сколько снижает 1 user-event

    # Prime-directive recording: раз в час пишем sync_error EMA trend в
    # `data/prime_directive.jsonl`. Читается `/assist/prime-directive`
    # endpoint для валидации через 2 мес use (mean_slow падает = OK).
    PRIME_DIRECTIVE_INTERVAL = 3600            # 1 час между snapshot'ами

    # User surprise detection (OQ #7): HRV + text markers → event.
    # Throttle 5 мин — surprise-события редкие, не спамим граф.
    # Processed message tracking: не повторно анализируем одно сообщение
    # при каждой проверке.
    USER_SURPRISE_CHECK_INTERVAL = 300         # 5 мин

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
        self._last_graph_flush = 0.0
        self._last_activity_tick = 0.0  # для activity → energy cost
        self._activity_cost_carry = 0.0  # остаток < 0.1 между тиками (не терять копейки)
        # Phase B: per-detector throttle полей _last_low_energy_check,
        # _last_plan_reminder_check, _reminded_plan_keys, _last_recurring_check,
        # _notified_lag, _last_sync_seeking, _last_proactive_alert_ts удалены
        # — их роль берёт Dispatcher (budget + dedup_key + window_s).
        self._last_evening_retro_date: str = ""  # YYYY-MM-DD последнего ретро (используется detector'ом)
        self._last_heartbeat = 0.0
        self._last_dmn_deep = 0.0  # таймер DMN autonomous deep-research
        self._last_dmn_converge = 0.0  # таймер server-side tick-autorun до STABLE
        self._last_suggestions_check = 0.0  # таймер observation suggestions (compute throttle)
        self._last_agency_update = 0.0      # таймер agency EMA update
        self._last_cognitive_load_update = 0.0  # Phase C: cogload bookkeeping
        self._last_action_outcomes_check = 0.0  # таймер closing action outcomes
        self._last_prime_directive = 0.0    # таймер hourly prime_directive.jsonl записи
        self._last_user_surprise_check = 0.0  # таймер OQ #7 detector
        self._last_analyzed_msg_ts: float = 0.0  # timestamp последнего проанализированного user-msg
        # Action Memory — per-kind storage для open actions:
        # {action_idx: recorded_ts}. Используется `_check_action_outcomes`.
        self._open_actions: dict[int, float] = {}
        # Persist overnight findings отдельно от alerts_queue — UI drain'ит очередь
        # быстрее чем briefing её читает. Briefing читает recent_bridges напрямую.
        self._recent_bridges: list = []  # [{ts, text, source: "dmn"|"scout"}], max 10
        self._last_night_summary: Optional[dict] = None
        self._alerts_queue: list = []
        self._lock = threading.Lock()

        # Thinking-state: что система сейчас делает в фоне. UI читает через
        # /assist/state → рисует живой конус (dual cones для pump, pulse для
        # elaborate, freeze-overlay для protective_freeze). Формат:
        #   {"kind": "pump"|"elaborate"|"smartdc"|"scout"|"synthesize"|"idle",
        #    "started_at": ts, "detail": ...}
        self._thinking_state: dict = {"kind": "idle", "started_at": 0.0}

        # Adaptive idle state. silence_pressure / imbalance_pressure /
        # sync_error_ema_* живут в neurochem.ProtectiveFreeze (через
        # horizon.get_global_state().freeze). Здесь только timestamp
        # последнего loop tick чтобы считать dt для `_advance_tick`.
        self._last_loop_tick_ts = 0.0

        # Signal dispatcher — Phase B (2026-04-25). 13 детекторов в DETECTORS
        # collect signals → dispatcher решает emit/drop по urgency+budget+dedup.
        # См. правило 1 в docs/architecture-rules.md.
        self._dispatcher = Dispatcher(
            budget_per_window=5,        # max 5 non-critical alerts/час
            window_s=3600.0,             # 1 час окно для budget + dedup
            critical_threshold=0.9,      # urgency≥0.9 bypass budget
        )

    def set_thinking(self, kind: str, detail: Optional[dict] = None):
        """Отметить текущее тяжёлое действие (pump / elaborate / ...).
        UI polls /assist/state и рисует соответствующий cone-режим. Вызывать
        перед долгой операцией и clear_thinking() после.
        """
        self._thinking_state = {
            "kind": kind,
            "started_at": time.time(),
            "detail": detail or {},
        }

    def clear_thinking(self):
        self._thinking_state = {"kind": "idle", "started_at": time.time()}

    def get_thinking(self) -> dict:
        return dict(self._thinking_state or {"kind": "idle", "started_at": 0.0})

    # ── Action Memory: запись + closing ────────────────────────────────

    def _check_action_outcomes(self):
        """Раз в 5 мин проходим `_open_actions`, закрываем outcomes.

        Для каждого open action ищем:
          1. **User-reaction** в окне [action.ts, now]:
             - `user_chat` → reaction = "chat" (для sync_seeking / reminders)
             - `user_accept` / `user_reject` → для suggestions
             Если найдено — closing immediate, latency = от action до reaction.
          2. **Timeout expired** → forced close с reaction="silence"/"ignore".

        Delta = `sync_error_before` из action.context минус текущий sync_error.
        """
        if not self._throttled("_last_action_outcomes_check",
                                self.ACTION_OUTCOMES_CHECK_INTERVAL):
            return
        if not self._open_actions:
            return

        try:
            from .graph_logic import list_open_actions, close_action, _graph
        except Exception:
            return

        now = time.time()
        try:
            current_sync_err = float(get_global_state().sync_error)
        except Exception:
            current_sync_err = 0.0

        # Свежий snapshot open-actions из графа (не надёжно верить буферу —
        # ноды могли быть удалены через consolidation)
        open_list = list_open_actions()
        still_open_set = {idx for idx, _ in open_list}
        # Прочищаем _open_actions от несуществующих
        for idx in list(self._open_actions.keys()):
            if idx not in still_open_set:
                self._open_actions.pop(idx, None)

        # Читаем нод-буфер один раз
        nodes = _graph.get("nodes", [])

        for action_idx, action_node in open_list:
            kind = action_node.get("action_kind", "unknown")
            recorded_ts = self._open_actions.get(action_idx)
            if recorded_ts is None:
                # Action из старых сессий — восстановим ts из created_at
                from datetime import datetime as _dt
                try:
                    ts_iso = action_node.get("created_at")
                    if ts_iso:
                        recorded_ts = _dt.fromisoformat(str(ts_iso).replace("Z","+00:00")).timestamp()
                    else:
                        recorded_ts = now  # нет ts — считаем сейчас, игнорим в этот проход
                except Exception:
                    recorded_ts = now
                self._open_actions[action_idx] = recorded_ts

            age_s = now - recorded_ts
            timeout = self.ACTION_TIMEOUTS.get(kind, self.ACTION_TIMEOUT_DEFAULT)

            # 1. Ищем user-reaction в окне [recorded_ts, now]
            reaction = None
            reaction_ts = None
            # Быстрый проход по свежим нодам (последние 30 — юзер-реакции редкие)
            for later_node in reversed(nodes[-50:]):
                if later_node.get("type") != "action":
                    continue
                if later_node.get("actor") != "user":
                    continue
                later_kind = later_node.get("action_kind", "")
                later_ts_iso = later_node.get("created_at", "")
                try:
                    from datetime import datetime as _dt
                    later_ts = _dt.fromisoformat(str(later_ts_iso).replace("Z","+00:00")).timestamp()
                except Exception:
                    continue
                if later_ts < recorded_ts or later_ts > now:
                    continue
                # User-chat закрывает sync_seeking
                if later_kind == "user_chat" and kind == "sync_seeking":
                    reaction = "chat"
                    reaction_ts = later_ts
                    break
                # Accept/reject закрывает suggestions
                if kind.startswith("suggestion_"):
                    if later_kind == "user_accept":
                        reaction = "accept"
                        reaction_ts = later_ts
                        break
                    if later_kind == "user_reject":
                        reaction = "reject"
                        reaction_ts = later_ts
                        break

            # 2. Принимаем решение о закрытии
            close_now = False
            latency_s = 0.0
            if reaction is not None:
                close_now = True
                latency_s = (reaction_ts or now) - recorded_ts
            elif age_s >= timeout:
                close_now = True
                # Default reaction по kind
                if kind.startswith("suggestion_"):
                    reaction = "ignore"
                else:
                    reaction = "silence"
                latency_s = age_s

            if not close_now:
                continue

            # Считаем delta = after - before (стандартная математическая дельта)
            # Negative = sync_error упал = resonance улучшился (good)
            # Positive = sync_error вырос = стало хуже
            pre_err = action_node.get("context", {}).get("sync_error_before", current_sync_err)
            try:
                pre_err_f = float(pre_err)
            except Exception:
                pre_err_f = current_sync_err
            delta = current_sync_err - pre_err_f

            # Confidence — short latency = высокая; длинное timeout = низкая
            confidence = max(0.2, min(1.0, 1.0 - (age_s / (timeout * 2.0))))

            try:
                close_action(
                    action_idx=action_idx,
                    delta_sync_error=delta,
                    user_reaction=reaction,
                    latency_s=latency_s,
                    confidence=confidence,
                )
                self._open_actions.pop(action_idx, None)
            except Exception as e:
                log.debug(f"[action-memory] close_action failed for {action_idx}: {e}")

    def _record_baddle_action(self, action_kind: str, text: str,
                                extras: Optional[dict] = None) -> Optional[int]:
        """Записать baddle-side action в граф и запомнить в _open_actions.
        Возвращает idx action-ноды или None при ошибке. Используется из
        всех proactive check'ов после успешного emit.
        """
        try:
            from .graph_logic import record_action
            idx = record_action(
                actor="baddle",
                action_kind=action_kind,
                text=text[:200] if text else action_kind,
                context=None,    # _current_snapshot() по умолчанию
                extras=extras,
            )
            self._open_actions[idx] = time.time()
            return idx
        except Exception as e:
            log.debug(f"[action-memory] record baddle action failed: {e}")
            return None

    # ── Adaptive idle (resonance protocol mechanics #2 + #4) ──────────

    def _get_freeze(self):
        """Helper: быстрый доступ к ProtectiveFreeze (носитель silence / imbalance /
        sync_error EMA). Возвращает None если horizon недоступен — защита при
        init race.
        """
        try:
            return get_global_state().freeze
        except Exception:
            return None

    def _idle_multiplier(self) -> float:
        """1.0 при полном resonance, MAX при max combined burnout.

        Делегирует в `ProtectiveFreeze.combined_burnout(user_burnout)` —
        единый расчёт для UI и замедления циклов. Эмпатия: если юзер
        устал (decisions/rejects → `user.burnout`), Baddle тоже тише.
        Это не отдельный check «предлагаем отдых», а встроенное молчание.
        """
        fz = self._get_freeze()
        if fz is None:
            return 1.0
        try:
            user_burnout = float(get_user_state().burnout)
        except Exception:
            user_burnout = 0.0
        combined = fz.combined_burnout(user_burnout)
        return 1.0 + combined * (self.IDLE_MULTIPLIER_MAX - 1.0)

    def _register_user_event(self, reason: str = ""):
        """User-event (сообщение, foreground tick, ручная правка графа).
        Снижает `silence_pressure` на SILENCE_EVENT_DROP (не обнуляет).
        ~20 таких событий возвращают multiplier в 1.0 из максимума.
        """
        fz = self._get_freeze()
        if fz is None:
            return
        prev = fz.silence_pressure
        fz.add_silence_pressure(-self.SILENCE_EVENT_DROP)
        if prev > 0.001:
            log.info(f"[cognitive_loop] silence_pressure {prev:.3f} -> "
                     f"{fz.silence_pressure:.3f} (event: {reason}, "
                     f"multiplier now {self._idle_multiplier():.2f}×)")

    def signal_user_input(self):
        """Публичный вход: юзер написал сообщение / дёрнул tick / открыл карточку.
        Вызывается из /assist/chat/append, tick_foreground и других
        user-initiated endpoints.
        """
        self._register_user_event(reason="user_input")

    def _advance_tick(self):
        """Единый time-based update всех feeders ProtectiveFreeze + self-prediction.

        За один проход:
          • silence_pressure += dt / SILENCE_RAMP_SECONDS   (таймер тишины)
          • imbalance_pressure EMA ← max всех 4 PE-каналов  (Friston-aggregate)
          • sync_error_ema_fast EMA ← current sync_error     (для sync-seeking)
          • sync_error_ema_slow EMA ← current sync_error     (для prime-directive)
          • neuro.tick_expectation()                         (self-baseline Baddle)
          • user.tick_expectation() уже вызван из update_from_* обёрток

        4 источника imbalance (все нормализованы в [0, 1], берём max):
          • user ‖PE_vec‖ / √3        — behaviour surprise по 3 осям
          • user.agency_gap           — goal-prediction miss (1 − agency)
          • user.hrv_surprise         — physical PE (real Polar baseline)
          • neuro.self_imbalance / √3 — Baddle's PE на самой себе

        Все EMA — time-constant based (независимы от tick frequency).
        """
        now = time.time()
        if self._last_loop_tick_ts <= 0:
            self._last_loop_tick_ts = now
            return
        dt = min(now - self._last_loop_tick_ts, 300.0)  # cap 5 мин на случай пауз
        self._last_loop_tick_ts = now
        if dt <= 0:
            return
        fz = self._get_freeze()
        if fz is None:
            return
        sync_err = 0.0
        combined_imbalance = 0.0
        try:
            gs = get_global_state()
            u = get_user_state()
            sync_err = float(gs.sync_error)

            # Self-prediction: Baddle предсказывает собственную нейрохимию
            gs.neuro.tick_expectation()

            # 4 PE-канала, все в [0, 1]
            sqrt3 = 1.7320508
            user_pe = float(u.imbalance) / sqrt3              # ‖3D PE_vec‖ / √3
            self_pe = float(gs.neuro.self_imbalance) / sqrt3  # self ‖PE_vec‖ / √3
            agency_gap = float(u.agency_gap)
            hrv_pe = float(u.hrv_surprise)
            combined_imbalance = max(user_pe, self_pe, agency_gap, hrv_pe)
        except Exception:
            pass
        fz.feed_tick(dt=dt, sync_err=sync_err, imbalance=combined_imbalance)

        # Counter-wave (Правило 7): R/C bit через гистерезис.
        # User mirror perturbation = sync_error (рассогласование с system).
        # System mirror perturbation = combined_imbalance (max PE по 4 каналам).
        # При perturbation > 0.15 mode → 'C' (counter-wave), при < 0.08 → 'R'.
        # Используется Dispatcher'ом (signals.py): при user.mode == 'C'
        # urgency push-style сигналов понижается на 0.3.
        try:
            u.update_mode(sync_err)
            gs.neuro.update_mode(combined_imbalance)
        except Exception as e:
            log.debug(f"[advance_tick] counter-wave update_mode failed: {e}")

        # Phase D Step 5b — 5-axis chem feeders.
        # System ACh: node-creation rate (cap 10/hour → 1.0). v1 proxy.
        # System GABA: freeze.active boolean + embedding_scattering proxy
        #   (recent_bridges count за час / 5 cap). Больше bridges = больше
        #   distributed attention = низкий damping. v1 proxy без embedding ops.
        # User GABA: derived from focus_residue (existing field).
        # User ACh не fed здесь — только при surprise boost (см. _check_user_surprise).
        # См. docs/neurochem-design.md «ACh+GABA feeders».
        try:
            from .graph_logic import nodes_created_within
            rate = min(1.0, nodes_created_within(3600) / 10.0)
            gs.neuro.feed_acetylcholine(node_creation_rate=rate)
            recent_bridge_count = sum(
                1 for b in self._recent_bridges
                if now - float(b.get("ts", 0)) < 3600.0
            )
            scattering = min(1.0, recent_bridge_count / 5.0)
            gs.neuro.feed_gaba(freeze_active=fz.active,
                                embedding_scattering=scattering)
            u.feed_gaba()
        except Exception as e:
            log.debug(f"[advance_tick] phase_d feeders failed: {e}")

        # Focus residue естественное затухание: −0.05 за минуту покоя;
        # rebuilt через bump_focus_residue в record_action на user-event'ы.
        # См. docs/resonance-model.md.
        try:
            get_user_state().decay_focus_residue(dt_seconds=dt)
        except Exception:
            pass

    def _check_user_surprise(self):
        """OQ #7: detect момента когда юзер встретил неожиданное.

        Два канала (OR):
          A. HRV spike — RMSSD drop > 1.5σ от baseline (требует реального
             sensor_stream'а с HRV readings, иначе skip)
          B. Text markers — «воу», «не ожидал», «??», многоточие, капс
             в последнем user-сообщении

        При detect:
          1. `user_state.apply_surprise_boost(3)` — ускоренный EMA decay
             на 3 tick'а (expectation быстро подстроится к новой реальности)
          2. `record_action("user_surprise")` в граф — для action-memory
             и последующего DMN pump между surprise-нодами
          3. Log + alert с мягким wording (без кнопок, молчаливое
             замечание системы)

        Throttle 5 мин + processed_msg_ts protection — не анализируем
        одно user-сообщение повторно.
        """
        if not self._throttled("_last_user_surprise_check",
                                self.USER_SURPRISE_CHECK_INTERVAL):
            return
        try:
            from .surprise_detector import detect_user_surprise
            from .sensor_stream import get_stream
        except Exception as e:
            log.debug(f"[cognitive_loop] user_surprise import failed: {e}")
            return

        # Последнее user-сообщение — только новое (ts > last_analyzed)
        latest_msg_text = None
        latest_msg_ts = 0.0
        try:
            from .chat_history import load_history
            history = load_history()
            for entry in reversed(history[-20:]):  # достаточно 20 свежих
                if (entry.get("role") or "").lower() == "user":
                    ts = float(entry.get("ts") or 0)
                    if ts > self._last_analyzed_msg_ts:
                        latest_msg_text = entry.get("content") or ""
                        latest_msg_ts = ts
                    break
        except Exception as e:
            log.debug(f"[cognitive_loop] chat_history load failed: {e}")

        # Activity magnitude — для HRV gate (игнорим если юзер бежит)
        activity = None
        try:
            activity = get_stream().recent_activity(window_s=60)
        except Exception:
            activity = None

        # Detect
        result = detect_user_surprise(
            text=latest_msg_text,
            activity_magnitude=activity,
        )

        # Mark message as processed (чтобы не триггерить дважды на одном)
        if latest_msg_ts > 0:
            self._last_analyzed_msg_ts = latest_msg_ts

        if not result.get("event"):
            return

        source = result.get("source", "unknown")
        conf = result.get("confidence", 0.0)
        hrv_info = result.get("hrv") or {}
        txt_info = result.get("text") or {}

        log.info(
            f"[cognitive_loop] user_surprise: source={source} "
            f"conf={conf:.2f} "
            f"hrv_z={hrv_info.get('score', 0)} "
            f"text_markers={txt_info.get('markers', [])}"
        )

        # 1. Apply boost к expectation EMA + ACh boost (Phase D Step 5b).
        # User-side ACh feeder: surprise event = «юзер открыт новому» → bump
        # plasticity до ≥0.85 с быстрым decay 0.85 (см. UserState.feed_acetylcholine).
        # v1: novelty=conf от detector, boost=True. Документировано в
        # docs/neurochem-design.md §6 «User-side ACh».
        try:
            us = get_user_state()
            us.apply_surprise_boost(n_ticks=3)
            us.feed_acetylcholine(novelty=float(conf), boost=True)
        except Exception as e:
            log.debug(f"[cognitive_loop] surprise_boost failed: {e}")

        # 2. Record user-action в граф для action memory / DMN
        #    (не baddle-action — это сам юзер произвёл surprise)
        try:
            from .graph_logic import record_action
            summary = (f"User surprise ({source}): "
                       f"{(latest_msg_text or '')[:80]}" if latest_msg_text
                       else f"User surprise (hrv-only, Δ={hrv_info.get('delta_ms')}ms)")
            record_action(
                actor="user",
                action_kind="user_surprise",
                text=summary[:200],
                context=None,
                extras={
                    "source": source,
                    "confidence": round(conf, 2),
                    "hrv_z_score": hrv_info.get("score"),
                    "text_markers": txt_info.get("markers", []),
                },
            )
        except Exception as e:
            log.debug(f"[cognitive_loop] record user_surprise failed: {e}")

    def _check_prime_directive_record(self):
        """Раз в час пишем snapshot sync_error EMA в data/prime_directive.jsonl.

        Пишется aggregate burnout_imbalance **плюс** decomposition на
        4 канала (user_imbalance, self_imbalance, agency_gap, hrv_surprise) —
        чтобы через 2 мес видеть не только общий тренд, но и какой именно
        PE-канал реально двигал. Нулевые каналы покажут что сигнал слабый
        (например Polar не подключен → hrv_surprise=0 везде).
        """
        if not self._throttled("_last_prime_directive", self.PRIME_DIRECTIVE_INTERVAL):
            return
        fz = self._get_freeze()
        if fz is None:
            return
        try:
            from .prime_directive import record_tick
            gs = get_global_state()
            u = get_user_state()
            record_tick(
                sync_error=float(gs.sync_error),
                sync_error_ema_fast=float(fz.sync_error_ema_fast),
                sync_error_ema_slow=float(fz.sync_error_ema_slow),
                imbalance_pressure=float(fz.imbalance_pressure),
                silence_pressure=float(fz.silence_pressure),
                conflict_accumulator=float(fz.conflict_accumulator),
                user_imbalance=float(u.imbalance),
                self_imbalance=float(gs.neuro.self_imbalance),
                agency_gap=float(u.agency_gap),
                hrv_surprise=float(u.hrv_surprise),
            )
        except Exception as e:
            log.debug(f"[cognitive_loop] prime_directive record failed: {e}")

    # ── Throttle helper ────────────────────────────────────────────────

    def _throttled(self, attr: str, interval_s: float) -> bool:
        """True если пора бежать _check_*, иначе False. Пишет `now` в attr при True.

        Заменяет 12 одинаковых двустрочников вида:
            if now - self._last_X < self.X_INTERVAL: return
            self._last_X = now
        Теперь: `if not self._throttled("_last_X", self.X_INTERVAL): return`.
        """
        now = time.time()
        last = getattr(self, attr, 0.0) or 0.0
        if now - last < interval_s:
            return False
        setattr(self, attr, now)
        return True

    def _throttled_idle(self, attr: str, base_interval_s: float) -> bool:
        """То же что `_throttled`, но интервал умножается на `_idle_multiplier`.
        Используется для investigation-checks (DMN / converge / scout / night /
        cross-graph) — чем дольше рассинхрон, тем реже они идут. Юзер-events
        постепенно снижают рассинхрон → эти циклы снова частят.
        """
        effective = base_interval_s * self._idle_multiplier()
        return self._throttled(attr, effective)


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
        # Юзер тикнул руками → снижаем silence_pressure (user-event)
        self._register_user_event(reason="foreground_tick")
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
        """Main loop body. Phase B (2026-04-25):

        1. Feeders (advance_tick, NE homeostasis)
        2. Build DetectorContext, iterate DETECTORS, collect Signals
        3. Dispatcher.dispatch() → emitted alerts
        4. _add_alert(sig.content) для каждого emitted
        5. Bookkeeping checks (action_outcomes, hrv_push, heartbeat,
           agency_update, activity_cost, graph_flush, user_surprise,
           prime_directive_record) — НЕ alert-emitting, остаются методами
        6. Adaptive sleep по NE

        DMN gate (frozen/ne_quiet/idle_enough) считается в build_detector_context
        и попадает в `ctx.dmn_eligible`. DMN-эвристические детекторы
        (dmn_bridge, dmn_deep, dmn_converge, state_walk, night_cycle) сами
        проверяют ctx.dmn_eligible в первой строке.
        """
        from .detectors import DETECTORS, build_detector_context

        while self.is_running and not self._stop_event.is_set():
            try:
                state = get_global_state()
                now = time.time()

                # 0. Adaptive idle + prime-directive feeders. Один проход
                # обновляет silence_pressure, imbalance_pressure (EMA |PE|),
                # sync_error_ema_{fast,slow}.
                self._advance_tick()

                # 1. NE homeostasis (decay в сторону baseline)
                ne = state.neuro.norepinephrine
                state.neuro.norepinephrine = (
                    ne * (1 - self.NE_DECAY_PER_TICK)
                    + self.NE_BASELINE * self.NE_DECAY_PER_TICK
                )

                # 2. Build context once, iterate detectors
                ctx = build_detector_context(self, now)
                candidates: list[Signal] = []
                for detector in DETECTORS:
                    try:
                        result = detector(ctx)
                    except Exception as e:
                        log.warning(f"[detector] {detector.__name__} failed: {e}")
                        continue
                    if result is None:
                        continue
                    if isinstance(result, Signal):
                        candidates.append(result)
                    else:
                        # Iterable[Signal] — observation_suggestions returns batch
                        try:
                            candidates.extend(result)
                        except TypeError:
                            log.warning(f"[detector] {detector.__name__} returned "
                                        f"non-iterable non-Signal: {type(result)}")

                # 3. Dispatch — urgency sort + budget gate + dedup + drop logging.
                # Counter-wave (Правило 7): user_mode='C' понижает urgency
                # push-style сигналов на 0.3 (см. signals.py COUNTER_WAVE_PUSH_TYPES).
                user_mode = "R"
                try:
                    user_mode = get_user_state().mode
                except Exception:
                    pass
                emitted = self._dispatcher.dispatch(candidates, now,
                                                     user_mode=user_mode)
                for sig in emitted:
                    self._add_alert(sig.content)

                # 4. Bookkeeping (НЕ alert-emitting — не идут через dispatcher)
                self._check_hrv_push()             # HRV → UserState sync (15s)
                self._check_graph_flush()           # auto-save (2 min)
                self._check_activity_cost()         # energy debit per category
                self._check_heartbeat()             # state_graph pulse (5 min)
                self._check_agency_update()         # plan completion EMA (1h)
                self._check_cognitive_load_update()  # capacity load aggregate (5 min)
                self._check_action_outcomes()       # close action-memory entries
                self._check_prime_directive_record()  # sync_error snapshot (1h)
                self._check_user_surprise()         # HRV spike + text markers
            except Exception as e:
                log.warning(f"[cognitive_loop] error: {e}")

            # 5. Adaptive sleep. Cap на HRV_PUSH_INTERVAL чтобы physical
            # state не устаревал.
            try:
                ne = get_global_state().neuro.norepinephrine
                scaled = self.TICK_INTERVAL * max(0.5, 1.2 - ne)
            except Exception:
                scaled = self.TICK_INTERVAL
            scaled = min(scaled, float(self.HRV_PUSH_INTERVAL))
            self._stop_event.wait(scaled)

    # ── Night cycle: Scout + REM emotional + REM creative + Consolidation ──

    def _run_night_cycle(self, ctx) -> Optional[Signal]:
        """24ч ночной цикл — Scout + REM emotional + REM creative + Consolidation.

        5 phases: scout pump+save, REM emotional pump, REM creative merge,
        consolidation (decay/prune/archive), patterns detect, goals rotation.

        Side effects: _last_night_summary, _recent_bridges.
        Phase B (2026-04-25): экстрагировано из _check_night_cycle.
        Returns: Signal с urgency=0.6 (fixed daily) или None.
        """
        if not self._throttled_idle("_last_night_cycle", self.NIGHT_CYCLE_INTERVAL):
            return None
        log.info("[cognitive_loop] night cycle starting")
        self.set_thinking("scout", {"phase": "night"})

        summary: dict = {}

        # Phase 1: Scout pump+save
        if len(_graph.get("nodes", [])) >= 5:
            bridge = self._run_pump_bridge(max_iterations=2, save=True)
            summary["scout"] = {
                "bridge_saved": bridge is not None,
                "bridge_text": (bridge.get("text", "") if bridge else "")[:60],
            }
            # Action Memory: ночной scout-save = action. Outcome 24ч.
            if bridge:
                self._record_baddle_action(
                    "scout_bridge",
                    text=f"Night scout: {(bridge.get('text') or '')[:120]}",
                    extras={"quality": round(bridge.get("quality", 0), 3)},
                )
        else:
            summary["scout"] = {"skipped": "graph_too_small"}

        # Phase 2: REM emotional
        summary["rem_emotional"] = self._rem_emotional()

        # Phase 3: REM creative
        summary["rem_creative"] = self._rem_creative()

        # Phase 4: Consolidation (decay → prune → archive)
        try:
            from .consolidation import consolidate_all
            res = consolidate_all()
            summary["consolidation"] = {
                "decayed": res.get("decay", {}).get("decayed", 0),
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
        cs = s.get("consolidation", {}) or {}
        text = (
            f"Ночной цикл: "
            f"Scout {'+мост' if s['scout'].get('bridge_saved') else 'пропуск'} · "
            f"REM эмо pump {s['rem_emotional'].get('pumped', 0)} · "
            f"REM merge {s['rem_creative'].get('merged', 0)} · "
            f"decay {cs.get('decayed', 0)} / "
            f"прунинг {cs.get('pruned', 0)} / "
            f"архив {cs.get('archived', 0)}"
        )
        # Side effects (preserved): persist summary outside alerts queue —
        # briefing reads _last_night_summary directly. Bridge from scout
        # phase goes to _recent_bridges for morning briefing.
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
        self.clear_thinking()

        import datetime as _dt
        today_str = _dt.date.today().strftime("%Y-%m-%d")
        return Signal(
            type="night_cycle",
            urgency=0.6,
            content={
                "type": "night_cycle",
                "severity": "info",
                "text": text,
                "text_en": text,
                "summary": summary,
            },
            expires_at=ctx.now + 43200,   # 12h — overnight summary stays fresh
            dedup_key=f"night_cycle:{today_str}",
            source="detect_night_cycle",
        )

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

    def _run_dmn_deep_research(self, ctx) -> Optional[Signal]:
        """DMN autonomous deep-research: 3-step execute_deep на open-goal.

        Отличается от `_run_dmn_continuous` — полноценное исследование
        engine'ом chat'а на одной цели. Возвращает Signal с novelty-based
        urgency или None.

        Guards: ≥ 1 open goal, граф < 30 нод. Side effects: _recent_bridges.
        Phase B (2026-04-25): экстрагировано из _check_dmn_deep_research.
        """
        # Adaptive idle: интервал масштабируется _idle_multiplier'ом (silence + PE)
        if not self._throttled_idle("_last_dmn_deep", self.DMN_DEEP_INTERVAL):
            return None
        try:
            from .goals_store import list_goals
            open_goals = list_goals(status="open", limit=5)
        except Exception:
            return None
        if not open_goals:
            return None
        if len(_graph.get("nodes", [])) > 30:
            return None
        goal = open_goals[0]
        goal_text = (goal.get("text") or "")[:200]
        if not goal_text:
            return None

        log.info(f"[cognitive_loop] DMN deep-research starting on goal: {goal_text[:60]}")
        self.set_thinking("synthesize", {"goal": goal_text[:60]})
        try:
            from .assistant_exec import execute_deep
            result = execute_deep(goal_text, lang="ru", mode_id="horizon",
                                   profile_hint="")
        except Exception as e:
            log.warning(f"[cognitive_loop] DMN deep failed: {e}")
            self.clear_thinking()
            return None

        card = (result.get("cards") or [{}])[0]
        synthesis = (card.get("synthesis") or "")[:150]
        nodes_created = card.get("nodes_created") or 0
        trace_len = len(card.get("trace") or [])

        # Side effect: register bridge для morning_briefing
        if synthesis:
            self._recent_bridges.append({
                "ts": time.time(),
                "text": f"[deep-research] {goal_text[:40]}: {synthesis[:80]}",
                "source": "dmn_deep",
                "quality": 0.7,
            })
            self._recent_bridges = self._recent_bridges[-10:]
            # Phase D: System ACh boost — DMN deep research success.
            # Quality proxy = min(1.0, nodes_created/10) — research breadth.
            try:
                from .horizon import get_global_state
                deep_quality = min(1.0, max(0.0, nodes_created / 10.0))
                get_global_state().neuro.feed_acetylcholine(
                    node_creation_rate=0.0, bridge_quality=deep_quality)
            except Exception as e:
                log.debug(f"[dmn_deep] ACh feed failed: {e}")
        log.info(f"[cognitive_loop] DMN deep done: {nodes_created} nodes, synthesis={synthesis[:60]}")
        self.clear_thinking()

        # urgency: novelty proxy = nodes_created (clamped 0..1 на 10 нод max)
        novelty = min(1.0, max(0.0, nodes_created / 10.0))
        return Signal(
            type="dmn_deep_research",
            urgency=0.4 + 0.3 * novelty,
            content={
                "type": "dmn_deep_research",
                "severity": "info",
                "text": f"🧠 DMN автономно исследовала цель «{goal_text[:50]}»: "
                        f"{nodes_created} нод, {trace_len} шагов."
                        + (f" Синтез: {synthesis[:100]}" if synthesis else ""),
                "text_en": f"DMN autonomous research on '{goal_text[:50]}': "
                           f"{nodes_created} nodes, {trace_len} steps. "
                           f"{synthesis[:100]}",
                "goal_id": goal.get("id"),
                "goal_text": goal_text,
                "nodes_created": nodes_created,
                "trace_len": trace_len,
                "synthesis": synthesis,
                "card": card,
            },
            expires_at=ctx.now + 7200,   # 2h — research insights stay relevant
            dedup_key=f"dmn_deep:{goal.get('id')}",
            source="detect_dmn_deep_research",
        )

    def _run_dmn_converge(self, ctx) -> Optional[Signal]:
        """DMN server-side autorun loop до STABLE — аналог graph-tab Run.

        Раз в час (когда юзер idle + NE low) tick→execute→tick-loop на
        текущем workspace графе. Заканчивается converged/wall_time/stalled/
        max_steps. Forced synthesis at end.

        Side effects: _recent_bridges.append (per pump + final synthesis).
        Phase B (2026-04-25): экстрагировано из _check_dmn_converge.
        Returns: Signal с urgency=0.5 (fixed) или None.
        """
        if not self._throttled_idle("_last_dmn_converge", self.DMN_CONVERGE_INTERVAL):
            return None
        nodes_n = len(_graph.get("nodes", []))
        if nodes_n < 5 or nodes_n > 40:
            return None
        # Depth config из settings — юзер может override class-level дефолты
        max_steps = self.DMN_CONVERGE_MAX_STEPS
        stall_window = self.DMN_CONVERGE_STALL_WINDOW
        max_wall_s = self.DMN_CONVERGE_MAX_WALL_S
        try:
            from .api_backend import get_depth_defaults
            _dd = get_depth_defaults()
            max_steps = int(_dd.get("dmn_converge_max_steps", max_steps))
            stall_window = int(_dd.get("dmn_converge_stall_window", stall_window))
            max_wall_s = int(_dd.get("dmn_converge_max_wall_s", max_wall_s))
        except Exception:
            pass
        log.info(f"[cognitive_loop] DMN converge-loop starting "
                 f"(nodes={nodes_n} max_steps={max_steps} max_wall={max_wall_s}s stall={stall_window})")

        from .tick_nand import tick_emergent
        from .graph_logic import _compute_edges, _add_node, _graph_generate
        # executor dispatcher server-side
        steps_taken = 0
        actions_log = []
        final_action = None
        wall_start = time.time()
        last_growth_step = 0
        last_node_count = len(_graph.get("nodes", []))
        exit_reason = "converged"
        for step in range(max_steps):
            # Wall-time guard — не жжём LM дольше лимита
            if time.time() - wall_start > max_wall_s:
                exit_reason = "wall_time_limit"
                break
            # Progress guard — если STALL_WINDOW тиков без роста нод, стоп
            cur_n = len(_graph.get("nodes", []))
            if cur_n > last_node_count:
                last_node_count = cur_n
                last_growth_step = step
            elif step - last_growth_step >= stall_window:
                exit_reason = "stalled_no_growth"
                break

            nodes = _graph.get("nodes", [])
            edges = _compute_edges(nodes, 0.91, "embedding")
            tick_result = tick_emergent(nodes, edges, _graph,
                                         threshold=0.91, stable_threshold=0.8,
                                         min_hyp=5, user_initiated=False)
            action = tick_result.get("action", "?")
            actions_log.append(action)
            steps_taken += 1
            final_action = action
            if action in ("stable", "done", "none"):
                exit_reason = "converged"
                break
            # Dispatch action server-side (минимальный executor)
            target = tick_result.get("target")
            try:
                if action == "think_toward":
                    goal_idx = next((i for i, n in enumerate(nodes)
                                     if n.get("type") == "goal"), None)
                    if goal_idx is None:
                        break
                    goal_text = nodes[goal_idx].get("text", "")
                    res, _ = _graph_generate(
                        [{"role": "system", "content": "/no_think"},
                         {"role": "user",
                          "content": f"Тема: {goal_text}\nСгенерируй 3 гипотезы, по одной на строке."}],
                        max_tokens=2000, temp=0.7, top_k=40,
                    )
                    for line in res.split("\n"):
                        t = line.strip(" -•*1234567890.")
                        if len(t) > 8:
                            _add_node(t, depth=1, node_type="hypothesis", confidence=0.5)
                elif action == "elaborate" and isinstance(target, int):
                    if 0 <= target < len(nodes):
                        t_text = nodes[target].get("text", "")
                        res, _ = _graph_generate(
                            [{"role": "system", "content": "/no_think"},
                             {"role": "user",
                              "content": f"Углуби: «{t_text}». Дай 2 evidence."}],
                            max_tokens=2000, temp=0.5, top_k=30,
                        )
                        for line in res.split("\n"):
                            t = line.strip(" -•*1234567890.")
                            if len(t) > 8:
                                eidx = _add_node(t, depth=2, node_type="evidence", confidence=0.65)
                                _graph["edges"].setdefault("directed", []).append([target, eidx])
                elif action == "collapse" and isinstance(target, list) and len(target) >= 2:
                    # Server-side minimal collapse: mark first as hub, drop similar
                    # Упрощённо: просто поднимаем confidence первого, не удаляем — safer.
                    if 0 <= target[0] < len(nodes):
                        nodes[target[0]]["confidence"] = min(0.95,
                            nodes[target[0]].get("confidence", 0.5) + 0.1)
                elif action == "smartdc" and isinstance(target, int):
                    if 0 <= target < len(nodes):
                        t_text = nodes[target].get("text", "")
                        res, _ = _graph_generate(
                            [{"role": "system", "content": "/no_think"},
                             {"role": "user",
                              "content": f"Гипотеза: {t_text}\nFOR/AGAINST/SYNTHESIS. 3 строки."}],
                            max_tokens=2000, temp=0.4, top_k=20,
                        )
                        # Confidence-update по длинам
                        fr = ag = sy = ""
                        for l in res.split("\n"):
                            L = l.strip()
                            if L.upper().startswith(("FOR:", "ЗА:")):
                                fr = L.split(":",1)[1].strip()
                            elif L.upper().startswith(("AGAINST:","ПРОТИВ:")):
                                ag = L.split(":",1)[1].strip()
                            elif L.upper().startswith(("SYNTHESIS:","СИНТЕЗ:")):
                                sy = L.split(":",1)[1].strip()
                        if fr and ag:
                            nodes[target]["confidence"] = (
                                0.75 if len(fr) >= len(ag) else 0.35)
                elif action == "pump":
                    bridge = self._run_pump_bridge(max_iterations=1, save=True)
                    if bridge:
                        b_quality = bridge.get("quality", 0)
                        self._recent_bridges.append({
                            "ts": time.time(),
                            "text": (bridge.get("text") or "")[:100],
                            "source": "converge_loop",
                            "quality": b_quality,
                        })
                        self._recent_bridges = self._recent_bridges[-10:]
                        # Phase D: System ACh boost — converge loop bridge.
                        if b_quality > 0.5:
                            try:
                                from .horizon import get_global_state
                                get_global_state().neuro.feed_acetylcholine(
                                    node_creation_rate=0.0, bridge_quality=b_quality)
                            except Exception as e:
                                log.debug(f"[dmn_converge] ACh feed failed: {e}")
            except Exception as e:
                log.debug(f"[cognitive_loop] converge step {step} {action}: {e}")
                break

        final_nodes = len(_graph.get("nodes", []))
        from collections import Counter
        actions_count = Counter(actions_log)

        # Forced synthesis — общий helper `force_synthesize_top` из
        # graph_logic. Один source of truth для DMN + graph-tab autorun.
        forced_synthesis = None
        forced_confidence = None
        try:
            from .graph_logic import force_synthesize_top
            syn = force_synthesize_top(n=5, lang="ru", max_tokens=3000)
            if syn:
                forced_synthesis = syn["text"]
                forced_confidence = syn["confidence"]
                self._recent_bridges.append({
                    "ts": time.time(),
                    "text": f"[converge synthesis] {forced_synthesis[:100]}",
                    "source": "dmn_converge",
                    "quality": forced_confidence or 0.5,
                })
                self._recent_bridges = self._recent_bridges[-10:]
                log.info(f"[cognitive_loop] forced synth node #{syn.get('node_idx')} conf {forced_confidence}")
        except Exception as e:
            log.debug(f"[cognitive_loop] forced synth failed: {e}")

        wall_s = round(time.time() - wall_start, 1)
        if exit_reason == "converged" and final_action in ("stable", "done"):
            status_emoji = "✓"
            status_text = f"сошлось за {steps_taken} шагов ({wall_s}s)"
        elif exit_reason == "wall_time_limit":
            status_emoji = "⏱"
            status_text = f"wall-time лимит ({wall_s}s, {steps_taken} шагов)"
        elif exit_reason == "stalled_no_growth":
            status_emoji = "⊘"
            status_text = f"stall — нет роста {stall_window} шагов подряд ({steps_taken} total)"
        else:
            status_emoji = "⋯"
            status_text = f"MAX_STEPS {max_steps} ({wall_s}s)"
        summary_txt = (f"🔁 DMN converge: {status_emoji} {status_text}. "
                       f"Актов: {dict(actions_count)}. Нод стало: {final_nodes}.")
        if forced_synthesis:
            summary_txt += f"\n💡 Синтез (conf {forced_confidence}): {forced_synthesis[:150]}"

        log.info(f"[cognitive_loop] DMN converge done: {summary_txt}")
        return Signal(
            type="dmn_converge",
            urgency=0.5,
            content={
                "type": "dmn_converge",
                "severity": "info",
                "text": summary_txt,
                "text_en": summary_txt,
                "steps_taken": steps_taken,
                "final_action": final_action,
                "exit_reason": exit_reason,
                "wall_s": wall_s,
                "actions_count": dict(actions_count),
                "final_node_count": final_nodes,
                "synthesis": forced_synthesis,
                "synthesis_confidence": forced_confidence,
            },
            expires_at=ctx.now + 7200,   # 2h
            dedup_key="dmn_converge",
            source="detect_dmn_converge",
        )

    def _run_dmn_continuous(self, ctx) -> Optional[Signal]:
        """DMN pump-bridge между двумя удалёнными нодами графа.

        Side effects (preserved): _recent_bridges.append, _record_baddle_action.
        Filter: quality > 0.5 AND len(text) >= 10 — пустой LLM-output не emit.

        Returns: Signal с urgency = 0.2 + 0.7 × quality, или None.
        Phase B (2026-04-25): экстрагировано из _check_dmn_continuous,
        _add_alert убран — dispatcher эмитит через _loop().
        """
        if len(_graph.get("nodes", [])) < 4:
            return None
        # Adaptive idle: интервал растягивается _idle_multiplier'ом — чем
        # выше combined_burnout тем реже pump. При burnout=0 — стандартные
        # 10 мин, при burnout=1 — 100 мин. Без бинарных порогов.
        if not self._throttled_idle("_last_dmn", self.DMN_INTERVAL):
            return None

        self.set_thinking("pump", {"source": "dmn"})
        try:
            bridge = self._run_pump_bridge(max_iterations=1, save=False)
        finally:
            self.clear_thinking()

        # Guard: пустой/слишком короткий текст моста = LLM-generation failed,
        # alert без тела был бы как пустой «🔗 DMN-инсайт:» — skip.
        if not bridge:
            return None
        quality = bridge.get("quality", 0)
        bridge_text = (bridge.get("text") or "").strip()
        if quality <= 0.5 or len(bridge_text) < 10:
            return None

        # Side effects (произошли — bridge production реален независимо от
        # dispatcher decision)
        self._recent_bridges.append({
            "ts": time.time(),
            "text": (bridge.get("text") or "")[:100],
            "quality": quality,
            "source": "dmn",
        })
        self._recent_bridges = self._recent_bridges[-10:]
        self._record_baddle_action(
            "dmn_bridge",
            text=f"DMN bridge: {bridge_text[:120]}",
            extras={"quality": round(quality, 3)},
        )

        # Phase D: System ACh boost — DMN нашёл значимый мост (quality > 0.5).
        # Closes 50% отложенных Phase D feeders (см. docs/neurochem-design.md §6.5).
        # bridge_quality передаётся как secondary feeder; node_creation_rate здесь 0
        # (этот канал кормится в _advance_tick).
        try:
            from .horizon import get_global_state
            get_global_state().neuro.feed_acetylcholine(
                node_creation_rate=0.0, bridge_quality=quality)
        except Exception as e:
            log.debug(f"[dmn_bridge] ACh feed failed: {e}")

        return Signal(
            type="dmn_bridge",
            urgency=min(1.0, 0.2 + 0.7 * quality),
            content={
                "type": "dmn_bridge",
                "severity": "info",
                "text": f"DMN-инсайт: {bridge_text[:80]} (quality {quality:.0%})",
                "text_en": f"DMN insight: {bridge_text[:80]} (quality {quality:.0%})",
                "bridge": bridge,
            },
            expires_at=ctx.now + 1800,   # 30 мин — bridge stale fast
            dedup_key="dmn_bridge",
            source="detect_dmn_bridge",
        )

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

    # ── Morning briefing push (once per day, after wake_hour) ───────────

    def _build_morning_briefing_sections(self) -> list:
        """Структурированный briefing — список карточек {emoji, title, subtitle, kind}.

        UI рендерит как набор секций (см. mockup Thursday briefing). Порядок:
          1. Sleep      (из activity log)
          2. Recovery   (HRV energy_recovery + named_state)
          3. Capacity   (3-zone)
          4. Overnight  (Scout bridges найденные ночью)
          5. Activity   (вчера: N часов по категориям)
          6. Goals      (открытые + первая)
          7. Pattern    (weekday hint если есть)

        kind ∈ {info, warn, highlight, neutral} → CSS-класс акцента.
        """
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

        # 3. Capacity zone
        try:
            metrics = get_global_state().get_metrics()
            user = metrics.get("user_state") or {}
            cap_zone = user.get("capacity_zone")
            if cap_zone:
                emoji = {"green": "🟢", "yellow": "🟡",
                         "red": "🔴"}.get(cap_zone, "⚪")
                reasons = user.get("capacity_reason") or []
                kind = ("info" if cap_zone == "green"
                        else "warn" if cap_zone == "yellow"
                        else "alert")
                sub = (", ".join(reasons) if reasons
                       else "все три контура ok")
                sections.append({"emoji": emoji,
                                 "title": f"Capacity {cap_zone}",
                                 "subtitle": sub, "kind": kind})
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

        # User state (named region + capacity zone)
        try:
            cs = get_global_state()
            metrics = cs.get_metrics()
            user = metrics.get("user_state") or {}
            named = user.get("named_state") or {}
            if named.get("label"):
                bits.append(f"Состояние: {named['label'].lower()}.")
            cap_zone = user.get("capacity_zone")
            if cap_zone and cap_zone != "green":
                reasons = user.get("capacity_reason") or []
                detail = ("(" + ", ".join(reasons) + ")") if reasons else ""
                bits.append(f"Capacity: {cap_zone} {detail}".strip() + ".")
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
        mgr = get_hrv_manager()
        if not mgr.is_running:
            return
        if not self._throttled("_last_hrv_push", self.HRV_PUSH_INTERVAL):
            return
        try:
            state = mgr.get_baddle_state() or {}
            get_user_state().update_from_hrv(
                coherence=state.get("coherence"),
                rmssd=state.get("rmssd"),
                stress=state.get("stress"),
                activity=state.get("activity_magnitude"),
            )
        except Exception as e:
            log.debug(f"[cognitive_loop] hrv push failed: {e}")

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
        if not self._throttled("_last_heartbeat", self.HEARTBEAT_INTERVAL):
            return
        now = time.time()
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
                "capacity_zone": us.get("capacity_zone"),
                "cognitive_load": us.get("cognitive_load_today"),
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

    # ── Agency update (OQ #2, 5-я ось) ─────────────────────────────────

    def _check_cognitive_load_update(self):
        """Phase C: раз в 5 мин пересчитать UserState.cognitive_load_today.

        Compute throttle (300s) — pull из activity_log + sync_error EMA для
        capacity zone. Не emit'ит alert, это bookkeeping.

        Spec: docs/capacity-design.md §Дневная метрика.
        """
        if not self._throttled("_last_cognitive_load_update", 300):
            return
        try:
            get_user_state().update_cognitive_load()
        except Exception as e:
            log.debug(f"[cognitive_load] update failed: {e}")

    def _check_agency_update(self):
        """Раз в час пересчитываем `UserState.agency` из сегодняшних
        plans + completed activities. EMA-сглаживание внутри UserState.

        Источник: `plans.schedule_for_day()` даёт запланированное +
        флаг `done`. Completed = count(done=True), planned = len(schedule).
        Если planned=0 — пропускаем (нет сигнала, не шумим baseline).
        """
        if not self._throttled("_last_agency_update", self.AGENCY_UPDATE_INTERVAL):
            return
        try:
            from .plans import schedule_for_day
        except Exception:
            return
        try:
            schedule = schedule_for_day()
        except Exception as e:
            log.debug(f"[cognitive_loop] schedule_for_day failed: {e}")
            return
        if not schedule:
            return
        planned = len(schedule)
        completed = sum(1 for s in schedule if s.get("done"))
        try:
            get_user_state().update_from_plan_completion(completed=completed, planned=planned)
            log.debug(f"[cognitive_loop] agency update: {completed}/{planned}")
        except Exception as e:
            log.debug(f"[cognitive_loop] agency update failed: {e}")

    # ── Active sync-seeking: Baddle пишет первым когда долго молчит ────
    #
    # Phase B (2026-04-25): сама logic переехала в `detect_sync_seeking`
    # (src/detectors.py). Helper `_generate_sync_seeking_message` остаётся
    # здесь — детектор вызывает его через `ctx.loop._generate_sync_seeking_message(...)`.

    def _generate_sync_seeking_message(self, silence: float, idle_hours: float) -> tuple[str, str]:
        """LLM-генерация мягкого сообщения для восстановления контакта.

        Контекст в prompt: время суток, уровень тишины, сколько юзер
        молчит, последнее что он делал, recent topics из графа, HRV-снимок.
        Temperature высокая (0.9) + top_k большой → каждый раз разное.

        Returns: (text, tone) — tone выбирается LLM из палитры.
        Fallback при ошибке LLM: случайный короткий шаблон.
        """
        import random
        import datetime as _dt

        # --- Контекст ---
        now = _dt.datetime.now()
        hour = now.hour
        if 5 <= hour < 11:      time_of_day = "утро"
        elif 11 <= hour < 17:   time_of_day = "день"
        elif 17 <= hour < 23:   time_of_day = "вечер"
        else:                    time_of_day = "ночь"

        # Уровень тишины → severity hint
        if silence < 0.4:    severity = "лёгкий"
        elif silence < 0.7:  severity = "средний"
        else:                 severity = "высокий"

        # Last activity category
        last_activity = ""
        try:
            from .activity_log import list_activities
            recent = list_activities(limit=1)
            if recent:
                la = recent[0]
                last_activity = la.get("category", "") or la.get("name", "") or ""
        except Exception:
            pass

        # Recent graph topics (top 3 by access)
        recent_topics = []
        try:
            from .graph_logic import _graph
            nodes = _graph.get("nodes", [])[-10:]  # последние 10 для context
            seen = set()
            for n in nodes:
                topic = n.get("topic", "") or n.get("text", "")[:40]
                if topic and topic not in seen:
                    seen.add(topic)
                    recent_topics.append(topic)
                if len(recent_topics) >= 3:
                    break
        except Exception:
            pass

        # HRV short summary
        hrv_hint = ""
        try:
            us = get_user_state()
            coh = us.hrv_coherence
            if coh is not None:
                if coh > 0.6:     hrv_hint = "спокоен"
                elif coh < 0.35:  hrv_hint = "напряжён"
                else:              hrv_hint = "смешанное"
        except Exception:
            pass

        # named_state (8-region РГК-карта по chem profile) имеет приоритет
        # над frequency_regime. 5-axis chem точнее ловит режим чем 2-axis
        # HRV-derived freq. См. docs/neurochem-design.md.
        # Mapping из РГК v1.0 §«Влияние на промпт-роутинг»:
        #   flow → ambient (поддержка потока, не мешать)
        #   stable → simple (нейтрально)
        #   focus → reference (по делу, без воды — туннельное внимание)
        #   explore → curious (поощрить exploration, аналогии)
        #   overload/apathy/burnout → caring (мягко, без давления)
        #   insight → reference (зафиксировать аттрактор)
        _NAMED_TO_TONE = {
            "flow": "ambient", "stable": "simple", "focus": "reference",
            "explore": "curious", "overload": "caring", "apathy": "caring",
            "burnout": "caring", "insight": "reference",
        }
        try:
            user = get_user_state()
            ns_key = (user.named_state or {}).get("key")
            freq = user.frequency_regime
        except Exception:
            ns_key = None
            freq = "flat"

        if ns_key in _NAMED_TO_TONE:
            heuristic_tone = _NAMED_TO_TONE[ns_key]
        elif freq == "short_wave":
            heuristic_tone = "simple" if not recent_topics else "reference"
        elif freq == "long_wave":
            heuristic_tone = "curious" if recent_topics else "ambient"
        elif silence > 0.7:           heuristic_tone = "caring"
        elif hrv_hint == "напряжён":  heuristic_tone = "caring"
        elif recent_topics:           heuristic_tone = "reference"
        elif time_of_day == "ночь":   heuristic_tone = "ambient"
        else:                          heuristic_tone = "simple"

        # Counter-wave (Правило 7): при user.mode='C' резонатор уже активно
        # компенсирует рассинхрон. Push-style тоны (caring/simple) добавляют
        # шум: эмо-жалость или обыденный «как ты?» при desync воспринимаются
        # как давление. Сдвиг в reference (опираемся на факт из графа если
        # есть topics) или curious (мягкое любопытство без оценки).
        # Симметрия с signals.COUNTER_WAVE_PUSH_TYPES — там тот же тип
        # сигнала понижается по urgency, здесь — по тону.
        try:
            if get_user_state().mode == "C" and heuristic_tone in ("caring", "simple"):
                _old = heuristic_tone
                heuristic_tone = "reference" if recent_topics else "curious"
                log.debug(f"[counter-wave] sync_seeking tone {_old}→{heuristic_tone}")
        except Exception:
            pass

        # Action Memory (этап 5): если у нас есть история past sync_seeking
        # outcomes, override heuristic_tone если есть явный winner.
        # Cold start (<3 closed actions) → scoring=all 0 → heuristic wins.
        try:
            from .graph_logic import score_action_candidates
            tone_candidates = ["caring", "ambient", "curious", "reference", "simple"]
            # Map time_of_day русский → english для context-match
            tod_map = {"утро": "morning", "день": "day",
                        "вечер": "evening", "ночь": "night"}
            scores = score_action_candidates(
                action_kind="sync_seeking",
                candidates=tone_candidates,
                variant_field="tone",
                time_of_day=tod_map.get(time_of_day),
                min_history=3,
            )
            # Если есть non-trivial преимущество (max ≥ 0.05 разница над 2-м) — берём winner
            sorted_scores = sorted(scores.items(), key=lambda kv: -kv[1])
            if sorted_scores and sorted_scores[0][1] >= 0.05:
                top, top_score = sorted_scores[0]
                second_score = sorted_scores[1][1] if len(sorted_scores) > 1 else 0.0
                if (top_score - second_score) >= 0.05:
                    log.info(f"[action-memory] sync_seeking tone override: "
                             f"{heuristic_tone} → {top} "
                             f"(score={top_score:+.3f}, heuristic lost)")
                    heuristic_tone = top
        except Exception as e:
            log.debug(f"[action-memory] score tones failed: {e}")

        # --- LLM prompt ---
        system = (
            "/no_think\n"
            "Ты — Baddle, партнёр по мышлению одного человека. Он не писал тебе "
            f"{idle_hours:.0f} часов. Молчание {severity}.\n"
            "Напиши ОДНО короткое (1 предложение, макс 100 знаков) мягкое "
            "сообщение — попытка восстановить контакт. Это НЕ приветствие, "
            "НЕ представление возможностей, НЕ напоминание. Просто присутствие.\n"
            "БЕЗ восклицаний. БЕЗ сиропа. БЕЗ «не забудь». БЕЗ emoji. БЕЗ кавычек.\n"
            "Ответ — ТОЛЬКО текст сообщения, одной строкой. Без префиксов, "
            "без лейблов, без объяснений."
        )
        user_ctx_parts = [f"Время: {time_of_day}"]
        if last_activity:
            user_ctx_parts.append(f"Последнее что делал: {last_activity}")
        if recent_topics:
            user_ctx_parts.append(f"Темы в графе: {', '.join(recent_topics[:3])}")
        if hrv_hint:
            user_ctx_parts.append(f"HRV: {hrv_hint}")
        user_prompt = "\n".join(user_ctx_parts) + "\n\nСообщение:"

        try:
            from .graph_logic import _graph_generate
            res, _ent = _graph_generate(
                [{"role": "system", "content": system},
                 {"role": "user", "content": user_prompt}],
                max_tokens=150, temp=0.9, top_k=60,
            )
            res = (res or "").strip()
            if res:
                # Берём первую непустую строку — «сообщение»
                lines = [l.strip() for l in res.split("\n") if l.strip()]
                if lines:
                    text = lines[0].strip(' "«»')
                    # Отфильтровываем хвостовые tone-слова если модель их
                    # приклеила (например «... ambient» в конце)
                    for tw in ("caring", "ambient", "curious", "reference", "simple"):
                        if text.lower().endswith(" " + tw):
                            text = text[:-len(tw)].rstrip(" .,")
                            break
                    # Sanity: 3..200 знаков, не начинается с лейбла
                    bad_starts = ("message:", "сообщение:", "tone:", "тон:")
                    if (3 <= len(text) <= 200
                            and not any(text.lower().startswith(b) for b in bad_starts)):
                        return text, heuristic_tone
        except Exception as e:
            log.debug(f"[cognitive_loop] sync_seeking LLM failed: {e}")

        # Fallback: случайный мягкий шаблон
        fallbacks_by_severity = {
            "лёгкий": ["Как ты?", "Что сегодня?", "Я тут, если нужно.", "На связи?"],
            "средний": ["Давно не слышу. Всё в порядке?", "Ты как? Я рядом.",
                        "Если появится момент — я тут.", "Что происходит у тебя?"],
            "высокий": ["Ты где? Всё ли ок?", "Я начал скучать. Ты в порядке?",
                        "Давно тебя нет. Просто отмечусь — я тут.",
                        "Хочу убедиться что с тобой всё хорошо."],
        }
        return random.choice(fallbacks_by_severity[severity]), "simple"

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

    # ── Graph auto-save (embeddings + nodes persistence) ─────────────

    def _check_graph_flush(self):
        """Каждые ~2 мин сбрасываем граф на диск.

        При крэше/рестарте без periodic flush терялись бы новые ноды и
        embeddings, накопленные с момента старта. Auto-flush делает
        persistence надёжным.
        """
        if not self._throttled("_last_graph_flush", self.GRAPH_FLUSH_INTERVAL):
            return
        try:
            from .graph_store import save_graph
            save_graph()
        except Exception as e:
            log.debug(f"[cognitive_loop] graph flush failed: {e}")

    # ── Alerts queue ───────────────────────────────────────────────────

    def _add_alert(self, alert: dict):
        """Append alert в queue. Dedup делает Dispatcher через dedup_key + window_s."""
        with self._lock:
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
            "last_dmn_deep": self._last_dmn_deep,
            "last_dmn_converge": self._last_dmn_converge,
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
