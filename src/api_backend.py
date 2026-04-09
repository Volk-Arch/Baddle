"""API backend — OpenAI-compatible client for graph/chat generation."""

import json
import time
import logging
import urllib.request
import urllib.error
from pathlib import Path

log = logging.getLogger(__name__)

_SETTINGS_FILE = Path(__file__).parent.parent / "settings.json"

_settings = {
    "api_url": "http://localhost:1234",   # LM Studio default, or OpenAI/etc
    "api_key": "",                         # optional — for OpenAI/cloud
    "api_model": "",                       # e.g. "qwen/qwen3-8b"
    "embedding_model": "",                 # e.g. "text-embedding-nomic-embed-text-v1.5"
    "local_ctx": 32768,                    # hint only, server enforces its own ctx
    # Experimental
    "live_bayes": False,
}


def _load_settings():
    """Load settings from disk if exists."""
    try:
        data = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
        for k, v in data.items():
            if k in _settings:
                _settings[k] = v
    except (FileNotFoundError, json.JSONDecodeError):
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


def update_settings(new: dict):
    for k in ("api_url", "api_key", "api_model",
              "embedding_model", "local_ctx", "live_bayes"):
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


def _api_request(url: str, data: bytes = None, headers: dict = None,
                 method: str = "POST", timeout: int = 120) -> dict:
    """HTTP request with retry + exponential backoff on 5xx/timeout."""
    last_err = None
    for attempt in range(_MAX_RETRIES):
        req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            if e.code < 500 and e.code != 429:
                # Client error (4xx except 429) — don't retry
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
