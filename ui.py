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

# Force UTF-8 для stdout/stderr — критично на Windows где default cp1251
# ломает print() с Unicode (→ ← ← ↔ эмодзи). Без этого /graph/elaborate
# и другие endpoints возвращают HTTP 500 на любом print'е.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    from flask import Flask, request, render_template, jsonify
except ImportError:
    sys.exit("[error] flask not found.  pip install flask")

from src.paths import ensure_data_dir
ensure_data_dir()

from src.graph_routes import graph_bp
from src.chat import chat_bp
from src.assistant import assistant_bp
from src.api_backend import get_settings, update_settings, fetch_models
from src.cognitive_loop import get_cognitive_loop

app = Flask(__name__)
app.register_blueprint(graph_bp)
app.register_blueprint(chat_bp)
app.register_blueprint(assistant_bp)

# Seed-on-empty: первый запуск (нет `graphs/main/`) — заливаем демо-контент,
# чтобы UI не выглядел мёртвым.
try:
    from src.demo import should_auto_seed, seed_demo
    if should_auto_seed():
        seed_demo()
        print("  [demo] auto-seed: main (first run)")
except Exception as _e:
    print(f"  [demo] auto-seed skipped: {_e}")

# Bootstrap: загрузить граф (nodes + embeddings) в runtime-state. Без этого
# embeddings/ноды терялись бы на рестарт.
try:
    from src.graph_store import bootstrap as _bootstrap_graph
    _bootstrap_graph()
except Exception as _e:
    print(f"  [graph_store] bootstrap failed: {_e}")

# Start background cognitive loop (Scout, DMN, HRV alerts, NE homeostasis)
get_cognitive_loop().start()

# HRV auto-start: если в profile.context.hrv_autostart=true — запускаем
# симулятор при инициализации процесса (без него HRV-петля не видит юзера
# и sync_error всегда фикция). Закрывает блокер daily-use.
try:
    from src.user_profile import load_profile
    from src.hrv_manager import get_manager as get_hrv_manager
    _prof = load_profile()
    if (_prof.get("context") or {}).get("hrv_autostart"):
        get_hrv_manager().start(mode="simulator")
        print("  [HRV] auto-started (simulator) — profile.context.hrv_autostart=true")
except Exception as _e:
    print(f"  [HRV] auto-start skipped: {_e}")


# ── Roles / Templates ────────────────────────────────────────────────────────
# Живут в data/ (как user-editable JSON). Первый запуск — пишем ship-defaults
# из src/defaults.py, дальше юзер может править. /data/reset удалит файлы,
# следующий старт снова создаст дефолты — это фича (сброс = чистое состояние).

from src.paths import ROLES_FILE as _ROLES_FILE, TEMPLATES_FILE as _TEMPLATES_FILE
from src.defaults import DEFAULT_ROLES, DEFAULT_TEMPLATES


def _ensure_defaults_seed(path: Path, defaults: list):
    """Если файл не существует, пишем ship-defaults. Idempotent."""
    if path.exists():
        return
    try:
        path.write_text(json.dumps(defaults, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except Exception as e:
        print(f"  [defaults] seed {path.name} failed: {e}")


_ensure_defaults_seed(_ROLES_FILE, DEFAULT_ROLES)
_ensure_defaults_seed(_TEMPLATES_FILE, DEFAULT_TEMPLATES)


def _load_roles():
    try:
        return json.loads(_ROLES_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return DEFAULT_ROLES


def _load_templates():
    try:
        return json.loads(_TEMPLATES_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return DEFAULT_TEMPLATES


# ── Common routes ───────────────────────────────────────────────────────────

@app.route("/")
def index():
    s = get_settings()
    model = s.get("api_model") or "(not configured)"
    return render_template("index.html", model=model, page_title="baddle")


@app.route("/lab")
def lab():
    s = get_settings()
    model = s.get("api_model") or "(not configured)"
    return render_template("lab.html", model=model, page_title="baddle lab")


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


# ── Reset user data ─────────────────────────────────────────────────────────

# ── Demo seed ───────────────────────────────────────────────────────────────

@app.route("/demo/reload", methods=["POST"])
def demo_reload():
    """Сносит все user-данные и заливает DEMO. Атомарная операция.

    Требует {"confirm": "DEMO"} в body для защиты от случайного вызова.
    """
    data = request.get_json(silent=True) or {}
    if data.get("confirm") != "DEMO":
        return jsonify({"error": "need {\"confirm\": \"DEMO\"}"}), 400
    try:
        from src.demo import reset_and_seed
        result = reset_and_seed()
        # После wipe+seed — перезагружаем граф в runtime
        try:
            from src.graph_store import bootstrap as _bootstrap_graph
            _bootstrap_graph()
        except Exception as e:
            return jsonify({"ok": True, "result": result,
                            "warning": f"reload failed: {e}"})
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@app.route("/data/reset", methods=["POST"])
def data_reset():
    """Полная очистка runtime данных юзера.

    Требует {"confirm": "RESET"} в body для защиты от случайного вызова.
    Удаляет: user_state, user_profile, goals, activity, checkins, patterns,
    plans, legacy state_graph, all workspace graphs и solved archives.
    НЕ трогает: settings (API config), roles, templates, .git, исходники.
    """
    import shutil
    data = request.get_json(silent=True) or {}
    if data.get("confirm") != "RESET":
        return jsonify({"ok": False, "error": "confirm=RESET required"}), 400
    try:
        from src.paths import get_resettable_files
        removed, failed = [], []
        for p in get_resettable_files():
            try:
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                    removed.append(str(p.name))
                elif p.exists():
                    p.unlink()
                    removed.append(str(p.name))
            except Exception as e:
                failed.append({"path": str(p), "error": str(e)[:100]})
        # Reset runtime in-memory state тоже — иначе юзер увидит старые данные
        # до следующего fetch'а. CognitiveState/UserState singletons держат копии.
        try:
            from src.graph_logic import reset_graph
            reset_graph()
        except Exception:
            pass
        try:
            from src.horizon import set_global_state, CognitiveState
            set_global_state(CognitiveState())
        except Exception:
            pass
        return jsonify({
            "ok": True,
            "removed_count": len(removed),
            "removed": removed[:30],
            "failed": failed,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


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
