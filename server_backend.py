"""
llama-server HTTP backend for Baddle.

Sends two concurrent streaming requests to a llama-server instance,
merging the SSE streams in lockstep to produce the same yield interface
as _batch_generate_iter / _interleaved_generate_iter:

    (text_a, text_b, step, done_a, done_b)

No external dependencies -- uses stdlib only (urllib, threading, queue, json).
"""
from __future__ import annotations

import atexit
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional, Tuple

if TYPE_CHECKING:
    from main import StreamCfg

_server_proc: Optional[subprocess.Popen] = None


def server_available(base_url: str, timeout: float = 3.0) -> bool:
    """Check if llama-server is reachable.

    Tries /health (native llama-server) then /v1/models (Python llama_cpp.server).
    """
    base = base_url.rstrip("/")
    for path in ("/health", "/v1/models"):
        try:
            req = urllib.request.Request(base + path, method="GET")
            with urllib.request.urlopen(req, timeout=timeout):
                return True
        except Exception:
            continue
    return False


def is_native_server(base_url: str, timeout: float = 3.0) -> bool:
    """True if the server is native llama-server (has /health endpoint)."""
    try:
        req = urllib.request.Request(base_url.rstrip("/") + "/health", method="GET")
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except Exception:
        return False


def has_native_server() -> bool:
    """Check if a native llama-server binary is available (project dir or PATH)."""
    project_dir = Path(__file__).resolve().parent / "llama-server"
    for name in ("llama-server", "llama-server.exe"):
        if (project_dir / name).is_file():
            return True
    return shutil.which("llama-server") is not None


def _build_server_cmd(model_path: str, port: int, n_ctx: int, gpu_layers: int) -> list:
    """Build the command to launch a llama server.

    Tries in order:
    1. llama-server binary on PATH or next to llama_cpp package
    2. python -m llama_cpp.server (pip install llama-cpp-python[server])
    """
    # 1. Native binary (llama.cpp releases)
    # Check: project/llama-server/ folder, then PATH, then llama_cpp package
    binary = None
    project_dir = Path(__file__).resolve().parent / "llama-server"
    for name in ("llama-server", "llama-server.exe"):
        candidate = project_dir / name
        if candidate.is_file():
            binary = str(candidate)
            break

    if not binary:
        binary = shutil.which("llama-server")

    if not binary:
        try:
            import llama_cpp
            pkg_dir = Path(llama_cpp.__file__).parent
            for name in ("llama-server", "llama-server.exe"):
                candidate = pkg_dir / name
                if candidate.is_file():
                    binary = str(candidate)
                    break
        except Exception:
            pass

    if binary:
        return [
            binary,
            "-m", str(model_path),
            "-c", str(n_ctx),
            "--port", str(port),
            "-ngl", str(gpu_layers),
            "--parallel", "2",
        ]

    # 2. Python module (llama_cpp.server via uvicorn)
    try:
        import llama_cpp.server  # noqa: F401
        return [
            sys.executable, "-m", "llama_cpp.server",
            "--model", str(model_path),
            "--n_ctx", str(n_ctx),
            "--port", str(port),
            "--n_gpu_layers", str(gpu_layers),
        ]
    except ImportError:
        pass

    raise RuntimeError(
        "No llama server found. Install one of:\n"
        "  pip install llama-cpp-python[server]   (Python server)\n"
        "  or download llama-server from https://github.com/ggml-org/llama.cpp/releases"
    )


def launch_server(
    model_path: str,
    port: int = 8090,
    n_ctx: int = 4096,
    gpu_layers: int = -1,
) -> str:
    """Launch llama server as a subprocess, wait for /health, return base URL.

    The subprocess is killed automatically on exit (atexit).
    Raises RuntimeError if no server is available or it doesn't start.
    """
    global _server_proc

    cmd = _build_server_cmd(model_path, port, n_ctx, gpu_layers)

    # Launch with output going to stderr so it doesn't mix with our stdout
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    _server_proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=sys.stderr,
        env=env,
    )
    atexit.register(_kill_server)

    base_url = f"http://localhost:{port}"
    # Wait up to 60s for the server to be ready
    for i in range(120):
        if _server_proc.poll() is not None:
            raise RuntimeError(
                f"llama-server exited with code {_server_proc.returncode}"
            )
        if server_available(base_url, timeout=1.0):
            return base_url
        time.sleep(0.5)

    _kill_server()
    raise RuntimeError("llama-server did not become ready within 60 seconds")


def _kill_server():
    global _server_proc
    if _server_proc and _server_proc.poll() is None:
        _server_proc.terminate()
        try:
            _server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _server_proc.kill()
    _server_proc = None


def _stream_completions(
    base_url: str,
    prompt: str,
    max_tokens: int,
    cfg: StreamCfg,
    out_q: queue.Queue,
):
    """Stream tokens from /v1/completions into a queue.

    Puts str tokens into the queue.  Puts None when done (EOS or max_tokens).
    On error puts an Exception object.
    """
    url = base_url.rstrip("/") + "/v1/completions"
    payload = {
        "prompt": prompt,
        "max_tokens": max_tokens,
        "temperature": cfg.temp,
        "top_k": cfg.top_k,
        "stream": True,
    }
    if cfg.seed >= 0:
        payload["seed"] = cfg.seed
    body = json.dumps(payload).encode()

    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )

    try:
        import http.client
        from urllib.parse import urlparse
        parsed = urlparse(url)
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=120)
        conn.request("POST", parsed.path, body=body, headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        })
        resp = conn.getresponse()
        if resp.status != 200:
            raise RuntimeError(f"Server returned {resp.status}: {resp.read().decode()}")

        buf = b""
        while True:
            # Read one byte at a time from raw socket to avoid IncompleteRead
            # with chunked transfer encoding.  http.client handles de-chunking.
            chunk = resp.read(1)
            if not chunk:
                break
            buf += chunk
            if chunk == b"\n":
                line = buf.decode("utf-8", errors="replace").strip()
                buf = b""
                if not line:
                    continue
                if line == "data: [DONE]":
                    break
                if line.startswith("data: "):
                    payload = line[len("data: "):]
                    try:
                        obj = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    choices = obj.get("choices", [])
                    if choices:
                        text = choices[0].get("text", "")
                        if text:
                            out_q.put(text)
                        finish = choices[0].get("finish_reason")
                        if finish:
                            break
        conn.close()
    except Exception as exc:
        out_q.put(exc)
    finally:
        out_q.put(None)


def _collect_tokens(base_url: str, prompt: str, max_tokens: int, cfg) -> list:
    """Generate all tokens via HTTP and return as a list of strings."""
    tokens = []
    q: queue.Queue = queue.Queue()
    _stream_completions(base_url, prompt, max_tokens, cfg, q)
    while True:
        item = q.get()
        if item is None:
            break
        if isinstance(item, Exception):
            raise RuntimeError(f"Server error: {item}")
        tokens.append(item)
    return tokens


def _server_generate_iter(
    base_url: str,
    pa: str,
    pb: str,
    max_tokens: int,
    cfg_a: StreamCfg,
    cfg_b: StreamCfg,
) -> Iterator[Tuple[str, str, int, bool, bool]]:
    """Dual-stream generation via llama-server.

    Generates A fully first, then streams B — yielding both in lockstep.
    Sequential because Python llama_cpp.server can't handle concurrent
    streaming requests (single model instance).
    """
    # Phase 1: generate A fully (no yields yet)
    tokens_a = _collect_tokens(base_url, pa, max_tokens, cfg_a)

    # Phase 2: stream B, yielding both at each step
    q_b: queue.Queue = queue.Queue()
    t_b = threading.Thread(
        target=_stream_completions,
        args=(base_url, pb, max_tokens, cfg_b, q_b),
        daemon=True,
    )
    t_b.start()

    text_a = ""
    text_b = ""
    done_b = False
    step = 0

    while not done_b:
        tok = q_b.get(timeout=120)
        if tok is None:
            done_b = True
        elif isinstance(tok, Exception):
            raise RuntimeError(f"Server stream B error: {tok}")
        else:
            text_b += tok

        # Reveal A tokens in lockstep
        if step < len(tokens_a):
            text_a += tokens_a[step]
        done_a = step >= len(tokens_a) - 1

        yield text_a, text_b, step, done_a, done_b
        step += 1

    # Flush remaining A tokens if A was longer
    while step <= len(tokens_a) - 1:
        text_a += tokens_a[step]
        yield text_a, text_b, step, True, True
        step += 1

    t_b.join(timeout=5)
