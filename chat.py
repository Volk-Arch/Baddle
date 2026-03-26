"""Chat mode — conversation with the model."""

import json
from flask import Blueprint, Response, request, jsonify, stream_with_context

chat_bp = Blueprint("chat", __name__)

_llm = None
_sample_fn = None
_get_logits_fn = None
_entropy_fn = None
_format_chat_fn = None

_chat = {
    "messages": [],
    "tokens":   [],
    "temp":     0.7,
    "top_k":    40,
    "ready":    False,
}


def init_chat(llm, sample_fn, get_logits_fn, entropy_fn, format_chat_fn):
    global _llm, _sample_fn, _get_logits_fn, _entropy_fn, _format_chat_fn
    _llm = llm
    _sample_fn = sample_fn
    _get_logits_fn = get_logits_fn
    _entropy_fn = entropy_fn
    _format_chat_fn = format_chat_fn


@chat_bp.route("/chat/send", methods=["POST"])
def chat_send():
    """Add user message, generate assistant response via SSE."""
    if _llm is None:
        return jsonify({"error": "Chat mode requires in-process model (no --server)"})
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

    # Format with chat template, tokenize and eval — keeps KV cache for fast continue
    prompt_str = _format_chat_fn(_llm, _chat["messages"])
    tokens = _llm.tokenize(prompt_str.encode())
    _llm.reset()
    _llm.eval(tokens)
    _chat["tokens"] = list(tokens)
    _chat["ready"] = True

    return jsonify({"ok": True, "token_count": len(tokens)})


def _chat_stream_impl(max_tokens: int, initial_text: str = ""):
    """Shared streaming generator for chat stream and continue."""
    response_text = initial_text
    eos = _llm.token_eos()
    tok_texts = []
    ents = []

    # Build stop token set
    stop_ids = {eos}
    try:
        im_end = _llm.tokenize("<|im_end|>".encode(), add_bos=False)
        if im_end:
            stop_ids.add(im_end[-1])
    except Exception:
        pass

    for step in range(max_tokens):
        logits = _get_logits_fn(_llm)
        ent = float(_entropy_fn(logits))
        tok = _sample_fn(_llm, _chat["temp"], _chat["top_k"])
        _llm.eval([tok])
        _chat["tokens"].append(tok)

        if tok in stop_ids:
            yield f"data: {json.dumps({'done': True, 'reason': 'eos', 'text': response_text, 'total_tokens': len(_chat['tokens']), 'toks': tok_texts, 'ents': [round(e,3) for e in ents]})}\n\n"
            break

        piece = _llm.detokenize([tok]).decode("utf-8", errors="replace")
        response_text += piece
        tok_texts.append(piece)
        ents.append(ent)

        yield f"data: {json.dumps({'text': response_text, 'step': step, 'total_tokens': len(_chat['tokens']), 'toks': tok_texts, 'ents': [round(e,3) for e in ents]})}\n\n"
    else:
        yield f"data: {json.dumps({'done': True, 'reason': 'limit', 'text': response_text, 'total_tokens': len(_chat['tokens']), 'toks': tok_texts, 'ents': [round(e,3) for e in ents]})}\n\n"

    # Save/update assistant message
    if _chat["messages"] and _chat["messages"][-1]["role"] == "assistant":
        _chat["messages"][-1]["content"] = response_text
    else:
        _chat["messages"].append({"role": "assistant", "content": response_text})
    _chat["ready"] = True


@chat_bp.route("/chat/stream")
def chat_stream():
    """Stream assistant response token by token."""
    if not _chat["ready"]:
        def err():
            yield f"data: {json.dumps({'error': 'not ready'})}\n\n"
        return Response(err(), mimetype="text/event-stream")

    max_tokens = int(request.args.get("n", 200))
    return Response(
        stream_with_context(_chat_stream_impl(max_tokens)),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@chat_bp.route("/chat/continue")
def chat_continue():
    """Continue generating from where the last response was truncated."""
    if not _chat["ready"] or not _chat["messages"] or _chat["messages"][-1]["role"] != "assistant":
        def err():
            yield f"data: {json.dumps({'error': 'nothing to continue'})}\n\n"
        return Response(err(), mimetype="text/event-stream")

    max_tokens = int(request.args.get("n", 200))
    prev_text = _chat["messages"][-1]["content"]
    return Response(
        stream_with_context(_chat_stream_impl(max_tokens, initial_text=prev_text)),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@chat_bp.route("/chat/reset", methods=["POST"])
def chat_reset():
    _chat["messages"] = []
    _chat["tokens"] = []
    _chat["ready"] = False
    if _llm:
        _llm.reset()
    return jsonify({"ok": True})


@chat_bp.route("/chat/history")
def chat_history():
    return jsonify(_chat["messages"])
