"""API backend — OpenAI-compatible client for graph/chat generation."""

import json
import logging
import urllib.request
import urllib.error
from pathlib import Path

log = logging.getLogger(__name__)

_SETTINGS_FILE = Path(__file__).parent / "settings.json"

_settings = {
    "mode": "local",        # "local" | "api" | "hybrid"
    "api_url": "",          # e.g. "https://api.openai.com/v1"
    "api_key": "",
    "api_model": "",        # e.g. "gpt-4o", "claude-sonnet-4-20250514"
    # Hybrid routing
    "hybrid_graph": "api",      # "api" | "local"
    "hybrid_embeddings": "local",  # "api" | "local"
    "hybrid_chat": "api",       # "api" | "local"
    # Local model
    "local_model": "",          # filename in models/
    "local_gpu_layers": -1,
    "local_ctx": 4096,
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
    for k in ("mode", "api_url", "api_key", "api_model",
              "hybrid_graph", "hybrid_embeddings", "hybrid_chat",
              "local_model", "local_gpu_layers", "local_ctx"):
        if k in new:
            _settings[k] = new[k]
    _save_settings()


def list_local_models() -> list[str]:
    """Scan models/ directory for GGUF files."""
    models_dir = Path(__file__).parent / "models"
    if not models_dir.exists():
        return []
    return sorted([f.name for f in models_dir.glob("*.gguf")])


def is_api_mode():
    return _settings["mode"] == "api" and _settings["api_url"] and _settings["api_model"]


def use_api_for(component: str) -> bool:
    """Check if a component should use API. component: 'graph', 'embeddings', 'chat'."""
    if _settings["mode"] == "local":
        return False
    if _settings["mode"] == "api":
        return bool(_settings["api_url"] and _settings["api_model"])
    # hybrid
    if not _settings["api_url"] or not _settings["api_model"]:
        return False
    key = f"hybrid_{component}"
    return _settings.get(key, "local") == "api"


def fetch_models(api_url: str, api_key: str) -> dict:
    """Fetch available models from OpenAI-compatible /v1/models endpoint."""
    url = api_url.rstrip("/") + "/models"
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


def api_chat_completion(messages: list, max_tokens: int = 200, temperature: float = 0.9,
                        top_k: int = 40, repeat_penalty: float = 1.1) -> tuple:
    """Call OpenAI-compatible chat completion API.

    Returns (text, entropy_avg, uncertainty_pct, token_entropies, token_texts)
    where entropy/token data may be empty if API doesn't support logprobs.
    """
    url = _settings["api_url"].rstrip("/") + "/chat/completions"

    body = {
        "model": _settings["api_model"],
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    # Try to request logprobs (OpenAI supports this)
    body["logprobs"] = True
    body["top_logprobs"] = 1

    data = json.dumps(body).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
    }
    if _settings["api_key"]:
        headers["Authorization"] = f"Bearer {_settings['api_key']}"

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        log.error(f"[api] HTTP {e.code}: {error_body[:500]}")
        raise RuntimeError(f"API error {e.code}: {error_body[:200]}")
    except Exception as e:
        log.error(f"[api] Request failed: {e}")
        raise RuntimeError(f"API request failed: {e}")

    choice = result.get("choices", [{}])[0]
    message = choice.get("message", {})
    text = message.get("content", "")

    # Extract logprobs if available
    token_ents = []
    token_texts = []
    logprobs_data = choice.get("logprobs")
    if logprobs_data and "content" in logprobs_data:
        for tok_info in logprobs_data["content"]:
            lp = tok_info.get("logprob", 0)
            token_ents.append(-lp if lp else 0.0)
            token_texts.append(tok_info.get("token", ""))

    if token_ents:
        avg_ent = sum(token_ents) / len(token_ents)
        high_threshold = 2.0
        unc_pct = sum(1 for e in token_ents if e > high_threshold) / len(token_ents)
    else:
        avg_ent = 0.0
        unc_pct = 0.0

    return text, avg_ent, unc_pct, token_ents, token_texts


def api_get_embedding(text: str) -> list:
    """Get embedding via OpenAI-compatible API.

    Returns embedding vector as list of floats, or empty list if not available.
    """
    url = _settings["api_url"].rstrip("/") + "/embeddings"

    body = {
        "model": _settings["api_model"],
        "input": text,
    }

    data = json.dumps(body).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
    }
    if _settings["api_key"]:
        headers["Authorization"] = f"Bearer {_settings['api_key']}"

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return result["data"][0]["embedding"]
    except Exception as e:
        log.warning(f"[api] Embedding failed: {e}")
        return []
