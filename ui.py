#!/usr/bin/env python3
"""baddle — web UI  (python ui.py)"""

import sys
import json
import argparse
import threading
import webbrowser
from pathlib import Path

try:
    from flask import Flask, Response, request, render_template_string, stream_with_context
except ImportError:
    sys.exit("[error] flask not found.  pip install flask")

# Import shared logic from main.py
sys.path.insert(0, str(Path(__file__).parent))
from main import (
    pick_model, load_model, StreamCfg,
    _batch_generate_iter, _interleaved_generate_iter,
)

app = Flask(__name__)
llm        = None
model_name = ""

# ── HTML ──────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>baddle</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    body { background: #0f172a; font-family: 'Courier New', monospace; }
    .stream-content { white-space: pre-wrap; word-break: break-word; min-height: 220px; line-height: 1.6; }
    input[type=text], input[type=number] {
      background: #1e293b; border: 1px solid #334155; color: #e2e8f0;
      padding: 6px 10px; border-radius: 6px; width: 100%; outline: none;
    }
    input:focus { border-color: #38bdf8; }
    .tab-active   { background: #0369a1; color: #fff; }
    .tab-inactive { background: #1e293b; color: #94a3b8; }
    .tab-inactive:hover { background: #273549; color: #e2e8f0; }
    .cfg-box { background: #1e293b; border-radius: 8px; padding: 14px; }
    .scroll-panel { max-height: 420px; overflow-y: auto; }
  </style>
</head>
<body class="text-slate-200 min-h-screen p-6">
<div class="max-w-6xl mx-auto">

  <!-- Header -->
  <div class="flex items-baseline gap-4 mb-6">
    <h1 class="text-2xl font-bold text-sky-400">baddle</h1>
    <span class="text-slate-500 text-sm">{{ model }}</span>
    <span id="batch-tag" class="ml-auto text-xs text-slate-600"></span>
  </div>

  <!-- Mode tabs -->
  <div class="flex gap-2 mb-5">
    <button id="tab-parallel" onclick="setMode('parallel')"
      class="tab-active px-4 py-1.5 rounded text-sm transition-colors">parallel</button>
    <button id="tab-compare" onclick="setMode('compare')"
      class="tab-inactive px-4 py-1.5 rounded text-sm transition-colors">compare</button>
  </div>

  <!-- ── Parallel config ── -->
  <div id="cfg-parallel">
    <div class="grid grid-cols-1 gap-3 mb-4 max-w-2xl">
      <div class="flex gap-3 items-center">
        <span class="text-sky-400 text-sm w-20 shrink-0">Prompt A</span>
        <input id="pa" type="text" placeholder="First prompt…">
      </div>
      <div class="flex gap-3 items-center">
        <span class="text-purple-400 text-sm w-20 shrink-0">Prompt B</span>
        <input id="pb" type="text" placeholder="Second prompt…">
      </div>
      <div class="flex gap-6 items-center mt-1">
        <label class="text-slate-400 text-sm flex items-center gap-2">
          temperature
          <input id="temp-p" type="number" value="0.7" step="0.1" min="0" max="2" style="width:70px">
        </label>
        <label class="text-slate-400 text-sm flex items-center gap-2">
          max tokens
          <input id="n-p" type="number" value="50" min="1" max="500" style="width:70px">
        </label>
      </div>
    </div>
  </div>

  <!-- ── Compare config ── -->
  <div id="cfg-compare" class="hidden">
    <div class="flex flex-col gap-3 mb-4 max-w-2xl">
      <div class="flex gap-3 items-center">
        <span class="text-slate-400 text-sm w-20 shrink-0">Prompt</span>
        <input id="pc" type="text" placeholder="Shared prompt…">
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
      <label class="text-slate-400 text-sm flex items-center gap-2">
        max tokens
        <input id="n-c" type="number" value="60" min="1" max="500" style="width:70px">
      </label>
    </div>
  </div>

  <!-- Generate button + status -->
  <div class="flex items-center gap-4 mb-5">
    <button id="btn-gen" onclick="generate()"
      class="px-6 py-2 bg-sky-600 hover:bg-sky-500 text-white rounded text-sm transition-colors">
      Generate
    </button>
    <button id="btn-stop" onclick="stop()" style="display:none"
      class="px-6 py-2 bg-red-700 hover:bg-red-600 text-white rounded text-sm transition-colors">
      Stop
    </button>
    <span id="status" class="text-slate-500 text-sm"></span>
  </div>

  <!-- Stream panels -->
  <div class="grid grid-cols-2 gap-4">
    <!-- Panel A -->
    <div class="rounded-lg overflow-hidden border border-sky-800">
      <div class="bg-slate-900 px-4 py-2 flex items-center gap-2 border-b border-sky-900">
        <span id="title-a" class="text-sky-400 text-sm font-bold">Stream A</span>
        <span id="step-a" class="text-slate-500 text-xs ml-auto"></span>
      </div>
      <div class="bg-slate-800 scroll-panel">
        <div id="output-a" class="stream-content p-4 text-sm text-slate-200"></div>
      </div>
    </div>
    <!-- Panel B -->
    <div class="rounded-lg overflow-hidden border border-purple-800">
      <div class="bg-slate-900 px-4 py-2 flex items-center gap-2 border-b border-purple-900">
        <span id="title-b" class="text-purple-400 text-sm font-bold">Stream B</span>
        <span id="step-b" class="text-slate-500 text-xs"></span>
        <span id="diverge-badge"
          class="hidden ml-auto text-xs bg-amber-500 text-slate-900 px-2 py-0.5 rounded-full">
        </span>
      </div>
      <div class="bg-slate-800 scroll-panel">
        <div id="output-b" class="stream-content p-4 text-sm text-slate-200"></div>
      </div>
    </div>
  </div>

</div>
<script>
  let mode = 'parallel';
  let es   = null;

  function setMode(m) {
    mode = m;
    document.getElementById('cfg-parallel').classList.toggle('hidden', m !== 'parallel');
    document.getElementById('cfg-compare').classList.toggle('hidden',  m !== 'compare');
    document.getElementById('tab-parallel').className =
      (m === 'parallel' ? 'tab-active' : 'tab-inactive') + ' px-4 py-1.5 rounded text-sm transition-colors';
    document.getElementById('tab-compare').className =
      (m === 'compare'  ? 'tab-active' : 'tab-inactive') + ' px-4 py-1.5 rounded text-sm transition-colors';
  }

  function stop() {
    if (es) { es.close(); es = null; }
    document.getElementById('status').textContent = 'Stopped.';
    document.getElementById('btn-gen').style.display  = '';
    document.getElementById('btn-stop').style.display = 'none';
  }

  function generate() {
    if (es) { es.close(); es = null; }

    ['output-a','output-b'].forEach(id => document.getElementById(id).textContent = '');
    ['step-a','step-b'].forEach(id => document.getElementById(id).textContent = '');
    document.getElementById('diverge-badge').classList.add('hidden');
    document.getElementById('batch-tag').textContent = '';
    document.getElementById('status').textContent = 'Generating…';
    document.getElementById('btn-gen').style.display  = 'none';
    document.getElementById('btn-stop').style.display = '';

    const params = new URLSearchParams({ mode });
    let promptA, promptB;

    if (mode === 'parallel') {
      promptA = document.getElementById('pa').value;
      promptB = document.getElementById('pb').value;
      const temp = document.getElementById('temp-p').value;
      params.set('pa', promptA); params.set('pb', promptB);
      params.set('n',  document.getElementById('n-p').value);
      params.set('temp_a', temp); params.set('temp_b', temp);
      params.set('top_k_a', 40);  params.set('top_k_b', 40);
      document.getElementById('title-a').textContent = 'Stream A';
      document.getElementById('title-b').textContent = 'Stream B';
    } else {
      promptA = promptB = document.getElementById('pc').value;
      const ta = document.getElementById('temp-a').value;
      const tb = document.getElementById('temp-b').value;
      const ka = document.getElementById('topk-a').value;
      const kb = document.getElementById('topk-b').value;
      params.set('pa', promptA); params.set('pb', promptA);
      params.set('n',  document.getElementById('n-c').value);
      params.set('temp_a', ta); params.set('temp_b', tb);
      params.set('top_k_a', ka); params.set('top_k_b', kb);
      document.getElementById('title-a').textContent = `temp=${ta}  top_k=${ka}`;
      document.getElementById('title-b').textContent = `temp=${tb}  top_k=${kb}`;
    }

    let diverged = false;
    es = new EventSource('/stream?' + params.toString());

    es.onmessage = function(e) {
      const d = JSON.parse(e.data);

      if (d.error) {
        document.getElementById('status').textContent = 'Error: ' + d.error;
        stop(); return;
      }
      if (d.mode_tag) {
        document.getElementById('batch-tag').textContent = d.mode_tag;
        return;
      }
      if (d.done) {
        document.getElementById('status').textContent = 'Done.';
        stop(); return;
      }

      document.getElementById('output-a').textContent = d.a;
      document.getElementById('output-b').textContent = d.b;
      document.getElementById('step-a').textContent = d.done_a ? 'EOS' : 'step ' + d.step;
      document.getElementById('step-b').textContent = d.done_b ? 'EOS' : 'step ' + d.step;

      // Divergence badge (compare mode)
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

    es.onerror = function() {
      document.getElementById('status').textContent = 'Stream ended.';
      stop();
    };
  }

  // Ctrl+Enter shortcut
  document.addEventListener('keydown', e => { if (e.ctrlKey && e.key === 'Enter') generate(); });
</script>
</body>
</html>"""


# ── SSE endpoint ──────────────────────────────────────────────────────────────

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

    cfg_a = StreamCfg(label="A", temp=temp_a, top_k=top_k_a, color="cyan")
    cfg_b = StreamCfg(label="B", temp=temp_b, top_k=top_k_b, color="magenta")

    def generate():
        # Try batch, fall back to interleaved
        try:
            it       = _batch_generate_iter(llm, pa, pb, n, cfg_a, cfg_b)
            mode_tag = "true batch"
        except Exception as e:
            try:
                it       = _interleaved_generate_iter(llm, pa, pb, n, cfg_a, cfg_b)
                mode_tag = "interleaved"
            except Exception as e2:
                yield f"data: {json.dumps({'error': str(e2)})}\n\n"
                return

        yield f"data: {json.dumps({'mode_tag': mode_tag})}\n\n"

        try:
            for text_a, text_b, step, done_a, done_b in it:
                yield f"data: {json.dumps({'a': text_a, 'b': text_b, 'step': step, 'done_a': done_a, 'done_b': done_b})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            return

        yield f"data: {json.dumps({'done': True})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="baddle web UI")
    parser.add_argument("-m", "--model",    help="model path or filename in models/")
    parser.add_argument("--no-gpu",         action="store_true")
    parser.add_argument("--gpu-layers",     type=int, default=-1)
    parser.add_argument("--ctx",            type=int, default=4096)
    parser.add_argument("--port",           type=int, default=7860)
    args = parser.parse_args()

    global llm, model_name
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
