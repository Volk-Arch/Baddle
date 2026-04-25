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
    LOW_ENERGY_THRESHOLD = 30         # ниже этого — тяжёлые решения предлагаем отложить
    LOW_ENERGY_CHECK_INTERVAL = 30 * 60  # раз в 30 мин — не спамить
    HEAVY_MODES = ("dispute", "tournament", "bayes", "race", "builder", "cascade", "scales")

    # Plan reminders: push-alert за N min до planned events
    PLAN_REMINDER_MINUTES = 10        # за сколько минут до события пушить
    PLAN_REMINDER_CHECK_INTERVAL = 60 # раз в минуту проверяем upcoming

    # Recurring goals lag: для «вечных» целей (kind=recurring) проверяем
    # отставание от расписания (expected_by_now vs done_today).
    RECURRING_LAG_CHECK_INTERVAL = 30 * 60   # каждые 30 мин
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
        "suggestion_recurring":    7 * 86400,     # 7 дней на accept/reject
        "suggestion_constraint":   7 * 86400,
        "suggestion_generic":      7 * 86400,
        "reminder_plan":           30 * 60,
        "alert_low_energy":        60 * 60,
        "morning_briefing":        4 * 3600,
    }
    ACTION_TIMEOUT_DEFAULT = 60 * 60              # 1 час если kind не в словаре

    # Active sync-seeking (resonance protocol механика #3):
    # Когда `freeze.silence_pressure` высокий И юзер давно не писал — Baddle
    # пишет мягкое сообщение чтобы восстановить контакт. LLM генерирует
    # текст с учётом контекста (время дня, recent topics, silence-уровень),
    # чтобы каждый раз по-разному. Не nag — попытка резонанс зеркала.
    SYNC_SEEKING_SILENCE_MIN = 0.3         # ниже — не пушим, тишина ещё мягкая
    SYNC_SEEKING_IDLE_SECONDS = 2 * 3600   # физически давно не писал (2 часа)
    SYNC_SEEKING_INTERVAL = 2 * 3600       # не чаще раза в 2 часа
    SYNC_SEEKING_COUNTERFACTUAL_RATE = 0.10  # в 10% случаев когда все gate'ы
                                             # прошли — намеренно промолчать и
                                             # залогировать как counterfactual.
                                             # Baseline для A/B измерения:
                                             # двигает ли recovery само вмешательство
                                             # Baddle vs юзер возвращается сам.
    # Защита от шума: если любой proactive alert был недавно — пропускаем
    SYNC_SEEKING_QUIET_AFTER_OTHER = 30 * 60   # 30 мин после последнего alert

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
        self._last_low_energy_check = 0.0  # дроссель low_energy_heavy alerts
        self._last_plan_reminder_check = 0.0
        self._reminded_plan_keys: set = set()  # "plan_id:YYYY-MM-DD" dedup
        self._last_evening_retro_date: str = ""  # YYYY-MM-DD последнего ретро
        self._last_heartbeat = 0.0
        self._last_dmn_deep = 0.0  # таймер DMN autonomous deep-research
        self._last_dmn_converge = 0.0  # таймер server-side tick-autorun до STABLE
        self._last_dmn_cross = 0.0  # таймер cross-graph bridge scan
        self._last_recurring_check = 0.0  # таймер recurring lag check
        self._notified_lag: dict[str, float] = {}  # goal_id → ts последнего alert
        self._last_suggestions_check = 0.0  # таймер observation suggestions
        self._last_sync_seeking = 0.0       # таймер active sync-seeking
        self._last_proactive_alert_ts = 0.0 # любой proactive alert (не sync-seek'им если недавно)
        self._last_agency_update = 0.0      # таймер agency EMA update
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
        # См. planning/phase-b-signal-dispatcher.md.
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
                # User-chat закрывает sync_seeking, reminder, briefing
                if later_kind == "user_chat" and kind in ("sync_seeking", "reminder_plan",
                                                            "morning_briefing", "alert_low_energy"):
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

        # 1. Apply boost к expectation EMA
        try:
            get_user_state().apply_surprise_boost(n_ticks=3)
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

    def _log_throttle_drop(self, check: str, **ctx):
        """Записать что proactive check сработал по pre-conditions, но
        throttle заблокировал. Данные для OQ «теряем ли ценное?»:
        через 2 нед анализа увидим какие дропы high-urgency (silence=0.9,
        lag=5, patterns=3) vs noise (silence=0.32, lag=1).

        Вызывается ТОЛЬКО из alert-emitting check'ов в точке «я бы
        выпустил alert сейчас, но throttle не даёт». Не инструментировать
        bookkeeping-циклы (heartbeat / flush / hrv_push / consolidation).

        Пишет в `data/throttle_drops.jsonl` append-only. Формат:
            {"ts": 1234.5, "check": "sync_seeking", "ctx": {...}}
        """
        try:
            from .paths import THROTTLE_DROPS_FILE
            entry = {"ts": round(time.time(), 3), "check": check, "ctx": ctx}
            with THROTTLE_DROPS_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            log.debug(f"[throttle_log] write failed: {e}")

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

                # 3. Dispatch — urgency sort + budget gate + dedup + drop logging
                emitted = self._dispatcher.dispatch(candidates, now)
                for sig in emitted:
                    self._add_alert(sig.content)

                # 4. Bookkeeping (НЕ alert-emitting — не идут через dispatcher)
                self._check_hrv_push()             # HRV → UserState sync (15s)
                self._check_graph_flush()           # auto-save (2 min)
                self._check_activity_cost()         # energy debit per category
                self._check_heartbeat()             # state_graph pulse (5 min)
                self._check_agency_update()         # plan completion EMA (1h)
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
                        self._recent_bridges.append({
                            "ts": time.time(),
                            "text": (bridge.get("text") or "")[:100],
                            "source": "converge_loop",
                            "quality": bridge.get("quality", 0),
                        })
                        self._recent_bridges = self._recent_bridges[-10:]
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

    def _check_state_walk(self):
        """DMN по state-графу: похожие моменты из прошлого → эпизодический alert.

        1. Прогреваем embedding-кэш для хвоста (≤30 entries) — амортизация.
        2. Берём embedding текущей сигнатуры.
        3. query_similar(k=3), фильтруем < 1 час (тривиально-свежие).
        4. Если топ-match достаточно близкий — surface as alert.
        """
        from .state_graph import get_state_graph
        sg = get_state_graph()
        if sg.count() < 10:
            return  # слишком мало истории
        if not self._throttled_idle("_last_state_walk", self.STATE_WALK_INTERVAL):
            return

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
        # Internal tick-reasons ("EMERGENT: 0/5 nodes. Need mass.") не лезут
        # в UI. Вместо них — краткая человеческая формулировка что система
        # делала в тот момент. Переводим action → глагол.
        _ACTION_RU = {
            "think_toward":    "генерировал новые идеи",
            "elaborate":       "углублял важную мысль",
            "elaborate_toward":"углублял в сторону цели",
            "smartdc":         "проверял противоречия",
            "doubt":           "ставил гипотезу под сомнение",
            "expand":          "расширял линию мышления",
            "collapse":        "сжимал похожие идеи",
            "compare":         "сравнивал варианты",
            "pump":            "искал мост между далёкими идеями",
            "synthesize":      "собирал итог",
            "ask":             "задавал вопрос",
            "stable":          "отдыхал (достиг стабильности)",
            "merge":           "объединял близкие идеи",
            "walk":            "гулял по графу мыслей",
        }
        _ACTION_EN = {
            "think_toward":    "generated new ideas",
            "elaborate":       "deepened a key thought",
            "elaborate_toward":"deepened toward a goal",
            "smartdc":         "checked contradictions",
            "doubt":           "doubted a hypothesis",
            "expand":          "expanded a line of thinking",
            "collapse":        "merged similar ideas",
            "compare":         "compared options",
            "pump":            "searched bridges between distant ideas",
            "synthesize":      "synthesized",
            "ask":             "asked a question",
            "stable":          "rested (converged)",
            "merge":           "merged close ideas",
            "walk":            "walked the graph",
        }
        verb_ru = _ACTION_RU.get(action, action)
        verb_en = _ACTION_EN.get(action, action)
        self._add_alert({
            "type": "state_walk",
            "severity": "info",
            "text": f"🕰 Похожий момент ({ts_disp}): тогда я {verb_ru}.",
            "text_en": f"🕰 Similar moment ({ts_disp}): back then I {verb_en}.",
            "match": {
                "hash": best.get("hash"),
                "action": action,
                "reason": reason,        # internal — только в meta, не в text
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

        # Throttle check отдельно — потому что есть дополнительное условие
        # wake_hour gate выше, которое может вернуть без обновления timestamp'а.
        # _throttled пишет now только когда interval прошёл.
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
        # Action Memory: morning_briefing — action, outcome через 4ч
        self._record_baddle_action(
            "morning_briefing",
            text=f"Morning briefing @ {local_hour}:00",
            extras={"hour": local_hour, "sections_count": len(sections)},
        )

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

    # ── Low-energy heavy-decision guard ─────────────────────────────────

    def _check_low_energy_heavy(self):
        """Проактивная защита: если daily_remaining < THRESHOLD И в open_goals
        есть цель с тяжёлым mode — предлагаем перенести на утро.

        Mockup: «Heavy decision 'change tech stack?' — move to tomorrow
        morning?». Дроссель раз в 30 минут чтобы не спамить.
        """
        if not self._throttled("_last_low_energy_check", self.LOW_ENERGY_CHECK_INTERVAL):
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
            # Action Memory: низкая энергия → совет перенести. Outcome 1ч.
            self._record_baddle_action(
                "alert_low_energy",
                text=f"Suggested to postpone heavy goal #{g0.get('id')}: {txt[:80]}",
                extras={"energy": int(daily), "goal_id": g0.get("id"),
                         "goal_mode": g0.get("mode")},
            )
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
        if not self._throttled("_last_plan_reminder_check", self.PLAN_REMINDER_CHECK_INTERVAL):
            return

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
                self._record_baddle_action(
                    "reminder_plan",
                    text=f"Reminder: {it.get('name', '')} in {mins_left}min",
                    extras={"plan_id": it["id"], "minutes_before": mins_left,
                             "plan_name": it.get("name", "")},
                )
        except Exception as e:
            log.debug(f"[cognitive_loop] plan reminder failed: {e}")

    def _check_recurring_lag(self):
        """Recurring-цели с отставанием — push alert.

        Для каждой recurring-цели у которой `lag ≥ RECURRING_LAG_MIN`,
        отдаём alert. Dedup через `_notified_lag[goal_id]` — один alert
        на goal за RECURRING_LAG_CHECK_INTERVAL, чтобы не спамить.
        """
        if not self._throttled("_last_recurring_check",
                                self.RECURRING_LAG_CHECK_INTERVAL):
            return
        try:
            from .recurring import list_lagging
        except Exception:
            return
        try:
            lagging = list_lagging(min_lag=self.RECURRING_LAG_MIN)
        except Exception as e:
            log.debug(f"[cognitive_loop] lag check failed: {e}")
            return
        if not lagging:
            return
        now = time.time()
        for p in lagging:
            gid = p.get("goal_id") or ""
            # Dedup: не шлём чаще чем раз в 2×interval
            last = self._notified_lag.get(gid, 0.0)
            if now - last < self.RECURRING_LAG_CHECK_INTERVAL * 2:
                # Цель реально отстаёт, но dedup блокирует — логируем
                self._log_throttle_drop("recurring_lag",
                    reason="dedup_per_goal",
                    goal=(p.get("text") or "")[:60],
                    lag=p.get("lag", 0),
                    done_today=p.get("done_today", 0),
                    times_per_day=p.get("times_per_day", 0),
                    seconds_since_last=round(now - last, 0))
                continue
            self._notified_lag[gid] = now
            lag = p.get("lag", 0)
            done = p.get("done_today", 0)
            tpd = p.get("times_per_day", 0)
            text = (f"⏰ «{p.get('text','')}» — отставание {lag} "
                    f"(сегодня {done}/{tpd}). Напомню через 30 мин если не отметишь.")
            self._add_alert({
                "type": "recurring_lag",
                "severity": "info",
                "text": text,
                "text_en": (f"«{p.get('text','')}» lagging {lag} ({done}/{tpd} today)."),
                "goal_id": gid,
                "lag": lag,
                "done_today": done,
                "times_per_day": tpd,
            })
            log.info(f"[cognitive_loop] recurring_lag alert: {p.get('text','')[:40]} "
                     f"lag={lag}")

    # ── Agency update (OQ #2, 5-я ось) ─────────────────────────────────

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

    def _check_sync_seeking(self):
        """Resonance protocol #3: когда тишина высокая И юзер давно не
        писал — Baddle шлёт мягкое сообщение для восстановления контакта.

        Gate'ы (все должны быть True):
          1. `freeze.silence_pressure > SYNC_SEEKING_SILENCE_MIN` — тишина
             накопилась, не пишем в активной сессии.
          2. `time_since_last_input > SYNC_SEEKING_IDLE_SECONDS` — юзер
             физически давно молчит. Защита от случая «silence накопилась
             исторически, но юзер только что написал».
          3. Throttle — не чаще раза в `SYNC_SEEKING_INTERVAL`.
          4. После последнего proactive alert прошло ≥ `QUIET_AFTER_OTHER`.
             Иначе получим briefing → через 30 мин seeking → нагромождение.
        """
        now = time.time()

        # Gate #1: silence accumulated (проверяем до quiet_after_other —
        # если silence низкая, это не «мы бы действовали», нечего логировать)
        try:
            silence = float(get_global_state().freeze.silence_pressure)
        except Exception:
            return
        if silence < self.SYNC_SEEKING_SILENCE_MIN:
            return

        # Gate #2: юзер физически давно не писал
        try:
            last_input_ts = get_user_state()._last_input_ts or 0.0
        except Exception:
            last_input_ts = 0.0
        idle_seconds = now - last_input_ts if last_input_ts else float("inf")
        if idle_seconds < self.SYNC_SEEKING_IDLE_SECONDS:
            return

        # Начиная отсюда — «мы бы выпустили alert сейчас». Throttle-дропы
        # ниже логируются для OQ «теряем ли ценное?».

        # Gate #4: quiet после других proactive
        if (self._last_proactive_alert_ts and
                now - self._last_proactive_alert_ts < self.SYNC_SEEKING_QUIET_AFTER_OTHER):
            self._log_throttle_drop("sync_seeking",
                reason="quiet_after_other",
                silence=round(silence, 3),
                idle_hours=round(idle_seconds / 3600.0, 1),
                seconds_since_other=round(now - self._last_proactive_alert_ts, 0))
            return

        # Gate #3: throttle (после этой проверки пишем timestamp → будущие
        # тики skip)
        if not self._throttled("_last_sync_seeking", self.SYNC_SEEKING_INTERVAL):
            self._log_throttle_drop("sync_seeking",
                reason="interval",
                silence=round(silence, 3),
                idle_hours=round(idle_seconds / 3600.0, 1),
                seconds_since_last=round(now - (self._last_sync_seeking or 0), 0))
            return

        # Counterfactual honesty: в SYNC_SEEKING_COUNTERFACTUAL_RATE случаев
        # намеренно молчим когда все gate'ы прошли. Это A/B baseline —
        # через месяц сравнить recovery-time (время до следующего
        # user_input) с вмешательством и без. Throttle уже записан выше,
        # так что следующий check будет через стандартный интервал.
        import random as _rnd
        if _rnd.random() < self.SYNC_SEEKING_COUNTERFACTUAL_RATE:
            log.info(f"[cognitive_loop] sync_seeking COUNTERFACTUAL skip: silence={silence:.2f} idle={idle_seconds/3600:.1f}h")
            self._record_baddle_action(
                "sync_seeking_counterfactual",
                text=f"Counterfactual skip: silence={silence:.2f} idle={idle_seconds/3600:.1f}h",
                extras={"silence_at_skip": round(silence, 3),
                        "idle_hours": round(idle_seconds / 3600.0, 1)},
            )
            return

        # Всё ок — генерируем сообщение
        text, tone = self._generate_sync_seeking_message(
            silence=silence, idle_hours=idle_seconds / 3600.0,
        )
        if not text:
            return

        log.info(f"[cognitive_loop] sync_seeking: silence={silence:.2f} idle={idle_seconds/3600:.1f}h → «{text[:60]}»")
        self._add_alert({
            "type": "sync_seeking",
            "severity": "info",
            "text": text,
            "text_en": text,
            "tone": tone,                 # caring|ambient|curious|reference|simple
            "silence_level": round(silence, 3),
            "idle_hours": round(idle_seconds / 3600.0, 1),
        })
        # Action Memory: запоминаем что мы сделали; outcome закроется в
        # _check_action_outcomes через 30 мин или когда юзер ответит.
        self._record_baddle_action(
            "sync_seeking",
            text=f"Baddle: «{text[:120]}»",
            extras={"tone": tone, "silence_at_action": round(silence, 3)},
        )

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

        # Эвристический tone до LLM (используется как дефолт если
        # LLM не отдаст структурированно)
        if silence > 0.7:             heuristic_tone = "caring"
        elif hrv_hint == "напряжён":  heuristic_tone = "caring"
        elif recent_topics:           heuristic_tone = "reference"
        elif time_of_day == "ночь":   heuristic_tone = "ambient"
        else:                          heuristic_tone = "simple"

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

    def _check_observation_suggestions(self):
        """Раз в сутки: собрать draft-карточки из patterns / checkins /
        stress-зон → положить в alerts. Юзер видит их в chat как
        `intent_confirm` карточки с кнопками Да/Изменить/Нет.

        Guard: если юзер **активен** (последний input < 10 мин) — skip
        БЕЗ обновления throttle, чтобы при следующей паузе попробовать снова.
        """
        # Сначала проверка user-active — не долбим юзера предложениями
        # во время работы над задачей. Throttle НЕ трогаем — чтобы при
        # следующем тихом моменте попробовать снова в тот же день.
        try:
            last_ts = get_user_state()._last_input_ts
            if last_ts and (time.time() - last_ts) < 600:  # 10 мин
                return  # silent skip, throttle остаётся на прежнем ts
        except Exception:
            pass
        if not self._throttled("_last_suggestions_check",
                                self.SUGGESTIONS_CHECK_INTERVAL):
            return
        try:
            from .suggestions import collect_suggestions, make_suggestion_card
        except Exception as e:
            log.debug(f"[cognitive_loop] suggestions import failed: {e}")
            return
        try:
            items = collect_suggestions(lang="ru")
        except Exception as e:
            log.debug(f"[cognitive_loop] collect_suggestions failed: {e}")
            return
        if not items:
            return
        # Логируем дропы: если items > MAX_PER_DAY, отсекаем хвост —
        # он может содержать важные паттерны.
        if len(items) > self.SUGGESTIONS_MAX_PER_DAY:
            for dropped in items[self.SUGGESTIONS_MAX_PER_DAY:]:
                trig = (dropped.get("trigger") or {}).get("type", "")
                self._log_throttle_drop("observation_suggestion",
                    reason="daily_cap",
                    trigger_type=trig,
                    total_items=len(items),
                    cap=self.SUGGESTIONS_MAX_PER_DAY)
        # Ограничиваем количество карточек в день
        for item in items[:self.SUGGESTIONS_MAX_PER_DAY]:
            try:
                card = make_suggestion_card(item, lang="ru")
                # Guard: если LLM-draft получился слабым (пустой text, нет title),
                # не пушим — иначе в чате висит «💡 Я заметил паттерн:» без тела.
                draft_text = ((card.get("draft") or {}).get("text") or "").strip()
                card_title = (card.get("title") or "").strip()
                if len(draft_text) < 3 or not card_title:
                    log.info(f"[cognitive_loop] suggestion skipped: empty draft/title "
                             f"({(item.get('trigger') or {}).get('type', '?')})")
                    continue
                trigger = (item.get("trigger") or {}).get("type", "")
                self._add_alert({
                    "type": "observation_suggestion",
                    "severity": "info",
                    "text": f"💡 {card.get('title', 'Предложение')}",
                    "text_en": card.get("title", "Suggestion"),
                    "card": card,       # UI рендерит через card.type=intent_confirm
                    "source": trigger,
                })
                log.info(f"[cognitive_loop] suggestion: {trigger} → {draft_text[:60]}")
                # Action Memory: suggestion — долгий outcome (7 дней, ждём accept/reject).
                # `action_kind` = kind.subkind для легкой фильтрации позже.
                suggestion_kind = (card.get("draft") or {}).get("kind", "generic")
                self._record_baddle_action(
                    f"suggestion_{suggestion_kind}",
                    text=f"Suggested: {card.get('title', '')} — {draft_text[:100]}",
                    extras={"trigger": trigger, "draft_kind": suggestion_kind},
                )
            except Exception as e:
                log.debug(f"[cognitive_loop] suggestion card build failed: {e}")

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
        self._record_baddle_action(
            "evening_retro",
            text=f"Evening retro: {n_un} unfinished",
            extras={"unfinished_count": n_un, "hour": local_hour},
        )

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

    # Types of alerts которые считаются «мы пишем юзеру» — используются
    # для gate sync-seeking (не долбим двумя проактивами подряд).
    _PROACTIVE_ALERT_TYPES = frozenset([
        "morning_briefing", "night_cycle", "dmn_bridge", "dmn_deep_research",
        "dmn_converge", "dmn_cross_graph", "state_walk", "observation_suggestion",
        "plan_reminder", "recurring_lag", "evening_retro", "low_energy_heavy",
        "scout_bridge", "sync_seeking",
    ])

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
            # Помечаем время последнего «активного» обращения к юзеру
            if alert.get("type") in self._PROACTIVE_ALERT_TYPES:
                self._last_proactive_alert_ts = alert["ts"]

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
            "last_dmn_cross": self._last_dmn_cross,
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
