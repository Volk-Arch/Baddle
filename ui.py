#!/usr/bin/env python3
"""baddle — web UI  (python ui.py)"""

import sys
import json
import argparse
import threading
import webbrowser
import numpy as np
from pathlib import Path

try:
    from flask import Flask, Response, request, render_template, stream_with_context, jsonify
except ImportError:
    sys.exit("[error] flask not found.  pip install flask")

sys.path.insert(0, str(Path(__file__).parent))
from main import pick_model, StreamCfg

# These need llama-cpp-python — may be None in server-only mode
try:
    from main import load_model, _batch_generate_iter, _interleaved_generate_iter, _sample, _get_logits, _entropy, format_chat
except ImportError:
    load_model = _batch_generate_iter = _interleaved_generate_iter = _sample = _get_logits = _entropy = format_chat = None

from graph import graph_bp, init_graph

app = Flask(__name__)
app.register_blueprint(graph_bp)
llm        = None
model_name = ""
server_url = None

# ── Roles ─────────────────────────────────────────────────────────────────────

_ROLES_FILE = Path(__file__).parent / "roles.json"

def _load_roles():
    try:
        return json.loads(_ROLES_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return [{"name": "(none)", "text": ""}]

# ── Templates ────────────────────────────────────────────────────────────────

_TEMPLATES_FILE = Path(__file__).parent / "templates.json"

def _load_templates():
    try:
        return json.loads(_TEMPLATES_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []

# ── Step mode server state ─────────────────────────────────────────────────────

_step = {
    "tokens":        [],   # all tokens (prompt + generated)
    "prompt_tokens": [],   # prompt-only tokens (for reset)
    "temp":          0.0,
    "top_k":         40,
    "ready":         False,
    "ents":          [],   # entropy per generated token
    "tok_texts":     [],   # text of each generated token
}

_dual_result = {"text_a": "", "text_b": ""}


def _step_top_tokens(n: int = 10):
    if llm.n_tokens == 0:
        return []
    logits = _get_logits(llm)
    logits -= logits.max()
    probs = np.exp(logits)
    probs /= probs.sum()
    top = np.argsort(-probs)[:n]
    return [
        {
            "id":   int(tid),
            "text": llm.detokenize([int(tid)]).decode("utf-8", errors="replace"),
            "prob": float(probs[tid]),
        }
        for tid in top
    ]


def _step_full_text():
    return llm.detokenize(_step["tokens"]).decode("utf-8", errors="replace")


def _step_reset_to_prompt():
    llm.reset()
    llm.eval(_step["prompt_tokens"])
    _step["tokens"] = list(_step["prompt_tokens"])
    _step["ents"] = []
    _step["tok_texts"] = []


# ── Step endpoints ─────────────────────────────────────────────────────────────

@app.route("/step/init", methods=["POST"])
def step_init():
    if llm is None:
        return jsonify({"error": "Step mode requires in-process model (no --server)"})
    data   = request.get_json(force=True)
    prompt = data.get("prompt", "").strip()
    temp   = float(data.get("temp", 0.0))
    top_k  = int(data.get("top_k", 40))
    if not prompt:
        return jsonify({"error": "empty prompt"})
    try:
        tokens = llm.tokenize(prompt.encode())
        llm.reset()
        llm.eval(tokens)
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


@app.route("/step/next", methods=["POST"])
def step_next():
    if not _step["ready"]:
        return jsonify({"error": "not initialized"})
    try:
        logits = _get_logits(llm)
        ent = float(_entropy(logits))
        tok    = _sample(llm, _step["temp"], _step["top_k"])
        piece  = llm.detokenize([tok]).decode("utf-8", errors="replace")
        llm.eval([tok])
        _step["tokens"].append(tok)
        _step["ents"].append(ent)
        _step["tok_texts"].append(piece)
        is_eos = tok == llm.token_eos()
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


@app.route("/step/auto")
def step_auto():
    if not _step["ready"]:
        return jsonify({"error": "not initialized"})
    n = int(request.args.get("n", 10))

    def generate():
        try:
            for i in range(n):
                logits = _get_logits(llm)
                ent = float(_entropy(logits))
                tok = _sample(llm, _step["temp"], _step["top_k"])
                piece = llm.detokenize([tok]).decode("utf-8", errors="replace")
                llm.eval([tok])
                _step["tokens"].append(tok)
                _step["ents"].append(ent)
                _step["tok_texts"].append(piece)
                is_eos = tok == llm.token_eos()
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


@app.route("/step/reset", methods=["POST"])
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


@app.route("/step/edit", methods=["POST"])
def step_edit():
    """Sync model state with edited text from contenteditable output."""
    if llm is None:
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
            # No change
            return jsonify({"full_text": cur_text, "top_tokens": _step_top_tokens(10), "action": "none"})

        if cur_text.startswith(new_text):
            # Text was trimmed — cut
            new_tokens = llm.tokenize(new_text.encode())
            _step["tokens"] = list(new_tokens)
            llm.reset()
            llm.eval(new_tokens)
            _step["ready"] = True
            return jsonify({
                "full_text": _step_full_text(),
                "top_tokens": _step_top_tokens(10),
                "action": "cut",
                "token_count": len(new_tokens),
            })

        if new_text.startswith(cur_text):
            # Text was appended — inject the tail
            tail = new_text[len(cur_text):]
            toks = llm.tokenize(tail.encode(), add_bos=False)
            for t in toks:
                llm.eval([t])
                _step["tokens"].append(t)
            _step["ready"] = True
            return jsonify({
                "full_text": _step_full_text(),
                "top_tokens": _step_top_tokens(10),
                "action": "inject",
                "injected_tokens": len(toks),
            })

        # Text was changed in the middle — full re-eval
        new_tokens = llm.tokenize(new_text.encode())
        _step["tokens"] = list(new_tokens)
        llm.reset()
        llm.eval(new_tokens)
        _step["ready"] = True
        return jsonify({
            "full_text": _step_full_text(),
            "top_tokens": _step_top_tokens(10),
            "action": "re-eval",
            "token_count": len(new_tokens),
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/step/temp", methods=["POST"])
def step_temp():
    data = request.get_json(force=True)
    _step["temp"] = float(data.get("temp", 0.0))
    if "top_k" in data:
        _step["top_k"] = int(data["top_k"])
    return jsonify({"temp": _step["temp"], "top_k": _step["top_k"]})


@app.route("/roles")
def get_roles():
    return jsonify(_load_roles())

@app.route("/templates")
def get_templates():
    return jsonify(_load_templates())

@app.route("/model/info")
def model_info():
    ctx = llm.n_ctx() if llm else 0
    return jsonify({"n_ctx": ctx})


# ── Chat mode state ───────────────────────────────────────────────────────────

_chat = {
    "messages": [],     # [{"role": ..., "content": ...}]
    "tokens":   [],     # all tokens in current context
    "temp":     0.7,
    "ready":    False,
}


@app.route("/chat/send", methods=["POST"])
def chat_send():
    """Add user message, generate assistant response via SSE."""
    if llm is None:
        return jsonify({"error": "Chat mode requires in-process model (no --server)"})
    data = request.get_json(force=True)
    text = data.get("text", "").strip()
    system = data.get("system", "")
    temp = float(data.get("temp", 0.7))
    if not text:
        return jsonify({"error": "empty message"})

    _chat["temp"] = temp

    # Build messages list
    if not _chat["messages"] and system:
        _chat["messages"].append({"role": "system", "content": system})
    # If system changed mid-conversation, update it
    if _chat["messages"] and _chat["messages"][0]["role"] == "system":
        _chat["messages"][0]["content"] = system

    _chat["messages"].append({"role": "user", "content": text})

    # Format with chat template
    prompt_str = format_chat(llm, _chat["messages"])

    # Tokenize and eval full conversation
    tokens = llm.tokenize(prompt_str.encode())
    llm.reset()
    llm.eval(tokens)
    _chat["tokens"] = list(tokens)
    _chat["ready"] = True

    return jsonify({"ok": True, "token_count": len(tokens)})


@app.route("/chat/stream")
def chat_stream():
    """Stream assistant response token by token."""
    if not _chat["ready"]:
        def err():
            yield f"data: {json.dumps({'error': 'not ready'})}\n\n"
        return Response(err(), mimetype="text/event-stream")

    max_tokens = int(request.args.get("n", 200))

    def generate():
        response_text = ""
        eos = llm.token_eos()
        tok_texts = []
        ents = []
        # Detect im_end token for chat models
        try:
            im_end_tokens = llm.tokenize("<|im_end|>".encode(), add_bos=False)
        except Exception:
            im_end_tokens = []

        for step in range(max_tokens):
            logits = _get_logits(llm)
            ent = float(_entropy(logits))
            tok = _sample(llm, _chat["temp"])
            llm.eval([tok])
            _chat["tokens"].append(tok)

            if tok == eos:
                yield f"data: {json.dumps({'done': True, 'reason': 'eos', 'text': response_text, 'total_tokens': len(_chat['tokens']), 'toks': tok_texts, 'ents': [round(e,3) for e in ents]})}\n\n"
                break

            piece = llm.detokenize([tok]).decode("utf-8", errors="replace")
            response_text += piece
            tok_texts.append(piece)
            ents.append(ent)

            # Check for <|im_end|> in response
            if "<|im_end|>" in response_text:
                response_text = response_text.replace("<|im_end|>", "")
                yield f"data: {json.dumps({'done': True, 'reason': 'eos', 'text': response_text, 'total_tokens': len(_chat['tokens']), 'toks': tok_texts, 'ents': [round(e,3) for e in ents]})}\n\n"
                break

            yield f"data: {json.dumps({'text': response_text, 'step': step, 'total_tokens': len(_chat['tokens']), 'toks': tok_texts, 'ents': [round(e,3) for e in ents]})}\n\n"
        else:
            yield f"data: {json.dumps({'done': True, 'reason': 'limit', 'text': response_text, 'total_tokens': len(_chat['tokens']), 'toks': tok_texts, 'ents': [round(e,3) for e in ents]})}\n\n"

        # Save assistant response to history (will be updated on continue)
        _chat["messages"].append({"role": "assistant", "content": response_text})
        _chat["ready"] = True

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/chat/continue")
def chat_continue():
    """Continue generating from where the last response was truncated."""
    if not _chat["ready"] or not _chat["messages"] or _chat["messages"][-1]["role"] != "assistant":
        def err():
            yield f"data: {json.dumps({'error': 'nothing to continue'})}\n\n"
        return Response(err(), mimetype="text/event-stream")

    max_tokens = int(request.args.get("n", 200))
    prev_text = _chat["messages"][-1]["content"]

    def generate():
        response_text = prev_text
        eos = llm.token_eos()
        tok_texts = []
        ents = []

        for step in range(max_tokens):
            logits = _get_logits(llm)
            ent = float(_entropy(logits))
            tok = _sample(llm, _chat["temp"])
            llm.eval([tok])
            _chat["tokens"].append(tok)

            if tok == eos:
                yield f"data: {json.dumps({'done': True, 'reason': 'eos', 'text': response_text, 'total_tokens': len(_chat['tokens']), 'toks': tok_texts, 'ents': [round(e,3) for e in ents]})}\n\n"
                break

            piece = llm.detokenize([tok]).decode("utf-8", errors="replace")
            response_text += piece
            tok_texts.append(piece)
            ents.append(ent)

            if "<|im_end|>" in response_text:
                response_text = response_text.replace("<|im_end|>", "")
                yield f"data: {json.dumps({'done': True, 'reason': 'eos', 'text': response_text, 'total_tokens': len(_chat['tokens']), 'toks': tok_texts, 'ents': [round(e,3) for e in ents]})}\n\n"
                break

            yield f"data: {json.dumps({'text': response_text, 'step': step, 'total_tokens': len(_chat['tokens']), 'toks': tok_texts, 'ents': [round(e,3) for e in ents]})}\n\n"
        else:
            yield f"data: {json.dumps({'done': True, 'reason': 'limit', 'text': response_text, 'total_tokens': len(_chat['tokens']), 'toks': tok_texts, 'ents': [round(e,3) for e in ents]})}\n\n"

        # Update last assistant message
        _chat["messages"][-1]["content"] = response_text
        _chat["ready"] = True

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/chat/reset", methods=["POST"])
def chat_reset():
    _chat["messages"] = []
    _chat["tokens"] = []
    _chat["ready"] = False
    if llm:
        llm.reset()
    return jsonify({"ok": True})


@app.route("/chat/history")
def chat_history():
    return jsonify(_chat["messages"])


# ── SSE endpoint (parallel / compare) ─────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", model=model_name)


@app.route("/stream")
def stream():
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
    prompt_toks = len(llm.tokenize(pa.encode())) if llm else 0

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
                for item in _batch_generate_iter(llm, pa, pb, n, cfg_a, cfg_b):
                    yield "data", item
                return
            except Exception:
                pass
            tag = "1-prefill interleaved" if pa == pb else "interleaved"
            yield "tag", tag
            for item in _interleaved_generate_iter(llm, pa, pb, n, cfg_a, cfg_b):
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


@app.route("/dual/to-step", methods=["POST"])
def dual_to_step():
    """Switch from parallel/compare result to step mode."""
    if not llm:
        return jsonify({"error": "no local model (server mode)"}), 400
    data = request.json or {}
    stream = data.get("stream", "a")
    temp = float(data.get("temp", 0.0))
    top_k = int(data.get("top_k", 40))
    text = _dual_result["text_a"] if stream == "a" else _dual_result["text_b"]
    if not text:
        return jsonify({"error": "no dual result"}), 400
    tokens = llm.tokenize(text.encode())
    llm.reset()
    llm.eval(tokens)
    _step["tokens"] = list(tokens)
    _step["prompt_tokens"] = list(tokens)
    _step["temp"] = temp
    _step["top_k"] = top_k
    _step["ready"] = True
    return jsonify({
        "text": text,
        "top": _step_top_tokens(),
        "total_tokens": len(tokens),
    })


# ── entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="baddle web UI")
    parser.add_argument("-m", "--model",    help="model path or filename in models/")
    parser.add_argument("--no-gpu",         action="store_true")
    parser.add_argument("--gpu-layers",     type=int, default=-1)
    parser.add_argument("--ctx",            type=int, default=4096)
    parser.add_argument("--port",           type=int, default=7860)
    parser.add_argument("--server",         type=str, default=None, nargs="?", const="auto",
                        help="llama-server URL or just --server to auto-launch")
    args = parser.parse_args()

    global llm, model_name, server_url

    if args.server is not None:
        if args.server == "auto" or not args.server.startswith("http"):
            model_path = pick_model(args.model)
            gpu_layers = 0 if args.no_gpu else args.gpu_layers
            from server_backend import launch_server
            print("  Starting llama-server...")
            server_url = launch_server(
                str(model_path), n_ctx=args.ctx, gpu_layers=gpu_layers,
            )
            model_name = f"server: {server_url}"
            print(f"  Server ready: {server_url}")
        else:
            from server_backend import server_available
            if server_available(args.server):
                server_url = args.server.rstrip("/")
                model_name = f"server: {server_url}"
                print(f"  Server mode: {server_url}")
            else:
                print(f"  Server at {args.server} not reachable, loading model locally...")

    if server_url is None:
        if load_model is None:
            sys.exit("[error] llama-cpp-python not found and no llama-server available.\n"
                     "Run: python setup.py")
        model_path = pick_model(args.model)
        model_name = model_path.name
        gpu_layers = 0 if args.no_gpu else args.gpu_layers
        llm        = load_model(model_path, gpu_layers, args.ctx, embedding=True)
        init_graph(llm)

    url = f"http://localhost:{args.port}"
    print(f"\n  Open: {url}\n")
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host="0.0.0.0", port=args.port, threaded=False)


if __name__ == "__main__":
    main()
