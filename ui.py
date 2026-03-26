#!/usr/bin/env python3
"""baddle — web UI  (python ui.py)"""

import sys
import json
import argparse
import threading
import webbrowser
from pathlib import Path

try:
    from flask import Flask, request, render_template, jsonify
except ImportError:
    sys.exit("[error] flask not found.  pip install flask")

sys.path.insert(0, str(Path(__file__).parent))
from main import pick_model

# These need llama-cpp-python — may be None in server-only mode
try:
    from main import (load_model, _batch_generate_iter, _interleaved_generate_iter,
                      _sample, _get_logits, _entropy, format_chat)
except ImportError:
    load_model = _batch_generate_iter = _interleaved_generate_iter = None
    _sample = _get_logits = _entropy = format_chat = None

from graph import graph_bp, init_graph
from step import step_bp, init_step, get_step_state
from chat import chat_bp, init_chat
from parallel import parallel_bp, init_parallel, get_dual_result

app = Flask(__name__)
app.register_blueprint(graph_bp)
app.register_blueprint(step_bp)
app.register_blueprint(chat_bp)
app.register_blueprint(parallel_bp)

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

# ── Common routes ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", model=model_name)


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


# ── Settings (local/API) ─────────────────────────────────────────────────────

from api_backend import get_settings, update_settings, fetch_models, list_local_models

@app.route("/settings", methods=["GET"])
def settings_get():
    s = get_settings()
    # Add current model name
    s["current_model"] = model_name
    return jsonify(s)

@app.route("/settings", methods=["POST"])
def settings_post():
    data = request.get_json(force=True)
    update_settings(data)
    return jsonify(get_settings())

@app.route("/settings/models", methods=["POST"])
def settings_models():
    data = request.get_json(force=True)
    models = fetch_models(data.get("api_url", ""), data.get("api_key", ""))
    return jsonify(models)

@app.route("/settings/local-models")
def settings_local_models():
    return jsonify({"models": list_local_models()})

@app.route("/settings/reload-model", methods=["POST"])
def settings_reload_model():
    """Reload local model with new settings. Requires restart-like reinit."""
    global llm, model_name
    if load_model is None:
        return jsonify({"error": "llama-cpp-python not available"}), 400
    data = request.get_json(force=True)
    new_model = data.get("model", "")
    gpu_layers = int(data.get("gpu_layers", -1))
    ctx = int(data.get("ctx", 4096))
    if not new_model:
        return jsonify({"error": "no model specified"}), 400
    try:
        model_path = pick_model(new_model)
        model_name = model_path.name
        llm = load_model(model_path, gpu_layers, ctx)
        # Re-init all modules
        init_graph(llm)
        init_step(llm, _sample, _get_logits, _entropy)
        init_chat(llm, _sample, _get_logits, _entropy, format_chat)
        init_parallel(llm, _batch_generate_iter, _interleaved_generate_iter,
                      lambda: server_url)
        return jsonify({"ok": True, "model": model_name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Hybrid: dual → step ──────────────────────────────────────────────────────

@app.route("/dual/to-step", methods=["POST"])
def dual_to_step():
    """Switch from parallel/compare result to step mode."""
    if not llm:
        return jsonify({"error": "no local model (server mode)"}), 400
    data = request.json or {}
    stream_name = data.get("stream", "a")
    temp = float(data.get("temp", 0.0))
    top_k = int(data.get("top_k", 40))
    dual = get_dual_result()
    text = dual["text_a"] if stream_name == "a" else dual["text_b"]
    if not text:
        return jsonify({"error": "no dual result"}), 400
    tokens = llm.tokenize(text.encode())
    llm.reset()
    llm.eval(tokens)
    step_state = get_step_state()
    step_state["tokens"] = list(tokens)
    step_state["prompt_tokens"] = list(tokens)
    step_state["temp"] = temp
    step_state["top_k"] = top_k
    step_state["ready"] = True
    from step import _step_top_tokens
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
        # Check if API mode is configured — skip local model loading
        from api_backend import get_settings
        saved_settings = get_settings()
        skip_local = saved_settings.get("mode") == "api" and saved_settings.get("api_url")

        if skip_local:
            model_name = f"API: {saved_settings.get('api_model', 'configured')}"
            print(f"  API mode configured — skipping local model load.")
            print(f"  API URL: {saved_settings.get('api_url')}")
        elif load_model is None:
            model_name = "(no local engine)"
            print("  llama-cpp-python not found — local modes unavailable.")
            print("  Configure API in Settings, or run: python setup.py")
        else:
            try:
                model_path = pick_model(args.model)
                model_name = model_path.name
                gpu_layers = 0 if args.no_gpu else args.gpu_layers
                llm        = load_model(model_path, gpu_layers, args.ctx)
            except (SystemExit, FileNotFoundError, Exception):
                # No model found — start without local model
                model_name = "(no model loaded)"
                print("  No local model found — starting without model.")
                print("  Configure API or load a model via Settings.")

        if llm is not None:
            # Initialize all modules with model reference
            init_graph(llm)
            init_step(llm, _sample, _get_logits, _entropy)
            init_chat(llm, _sample, _get_logits, _entropy, format_chat)
            init_parallel(llm, _batch_generate_iter, _interleaved_generate_iter,
                          lambda: server_url)

    url = f"http://localhost:{args.port}"
    print(f"\n  Open: {url}\n")
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host="0.0.0.0", port=args.port, threaded=False)


if __name__ == "__main__":
    main()
