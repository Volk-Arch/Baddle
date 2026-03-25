"""Parallel / Compare mode — two streams side by side."""

import json
import numpy as np
from flask import Blueprint, Response, request, jsonify, stream_with_context

parallel_bp = Blueprint("parallel", __name__)

_llm = None
_batch_generate_iter_fn = None
_interleaved_generate_iter_fn = None
_server_url_ref = None  # callable that returns current server_url

_dual_result = {"text_a": "", "text_b": ""}


def init_parallel(llm, batch_fn, interleaved_fn, server_url_fn):
    global _llm, _batch_generate_iter_fn, _interleaved_generate_iter_fn, _server_url_ref
    _llm = llm
    _batch_generate_iter_fn = batch_fn
    _interleaved_generate_iter_fn = interleaved_fn
    _server_url_ref = server_url_fn


def get_dual_result():
    """Return dual result for dual_to_step."""
    return _dual_result


@parallel_bp.route("/stream")
def stream():
    from main import StreamCfg

    pa      = request.args.get("pa", "")
    pb      = request.args.get("pb", pa)
    n       = int(request.args.get("n",       50))
    temp_a  = float(request.args.get("temp_a",  0.7))
    temp_b  = float(request.args.get("temp_b",  0.7))
    top_k_a = int(request.args.get("top_k_a", 40))
    top_k_b = int(request.args.get("top_k_b", 40))
    seed    = int(request.args.get("seed",    -1))

    cfg_a = StreamCfg(label="A", temp=temp_a, top_k=top_k_a, color="cyan", seed=seed)
    cfg_b = StreamCfg(label="B", temp=temp_b, top_k=top_k_b, color="magenta", seed=seed)

    if seed >= 0:
        np.random.seed(seed)

    # Estimate prompt token counts for token counter
    prompt_toks = len(_llm.tokenize(pa.encode())) if _llm else 0
    server_url = _server_url_ref() if _server_url_ref else None

    def generate():
        def _iter():
            """Try server, then batch, then interleaved."""
            if server_url:
                from server_backend import _server_generate_iter, is_native_server
                native = is_native_server(server_url)
                yield "tag", "llama-server (parallel)" if native else "llama-server (sequential)"
                for item in _server_generate_iter(server_url, pa, pb, n, cfg_a, cfg_b):
                    yield "data", item
                return
            try:
                yield "tag", "kv-shared (2 decodes/step)"
                for item in _batch_generate_iter_fn(_llm, pa, pb, n, cfg_a, cfg_b):
                    yield "data", item
                return
            except Exception:
                pass
            tag = "1-prefill interleaved" if pa == pb else "interleaved"
            yield "tag", tag
            for item in _interleaved_generate_iter_fn(_llm, pa, pb, n, cfg_a, cfg_b):
                yield "data", item

        try:
            for kind, val in _iter():
                if kind == "tag":
                    yield f"data: {json.dumps({'mode_tag': val})}\n\n"
                else:
                    text_a, text_b, step, done_a, done_b, ents_a, ents_b, toks_a, toks_b = val
                    total_tokens = prompt_toks + step + 1
                    payload = {'a': text_a, 'b': text_b, 'step': step, 'done_a': done_a, 'done_b': done_b, 'total_tokens': total_tokens}
                    if toks_a:
                        payload['toks_a'] = toks_a
                        payload['toks_b'] = toks_b
                        payload['ents_a'] = [round(e, 3) for e in ents_a]
                        payload['ents_b'] = [round(e, 3) for e in ents_b]
                    _dual_result["text_a"] = text_a
                    _dual_result["text_b"] = text_b
                    yield f"data: {json.dumps(payload)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            return

        yield f"data: {json.dumps({'done': True})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
