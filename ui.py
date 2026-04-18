#!/usr/bin/env python3
"""baddle — web UI (python ui.py)

API-only mode: all generation goes through an OpenAI-compatible endpoint
(LM Studio, llama-server, Ollama, OpenAI, etc.). Configure in Settings.
"""

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

from src.graph_routes import graph_bp
from src.chat import chat_bp
from src.assistant import assistant_bp
from src.api_backend import get_settings, update_settings, fetch_models
from src.cognitive_loop import get_cognitive_loop

app = Flask(__name__)
app.register_blueprint(graph_bp)
app.register_blueprint(chat_bp)
app.register_blueprint(assistant_bp)

# Start background cognitive loop (Scout, DMN, HRV alerts, NE homeostasis)
get_cognitive_loop().start()


# ── Roles / Templates ────────────────────────────────────────────────────────

_ROLES_FILE = Path(__file__).parent / "roles.json"
_TEMPLATES_FILE = Path(__file__).parent / "templates.json"


def _load_roles():
    try:
        return json.loads(_ROLES_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return [{"name": "(none)", "text": ""}]


def _load_templates():
    try:
        return json.loads(_TEMPLATES_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


# ── Common routes ───────────────────────────────────────────────────────────

@app.route("/")
def index():
    s = get_settings()
    model = s.get("api_model") or "(not configured)"
    return render_template("index.html", model=model)


@app.route("/roles")
def get_roles():
    return jsonify(_load_roles())


@app.route("/templates")
def get_templates():
    return jsonify(_load_templates())


@app.route("/model/info")
def model_info():
    s = get_settings()
    return jsonify({"n_ctx": s.get("local_ctx", 32768), "model": s.get("api_model", "")})


@app.route("/modes")
def get_modes():
    from src.modes import list_modes
    return jsonify(list_modes())


# ── Settings ────────────────────────────────────────────────────────────────

@app.route("/settings", methods=["GET"])
def settings_get():
    s = get_settings()
    s["current_model"] = s.get("api_model", "")
    return jsonify(s)


@app.route("/settings", methods=["POST"])
def settings_post():
    data = request.get_json(force=True)
    update_settings(data)
    return jsonify(get_settings())


@app.route("/settings/models", methods=["POST"])
def settings_models():
    data = request.get_json(force=True)
    result = fetch_models(data.get("api_url", ""), data.get("api_key", ""))
    return jsonify(result)


# ── entry point ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="baddle web UI")
    parser.add_argument("--port", type=int, default=7860)
    args = parser.parse_args()

    s = get_settings()
    api_url = s.get("api_url", "")
    api_model = s.get("api_model", "")
    if not api_url or not api_model:
        print("  ⚠ API not configured. Open Settings to set api_url + api_model.")
        print(f"    Default: http://localhost:1234 (LM Studio)")
    else:
        print(f"  API: {api_url}  ·  model: {api_model}")

    url = f"http://localhost:{args.port}"
    print(f"\n  Open: {url}\n")
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host="0.0.0.0", port=args.port, threaded=True)


if __name__ == "__main__":
    main()
