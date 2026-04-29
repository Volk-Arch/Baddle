"""Signal dispatcher — Правило 1 из docs/architecture-rules.md.

«Любое событие к юзеру это `Signal(type, urgency, content, expires_at)`».
Заменяет 13 bespoke alert-cascade'ов в `cognitive_loop.py` на:

  1. **Детекторы** — pure functions `(ctx) -> Optional[Signal]`. Не знают
     про throttle/intervals/last_emitted. Описывают «сейчас уместен сигнал
     такой-то urgency, живёт столько-то, dedup-key такой».

  2. **Dispatcher** — собирает кандидаты, фильтрует expired, дедуплицирует
     по dedup_key, сортирует по urgency desc, применяет attention-budget
     (top-K за окно), critical (urgency≥0.9) bypass.

  3. **Throttle drops** пишутся natively dispatcher'ом в
     `data/throttle_drops.jsonl` (формат сохраняется backward-compatible
     со старым `_log_throttle_drop`).

См. правило 1 в [docs/architecture-rules.md](../docs/architecture-rules.md).

## Использование

```python
from src.signals import Signal, Dispatcher

dispatcher = Dispatcher(budget_per_window=5, window_s=3600.0)

# В _loop tick'е:
candidates = []
for detector in DETECTORS:
    sig = detector(ctx)
    if sig:
        candidates.append(sig)

emitted = dispatcher.dispatch(candidates, now)
for sig in emitted:
    loop._emit_alert(sig, now)  # W14.5c: → workspace primitive
```

## Что НЕ в этом модуле

- Сами детекторы (Шаг 3 миграции, в cognitive_loop или отдельном модуле)
- DetectorContext (тонкий dataclass, в `src/detectors.py`)
- Heavy work функции для DMN/night (в их собственных модулях)
- emit на UI — dispatcher только решает, эмитит вызывающий
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)


# Counter-wave (Правило 7): типы сигналов которые «давят» — их urgency
# понижается при user.mode == 'C', чтобы не усиливать desync. Идея: когда
# юзер уже в counter-wave регулировании, push-style alerts (sync_seeking,
# recurring_lag, observation_suggestion) только усиливают рассогласование.
# Counter-wave подразумевает смену тактики: пауза, инверсия тона, смена
# несущей. Реализуется простым понижением urgency на 0.3.
COUNTER_WAVE_PUSH_TYPES = frozenset({
    "sync_seeking",
    "recurring_lag",
    "observation_suggestion",
    "morning_briefing",
    "evening_retro",
})


@dataclass
class Signal:
    """Unified alert envelope. Детектор возвращает это, dispatcher решает
    что эмитить юзеру.

    Attributes:
        type: short kind ("sync_seeking", "dmn_bridge", ...). 1:1 с
            существующими `alert.type` — UI читает `content["type"]` без
            изменений.
        urgency: [0.0, 1.0]. ≥`critical_threshold` (default 0.9) bypass'ит
            attention budget. Считается детектором по контексту (silence,
            lag_count, quality, etc).
        content: alert payload — `{type, severity, text, ...}`. Это то что
            попадает в workspace.add/record_committed (через _emit_alert)
            неизменным; UI не ломается.
        expires_at: unix ts когда сигнал теряет актуальность. После этого
            dispatcher дропает с reason="expired" (DMN-мост 2 часа спустя
            — уже не нужен, plan_reminder после события — тоже).
        dedup_key: опциональный ключ дедупликации. `None` означает «не
            дедуплицировать». Например `"recurring_lag:{goal_id}"` —
            та же цель не присылается дважды в одно окно.
        source: имя детектора для telemetry в throttle_drops.jsonl. UI не
            видит. Опционально.
    """

    type: str
    urgency: float
    content: dict
    expires_at: float
    dedup_key: Optional[str] = None
    source: Optional[str] = None
    # W14.5c: accumulating Signals идут в workspace.add(accumulate=True),
    # bypass'ят counter-wave/budget gate в Dispatcher (workspace.select
    # применяет их позже на pending). Dedup в Dispatcher остаётся —
    # window-based dedup полезен для всех Signals.
    accumulating: bool = False


class Dispatcher:
    """Attention-budget + top-K sort + dedup + drop logging.

    Единственный путь от детекторов к UI alerts. Реализует всё что было
    разбросано по 13 check-функциям в cognitive_loop.py:

      - `*_INTERVAL` константы → urgency-based scheduling (детекторы
        возвращают Signal каждый tick, dispatcher решает кого эмитить)
      - `*_QUIET_AFTER_OTHER` → attention budget per-window
      - `SUGGESTIONS_MAX_PER_DAY=2` → dedup_key с window_s
      - `_last_*_ts` поля → внутренний sliding history
      - `_log_throttle_drop(...)` → native log_drops для всех causes

    Thread-safe: один lock на весь state (history, dedup, drops). Внутри
    `dispatch()` всё выполняется атомарно.
    """

    def __init__(self,
                 budget_per_window: int = 5,
                 window_s: float = 3600.0,
                 critical_threshold: float = 0.9,
                 drops_file=None):
        """
        Args:
            budget_per_window: max non-critical alerts за окно. По умолчанию
                5 в час — достаточно для morning_briefing + sync_seeking +
                observation×2 + recurring_lag без переполнения. Critical
                (urgency≥critical_threshold) bypass'ит budget.
            window_s: размер sliding window для budget и dedup. По умолчанию
                1 час. Короче → plan_reminder + sync_seeking могут не влезть
                одновременно. Длиннее → backlog старых dedup_key'ев растёт.
            critical_threshold: urgency выше которого bypass budget. По
                умолчанию 0.9 — только true-critical (coherence_crit,
                plan_reminder<2min, low_energy<5).
            drops_file: путь для throttle_drops.jsonl. По умолчанию
                `paths.THROTTLE_DROPS_FILE`. Тесты переопределяют.
        """
        self.budget_per_window = budget_per_window
        self.window_s = window_s
        self.critical_threshold = critical_threshold
        self._drops_file = drops_file
        self._emitted_history: list[float] = []      # sliding window of ts
        self._dedup_seen: dict[str, float] = {}      # key → last_seen_ts
        self._lock = threading.Lock()

    def dispatch(self,
                 candidates: list[Signal],
                 now: float,
                 user_mode: str = "R") -> list[Signal]:
        """Решить какие сигналы эмитить, остальное дропнуть в JSONL.

        Algorithm:
          0. Counter-wave (Правило 7): если user_mode == 'C', понизить
             urgency push-style сигналов (COUNTER_WAVE_PUSH_TYPES) на 0.3.
             Critical (≥0.9) фактически перестаёт быть critical и попадает
             в budget — это сознательно: при desync push'ить нельзя даже
             критическим тоном, нужна counter-wave (пауза, смена несущей).
          1. Prune sliding window (forget emissions старше window_s)
          2. Filter expired (signal.expires_at ≤ now)
          3. Dedup (same dedup_key уже был в окне)
          4. Sort by urgency desc
          5. Budget gate: top-K + critical bypass

        Drops пишутся в throttle_drops.jsonl с reason ∈ {expired, dedup,
        budget, counter_wave}.

        Args:
            candidates: всё что детекторы вернули за этот tick.
            now: unix ts текущего момента.
            user_mode: 'R' (resonance) или 'C' (counter-wave). Передаётся
                из cognitive_loop, отражает state.user.mode после _advance_tick.

        Returns:
            Список signals одобренных к emit. Caller вызывает `_emit_alert`
            для каждого (W14.5c — был `_add_alert`).
        """
        with self._lock:
            self._prune_history(now)

            # W14.5c: accumulating Signals bypass'ят counter-wave + budget.
            # Они идут в workspace.add(accumulate=True), где select() сам
            # применяет counter-wave penalty + budget при emission. Dedup
            # остаётся в Dispatcher для всех — window-based dedup защищает
            # от рапид-фаер дубликатов независимо от path.
            accumulating = [s for s in candidates if s.accumulating]
            non_acc = [s for s in candidates if not s.accumulating]

            # ── Path 1: non-accumulating через Dispatcher (counter-wave +
            # expired + dedup + budget gate) ─────────────────────────────────

            # 0. Counter-wave urgency reduction (только non-accumulating)
            if user_mode == "C" and non_acc:
                from dataclasses import replace
                adjusted = []
                for sig in non_acc:
                    if sig.type in COUNTER_WAVE_PUSH_TYPES:
                        new_urg = max(0.0, sig.urgency - 0.3)
                        sig = replace(sig, urgency=new_urg)
                    adjusted.append(sig)
                non_acc = adjusted

            # 1. Filter expired
            alive: list[Signal] = []
            for sig in non_acc:
                if sig.expires_at <= now:
                    self._log_drop(sig, "expired", now)
                    continue
                alive.append(sig)

            # 2. Dedup
            fresh: list[Signal] = []
            for sig in alive:
                if sig.dedup_key:
                    last_seen = self._dedup_seen.get(sig.dedup_key)
                    if last_seen is not None and (now - last_seen) < self.window_s:
                        self._log_drop(sig, "dedup", now)
                        continue
                fresh.append(sig)

            # 3. Sort by urgency desc (stable sort sort sources алфавитно для повторяемости)
            fresh.sort(key=lambda s: (-s.urgency, s.type, s.source or ""))

            # 4. Budget gate
            emitted: list[Signal] = []
            budget_used = len(self._emitted_history)
            for sig in fresh:
                is_critical = sig.urgency >= self.critical_threshold
                if is_critical:
                    # Critical bypass — всегда эмитим, не считается в budget.
                    # Без этого флага каскад критических (coherence_crit +
                    # plan_reminder<2min + low_energy<5) забивал бы slot'ы
                    # обычным alerts. Защита от спама — через dedup_key.
                    emitted.append(sig)
                elif budget_used < self.budget_per_window:
                    emitted.append(sig)
                    budget_used += 1
                    self._emitted_history.append(now)
                else:
                    self._log_drop(sig, "budget", now)
                    continue

                # Dedup tracking — для всех emitted (включая critical),
                # чтобы тот же coherence_crit не фигачил 100 раз подряд.
                if sig.dedup_key:
                    self._dedup_seen[sig.dedup_key] = now

            # ── Path 2: accumulating — only expired + dedup gates ───────────
            # workspace.add сам применит counter-wave при select. Budget
            # тоже на стороне workspace (через select max_emit + cross-process).

            for sig in accumulating:
                if sig.expires_at <= now:
                    self._log_drop(sig, "expired", now)
                    continue
                if sig.dedup_key:
                    last_seen = self._dedup_seen.get(sig.dedup_key)
                    if last_seen is not None and (now - last_seen) < self.window_s:
                        self._log_drop(sig, "dedup", now)
                        continue
                    self._dedup_seen[sig.dedup_key] = now
                emitted.append(sig)

            return emitted

    def _prune_history(self, now: float) -> None:
        """Удалить из sliding window всё старше window_s."""
        cutoff = now - self.window_s
        self._emitted_history = [t for t in self._emitted_history if t > cutoff]
        self._dedup_seen = {k: t for k, t in self._dedup_seen.items() if t > cutoff}

    def _log_drop(self, sig: Signal, reason: str, now: float) -> None:
        """Append drop в throttle_drops.jsonl. Формат backward-compatible
        со старым `cognitive_loop._log_throttle_drop`:
            {"ts": ts, "check": type, "ctx": {reason, urgency, ...}}

        Дополнительные поля в ctx: urgency, dedup_key, expires_in_s, source.
        Старые анализаторы продолжают работать (читали ts/check/ctx.reason).
        """
        try:
            entry = {
                "ts": round(now, 3),
                "check": sig.type,
                "ctx": {
                    "reason": reason,
                    "urgency": round(sig.urgency, 3),
                    "dedup_key": sig.dedup_key,
                    "expires_in_s": round(sig.expires_at - now, 1),
                    "source": sig.source,
                },
            }
            target = self._drops_file or self._default_drops_file()
            if target is None:
                return
            with target.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            log.debug(f"[dispatcher] drop log failed: {e}")

    @staticmethod
    def _default_drops_file():
        """Lazy-import paths чтобы избежать circular import при unit test."""
        try:
            from ..paths import THROTTLE_DROPS_FILE
            return THROTTLE_DROPS_FILE
        except Exception:
            return None

    # ── Diagnostics ─────────────────────────────────────────────────────────

    def status(self, now: Optional[float] = None) -> dict:
        """Текущее состояние budget/dedup для /status endpoint."""
        if now is None:
            now = time.time()
        with self._lock:
            self._prune_history(now)
            return {
                "budget_per_window": self.budget_per_window,
                "window_s": self.window_s,
                "critical_threshold": self.critical_threshold,
                "emitted_in_window": len(self._emitted_history),
                "budget_remaining": max(0,
                    self.budget_per_window - len(self._emitted_history)),
                "dedup_keys_active": list(self._dedup_seen.keys()),
            }
