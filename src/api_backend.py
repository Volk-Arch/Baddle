"""API backend — OpenAI-compatible client for graph/chat generation."""

import json
import time
import logging
import urllib.request
import urllib.error

log = logging.getLogger(__name__)

from .paths import SETTINGS_FILE as _SETTINGS_FILE, ensure_data_dir
ensure_data_dir()

_settings = {
    "api_url": "http://localhost:1234",   # LM Studio default, or OpenAI/etc
    "api_key": "",                         # optional — for OpenAI/cloud
    "api_model": "",                       # e.g. "qwen/qwen3-8b"
    "embedding_model": "",                 # e.g. "text-embedding-nomic-embed-text-v1.5"
    "local_ctx": 32768,                    # hint only, server enforces its own ctx
    # Neural defaults — раньше жили только в graph tab. Теперь общие
    # для baddle chat + DMN + graph. Если value задан — используется
    # вместо hardcoded per-call defaults.
    "neural_threshold": 0.91,              # distinct edge threshold
    "neural_temp": 0.7,                    # LLM sampling temperature (дефолт)
    "neural_top_k": 40,                    # LLM top-k sampling
    "neural_seed": -1,                     # -1 = random
    "neural_novelty": 0.85,                # novelty gate (distinct) for dedup
    "neural_max_tokens": 3000,             # single-call упpер лимит
    # Depth knobs — сколько циклов thinking на каждом уровне.
    # Чем больше — тем глубже и дольше обрабатываем.
    "deep_chat_steps":       3,            # execute_deep: global fallback (если mode не в dict)
    # Per-mode depth override. Ключ = mode_id, значение = число итераций.
    # После базовых brainstorm+elaborate+smartdc делаем ещё N-3 раундов
    # углубления на weakest hypothesis, пока confidence не > 0.85 или не stall.
    # horizon/bayes тяжёлые по задумке (глубокое исследование), tournament
    # лёгкий (pairwise уже дорогой), free/scout средние.
    "deep_mode_steps": {
        "horizon":    5,    # research — deep exploration
        "bayes":      7,    # bayesian — prior+observations+posterior cycles
        "dispute":    4,    # dialectical — thesis/anti/synth ходит глубже
        "tournament": 3,    # comparative — pairwise SmartDC уже N² heavy
        "builder":    4,    # assembly — parts deeper
        "pipeline":   4,    # steps — walks longer
        "cascade":    3,    # priorities
        "scales":     3,    # balance
        "race":       2,    # any option — first match
        "fan":        3,    # brainstorm
        "scout":      3,    # wander
        "vector":     3,    # focus
        "free":       3,    # manual
    },
    "dmn_converge_max_steps":   100,       # server-side autorun до stable
    "dmn_converge_stall_window": 12,       # шагов без роста нод → stop
    "dmn_converge_max_wall_s":   900,      # абсолютный лимит wall-time (15 мин)
    # Diversity guard в brainstorm: если avg pairwise distinct между hypotheses
    # < этого порога, auto-trigger pump между двумя ближайшими для разброса
    # (serendipity axis injection) — иначе synthesize работает на слипшемся.
    "deep_diversity_min":       0.30,
    # Experimental
    "live_bayes": False,
}


def _load_settings():
    """Load settings from disk. Create with defaults if missing."""
    try:
        data = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
        for k, v in data.items():
            if k in _settings:
                _settings[k] = v
    except FileNotFoundError:
        _save_settings()  # create with defaults on first run
    except json.JSONDecodeError:
        pass


def _save_settings():
    """Persist settings to disk."""
    try:
        _SETTINGS_FILE.write_text(json.dumps(_settings, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log.warning(f"[settings] Could not save: {e}")


# Load on import
_load_settings()


def get_settings():
    return dict(_settings)


def get_neural_defaults() -> dict:
    """Общие settings.json neural-дефолты. Используются execute_deep, DMN
    executor и всеми эндпоинтами которым нужны temp/top_k/max_tokens.
    Позволяет юзеру overrид'нуть без правки кода.
    """
    return {
        "temperature": float(_settings.get("neural_temp", 0.7)),
        "top_k": int(_settings.get("neural_top_k", 40)),
        "max_tokens": int(_settings.get("neural_max_tokens", 3000)),
        "threshold": float(_settings.get("neural_threshold", 0.91)),
        "novelty": float(_settings.get("neural_novelty", 0.85)),
        "seed": int(_settings.get("neural_seed", -1)),
    }


def get_depth_defaults() -> dict:
    """Depth knobs — сколько циклов на каждом уровне мышления."""
    mode_steps = _settings.get("deep_mode_steps") or {}
    if not isinstance(mode_steps, dict):
        mode_steps = {}
    return {
        "deep_chat_steps":          int(_settings.get("deep_chat_steps", 3)),
        "deep_mode_steps":          dict(mode_steps),
        "deep_diversity_min":       float(_settings.get("deep_diversity_min", 0.30)),
        "dmn_converge_max_steps":   int(_settings.get("dmn_converge_max_steps", 100)),
        "dmn_converge_stall_window":int(_settings.get("dmn_converge_stall_window", 12)),
        "dmn_converge_max_wall_s":  int(_settings.get("dmn_converge_max_wall_s", 900)),
    }


def get_mode_depth(mode_id: str) -> int:
    """Число thinking iterations (collapse_at) для конкретного mode.

    Семантика совпадает с графовым autorun'ом в Lab:
      • `deep_chat_infinite = True`  → collapse_at = safety cap, loop идёт до
        should_stop=STABLE (hardStop = collapse_at × 2)
      • `deep_chat_infinite = False` → loop доходит до collapse_at, потом
        принудительный финальный collapse

    Per-mode через `deep_mode_steps` dict в settings.json. Cap [1, 200].
    """
    mode_steps = _settings.get("deep_mode_steps") or {}
    if isinstance(mode_steps, dict) and mode_id in mode_steps:
        try:
            val = int(mode_steps[mode_id])
            return max(1, min(200, val))
        except (TypeError, ValueError):
            pass
    return max(1, min(200, int(_settings.get("deep_chat_steps", 15))))


def is_deep_infinite() -> bool:
    """True если chat-initiated deep крутится до natural STABLE (как graph
    autorun infinite mode). False — до collapse_at шагов, затем forced collapse.
    Default: True (бесконечный) — совпадает с Lab autorun по умолчанию.
    """
    return bool(_settings.get("deep_chat_infinite", True))


def get_deep_response_format() -> str:
    """Формат финального synthesis в chat: brief | essay | article | list.
    Default: essay. Управляется через settings → Advanced → Deep format.
    """
    fmt = _settings.get("deep_response_format", "essay")
    if fmt not in ("brief", "essay", "article", "list"):
        fmt = "essay"
    return fmt


def is_deep_batched() -> bool:
    """True — использовать pyramidal batched collapse для надёжности на
    локальных LLM (sections из batch'ей → финал из sections). False —
    один LLM call на все ноды сразу (быстрее, но context limit).
    Default: True.
    """
    return bool(_settings.get("deep_batched_synthesis", True))


def update_settings(new: dict):
    for k in ("api_url", "api_key", "api_model",
              "embedding_model", "local_ctx", "live_bayes",
              "neural_threshold", "neural_temp", "neural_top_k",
              "neural_seed", "neural_novelty", "neural_max_tokens",
              "deep_chat_steps", "deep_mode_steps", "deep_diversity_min",
              "deep_chat_infinite", "deep_response_format", "deep_batched_synthesis",
              "dmn_converge_max_steps",
              "dmn_converge_stall_window", "dmn_converge_max_wall_s"):
        if k in new:
            _settings[k] = new[k]
    _save_settings()


def fetch_models(api_url: str, api_key: str) -> dict:
    """Fetch available models from OpenAI-compatible /v1/models endpoint."""
    base = api_url.rstrip("/")
    if not base.endswith("/v1"):
        base = base + "/v1"
    url = base + "/models"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        models = sorted([m["id"] for m in result.get("data", [])])
        return {"models": models}
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "models": []}
    except Exception as e:
        return {"error": str(e), "models": []}


_MAX_RETRIES = 3
_RETRY_BACKOFF = (1, 3, 8)  # seconds

# ── API health (для graceful degradation) ───────────────────────────────
# Держим trailing-state: last success/failure + consecutive failures.
# Статус выводится: ok (недавно успешный вызов), degraded (1-2 consecutive
# failures), offline (>=3 подряд или экплицитная ошибка retry-exhausted).
_api_health = {
    "last_ok_ts": 0.0,
    "last_fail_ts": 0.0,
    "consecutive_failures": 0,
    "last_error": "",
}

# Cooldown: если оффлайн — первый call после 60с всё равно пробуется
# (не смысла держать permanent offline, LM-сервер может вернуться).
_OFFLINE_RETRY_COOLDOWN = 60.0


def _health_mark_ok():
    _api_health["last_ok_ts"] = time.time()
    _api_health["consecutive_failures"] = 0
    _api_health["last_error"] = ""


def _health_mark_fail(err: str):
    _api_health["last_fail_ts"] = time.time()
    _api_health["consecutive_failures"] += 1
    _api_health["last_error"] = (err or "")[:200]


def get_api_health() -> dict:
    """Возвращает {status: ok|degraded|offline, ...}. Для UI-индикатора."""
    now = time.time()
    cf = _api_health["consecutive_failures"]
    last_ok = _api_health["last_ok_ts"]
    if cf == 0 and last_ok > 0:
        status = "ok"
    elif cf < 3:
        status = "degraded" if cf > 0 else ("unknown" if last_ok == 0 else "ok")
    else:
        status = "offline"
    return {
        "status": status,
        "consecutive_failures": cf,
        "last_ok_ts": last_ok,
        "last_fail_ts": _api_health["last_fail_ts"],
        "last_error": _api_health["last_error"],
        "seconds_since_ok": (now - last_ok) if last_ok else None,
    }


def _api_request(url: str, data: bytes = None, headers: dict = None,
                 method: str = "POST", timeout: int = 120) -> dict:
    """HTTP request with retry + exponential backoff on 5xx/timeout.

    Обновляет `_api_health` на каждом итоге — чтобы UI мог показать
    статус «LM offline» без тихих полома.
    """
    last_err = None
    for attempt in range(_MAX_RETRIES):
        req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            _health_mark_ok()
            return result
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            if e.code < 500 and e.code != 429:
                # Client error (4xx except 429) — don't retry, не offline
                log.error(f"[api] HTTP {e.code}: {error_body[:500]}")
                raise RuntimeError(f"API error {e.code}: {error_body[:200]}")
            last_err = f"HTTP {e.code}: {error_body[:200]}"
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last_err = str(e)
        except Exception as e:
            last_err = str(e)

        wait = _RETRY_BACKOFF[attempt] if attempt < len(_RETRY_BACKOFF) else _RETRY_BACKOFF[-1]
        log.warning(f"[api] Attempt {attempt + 1}/{_MAX_RETRIES} failed: {last_err}. Retrying in {wait}s...")
        time.sleep(wait)

    _health_mark_fail(last_err or "unknown")
    log.error(f"[api] All {_MAX_RETRIES} attempts failed: {last_err}")
    raise RuntimeError(f"API failed after {_MAX_RETRIES} retries: {last_err}")


def api_chat_completion(messages: list, max_tokens: int = 200, temperature: float = 0.9,
                        top_k: int = 40, repeat_penalty: float = 1.1,
                        top_logprobs: int = 1, return_full: bool = False) -> tuple:
    """Call OpenAI-compatible chat completion API.

    Returns (text, entropy_avg, uncertainty_pct, token_entropies, token_texts).
    If return_full=True, also returns top_candidates per token (for step mode).
    """
    base = _settings["api_url"].rstrip("/")
    if not base.endswith("/v1"):
        base = base + "/v1"
    url = base + "/chat/completions"

    body = {
        "model": _settings["api_model"],
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "logprobs": True,
        "top_logprobs": top_logprobs,
    }

    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if _settings["api_key"]:
        headers["Authorization"] = f"Bearer {_settings['api_key']}"

    result = _api_request(url, data=data, headers=headers, timeout=120)

    choice = result.get("choices", [{}])[0]
    message = choice.get("message", {})
    text = message.get("content", "")
    if not text and message.get("reasoning_content"):
        text = message["reasoning_content"]

    # Extract logprobs
    token_ents = []
    token_texts = []
    top_candidates = []  # list of lists: per-token top-N alternatives
    logprobs_data = choice.get("logprobs")
    if logprobs_data and "content" in logprobs_data:
        for tok_info in logprobs_data["content"]:
            lp = tok_info.get("logprob", 0)
            token_ents.append(-lp if lp else 0.0)
            token_texts.append(tok_info.get("token", ""))
            if return_full:
                alts = tok_info.get("top_logprobs", []) or []
                import math
                top_candidates.append([
                    {"token": a.get("token", ""), "prob": math.exp(a.get("logprob", -100))}
                    for a in alts
                ])

    if token_ents:
        avg_ent = sum(token_ents) / len(token_ents)
        unc_pct = sum(1 for e in token_ents if e > 2.0) / len(token_ents)
    else:
        avg_ent = 0.0
        unc_pct = 0.0

    if return_full:
        return text, avg_ent, unc_pct, token_ents, token_texts, top_candidates
    return text, avg_ent, unc_pct, token_ents, token_texts


def api_get_embedding(text: str) -> list:
    """Get embedding via OpenAI-compatible API.

    Returns embedding vector as list of floats, or empty list if not available.
    """
    base = _settings["api_url"].rstrip("/")
    if not base.endswith("/v1"):
        base = base + "/v1"
    url = base + "/embeddings"

    emb_model = _settings.get("embedding_model") or _settings["api_model"]
    body = {
        "model": emb_model,
        "input": text,
    }

    data = json.dumps(body).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
    }
    if _settings["api_key"]:
        headers["Authorization"] = f"Bearer {_settings['api_key']}"

    try:
        result = _api_request(url, data=data, headers=headers, timeout=30)
        return result["data"][0]["embedding"]
    except Exception as e:
        log.warning(f"[api] Embedding failed: {e}")
        return []
