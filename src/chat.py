"""Chat mode — conversation with the model via API backend.

Non-streaming by design: chat is a helper tool for the graph, not a live typing
demo. User sends a message, waits, gets the full response, decides what to do
with it (save as node, copy, etc).
"""

from flask import Blueprint, request, jsonify

from .api_backend import api_chat_completion

chat_bp = Blueprint("chat", __name__)

_MAX_TOKENS = 4000  # reasonable default, LM Studio/API server enforces its own ctx

_chat = {
    "messages": [],
    "temp": 0.7,
    "top_k": 40,
}


@chat_bp.route("/chat/send", methods=["POST"])
def chat_send():
    """Add user message and return the assistant response in one shot."""
    data = request.get_json(force=True)
    text = data.get("text", "").strip()
    system = data.get("system", "")
    temp = float(data.get("temp", 0.7))
    top_k = int(data.get("top_k", 40))
    if not text:
        return jsonify({"error": "empty message"})

    _chat["temp"] = temp
    _chat["top_k"] = top_k

    # Inject / update system message
    if not _chat["messages"] and system:
        _chat["messages"].append({"role": "system", "content": system})
    elif _chat["messages"] and _chat["messages"][0]["role"] == "system":
        _chat["messages"][0]["content"] = system

    _chat["messages"].append({"role": "user", "content": text})

    try:
        reply, _avg, _unc, _ents, _toks = api_chat_completion(
            _chat["messages"], max_tokens=_MAX_TOKENS, temperature=temp, top_k=top_k,
        )
    except Exception as e:
        return jsonify({"error": str(e)})

    _chat["messages"].append({"role": "assistant", "content": reply})

    return jsonify({"text": reply, "count": len(_chat["messages"])})


@chat_bp.route("/chat/reset", methods=["POST"])
def chat_reset():
    _chat["messages"] = []
    return jsonify({"ok": True})


@chat_bp.route("/chat/history")
def chat_history():
    return jsonify(_chat["messages"])
