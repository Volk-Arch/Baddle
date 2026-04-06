"""Step mode — token-by-token generation with manual control."""

import json
import numpy as np
from flask import Blueprint, Response, request, jsonify, stream_with_context

step_bp = Blueprint("step", __name__)

_llm = None
_sample_fn = None
_get_logits_fn = None
_entropy_fn = None

_step = {
    "tokens":        [],
    "prompt_tokens": [],
    "temp":          0.0,
    "top_k":         40,
    "ready":         False,
    "ents":          [],
    "tok_texts":     [],
}


def init_step(llm, sample_fn, get_logits_fn, entropy_fn):
    global _llm, _sample_fn, _get_logits_fn, _entropy_fn
    _llm = llm
    _sample_fn = sample_fn
    _get_logits_fn = get_logits_fn
    _entropy_fn = entropy_fn


def _step_top_tokens(n: int = 10):
    if _llm.n_tokens == 0:
        return []
    logits = _get_logits_fn(_llm)
    logits -= logits.max()
    probs = np.exp(logits)
    probs /= probs.sum()
    top = np.argsort(-probs)[:n]
    return [
        {
            "id":   int(tid),
            "text": _llm.detokenize([int(tid)]).decode("utf-8", errors="replace"),
            "prob": float(probs[tid]),
        }
        for tid in top
    ]


def _step_full_text():
    return _llm.detokenize(_step["tokens"]).decode("utf-8", errors="replace")


def _step_reset_to_prompt():
    _llm.reset()
    _llm.eval(_step["prompt_tokens"])
    _step["tokens"] = list(_step["prompt_tokens"])
    _step["ents"] = []
    _step["tok_texts"] = []


def get_step_state():
    """Return step state dict for dual_to_step."""
    return _step


@step_bp.route("/step/init", methods=["POST"])
def step_init():
    if _llm is None:
        return jsonify({"error": "Step mode requires in-process model (no --server)"})
    data   = request.get_json(force=True)
    prompt = data.get("prompt", "").strip()
    temp   = float(data.get("temp", 0.0))
    top_k  = int(data.get("top_k", 40))
    if not prompt:
        return jsonify({"error": "empty prompt"})
    try:
        tokens = _llm.tokenize(prompt.encode())
        _llm.reset()
        _llm.eval(tokens)
        _step["prompt_tokens"] = list(tokens)
        _step["tokens"]        = list(tokens)
        _step["temp"]          = temp
        _step["top_k"]         = top_k
        _step["ready"]         = True
        _step["ents"]          = []
        _step["tok_texts"]     = []
        return jsonify({
            "text":        prompt,
            "token_count": len(tokens),
            "top_tokens":  _step_top_tokens(10),
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@step_bp.route("/step/next", methods=["POST"])
def step_next():
    if not _step["ready"]:
        return jsonify({"error": "not initialized"})
    try:
        logits = _get_logits_fn(_llm)
        ent = float(_entropy_fn(logits))
        tok    = _sample_fn(_llm, _step["temp"], _step["top_k"])
        piece  = _llm.detokenize([tok]).decode("utf-8", errors="replace")
        _llm.eval([tok])
        _step["tokens"].append(tok)
        _step["ents"].append(ent)
        _step["tok_texts"].append(piece)
        is_eos = tok == _llm.token_eos()
        if is_eos:
            _step["ready"] = False
        return jsonify({
            "token_text": piece,
            "full_text":  _step_full_text(),
            "top_tokens": _step_top_tokens(10),
            "step":         len(_step["tokens"]) - len(_step["prompt_tokens"]),
            "total_tokens": len(_step["tokens"]),
            "is_eos":       is_eos,
            "ents":       [round(e, 3) for e in _step["ents"]],
            "tok_texts":  _step["tok_texts"],
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@step_bp.route("/step/auto")
def step_auto():
    if not _step["ready"]:
        return jsonify({"error": "not initialized"})
    n = int(request.args.get("n", 10))

    def generate():
        try:
            for i in range(n):
                logits = _get_logits_fn(_llm)
                ent = float(_entropy_fn(logits))
                tok = _sample_fn(_llm, _step["temp"], _step["top_k"])
                piece = _llm.detokenize([tok]).decode("utf-8", errors="replace")
                _llm.eval([tok])
                _step["tokens"].append(tok)
                _step["ents"].append(ent)
                _step["tok_texts"].append(piece)
                is_eos = tok == _llm.token_eos()
                payload = {
                    "full_text":    _step_full_text(),
                    "step":         len(_step["tokens"]) - len(_step["prompt_tokens"]),
                    "total_tokens": len(_step["tokens"]),
                    "top_tokens":   _step_top_tokens(5),
                    "eos":          is_eos,
                    "ents":       [round(e, 3) for e in _step["ents"]],
                    "tok_texts":  _step["tok_texts"],
                }
                yield f"data: {json.dumps(payload)}\n\n"
                if is_eos:
                    _step["ready"] = False
                    return
        except GeneratorExit:
            return
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            return
        yield f"data: {json.dumps({'done': True})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@step_bp.route("/step/reset", methods=["POST"])
def step_reset():
    if not _step["prompt_tokens"]:
        return jsonify({"error": "not initialized"})
    try:
        _step_reset_to_prompt()
        _step["ready"] = True
        return jsonify({
            "full_text":  _step_full_text(),
            "top_tokens": _step_top_tokens(10),
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@step_bp.route("/step/edit", methods=["POST"])
def step_edit():
    """Sync model state with edited text from contenteditable output."""
    if _llm is None:
        return jsonify({"error": "Step mode requires in-process model"})
    data = request.get_json(force=True)
    new_text = data.get("text", "")
    if not new_text:
        return jsonify({"error": "empty text"})
    try:
        _step["ents"] = []
        _step["tok_texts"] = []
        cur_text = _step_full_text()

        if new_text == cur_text:
            return jsonify({"full_text": cur_text, "top_tokens": _step_top_tokens(10), "action": "none"})

        if cur_text.startswith(new_text):
            new_tokens = _llm.tokenize(new_text.encode())
            _step["tokens"] = list(new_tokens)
            _llm.reset()
            _llm.eval(new_tokens)
            _step["ready"] = True
            return jsonify({
                "full_text": _step_full_text(),
                "top_tokens": _step_top_tokens(10),
                "action": "cut",
                "token_count": len(new_tokens),
            })

        if new_text.startswith(cur_text):
            tail = new_text[len(cur_text):]
            toks = _llm.tokenize(tail.encode(), add_bos=False)
            for t in toks:
                _llm.eval([t])
                _step["tokens"].append(t)
            _step["ready"] = True
            return jsonify({
                "full_text": _step_full_text(),
                "top_tokens": _step_top_tokens(10),
                "action": "inject",
                "injected_tokens": len(toks),
            })

        new_tokens = _llm.tokenize(new_text.encode())
        _step["tokens"] = list(new_tokens)
        _llm.reset()
        _llm.eval(new_tokens)
        _step["ready"] = True
        return jsonify({
            "full_text": _step_full_text(),
            "top_tokens": _step_top_tokens(10),
            "action": "re-eval",
            "token_count": len(new_tokens),
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@step_bp.route("/step/temp", methods=["POST"])
def step_temp():
    data = request.get_json(force=True)
    _step["temp"] = float(data.get("temp", 0.0))
    if "top_k" in data:
        _step["top_k"] = int(data["top_k"])
    return jsonify({"temp": _step["temp"], "top_k": _step["top_k"]})
