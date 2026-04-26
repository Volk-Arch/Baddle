"""Прайм-директива — append-only лог sync_error EMA для валидации.

Главная метрика проекта — `sync_error = ‖user − system‖`. TODO обещает
«через 2 мес use сравнить avg weekly sync_error» — для этого нужен
долгосрочный регистр. state_graph.jsonl конкурирует с consolidation
(архив старого), поэтому ведём отдельный файл `data/prime_directive.jsonl`.

Раз в час cognitive_loop._check_prime_directive_record пишет одну строку:

    {"ts": 1713789600.0,
     "sync_error": 0.421,              # мгновенный
     "sync_error_ema_fast": 0.412,     # EMA 1ч TC
     "sync_error_ema_slow": 0.388,     # EMA 3д TC — главная метрика
     "imbalance_pressure": 0.093,
     "silence_pressure":  0.141,
     "conflict_accumulator": 0.012}

Endpoint `/assist/prime-directive` агрегирует файл по окну и возвращает
mean/trend — для валидации резонансного протокола через 2 мес use.
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

_LOG_PATH = os.path.join("data", "prime_directive.jsonl")


def _ensure_dir() -> None:
    try:
        os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
    except Exception:
        pass


def record_tick(sync_error: float,
                sync_error_ema_fast: float,
                sync_error_ema_slow: float,
                imbalance_pressure: float,
                silence_pressure: float,
                conflict_accumulator: float,
                user_imbalance: float = 0.0,
                self_imbalance: float = 0.0,
                agency_gap: float = 0.0,
                hrv_surprise: float = 0.0,
                # Outcome dashboard fields (added 2026-04-26):
                balance_user: float | None = None,
                balance_system: float | None = None,
                capacity_zone: str | None = None,
                frequency_regime: str | None = None,
                mode_user: str | None = None,
                mode_system: str | None = None) -> bool:
    """Append one snapshot. Возвращает True если записалось.

    Все аргументы — float scalars (или string для categorical). Failed write
    (read-only fs, permissions) → False silent. Лог не критичен для работы.

    Дополнительные PE-компоненты (`user_imbalance`, `self_imbalance`,
    `agency_gap`, `hrv_surprise`) — decomposition агрегированного
    `imbalance_pressure` на источники.

    Outcome dashboard fields (Phase D + Counter-wave):
    - `balance_user`/`balance_system` — резонансный скаляр (DA·NE·ACh)/(5HT·GABA)
    - `capacity_zone` — green/yellow/red (Phase C)
    - `frequency_regime` — short_wave/flat/long_wave (HRV-derived)
    - `mode_user`/`mode_system` — R/C bit (Counter-wave, Правило 7)
    Все optional с None default — старые logs совместимы.
    """
    _ensure_dir()
    entry = {
        "ts": round(time.time(), 2),
        "sync_error":           round(float(sync_error), 4),
        "sync_error_ema_fast":  round(float(sync_error_ema_fast), 4),
        "sync_error_ema_slow":  round(float(sync_error_ema_slow), 4),
        "imbalance_pressure":   round(float(imbalance_pressure), 4),
        "silence_pressure":     round(float(silence_pressure), 4),
        "conflict_accumulator": round(float(conflict_accumulator), 4),
        "user_imbalance":       round(float(user_imbalance), 4),
        "self_imbalance":       round(float(self_imbalance), 4),
        "agency_gap":           round(float(agency_gap), 4),
        "hrv_surprise":         round(float(hrv_surprise), 4),
    }
    if balance_user     is not None: entry["balance_user"]     = round(float(balance_user), 4)
    if balance_system   is not None: entry["balance_system"]   = round(float(balance_system), 4)
    if capacity_zone    is not None: entry["capacity_zone"]    = str(capacity_zone)
    if frequency_regime is not None: entry["frequency_regime"] = str(frequency_regime)
    if mode_user        is not None: entry["mode_user"]        = str(mode_user)
    if mode_system      is not None: entry["mode_system"]      = str(mode_system)
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def read_all() -> list:
    """Читает весь jsonl. Возвращает list[dict], sorted by ts ascending.
    Пропускает битые строки без крика.
    """
    out: list = []
    if not os.path.exists(_LOG_PATH):
        return out
    try:
        with open(_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return out
    out.sort(key=lambda e: float(e.get("ts", 0)))
    return out


def aggregate(window_days: Optional[float] = None) -> dict:
    """Summary по последнему окну (дней) или по всему файлу.

    Returns:
        {count, days_span, first_ts, last_ts,
         mean_sync_error, mean_ema_fast, mean_ema_slow,
         mean_imbalance, mean_silence, mean_conflict,
         trend_slow_delta}  — где trend_slow_delta = mean(last third) −
        mean(first third). Negative = sync_error упал = резонансный
        протокол работает.
    """
    entries = read_all()
    if not entries:
        return {"count": 0}

    if window_days and window_days > 0:
        cutoff = time.time() - float(window_days) * 86400.0
        entries = [e for e in entries if float(e.get("ts", 0)) > cutoff]
        if not entries:
            return {"count": 0}

    def _mean(field: str) -> float:
        vals = [float(e.get(field, 0.0)) for e in entries]
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    def _mean_optional(field: str) -> Optional[float]:
        """Усреднить только entries где field явно заполнен (не None)."""
        vals = [float(e[field]) for e in entries if e.get(field) is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    def _categorical_counts(field: str) -> dict:
        """Распределение string-значения по entries (capacity_zone, regime, mode)."""
        out: dict = {}
        for e in entries:
            v = e.get(field)
            if v is None:
                continue
            out[str(v)] = out.get(str(v), 0) + 1
        return out

    span_s = float(entries[-1].get("ts", 0)) - float(entries[0].get("ts", 0))
    out = {
        "count":            len(entries),
        "days_span":        round(span_s / 86400.0, 2),
        "first_ts":         float(entries[0].get("ts", 0)),
        "last_ts":          float(entries[-1].get("ts", 0)),
        "mean_sync_error":  _mean("sync_error"),
        "mean_ema_fast":    _mean("sync_error_ema_fast"),
        "mean_ema_slow":    _mean("sync_error_ema_slow"),
        "mean_imbalance":   _mean("imbalance_pressure"),
        "mean_silence":     _mean("silence_pressure"),
        "mean_conflict":    _mean("conflict_accumulator"),
        # PE decomposition — какой канал реально двигал imbalance
        "mean_pe_user":     _mean("user_imbalance"),
        "mean_pe_self":     _mean("self_imbalance"),
        "mean_pe_agency":   _mean("agency_gap"),
        "mean_pe_hrv":      _mean("hrv_surprise"),
        # Outcome dashboard distributions (added 2026-04-26).
        # Optional fields: None если в окне нет ни одного snapshot с этим полем
        # (старые logs до Wave 3 расширения).
        "mean_balance_user":   _mean_optional("balance_user"),
        "mean_balance_system": _mean_optional("balance_system"),
        "capacity_zone_counts":    _categorical_counts("capacity_zone"),
        "frequency_regime_counts": _categorical_counts("frequency_regime"),
        "mode_user_counts":        _categorical_counts("mode_user"),
        "mode_system_counts":      _categorical_counts("mode_system"),
    }

    # Trend detection: первая треть vs последняя треть — по slow EMA.
    # Требуется ≥6 snapshot'ов чтобы треть была ≥2, иначе шум.
    if len(entries) >= 6:
        third = max(1, len(entries) // 3)
        first_third = entries[:third]
        last_third = entries[-third:]
        def _third_mean(items):
            vals = [float(e.get("sync_error_ema_slow", 0.0)) for e in items]
            return sum(vals) / len(vals) if vals else 0.0
        first_mean = _third_mean(first_third)
        last_mean = _third_mean(last_third)
        out["trend_slow_delta"] = round(last_mean - first_mean, 4)
        # Human-readable verdict для UI
        if out["trend_slow_delta"] < -0.02:
            out["trend_verdict"] = "improving"
        elif out["trend_slow_delta"] > 0.02:
            out["trend_verdict"] = "worsening"
        else:
            out["trend_verdict"] = "stable"
    else:
        out["trend_slow_delta"] = None
        out["trend_verdict"] = "insufficient_data"

    return out


def daily_bins(window_days: int = 30) -> list:
    """Группировка snapshot'ов по суткам для charting.

    Returns list[{date: 'YYYY-MM-DD', mean_slow, mean_fast, count}],
    sorted ascending. Пропущенные дни → не в списке.
    """
    import datetime as _dt
    entries = read_all()
    if not entries:
        return []
    cutoff = time.time() - window_days * 86400.0
    entries = [e for e in entries if float(e.get("ts", 0)) > cutoff]
    buckets: dict = {}
    for e in entries:
        try:
            ts = float(e.get("ts", 0))
            date = _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        except Exception:
            continue
        buckets.setdefault(date, []).append(e)
    out = []
    for date in sorted(buckets.keys()):
        items = buckets[date]
        fast = [float(e.get("sync_error_ema_fast", 0.0)) for e in items]
        slow = [float(e.get("sync_error_ema_slow", 0.0)) for e in items]
        out.append({
            "date": date,
            "count": len(items),
            "mean_fast": round(sum(fast) / len(fast), 4) if fast else 0.0,
            "mean_slow": round(sum(slow) / len(slow), 4) if slow else 0.0,
        })
    return out
