"""Chat mode — conversation with the model via API backend."""

import json
from flask import Blueprint, Response, request, jsonify, stream_with_context

from .api_backend import api_chat_completion

chat_bp = Blueprint("chat", __name__)

_chat = {
    "messages": [],
    "temp":     0.7,
    "top_k":    40,
    "ready":    False,
}


@chat_bp.route("/chat/send", methods=["POST"])
def chat_send():
    """Add user message — response generated via /chat/stream."""
    data = request.get_json(force=True)
    text = data.get("text", "").strip()
    system = data.get("system", "")
    temp = float(data.get("temp", 0.7))
    top_k = int(data.get("top_k", 40))
    if not text:
        return jsonify({"error": "empty message"})

    _chat["temp"] = temp
    _chat["top_k"] = top_k

    # Build messages list
    if not _chat["messages"] and system:
        _chat["messages"].append({"role": "system", "content": system})
    if _chat["messages"] and _chat["messages"][0]["role"] == "system":
        _chat["messages"][0]["content"] = system

    _chat["messages"].append({"role": "user", "content": text})
    _chat["ready"] = True

    return jsonify({"ok": True, "count": len(_chat["messages"])})


def _chat_response_impl(max_tokens: int, initial_text: str = ""):
    """Generate assistant response via API and stream as SSE.
    Since API is non-streaming in our wrapper, emit full result once then done."""
    messages = list(_chat["messages"])
    if initial_text:
        # Continue mode: last message is assistant, append a nudge
        if messages and messages[-1]["role"] == "assistant":
            messages = messages[:-1]
        messages.append({"role": "assistant", "content": initial_text})

    try:
        text, _avg, _unc, token_ents, token_texts = api_chat_completion(
            messages, max_tokens=max_tokens, temperature=_chat["temp"],
        )
    except Exception as e:
        yield f"data: {json.dumps({'error': str(e)})}\n\n"
        return

    response_text = (initial_text + text) if initial_text else text

    # Emit as one chunk (API is non-streaming) with token-level data for heatmap
    payload = {
        "text": response_text,
        "done": True,
        "reason": "api",
        "total_tokens": len(token_texts),
        "toks": token_texts,
        "ents": [round(float(e), 3) for e in token_ents],
    }
    yield f"data: {json.dumps(payload)}\n\n"

    # Save assistant message
    if _chat["messages"] and _chat["messages"][-1]["role"] == "assistant":
        _chat["messages"][-1]["content"] = response_text
    else:
        _chat["messages"].append({"role": "assistant", "content": response_text})


@chat_bp.route("/chat/stream")
def chat_stream():
    """Generate assistant response (single chunk — API is not streaming)."""
    if not _chat["ready"]:
        def err():
            yield f"data: {json.dumps({'error': 'not ready'})}\n\n"
        return Response(err(), mimetype="text/event-stream")

    max_tokens = int(request.args.get("n", 200))
    return Response(
        stream_with_context(_chat_response_impl(max_tokens)),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@chat_bp.route("/chat/continue")
def chat_continue():
    """Continue generating from the last assistant message."""
    if not _chat["ready"] or not _chat["messages"] or _chat["messages"][-1]["role"] != "assistant":
        def err():
            yield f"data: {json.dumps({'error': 'nothing to continue'})}\n\n"
        return Response(err(), mimetype="text/event-stream")

    max_tokens = int(request.args.get("n", 200))
    prev_text = _chat["messages"][-1]["content"]
    return Response(
        stream_with_context(_chat_response_impl(max_tokens, initial_text=prev_text)),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@chat_bp.route("/chat/reset", methods=["POST"])
def chat_reset():
    _chat["messages"] = []
    _chat["ready"] = False
    return jsonify({"ok": True})


@chat_bp.route("/chat/history")
def chat_history():
    return jsonify(_chat["messages"])
