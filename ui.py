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
    from flask import Flask, Response, request, render_template_string, stream_with_context, jsonify
except ImportError:
    sys.exit("[error] flask not found.  pip install flask")

sys.path.insert(0, str(Path(__file__).parent))
from main import pick_model, StreamCfg

# These need llama-cpp-python — may be None in server-only mode
try:
    from main import load_model, _batch_generate_iter, _interleaved_generate_iter, _sample, _get_logits, _entropy, format_chat
except ImportError:
    load_model = _batch_generate_iter = _interleaved_generate_iter = _sample = _get_logits = _entropy = format_chat = None

app = Flask(__name__)
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


# ── HTML ───────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>baddle</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    body { background: #0f172a; font-family: 'Courier New', monospace; }
    .stream-content { white-space: pre-wrap; word-break: break-word; min-height: 200px; line-height: 1.6; }
    input[type=text], input[type=number] {
      background: #1e293b; border: 1px solid #334155; color: #e2e8f0;
      padding: 6px 10px; border-radius: 6px; outline: none;
    }
    input:focus { border-color: #38bdf8; }
    .tab-active   { background: #0369a1; color: #fff; }
    .tab-inactive { background: #1e293b; color: #94a3b8; }
    .tab-inactive:hover { background: #273549; color: #e2e8f0; }
    .cfg-box { background: #1e293b; border-radius: 8px; padding: 14px; }
    .scroll-panel { max-height: 380px; overflow-y: auto; }
    .btn-action { padding: 6px 16px; border-radius: 6px; font-size: 0.875rem;
                  color: #fff; transition: background 0.15s; }
    .btn-action:disabled { opacity: 0.35; cursor: not-allowed; }
    .prob-bar { display: inline-block; background: #065f46; height: 10px; border-radius: 2px; }
    #step-output[contenteditable="true"] { outline: 2px solid #0ea5e9; cursor: text; }
    #step-output[contenteditable="true"]::after {
      content: '  Ctrl+Enter to sync';
      color: #475569; font-size: 0.7rem;
    }
    .editing-badge { background: #0ea5e9; color: #0f172a; font-size: 0.65rem;
                     padding: 1px 8px; border-radius: 4px; font-weight: bold; }
    .chat-msg { padding: 10px 14px; border-radius: 8px; margin-bottom: 8px; max-width: 85%; }
    .chat-user { background: #1e3a5f; margin-left: auto; }
    .chat-assistant { background: #1e293b; margin-right: auto; }
    .chat-system { background: #1a1a2e; color: #94a3b8; font-size: 0.75rem; margin: 0 auto 8px; text-align: center; max-width: 100%; }
  </style>
</head>
<body class="text-slate-200 min-h-screen p-6">
<div class="max-w-6xl mx-auto">

  <!-- Header -->
  <div class="flex items-baseline gap-4 mb-6">
    <h1 class="text-2xl font-bold text-sky-400">baddle</h1>
    <span class="text-slate-500 text-sm">{{ model }}</span>
    <span id="token-counter" class="text-xs text-slate-600"></span>
    <span id="batch-tag" class="ml-auto text-xs text-slate-600"></span>
  </div>

  <!-- Mode tabs -->
  <div class="flex gap-2 mb-5">
    <button id="tab-step"     onclick="setMode('step')"
      class="tab-active px-4 py-1.5 rounded text-sm transition-colors">step</button>
    <button id="tab-parallel" onclick="setMode('parallel')"
      class="tab-inactive px-4 py-1.5 rounded text-sm transition-colors">parallel</button>
    <button id="tab-compare"  onclick="setMode('compare')"
      class="tab-inactive px-4 py-1.5 rounded text-sm transition-colors">compare</button>
    <button id="tab-chat"    onclick="setMode('chat')"
      class="tab-inactive px-4 py-1.5 rounded text-sm transition-colors">chat</button>
  </div>

  <!-- ══ STEP mode ══ -->
  <div id="cfg-step">
    <!-- Config row -->
    <div class="flex flex-wrap gap-3 items-center mb-4">
      <span class="text-slate-400 text-sm w-16 shrink-0">Prompt</span>
      <input id="step-prompt" type="text" placeholder="Enter prompt…" style="width:380px">
      <span class="text-slate-400 text-sm">temp</span>
      <input id="step-temp" type="number" value="0.0" step="0.1" min="0" max="2" style="width:70px"
             onchange="stepUpdateParams()" onblur="stepUpdateParams()">
      <span class="text-slate-400 text-sm">top_k</span>
      <input id="step-topk" type="number" value="40" min="1" max="100" style="width:70px"
             onchange="stepUpdateParams()" onblur="stepUpdateParams()">
      <button onclick="stepInit()"
        class="btn-action" style="background:#0369a1"
        onmouseover="this.style.background='#0284c7'" onmouseout="this.style.background='#0369a1'">
        Init
      </button>
    </div>

    <!-- Output + top tokens -->
    <div class="grid gap-4 mb-4" style="grid-template-columns: 2fr 1fr">
      <!-- Generated text -->
      <div class="rounded-lg border border-slate-700 overflow-hidden">
        <div class="bg-slate-900 px-4 py-2 flex items-center border-b border-slate-700">
          <span class="text-slate-400 text-sm font-bold">Generated</span>
          <span id="step-status" class="ml-auto text-slate-500 text-xs"></span>
        </div>
        <div class="bg-slate-800 scroll-panel">
          <div id="step-output" class="stream-content p-4 text-sm text-slate-200"
               contenteditable="false" spellcheck="false"></div>
        </div>
      </div>
      <!-- Top tokens -->
      <div class="rounded-lg border border-slate-700 overflow-hidden">
        <div class="bg-slate-900 px-4 py-2 border-b border-slate-700">
          <span class="text-slate-400 text-sm font-bold">Next token probs</span>
        </div>
        <div class="bg-slate-800 scroll-panel p-3">
          <div id="step-top" class="text-xs text-slate-300 font-mono"></div>
        </div>
      </div>
    </div>

    <!-- Action bar -->
    <div class="flex flex-wrap items-center gap-3">
      <button id="step-btn-next" onclick="stepNext()" disabled
        class="btn-action" style="background:#065f46"
        onmouseover="this.style.background='#047857'" onmouseout="this.style.background='#065f46'">
        Next Token
      </button>

      <div class="flex items-center gap-2">
        <button id="step-btn-auto" onclick="stepAuto()" disabled
          class="btn-action" style="background:#3730a3"
          onmouseover="this.style.background='#4338ca'" onmouseout="this.style.background='#3730a3'">
          Auto
        </button>
        <input id="step-auto-n" type="number" value="20" min="1" max="500" style="width:60px">
        <span class="text-slate-500 text-xs">tokens</span>
      </div>

      <button id="step-btn-edit" onclick="stepToggleEdit()" disabled
        class="btn-action" style="background:#0c4a6e"
        onmouseover="this.style.background='#075985'" onmouseout="this.style.background='#0c4a6e'">
        Edit
      </button>

      <button id="step-btn-sync" onclick="stepSync()" style="display:none"
        class="btn-action" style="background:#0369a1"
        onmouseover="this.style.background='#0284c7'" onmouseout="this.style.background='#0369a1'">
        Sync (Ctrl+Enter)
      </button>

      <button id="step-btn-reset" onclick="stepReset()" disabled
        class="btn-action" style="background:#7f1d1d"
        onmouseover="this.style.background='#991b1b'" onmouseout="this.style.background='#7f1d1d'">
        Reset
      </button>

      <button id="step-btn-stop" onclick="stepStopAuto()" style="display:none"
        class="btn-action" style="background:#b91c1c"
        onmouseover="this.style.background='#dc2626'" onmouseout="this.style.background='#b91c1c'">
        Stop Auto
      </button>
    </div>
  </div>

  <!-- ══ PARALLEL mode ══ -->
  <div id="cfg-parallel" class="hidden">
    <div class="grid grid-cols-1 gap-3 mb-4 max-w-2xl">
      <div class="flex gap-3 items-center">
        <span class="text-sky-400 text-sm w-20 shrink-0">Prompt A</span>
        <input id="pa" type="text" placeholder="First prompt…" style="flex:1">
      </div>
      <div class="flex gap-3 items-center">
        <span class="text-sky-400 text-sm w-20 shrink-0">temp A</span>
        <input id="temp-pa" type="number" value="0.7" step="0.1" min="0" max="2" style="width:80px">
      </div>
      <div class="flex gap-3 items-center">
        <span class="text-purple-400 text-sm w-20 shrink-0">Prompt B</span>
        <input id="pb" type="text" placeholder="Second prompt…" style="flex:1">
      </div>
      <div class="flex gap-3 items-center">
        <span class="text-purple-400 text-sm w-20 shrink-0">temp B</span>
        <input id="temp-pb" type="number" value="0.7" step="0.1" min="0" max="2" style="width:80px">
      </div>
      <div class="flex gap-6 items-center mt-1">
        <label class="text-slate-400 text-sm flex items-center gap-2">
          max tokens
          <input id="n-p" type="number" value="50" min="1" max="500" style="width:70px">
        </label>
        <label class="text-slate-400 text-sm flex items-center gap-2">
          seed
          <input id="seed-p" type="number" value="-1" min="-1" style="width:80px">
        </label>
      </div>
    </div>
  </div>

  <!-- ══ COMPARE mode ══ -->
  <div id="cfg-compare" class="hidden">
    <div class="flex flex-col gap-3 mb-4 max-w-2xl">
      <div class="flex gap-3 items-center">
        <span class="text-slate-400 text-sm w-20 shrink-0">Prompt</span>
        <input id="pc" type="text" placeholder="Shared prompt…" style="flex:1">
      </div>
      <div class="grid grid-cols-2 gap-4">
        <div class="cfg-box border border-sky-900">
          <div class="text-sky-400 text-sm font-bold mb-3">Config A</div>
          <label class="text-slate-400 text-sm flex items-center gap-2 mb-2">
            temperature
            <input id="temp-a" type="number" value="0.0" step="0.1" min="0" max="2" style="width:70px">
          </label>
          <label class="text-slate-400 text-sm flex items-center gap-2">
            top_k
            <input id="topk-a" type="number" value="1" min="1" max="100" style="width:70px">
          </label>
        </div>
        <div class="cfg-box border border-purple-900">
          <div class="text-purple-400 text-sm font-bold mb-3">Config B</div>
          <label class="text-slate-400 text-sm flex items-center gap-2 mb-2">
            temperature
            <input id="temp-b" type="number" value="1.0" step="0.1" min="0" max="2" style="width:70px">
          </label>
          <label class="text-slate-400 text-sm flex items-center gap-2">
            top_k
            <input id="topk-b" type="number" value="40" min="1" max="100" style="width:70px">
          </label>
        </div>
      </div>
      <div class="flex gap-6 items-center">
        <label class="text-slate-400 text-sm flex items-center gap-2">
          max tokens
          <input id="n-c" type="number" value="60" min="1" max="500" style="width:70px">
        </label>
        <label class="text-slate-400 text-sm flex items-center gap-2">
          seed
          <input id="seed-c" type="number" value="-1" min="-1" style="width:80px">
        </label>
      </div>
    </div>
  </div>

  <!-- Generate button (parallel / compare only) -->
  <div id="dual-controls" class="hidden flex items-center gap-4 mb-5">
    <button id="btn-gen" onclick="generate()"
      class="px-6 py-2 bg-sky-600 hover:bg-sky-500 text-white rounded text-sm transition-colors">
      Generate
    </button>
    <button id="btn-stop" onclick="stopDual()" style="display:none"
      class="px-6 py-2 bg-red-700 hover:bg-red-600 text-white rounded text-sm transition-colors">
      Stop
    </button>
    <span id="status" class="text-slate-500 text-sm"></span>
  </div>

  <!-- Stream panels (parallel / compare) -->
  <div id="dual-panels" class="hidden grid grid-cols-2 gap-4">
    <div class="rounded-lg overflow-hidden border border-sky-800">
      <div class="bg-slate-900 px-4 py-2 flex items-center gap-2 border-b border-sky-900">
        <span id="title-a" class="text-sky-400 text-sm font-bold">Stream A</span>
        <span id="step-a" class="text-slate-500 text-xs ml-auto"></span>
        <button id="btn-to-step-a" onclick="dualToStep('a')" style="display:none"
          class="text-xs px-2 py-0.5 bg-sky-700 hover:bg-sky-600 text-white rounded transition-colors">→ Step</button>
      </div>
      <div class="bg-slate-800 scroll-panel">
        <div id="output-a" class="stream-content p-4 text-sm text-slate-200"></div>
      </div>
    </div>
    <div class="rounded-lg overflow-hidden border border-purple-800">
      <div class="bg-slate-900 px-4 py-2 flex items-center gap-2 border-b border-purple-900">
        <span id="title-b" class="text-purple-400 text-sm font-bold">Stream B</span>
        <span id="step-b" class="text-slate-500 text-xs"></span>
        <button id="btn-to-step-b" onclick="dualToStep('b')" style="display:none"
          class="text-xs px-2 py-0.5 bg-purple-700 hover:bg-purple-600 text-white rounded transition-colors">→ Step</button>
        <span id="diverge-badge"
          class="hidden ml-auto text-xs bg-amber-500 text-slate-900 px-2 py-0.5 rounded-full">
        </span>
      </div>
      <div class="bg-slate-800 scroll-panel">
        <div id="output-b" class="stream-content p-4 text-sm text-slate-200"></div>
      </div>
    </div>
  </div>

  <!-- ══ CHAT mode ══ -->
  <div id="cfg-chat" class="hidden">
    <div class="flex flex-wrap gap-3 items-center mb-4">
      <span class="text-slate-400 text-sm">Role</span>
      <select id="role-select" onchange="onRoleSelect()"
        style="background:#1e293b; border:1px solid #334155; color:#e2e8f0; padding:6px 10px; border-radius:6px; min-width:140px; font-size:0.875rem;">
      </select>
      <input id="role-text" type="text" placeholder="System prompt…"
        style="flex:1; min-width:200px; max-width:400px; font-size:0.875rem;">
      <span class="text-slate-400 text-sm ml-2">temp</span>
      <input id="chat-temp" type="number" value="0.7" step="0.1" min="0" max="2" style="width:70px">
      <span class="text-slate-400 text-sm">max</span>
      <input id="chat-max" type="number" value="200" min="1" max="2000" style="width:80px">
      <button onclick="chatReset()"
        class="btn-action ml-auto" style="background:#7f1d1d"
        onmouseover="this.style.background='#991b1b'" onmouseout="this.style.background='#7f1d1d'">
        Reset
      </button>
    </div>
    <div id="chat-messages" class="rounded-lg border border-slate-700 bg-slate-800 p-4 mb-4"
         style="min-height:300px; max-height:500px; overflow-y:auto; display:flex; flex-direction:column;">
    </div>
    <div class="flex gap-3">
      <input id="chat-input" type="text" placeholder="Type a message…"
        style="flex:1" onkeydown="if(event.key==='Enter' && !event.shiftKey){event.preventDefault(); chatSend();}">
      <button id="chat-btn-send" onclick="chatSend()"
        class="px-6 py-2 bg-sky-600 hover:bg-sky-500 text-white rounded text-sm transition-colors">
        Send
      </button>
      <button id="chat-btn-continue" onclick="chatContinue()" style="display:none"
        class="px-6 py-2 bg-amber-600 hover:bg-amber-500 text-white rounded text-sm transition-colors">
        Continue
      </button>
      <button id="chat-btn-stop" onclick="chatStop()" style="display:none"
        class="px-6 py-2 bg-red-700 hover:bg-red-600 text-white rounded text-sm transition-colors">
        Stop
      </button>
    </div>
  </div>

</div>
<script>
  let mode = 'step';
  let dualEs = null;
  let roles = [];
  let modelCtx = 0;

  fetch('/model/info').then(r => r.json()).then(d => { modelCtx = d.n_ctx; });

  function updateTokenCounter(used) {
    const el = document.getElementById('token-counter');
    if (!modelCtx || !used) { el.textContent = ''; return; }
    const pct = Math.round(used / modelCtx * 100);
    el.textContent = `${used} / ${modelCtx} tokens (${pct}%)`;
    el.style.color = pct > 90 ? '#ef4444' : pct > 70 ? '#f59e0b' : '';
  }

  // ── Roles ───────────────────────────────────────────────────────────────────
  fetch('/roles').then(r => r.json()).then(data => {
    roles = data;
    const sel = document.getElementById('role-select');
    data.forEach((p, i) => {
      const opt = document.createElement('option');
      opt.value = i;
      opt.textContent = p.name;
      sel.appendChild(opt);
    });
    // Add "Custom" option at the end
    const custom = document.createElement('option');
    custom.value = 'custom';
    custom.textContent = '(custom)';
    sel.appendChild(custom);
  });

  function onRoleSelect() {
    const sel = document.getElementById('role-select');
    const inp = document.getElementById('role-text');
    if (sel.value === 'custom') {
      inp.value = '';
      inp.focus();
    } else {
      inp.value = roles[parseInt(sel.value)].text;
    }
  }

  function getRole() {
    if (mode !== 'chat') return '';
    return document.getElementById('role-text').value;
  }

  // ── Heatmap rendering ──────────────────────────────────────────────────────
  function renderHeatmap(elOrId, toks, ents, promptText) {
    const el = typeof elOrId === 'string' ? document.getElementById(elOrId) : elOrId;
    el.innerHTML = '';
    // Show prompt as plain text before colored generated tokens
    if (promptText) {
      const pre = document.createElement('span');
      pre.textContent = promptText;
      pre.style.color = '#94a3b8';
      el.appendChild(pre);
    }
    // Normalize entropy: find max for color scaling
    const maxEnt = Math.max(...ents, 0.01);
    for (let i = 0; i < toks.length; i++) {
      const span = document.createElement('span');
      span.textContent = toks[i];
      const ratio = (ents[i] || 0) / maxEnt; // 0=confident, 1=uncertain
      // Green (confident) → Yellow → Red (uncertain)
      const r = Math.round(ratio < 0.5 ? ratio * 2 * 255 : 255);
      const g = Math.round(ratio < 0.5 ? 255 : (1 - ratio) * 2 * 255);
      span.style.color = `rgb(${r},${g},80)`;
      span.title = `entropy: ${(ents[i] || 0).toFixed(2)}`;
      el.appendChild(span);
    }
  }

  // ── Tab switching ──────────────────────────────────────────────────────────
  function setMode(m) {
    mode = m;
    ['step','parallel','compare','chat'].forEach(t => {
      document.getElementById('cfg-' + t).classList.toggle('hidden', t !== m);
      document.getElementById('tab-' + t).className =
        (t === m ? 'tab-active' : 'tab-inactive') + ' px-4 py-1.5 rounded text-sm transition-colors';
    });
    const isDual = m === 'parallel' || m === 'compare';
    document.getElementById('dual-controls').classList.toggle('hidden', !isDual);
    document.getElementById('dual-panels').classList.toggle('hidden', !isDual);
    // Clear dual panels and stop stream on tab switch
    if (dualEs) { dualEs.close(); dualEs = null; }
    ['output-a','output-b'].forEach(id => { document.getElementById(id).textContent = ''; document.getElementById(id).innerHTML = ''; });
    ['step-a','step-b'].forEach(id => document.getElementById(id).textContent = '');
    document.getElementById('diverge-badge').classList.add('hidden');
    document.getElementById('status').textContent = '';
    document.getElementById('btn-gen').style.display = '';
    document.getElementById('btn-stop').style.display = 'none';
    updateTokenCounter(0);
  }

  // ── Step mode ──────────────────────────────────────────────────────────────
  let stepAutoEs = null;

  function renderTop(tokens) {
    const el = document.getElementById('step-top');
    if (!tokens || !tokens.length) { el.innerHTML = '<span class="text-slate-600">—</span>'; return; }
    el.innerHTML = tokens.map((t, i) => {
      const bar  = Math.round(t.prob * 28);
      const pct  = (t.prob * 100).toFixed(1).padStart(5);
      const txt  = JSON.stringify(t.text);
      return `<div class="flex items-center gap-1 mb-1">
        <span class="text-slate-600 w-4 text-right shrink-0">${i+1}</span>
        <span class="text-sky-300 w-28 truncate shrink-0">${txt}</span>
        <span class="text-slate-400 w-12 text-right shrink-0">${pct}%</span>
        <div class="prob-bar ml-1" style="width:${bar*4}px"></div>
      </div>`;
    }).join('');
  }

  let stepEditing = false;
  let stepRoleLen = 0;  // length of role text to hide from display

  function setStepButtons(enabled) {
    ['step-btn-next','step-btn-auto','step-btn-reset','step-btn-edit'].forEach(id => {
      document.getElementById(id).disabled = !enabled;
    });
  }

  function stepToggleEdit() {
    const el = document.getElementById('step-output');
    stepEditing = !stepEditing;
    el.contentEditable = stepEditing ? 'true' : 'false';
    document.getElementById('step-btn-sync').style.display = stepEditing ? '' : 'none';
    document.getElementById('step-btn-edit').textContent = stepEditing ? 'Cancel Edit' : 'Edit';
    // Disable other buttons while editing
    ['step-btn-next','step-btn-auto','step-btn-reset'].forEach(id => {
      document.getElementById(id).disabled = stepEditing;
    });
    if (stepEditing) {
      el.focus();
      // Place cursor at end
      const range = document.createRange();
      range.selectNodeContents(el);
      range.collapse(false);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    }
  }

  async function stepSync() {
    const el = document.getElementById('step-output');
    const visibleText = el.textContent;
    const fullText = getRole() + visibleText;
    document.getElementById('step-status').textContent = 'Syncing…';
    const r = await fetch('/step/edit', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text: fullText})
    });
    const d = await r.json();
    if (d.error) { document.getElementById('step-status').textContent = 'Error: ' + d.error; return; }
    el.textContent = d.full_text.slice(stepRoleLen);
    renderTop(d.top_tokens);
    // Exit edit mode
    stepEditing = false;
    el.contentEditable = 'false';
    document.getElementById('step-btn-sync').style.display = 'none';
    document.getElementById('step-btn-edit').textContent = 'Edit';
    setStepButtons(true);
    const info = d.action === 'cut' ? `Cut to ${d.token_count} tokens`
               : d.action === 'inject' ? `Injected ${d.injected_tokens} tokens`
               : d.action === 're-eval' ? `Re-eval ${d.token_count} tokens`
               : 'No change';
    document.getElementById('step-status').textContent = info;
  }

  async function stepInit() {
    const raw    = document.getElementById('step-prompt').value.trim();
    const temp   = parseFloat(document.getElementById('step-temp').value) || 0;
    if (!raw) return;
    const pfx    = getRole();
    const prompt = pfx + raw;
    stepRoleLen = pfx.length;
    setStepButtons(false);
    document.getElementById('step-status').textContent = 'Initializing…';
    const r = await fetch('/step/init', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({prompt, temp, top_k: parseInt(document.getElementById('step-topk').value) || 40})
    });
    const d = await r.json();
    if (d.error) { document.getElementById('step-status').textContent = 'Error: ' + d.error; return; }
    document.getElementById('step-output').textContent = d.text.slice(stepRoleLen);
    renderTop(d.top_tokens);
    document.getElementById('step-status').textContent = 'Ready  (' + d.token_count + ' prompt tokens)';
    updateTokenCounter(d.token_count);
    setStepButtons(true);
  }

  async function stepNext() {
    const r = await fetch('/step/next', {method: 'POST'});
    const d = await r.json();
    if (d.error) { document.getElementById('step-status').textContent = 'Error: ' + d.error; return; }
    if (d.tok_texts && d.ents && d.tok_texts.length > 0) {
      const promptText = d.full_text.slice(stepRoleLen, d.full_text.length - d.tok_texts.join('').length);
      renderHeatmap('step-output', d.tok_texts, d.ents, promptText);
    } else {
      document.getElementById('step-output').textContent = d.full_text.slice(stepRoleLen);
    }
    renderTop(d.top_tokens);
    document.getElementById('step-status').textContent = d.is_eos ? 'EOS' : 'step ' + d.step;
    updateTokenCounter(d.total_tokens);
    if (d.is_eos) setStepButtons(false);
  }

  function stepAuto() {
    if (stepAutoEs) return;
    const n = parseInt(document.getElementById('step-auto-n').value) || 20;
    setStepButtons(false);
    document.getElementById('step-btn-stop').style.display = '';
    stepAutoEs = new EventSource('/step/auto?n=' + n);
    stepAutoEs.onmessage = function(e) {
      const d = JSON.parse(e.data);
      if (d.error) {
        document.getElementById('step-status').textContent = 'Error: ' + d.error;
        stepStopAuto(false); return;
      }
      if (d.done) { stepStopAuto(true); return; }
      if (d.tok_texts && d.ents && d.tok_texts.length > 0) {
        const promptText = d.full_text.slice(stepRoleLen, d.full_text.length - d.tok_texts.join('').length);
        renderHeatmap('step-output', d.tok_texts, d.ents, promptText);
      } else {
        document.getElementById('step-output').textContent = d.full_text.slice(stepRoleLen);
      }
      document.getElementById('step-status').textContent = 'step ' + d.step;
      updateTokenCounter(d.total_tokens);
      if (d.top_tokens) renderTop(d.top_tokens);
      if (d.eos) { stepStopAuto(false); document.getElementById('step-status').textContent = 'EOS'; }
    };
    stepAutoEs.onerror = function() { stepStopAuto(true); };
  }

  function stepUpdateParams() {
    if (!document.getElementById('step-output').textContent) return;
    fetch('/step/temp', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        temp: parseFloat(document.getElementById('step-temp').value) || 0,
        top_k: parseInt(document.getElementById('step-topk').value) || 40,
      })
    });
  }

  function stepStopAuto(restoreButtons) {
    if (stepAutoEs) { stepAutoEs.close(); stepAutoEs = null; }
    document.getElementById('step-btn-stop').style.display = 'none';
    if (restoreButtons !== false) setStepButtons(true);
  }

  async function stepReset() {
    const r = await fetch('/step/reset', {method: 'POST'});
    const d = await r.json();
    if (d.error) { document.getElementById('step-status').textContent = 'Error: ' + d.error; return; }
    document.getElementById('step-output').textContent = d.full_text.slice(stepRoleLen);
    renderTop(d.top_tokens);
    document.getElementById('step-status').textContent = 'Reset';
    setStepButtons(true);
  }

  // Ctrl+Enter → Sync if editing, Next Token otherwise
  document.addEventListener('keydown', e => {
    if (e.ctrlKey && e.key === 'Enter') {
      e.preventDefault();
      if (mode === 'step' && stepEditing) stepSync();
      else if (mode === 'step') stepNext();
      else generate();
    }
  });

  // ── Parallel / Compare ─────────────────────────────────────────────────────
  let dualIsServer = false;

  function hideToStepBtns() {
    document.getElementById('btn-to-step-a').style.display = 'none';
    document.getElementById('btn-to-step-b').style.display = 'none';
  }

  function stopDual() {
    if (dualEs) { dualEs.close(); dualEs = null; }
    document.getElementById('btn-gen').style.display  = '';
    document.getElementById('btn-stop').style.display = 'none';
  }

  function generate() {
    if (dualEs) { dualEs.close(); dualEs = null; }
    dualIsServer = false;
    hideToStepBtns();
    ['output-a','output-b'].forEach(id => { document.getElementById(id).textContent = ''; document.getElementById(id).innerHTML = ''; });
    ['step-a','step-b'].forEach(id => document.getElementById(id).textContent = '');
    document.getElementById('diverge-badge').classList.add('hidden');
    document.getElementById('batch-tag').textContent = '';
    document.getElementById('status').textContent = 'Generating…';
    document.getElementById('btn-gen').style.display  = 'none';
    document.getElementById('btn-stop').style.display = '';

    const params = new URLSearchParams({ mode });
    const pfx = getRole();
    let promptA, promptB;

    if (mode === 'parallel') {
      promptA = pfx + document.getElementById('pa').value;
      promptB = pfx + document.getElementById('pb').value;
      const ta = document.getElementById('temp-pa').value;
      const tb = document.getElementById('temp-pb').value;
      params.set('pa', promptA); params.set('pb', promptB);
      params.set('n',  document.getElementById('n-p').value);
      params.set('temp_a', ta); params.set('temp_b', tb);
      params.set('top_k_a', 40); params.set('top_k_b', 40);
      params.set('seed', document.getElementById('seed-p').value);
      document.getElementById('title-a').textContent = 'Stream A';
      document.getElementById('title-b').textContent = 'Stream B';
    } else {
      promptA = promptB = pfx + document.getElementById('pc').value;
      const ta = document.getElementById('temp-a').value;
      const tb = document.getElementById('temp-b').value;
      const ka = document.getElementById('topk-a').value;
      const kb = document.getElementById('topk-b').value;
      params.set('pa', promptA); params.set('pb', promptA);
      params.set('n',  document.getElementById('n-c').value);
      params.set('temp_a', ta); params.set('temp_b', tb);
      params.set('top_k_a', ka); params.set('top_k_b', kb);
      params.set('seed', document.getElementById('seed-c').value);
      document.getElementById('title-a').textContent = `temp=${ta}  top_k=${ka}`;
      document.getElementById('title-b').textContent = `temp=${tb}  top_k=${kb}`;
    }

    let diverged = false;
    dualEs = new EventSource('/stream?' + params.toString());

    dualEs.onmessage = function(e) {
      const d = JSON.parse(e.data);
      if (d.error) {
        document.getElementById('status').textContent = 'Error: ' + d.error;
        stopDual(); return;
      }
      if (d.mode_tag) {
        document.getElementById('batch-tag').textContent = d.mode_tag;
        dualIsServer = d.mode_tag.includes('llama-server');
        return;
      }
      if (d.done) {
        document.getElementById('status').textContent = 'Done.';
        stopDual();
        if (!dualIsServer) {
          document.getElementById('btn-to-step-a').style.display = '';
          document.getElementById('btn-to-step-b').style.display = '';
        }
        return;
      }
      if (d.toks_a && d.ents_a) {
        // Show prompt text (from full text minus generated tokens) before colored tokens
        const genTextA = d.toks_a.join('');
        const genTextB = d.toks_b.join('');
        const promptA_text = d.a.endsWith(genTextA) ? d.a.slice(0, d.a.length - genTextA.length) : '';
        const promptB_text = d.b.endsWith(genTextB) ? d.b.slice(0, d.b.length - genTextB.length) : '';
        renderHeatmap('output-a', d.toks_a, d.ents_a, promptA_text);
        renderHeatmap('output-b', d.toks_b, d.ents_b, promptB_text);
      } else {
        document.getElementById('output-a').textContent = d.a;
        document.getElementById('output-b').textContent = d.b;
      }
      document.getElementById('step-a').textContent = d.done_a ? 'EOS' : 'step ' + d.step;
      document.getElementById('step-b').textContent = d.done_b ? 'EOS' : 'step ' + d.step;
      if (d.total_tokens) updateTokenCounter(d.total_tokens);

      if (mode === 'compare' && !diverged) {
        const ga = d.a.slice(promptA.length);
        const gb = d.b.slice(promptB.length);
        if (ga !== gb && (ga || gb)) {
          diverged = true;
          const badge = document.getElementById('diverge-badge');
          badge.textContent = 'diverged @ step ' + d.step;
          badge.classList.remove('hidden');
        }
      }
    };
    dualEs.onerror = function() {
      document.getElementById('status').textContent = 'Stream ended.';
      stopDual();
    };
  }

  async function dualToStep(stream) {
    const temp = parseFloat(document.getElementById('step-temp').value) || 0.0;
    const top_k = parseInt(document.getElementById('step-topk').value) || 40;
    document.getElementById('status').textContent = 'Switching to step mode…';
    const r = await fetch('/dual/to-step', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({stream, temp, top_k})
    });
    const d = await r.json();
    if (d.error) {
      document.getElementById('status').textContent = 'Error: ' + d.error;
      return;
    }
    hideToStepBtns();
    setMode('step');
    document.getElementById('step-output').textContent = d.text;
    stepRoleLen = 0;
    if (d.top) renderTop(d.top);
    if (d.total_tokens) updateTokenCounter(d.total_tokens);
    document.getElementById('status').textContent = 'Step mode (from stream ' + stream.toUpperCase() + ')';
    ['step-btn-next','step-btn-auto','step-btn-reset','step-btn-edit'].forEach(id => {
      document.getElementById(id).disabled = false;
    });
  }

  // ── Chat mode ────────────────────────────────────────────────────────────────
  let chatEs = null;
  let chatLastMsgDiv = null;
  let chatLastLabel = null;

  function chatAddMsg(role, content) {
    const container = document.getElementById('chat-messages');
    const div = document.createElement('div');
    div.className = 'chat-msg text-sm ' + (role === 'user' ? 'chat-user' : role === 'system' ? 'chat-system' : 'chat-assistant');
    div.style.whiteSpace = 'pre-wrap';
    div.style.wordBreak = 'break-word';
    div.textContent = content;
    if (role !== 'system') {
      const label = document.createElement('div');
      label.className = 'text-xs mb-1 ' + (role === 'user' ? 'text-sky-400' : 'text-emerald-400');
      label.textContent = role;
      div.prepend(label);
    }
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    return div;
  }

  function chatShowButtons(state) {
    document.getElementById('chat-btn-send').style.display = state === 'send' || state === 'continue' ? '' : 'none';
    document.getElementById('chat-btn-continue').style.display = state === 'continue' ? '' : 'none';
    document.getElementById('chat-btn-stop').style.display = state === 'streaming' ? '' : 'none';
  }

  function chatStreamTo(url, msgDiv) {
    chatLastMsgDiv = msgDiv;
    chatLastLabel = msgDiv.querySelector('.text-xs');
    chatShowButtons('streaming');

    chatEs = new EventSource(url);
    chatEs.onmessage = function(e) {
      const d = JSON.parse(e.data);
      if (d.error) {
        msgDiv.textContent = 'Error: ' + d.error;
        chatDone('eos'); return;
      }
      if (d.toks && d.ents && d.toks.length > 0) {
        const newText = d.toks.join('');
        const prevText = d.text.endsWith(newText) ? d.text.slice(0, d.text.length - newText.length) : '';
        renderHeatmap(msgDiv, d.toks, d.ents, prevText);
        if (chatLastLabel) msgDiv.prepend(chatLastLabel);
      } else if (chatLastLabel) {
        msgDiv.textContent = d.text;
        msgDiv.prepend(chatLastLabel);
      } else {
        msgDiv.textContent = d.text;
      }
      document.getElementById('chat-messages').scrollTop = document.getElementById('chat-messages').scrollHeight;
      if (d.total_tokens) updateTokenCounter(d.total_tokens);
      if (d.done) chatDone(d.reason || 'eos');
    };
    chatEs.onerror = function() { chatDone('eos'); };
  }

  function chatDone(reason) {
    if (chatEs) { chatEs.close(); chatEs = null; }
    chatShowButtons(reason === 'limit' ? 'continue' : 'send');
  }

  async function chatSend() {
    const input = document.getElementById('chat-input');
    const text = input.value.trim();
    if (!text) return;
    input.value = '';

    chatAddMsg('user', text);

    const temp = parseFloat(document.getElementById('chat-temp').value) || 0.7;
    const system = getRole();

    chatShowButtons('streaming');

    const r = await fetch('/chat/send', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text, system, temp})
    });
    const d = await r.json();
    if (d.error) {
      chatAddMsg('system', 'Error: ' + d.error);
      chatShowButtons('send');
      return;
    }

    const maxN = parseInt(document.getElementById('chat-max').value) || 200;
    const msgDiv = chatAddMsg('assistant', '');
    chatStreamTo('/chat/stream?n=' + maxN, msgDiv);
  }

  function chatContinue() {
    if (!chatLastMsgDiv) return;
    const maxN = parseInt(document.getElementById('chat-max').value) || 200;
    chatStreamTo('/chat/continue?n=' + maxN, chatLastMsgDiv);
  }

  function chatStop() {
    chatDone('eos');
  }

  async function chatReset() {
    chatDone('eos');
    chatLastMsgDiv = null;
    chatLastLabel = null;
    await fetch('/chat/reset', {method: 'POST'});
    document.getElementById('chat-messages').innerHTML = '';
  }

  // Init UI state
  setMode('step');
</script>
</body>
</html>"""


# ── SSE endpoint (parallel / compare) ─────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(HTML, model=model_name)


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
            import traceback; traceback.print_exc()
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
        llm        = load_model(model_path, gpu_layers, args.ctx)

    url = f"http://localhost:{args.port}"
    print(f"\n  Open: {url}\n")
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host="0.0.0.0", port=args.port, threaded=False)


if __name__ == "__main__":
    main()
