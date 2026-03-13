#!/usr/bin/env python3
"""baddle — interactive neural token experiment CLI"""

import os
import sys
import json
import dataclasses
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

# Force VT100 mode — fixes prompt_toolkit in VS Code / Windows Terminal / any non-cmd shell
os.environ["PROMPT_TOOLKIT_NO_WIN32"] = "1"

# ── third-party ──────────────────────────────────────────────────────────────
try:
    import numpy as np
    from rich.console import Console
    from rich.panel import Panel
    from rich.layout import Layout
    from rich.live import Live
    from rich.table import Table
    from rich import box
    import questionary
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.history import InMemoryHistory
except ImportError as e:
    sys.exit(f"[error] Missing dependency: {e}\nRun: python setup.py")

try:
    from llama_cpp import Llama
    _HAS_LLAMA_CPP = True
except ImportError:
    Llama = None  # type: ignore
    _HAS_LLAMA_CPP = False

console = Console()
MODELS_DIR = Path(__file__).parent / "models"


# ── config dataclass ──────────────────────────────────────────────────────────

@dataclasses.dataclass
class StreamCfg:
    label: str
    temp:  float = 0.7
    top_k: int   = 40
    color: str   = "cyan"
    seed:  int   = -1     # -1 = random


# ── model helpers ─────────────────────────────────────────────────────────────

def pick_model(arg: Optional[str]) -> Path:
    if arg:
        p = Path(arg)
        if not p.exists():
            p = MODELS_DIR / arg
        if not p.exists():
            sys.exit(f"model not found: {arg}")
        return p

    models = sorted(MODELS_DIR.glob("*.gguf"))
    if not models:
        sys.exit(f"No .gguf files in {MODELS_DIR}")
    if len(models) == 1:
        console.print(f"[dim]model: {models[0].name}[/dim]")
        return models[0]

    choices = [f"{m.name}  ({m.stat().st_size / 1024 / 1024:.0f} MB)" for m in models]
    try:
        choice = questionary.select("Select model:", choices=choices).ask()
        if choice is None:
            sys.exit(0)
    except Exception:
        print("\nSelect model:")
        for i, c in enumerate(choices, 1):
            print(f"  {i}. {c}")
        while True:
            raw = input("Choice [1]: ").strip() or "1"
            if raw.isdigit() and 1 <= int(raw) <= len(choices):
                choice = choices[int(raw) - 1]
                break
            print(f"  Enter a number 1–{len(choices)}")
    return models[choices.index(choice)]


_batch_seq1_ok: Optional[bool] = None   # set by _probe_batch_support at load time


def load_model(path: Path, gpu_layers: int, n_ctx: int) -> Llama:
    gl = "all" if gpu_layers == -1 else gpu_layers
    kwargs = dict(model_path=str(path), n_gpu_layers=gpu_layers, n_ctx=n_ctx, verbose=False)
    with console.status(f"Loading [bold]{path.name}[/bold]  (gpu_layers={gl}, ctx={n_ctx})..."):
        llm = Llama(**kwargs)
    console.print("[green]Model ready[/green]")
    _probe_batch_support(llm)
    return llm


def _probe_batch_support(llm: Llama):
    """Mark batch path as unavailable.  The unified KV cache in this
    llama-cpp-python build (0.3.x) does not support multi-sequence
    operations -- even probing seq_id>=1 corrupts the context pointer
    and causes access-violation crashes in all subsequent calls."""
    global _batch_seq1_ok
    _batch_seq1_ok = False
    console.print("[dim]  batch: disabled (unified KV cache, no multi-seq)[/dim]\n")


# ── sampling ──────────────────────────────────────────────────────────────────

def _get_logits(llm: Llama) -> np.ndarray:
    """Get logits for the last evaluated token directly from the C context.
    llm.scores is NOT populated when logits_all=False (default in 0.3.x),
    so we must read from the C pointer."""
    raw = llm._ctx.get_logits()
    n_vocab = llm.n_vocab()
    return np.ctypeslib.as_array(raw, shape=(n_vocab,)).copy().astype(np.float32)


def _sample(llm: Llama, temp: float) -> int:
    """High-level sampler for step mode (uses llama_cpp internals)."""
    if temp == 0.0:
        return llm.sample(top_k=1, top_p=1.0, temp=1.0, repeat_penalty=1.0)
    return llm.sample(top_k=40, top_p=0.95, temp=temp, repeat_penalty=1.1)


def _sample_logits(logits: np.ndarray, temp: float, top_k: int = 40) -> int:
    """Sample from a raw logits array with top-k filtering."""
    if temp == 0.0:
        return int(np.argmax(logits))
    logits = logits.astype(np.float64)
    logits -= logits.max()
    probs = np.exp(logits / temp)
    probs /= probs.sum()
    if 0 < top_k < len(probs):
        top_idx = np.argsort(-probs)[:top_k]
        mask = np.zeros(len(probs), dtype=bool)
        mask[top_idx] = True
        probs = probs * mask
        probs /= probs.sum()
    return int(np.random.choice(len(probs), p=probs))


def _top_tokens_table(llm: Llama, n: int) -> Table:
    t = Table(title=f"Top {n} probable next tokens", box=box.SIMPLE_HEAVY)
    t.add_column("#",    style="dim", width=4)
    t.add_column("id",   width=7)
    t.add_column("prob", width=8)
    t.add_column("token")

    if llm.n_tokens == 0:
        t.add_row("-", "-", "-", "[dim](nothing evaluated yet)[/dim]")
        return t

    logits = _get_logits(llm)
    logits -= logits.max()
    probs = np.exp(logits)
    probs /= probs.sum()
    top = np.argsort(-probs)[:n]

    for rank, tid in enumerate(top):
        txt = llm.detokenize([int(tid)]).decode("utf-8", errors="replace")
        bar = "█" * int(probs[tid] * 30)
        t.add_row(
            str(rank + 1),
            str(int(tid)),
            f"{probs[tid]:.4f}",
            f"{repr(txt)}  [dim]{bar}[/dim]",
        )
    return t


# ── batch generation iterator ─────────────────────────────────────────────────

def _batch_generate_iter(
    llm: Llama, pa: str, pb: str, max_tokens: int, cfg_a: StreamCfg, cfg_b: StreamCfg
) -> Iterator[Tuple[str, str, int, bool, bool]]:
    """
    True parallel: both sequences in ONE llama_decode per step.
    Yields (text_a, text_b, step, done_a, done_b).
    Raises RuntimeError if seq_id ≥ 1 is not supported — callers fall back
    to _interleaved_generate_iter.
    """
    global _batch_seq1_ok
    if _batch_seq1_ok is False:
        raise RuntimeError("seq_id≥1 not supported (cached)")
    import llama_cpp as lc

    # ── Locate raw llama_context_p ────────────────────────────────────────────
    if hasattr(llm, "_ctx") and hasattr(llm._ctx, "ctx"):
        ctx = llm._ctx.ctx
    elif hasattr(llm, "_ctx"):
        ctx = llm._ctx
    elif hasattr(llm, "ctx"):
        ctx = llm.ctx
    else:
        raise AttributeError("Cannot locate llama_context_p")

    # ── Verify core C functions are bound ─────────────────────────────────────
    required = ["llama_batch_init", "llama_decode", "llama_get_logits_ith"]
    missing = [f for f in required if not hasattr(lc, f)]
    if missing:
        raise AttributeError(f"llama_cpp missing bindings: {missing}")

    # ── llama_batch_add / llama_batch_clear ───────────────────────────────────
    # These are Python helpers that just write into the batch struct.
    # Newer llama-cpp-python may not re-export them, so we implement them here.
    #
    # IMPORTANT: In llama.cpp ≥ b3660 the field was renamed from
    # `logits` → `output` in the C struct. The ctypes definition in
    # llama-cpp-python was updated to match, so we detect it dynamically.
    if hasattr(lc, "llama_batch_add"):
        _batch_add   = lc.llama_batch_add
        _batch_clear = lc.llama_batch_clear
    else:
        # Detect the correct output-flag field name at runtime
        _batch_fields = {name for name, _ in lc.llama_batch._fields_}
        if "output" in _batch_fields:
            _out_field = "output"
        elif "logits" in _batch_fields:
            _out_field = "logits"
        else:
            raise AttributeError(
                f"llama_batch has neither 'output' nor 'logits' field. "
                f"Known fields: {_batch_fields}"
            )

        def _batch_add(batch, token_id, pos, seq_ids, compute_logits):
            i = batch.n_tokens
            batch.token   [i] = token_id
            batch.pos     [i] = pos
            batch.n_seq_id[i] = len(seq_ids)
            for j, sid in enumerate(seq_ids):
                batch.seq_id[i][j] = sid
            getattr(batch, _out_field)[i] = compute_logits
            batch.n_tokens += 1

        def _batch_clear(batch):
            batch.n_tokens = 0

    # ── n_ctx ─────────────────────────────────────────────────────────────────
    n_ctx   = lc.llama_n_ctx(ctx) if hasattr(lc, "llama_n_ctx") else llm.n_ctx()
    n_vocab = llm.n_vocab()

    ta = llm.tokenize(pa.encode())
    tb = llm.tokenize(pb.encode())

    needed = len(ta) + len(tb) + max_tokens * 2
    if needed > n_ctx:
        raise RuntimeError(
            f"Context too small ({n_ctx} < {needed}). Restart with --ctx {needed + 256}."
        )

    # ── Clear KV cache (renamed in newer llama.cpp releases) ─────────────────
    if hasattr(lc, "llama_kv_cache_clear"):
        lc.llama_kv_cache_clear(ctx)
    elif hasattr(lc, "llama_kv_self_clear"):        # llama.cpp ≥ b4000
        lc.llama_kv_self_clear(ctx)
    elif hasattr(llm, "_ctx") and hasattr(llm._ctx, "kv_cache_clear"):
        llm._ctx.kv_cache_clear()
    else:
        llm.reset()

    # Allocate batch large enough for the largest single prefill + generation steps
    batch_size = max(len(ta), len(tb), 2) + max_tokens + 4
    batch = lc.llama_batch_init(batch_size, 0, 2)

    try:
        # ── Prefill (seq_id=0, or both 0+1 for compare) → sample tok_a ──────
        prefill_seqs = [0, 1] if pa == pb else [0]
        for pos, tok in enumerate(ta):
            _batch_add(batch, tok, pos, prefill_seqs, pos == len(ta) - 1)

        # Diagnostic: verify the output flag was written into the batch struct
        _flag_value = getattr(batch, _out_field)[len(ta) - 1]
        if _flag_value == 0:
            fields = [n for n, _ in lc.llama_batch._fields_]
            raise RuntimeError(
                f"Output flag not set — field '{_out_field}' = {_flag_value}. "
                f"All llama_batch fields: {fields}"
            )

        rc = lc.llama_decode(ctx, batch)
        if rc != 0:
            raise RuntimeError(f"llama_decode failed prefill A (rc={rc})")

        # Try llama_get_logits_ith first, fall back to llama_get_logits (flat array)
        raw = lc.llama_get_logits_ith(ctx, 0)
        if not raw and hasattr(lc, "llama_get_logits"):
            raw = lc.llama_get_logits(ctx)
        if not raw:
            raise RuntimeError("llama_get_logits returned NULL after successful prefill A decode")
        # Keep a stable numpy copy — the ctypes pointer may be invalidated by
        # subsequent KV operations (llama_kv_self_seq_cp, next decode, etc.)
        logits_a = np.array(raw[:n_vocab], dtype=np.float32)
        tok_a = _sample_logits(logits_a, cfg_a.temp, cfg_a.top_k)

        # ── Prefill B (seq_id=1) → sample tok_b ──────────────────────────────
        # llama_decode with seq_id=1 reliably returns rc=-1 in this build.
        # Work-around strategies:
        #
        #   compare mode (pa == pb):
        #       The two sequences share an identical prompt — copy seq 0's
        #       KV entries to seq 1 with llama_kv_self_seq_cp, then sample
        #       tok_b from the already-computed logits (different cfg only).
        #       Zero extra forward passes.
        #
        #   parallel mode (pa != pb):
        #       No shared prefix — raise so the caller falls back to
        #       interleaved (save_state / load_state).
        if pa == pb:
            # Prefill already wrote KV entries for both seq 0 and seq 1
            # (via seq_ids=[0,1]).  Same prompt → identical logits, just
            # sample with different cfg.  No KV copy, no second decode.
            tok_b = _sample_logits(logits_a, cfg_b.temp, cfg_b.top_k)
        else:
            # Parallel mode: different prompts → seq_id=1 decode fails in this
            # llama-cpp-python build.  Raise to trigger interleaved fallback.
            raise RuntimeError(
                "parallel mode: llama_decode with seq_id=1 returns rc=-1 "
                "in this build — falling back to interleaved"
            )

        ga, gb         = list(ta) + [tok_a], list(tb) + [tok_b]
        # tok_a/tok_b are the NEXT tokens to decode (sampled from prefill logits,
        # not yet in the KV cache).  They belong at positions len(ta) / len(tb).
        cur_pos_a      = len(ta)
        cur_pos_b      = len(tb)
        da             = tok_a == llm.token_eos()
        db             = tok_b == llm.token_eos()
        text_a         = llm.detokenize(ga).decode("utf-8", errors="replace")
        text_b         = llm.detokenize(gb).decode("utf-8", errors="replace")

        yield text_a, text_b, 0, da, db

        # ── Generation: two sequential single-seq decodes per step ──────────
        # Mixed-seq batches (seq_id=0 + seq_id=1 in one llama_decode call)
        # return rc=-1 in this llama-cpp-python build even with n_seq_max=4.
        # Workaround: one batch per sequence per step.  Both KV sequences stay
        # live in the cache so no save/load-state copies are needed — faster
        # than the interleaved fallback.
        for step in range(1, max_tokens):
            if da and db:
                break

            if not da:
                _batch_clear(batch)
                _batch_add(batch, tok_a, cur_pos_a, [0], True)
                rc = lc.llama_decode(ctx, batch)
                if rc != 0:
                    raise RuntimeError(f"llama_decode failed seq A at step {step} (rc={rc})")
                raw = lc.llama_get_logits_ith(ctx, 0)
                if not raw and hasattr(lc, "llama_get_logits"):
                    raw = lc.llama_get_logits(ctx)
                if not raw:
                    raise RuntimeError(f"llama_get_logits NULL for seq A at step {step}")
                tok_a = _sample_logits(np.array(raw[:n_vocab], dtype=np.float32), cfg_a.temp, cfg_a.top_k)
                ga.append(tok_a)
                text_a = llm.detokenize(ga).decode("utf-8", errors="replace")
                cur_pos_a += 1
                if tok_a == llm.token_eos():
                    da = True

            if not db:
                _batch_clear(batch)
                _batch_add(batch, tok_b, cur_pos_b, [1], True)
                rc = lc.llama_decode(ctx, batch)
                if rc != 0:
                    raise RuntimeError(f"llama_decode failed seq B at step {step} (rc={rc})")
                raw = lc.llama_get_logits_ith(ctx, 0)
                if not raw and hasattr(lc, "llama_get_logits"):
                    raw = lc.llama_get_logits(ctx)
                if not raw:
                    raise RuntimeError(f"llama_get_logits NULL for seq B at step {step}")
                tok_b = _sample_logits(np.array(raw[:n_vocab], dtype=np.float32), cfg_b.temp, cfg_b.top_k)
                gb.append(tok_b)
                text_b = llm.detokenize(gb).decode("utf-8", errors="replace")
                cur_pos_b += 1
                if tok_b == llm.token_eos():
                    db = True

            yield text_a, text_b, step, da, db

    finally:
        if hasattr(lc, "llama_batch_free"):
            lc.llama_batch_free(batch)
        # Reset model state — direct C API calls desync Llama's internals.
        # Wrap in try/except: a failed llama_decode can corrupt the KV cache
        # pointer, making llm.reset() itself crash (access violation in
        # llama_memory_seq_rm).  Clear KV at the C level first if possible.
        try:
            if hasattr(lc, "llama_kv_cache_clear"):
                lc.llama_kv_cache_clear(ctx)
            elif hasattr(lc, "llama_kv_self_clear"):
                lc.llama_kv_self_clear(ctx)
        except Exception:
            pass
        try:
            llm.reset()
        except Exception:
            pass


def _interleaved_generate_iter(
    llm: Llama, pa: str, pb: str, max_tokens: int, cfg_a: StreamCfg, cfg_b: StreamCfg
) -> Iterator[Tuple[str, str, int, bool, bool]]:
    """
    Streaming dual generation with ONE save_state + ONE load_state.

    Compare mode (pa == pb): one prefill, save state, run A (store tokens),
    load state, run B — yielding both streams in lockstep during B's generation.
    Parallel mode (pa != pb): two prefills, same streaming approach.
    """
    ta = llm.tokenize(pa.encode())
    tb = llm.tokenize(pb.encode())

    # ── Run A (collect tokens, no yield yet) ─────────────────────────────────
    llm.reset(); llm.eval(ta)
    state_for_b = (
        llm.save_state()
        if (pa == pb and hasattr(llm, "save_state") and hasattr(llm, "load_state"))
        else None
    )

    tokens_a = list(ta)
    for _ in range(max_tokens):
        logits = _get_logits(llm)
        tok = _sample_logits(logits, cfg_a.temp, cfg_a.top_k)
        llm.eval([tok])
        tokens_a.append(tok)
        if tok == llm.token_eos():
            break

    gen_a = tokens_a[len(ta):]

    # ── Run B, yielding both streams at each step ────────────────────────────
    if state_for_b is not None:
        llm.load_state(state_for_b)
    else:
        llm.reset(); llm.eval(tb)

    tokens_b = list(tb)
    # Pre-decode full A text once (avoids repeated detokenize)
    full_text_a = llm.detokenize(tokens_a).decode("utf-8", errors="replace")

    for step in range(max_tokens):
        logits = _get_logits(llm)
        tok = _sample_logits(logits, cfg_b.temp, cfg_b.top_k)
        llm.eval([tok])
        tokens_b.append(tok)

        done_b = tok == llm.token_eos()

        # A text up to this step (slice from pre-decoded full text, or cap at end)
        a_end = min(step + 1, len(gen_a))
        text_a = llm.detokenize(tokens_a[:len(ta) + a_end]).decode("utf-8", errors="replace")
        done_a = a_end >= len(gen_a)

        text_b = llm.detokenize(tokens_b).decode("utf-8", errors="replace")

        yield text_a, text_b, step, done_a, done_b

        if done_b:
            break

    # ── If A was longer than B, flush remaining A steps ──────────────────────
    b_steps = len(tokens_b) - len(tb)
    text_b = llm.detokenize(tokens_b).decode("utf-8", errors="replace")
    for step in range(b_steps, len(gen_a)):
        a_end = step + 1
        text_a = llm.detokenize(tokens_a[:len(ta) + a_end]).decode("utf-8", errors="replace")
        yield text_a, text_b, step, a_end >= len(gen_a), True


# ── dual-stream display ───────────────────────────────────────────────────────

def _make_layout(
    text_a: str, text_b: str, step: int, done_a: bool, done_b: bool,
    cfg_a: StreamCfg, cfg_b: StreamCfg, diverge_at: Optional[int] = None,
) -> Layout:
    layout = Layout()
    layout.split_row(Layout(name="A"), Layout(name="B"))

    status_a = "[red]EOS[/red]" if done_a else f"step {step}"
    status_b = "[red]EOS[/red]" if done_b else f"step {step}"
    div_tag  = f"  [yellow]diverged @ step {diverge_at}[/yellow]" if diverge_at is not None else ""

    layout["A"].update(Panel(
        text_a or "[dim](empty)[/dim]",
        title=f"[bold {cfg_a.color}]{cfg_a.label}[/bold {cfg_a.color}]  [dim]{status_a}[/dim]",
        border_style=cfg_a.color,
    ))
    layout["B"].update(Panel(
        text_b or "[dim](empty)[/dim]",
        title=f"[bold {cfg_b.color}]{cfg_b.label}[/bold {cfg_b.color}]  [dim]{status_b}[/dim]{div_tag}",
        border_style=cfg_b.color,
    ))
    return layout


def _run_dual_streams(
    llm, pa: str, pb: str, max_tokens: int,
    cfg_a: StreamCfg, cfg_b: StreamCfg, track_diverge: bool = False,
    server_url: Optional[str] = None,
):
    """Shared runner for parallel and compare modes. Tries server, batch, interleaved."""
    # Seed RNG for reproducibility
    if cfg_a.seed >= 0:
        np.random.seed(cfg_a.seed)

    def _gen():
        if server_url:
            from server_backend import _server_generate_iter
            console.print("[green]Using llama-server (parallel HTTP)[/green]")
            yield from _server_generate_iter(server_url, pa, pb, max_tokens, cfg_a, cfg_b)
            return
        try:
            yield from _batch_generate_iter(llm, pa, pb, max_tokens, cfg_a, cfg_b)
        except Exception as e:
            console.print(f"[yellow]Batch unavailable ({type(e).__name__}: {e}). Using interleaved.[/yellow]")
            yield from _interleaved_generate_iter(llm, pa, pb, max_tokens, cfg_a, cfg_b)

    diverge_at: Optional[int] = None

    with Live(
        _make_layout("", "", 0, False, False, cfg_a, cfg_b),
        refresh_per_second=8, console=console,
    ) as live:
        try:
            for text_a, text_b, step, done_a, done_b in _gen():
                if track_diverge and diverge_at is None:
                    gen_a = text_a[len(pa):]
                    gen_b = text_b[len(pb):]
                    if gen_a != gen_b and (gen_a or gen_b):
                        diverge_at = step
                live.update(_make_layout(text_a, text_b, step, done_a, done_b, cfg_a, cfg_b, diverge_at))
        except Exception as e:
            console.print(f"[red]Generation error: {e}[/red]")


# ── STEP mode ─────────────────────────────────────────────────────────────────

STEP_COMMANDS = ["top", "inject", "auto", "temp", "show", "save", "load", "reset", "quit"]

STEP_HELP = (
    "[dim]  Enter[/dim] next token   "
    "[dim]top [N][/dim] top tokens   "
    "[dim]inject <text>[/dim] force   "
    "[dim]auto <N>[/dim] auto   "
    "[dim]temp <f>[/dim] temperature   "
    "[dim]show[/dim] full text\n"
    "[dim]  save <file>[/dim] save session   "
    "[dim]load <file>[/dim] replay session   "
    "[dim]reset[/dim]   [dim]q[/dim] quit   "
    "[dim]↑↓ history   Tab autocomplete[/dim]"
)


def step_mode(llm: Llama, prompt: str, temp: float, model_name: str = ""):
    console.rule("[bold cyan]STEP MODE[/bold cyan]")
    console.print(Panel(STEP_HELP, border_style="dim"))

    init = llm.tokenize(prompt.encode())
    console.print(Panel(
        f"{repr(prompt)}\n[dim]{len(init)} tokens[/dim]",
        title="[bold]Prompt[/bold]",
        border_style="cyan",
    ))

    session = PromptSession(
        history=InMemoryHistory(),
        completer=WordCompleter(STEP_COMMANDS, sentence=True),
    )

    def _reset() -> List[int]:
        llm.reset()
        llm.eval(init)
        return list(init)

    gen      = _reset()
    cur_temp = temp
    events: List[dict] = []

    def _replay(ev_list: List[dict]):
        nonlocal gen, cur_temp
        gen = _reset()
        for ev in ev_list:
            t = ev["type"]
            if t == "step":
                tok = _sample(llm, cur_temp)
                llm.eval([tok]); gen.append(tok)
            elif t == "inject":
                toks = llm.tokenize(ev["text"].encode(), add_bos=False)
                for tk in toks:
                    llm.eval([tk]); gen.append(tk)
            elif t == "auto":
                for _ in range(ev["n"]):
                    tok = _sample(llm, cur_temp)
                    llm.eval([tok]); gen.append(tok)
                    if tok == llm.token_eos(): break
            elif t == "temp":
                cur_temp = ev["value"]
            elif t == "reset":
                gen = _reset()

    while True:
        try:
            cmd = session.prompt(">> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        parts = cmd.split(None, 1)
        verb  = parts[0] if parts else ""

        if not cmd:
            tok = _sample(llm, cur_temp)
            llm.eval([tok]); gen.append(tok)
            txt = llm.detokenize([tok]).decode("utf-8", errors="replace")
            console.print(f"  [yellow][{tok}][/yellow] {repr(txt)}")
            events.append({"type": "step"})
            if tok == llm.token_eos():
                console.print("  [red][EOS][/red]"); break

        elif verb == "top":
            n = int(parts[1]) if len(parts) > 1 else 10
            console.print(_top_tokens_table(llm, n))

        elif verb == "inject":
            if len(parts) < 2:
                console.print("[red]usage:[/red] inject <text>"); continue
            toks = llm.tokenize(parts[1].encode(), add_bos=False)
            for t in toks:
                llm.eval([t]); gen.append(t)
            decoded = llm.detokenize(toks).decode("utf-8", errors="replace")
            console.print(f"  [green]injected[/green] {len(toks)} tokens → {repr(decoded)}")
            events.append({"type": "inject", "text": parts[1]})

        elif verb == "auto":
            n = int(parts[1]) if len(parts) > 1 else 10
            buf = []
            for _ in range(n):
                tok = _sample(llm, cur_temp)
                llm.eval([tok]); gen.append(tok)
                buf.append(llm.detokenize([tok]).decode("utf-8", errors="replace"))
                if tok == llm.token_eos(): break
            console.print("".join(buf))
            events.append({"type": "auto", "n": n})

        elif verb == "temp":
            cur_temp = float(parts[1])
            console.print(f"  temp = [cyan]{cur_temp}[/cyan]")
            events.append({"type": "temp", "value": cur_temp})

        elif verb == "show":
            text = llm.detokenize(gen).decode("utf-8", errors="replace")
            console.print(Panel(text, title="Generated so far", border_style="green"))

        elif verb == "save":
            fname = (parts[1] if len(parts) > 1 else "session.json")
            data  = {"model": model_name, "prompt": prompt, "temp": temp, "events": events}
            Path(fname).write_text(json.dumps(data, indent=2, ensure_ascii=False))
            console.print(f"  [green]saved[/green] {len(events)} events → {fname}")

        elif verb == "load":
            fname = (parts[1] if len(parts) > 1 else "session.json")
            try:
                data = json.loads(Path(fname).read_text())
            except FileNotFoundError:
                console.print(f"[red]not found:[/red] {fname}"); continue
            events = data.get("events", [])
            _replay(events)
            text = llm.detokenize(gen).decode("utf-8", errors="replace")
            console.print(f"  [green]replayed[/green] {len(events)} events from {fname}")
            console.print(Panel(text, title="State after replay", border_style="green"))

        elif verb == "reset":
            gen = _reset(); events = []
            console.print("  [dim]reset.[/dim]")
            events.append({"type": "reset"})

        elif verb in ("q", "quit", "exit"):
            break

        else:
            console.print(STEP_HELP)

    text = llm.detokenize(gen).decode("utf-8", errors="replace")
    console.print(Panel(text, title="[bold green]Final text[/bold green]", border_style="green"))


# ── PARALLEL mode ─────────────────────────────────────────────────────────────

def parallel_mode(llm, pa: str, pb: str, max_tokens: int, temp: float,
                   seed: int = -1, server_url: Optional[str] = None):
    console.rule("[bold magenta]PARALLEL MODE[/bold magenta]")
    console.print(f"  [cyan]A:[/cyan] {repr(pa)}\n  [magenta]B:[/magenta] {repr(pb)}")
    if seed >= 0:
        console.print(f"  [dim]seed={seed}[/dim]")
    console.print()
    cfg_a = StreamCfg(label="Stream A", temp=temp, color="cyan", seed=seed)
    cfg_b = StreamCfg(label="Stream B", temp=temp, color="magenta", seed=seed)
    _run_dual_streams(llm, pa, pb, max_tokens, cfg_a, cfg_b, server_url=server_url)


# ── COMPARE mode ──────────────────────────────────────────────────────────────

def compare_mode(
    llm, prompt: str,
    temp_a: float, top_k_a: int,
    temp_b: float, top_k_b: int,
    max_tokens: int,
    seed: int = -1,
    server_url: Optional[str] = None,
):
    console.rule("[bold yellow]COMPARE MODE[/bold yellow]")
    console.print(
        f"  Prompt: {repr(prompt)}\n"
        f"  [cyan]A:[/cyan] temp={temp_a}  top_k={top_k_a}\n"
        f"  [magenta]B:[/magenta] temp={temp_b}  top_k={top_k_b}"
    )
    if seed >= 0:
        console.print(f"  [dim]seed={seed}[/dim]")
    console.print()
    cfg_a = StreamCfg(label=f"temp={temp_a}  top_k={top_k_a}", temp=temp_a, top_k=top_k_a, color="cyan", seed=seed)
    cfg_b = StreamCfg(label=f"temp={temp_b}  top_k={top_k_b}", temp=temp_b, top_k=top_k_b, color="magenta", seed=seed)
    _run_dual_streams(llm, prompt, prompt, max_tokens, cfg_a, cfg_b,
                      track_diverge=True, server_url=server_url)


# ── MAIN MENU ─────────────────────────────────────────────────────────────────

def _q_select(message: str, choices: list) -> str:
    """Arrow-key select with plain-text fallback when no Win32 console."""
    try:
        result = questionary.select(message, choices=choices).ask()
        if result is None:
            sys.exit(0)
        return result
    except Exception:
        print(f"\n{message}")
        for i, c in enumerate(choices, 1):
            print(f"  {i}. {c}")
        while True:
            raw = input("Choice [1]: ").strip() or "1"
            if raw.isdigit() and 1 <= int(raw) <= len(choices):
                return choices[int(raw) - 1]
            print(f"  Enter a number 1–{len(choices)}")


def _q_text(message: str, default: str = "") -> str:
    """Prompted text input with plain-text fallback."""
    try:
        result = questionary.text(message, default=default).ask()
        return result if result is not None else default
    except Exception:
        prompt_str = f"{message}" + (f" [{default}]" if default else "") + ": "
        raw = input(prompt_str).strip()
        return raw if raw else default


def main():
    import argparse
    parser = argparse.ArgumentParser(description="baddle — neural token experiment CLI")
    parser.add_argument("-m", "--model",    help="model path or filename in models/")
    parser.add_argument("--no-gpu",         action="store_true")
    parser.add_argument("--gpu-layers",     type=int, default=-1)
    parser.add_argument("--ctx",            type=int, default=4096,
                        help="context size -- 4096+ recommended for parallel/compare")
    parser.add_argument("--server",         type=str, default=None, nargs="?", const="auto",
                        help="llama-server URL or 'auto' to launch automatically")
    args = parser.parse_args()

    console.print("\n[bold]baddle[/bold] -- neural token experiment\n", justify="center")

    server_url: Optional[str] = None
    llm = None

    if args.server is not None:
        if args.server == "auto" or not args.server.startswith("http"):
            # Auto-launch llama-server
            model_path = pick_model(args.model)
            gpu_layers = 0 if args.no_gpu else args.gpu_layers
            from server_backend import launch_server
            console.print("[dim]Starting llama-server...[/dim]")
            server_url = launch_server(
                str(model_path), n_ctx=args.ctx, gpu_layers=gpu_layers,
            )
            console.print(f"[green]Server ready:[/green] {server_url}\n")
        else:
            from server_backend import server_available
            if server_available(args.server):
                server_url = args.server.rstrip("/")
                console.print(f"[green]Server mode:[/green] {server_url}\n")
            else:
                console.print(f"[yellow]Server at {args.server} not reachable, loading model locally...[/yellow]\n")

    if server_url is None:
        if not _HAS_LLAMA_CPP:
            sys.exit("[error] llama-cpp-python not found and no llama-server available.\n"
                     "Run: python setup.py")
        model_path = pick_model(args.model)
        gpu_layers = 0 if args.no_gpu else args.gpu_layers
        llm        = load_model(model_path, gpu_layers, args.ctx)

    while True:
        mode_choices = [
            "step     — interactive token-by-token",
            "parallel — two different prompts, one forward pass",
            "compare  — one prompt, two sampling configs side-by-side",
            "quit",
        ]
        if llm is None:
            # server mode: step unavailable
            mode_choices = [c for c in mode_choices if not c.startswith("step")]

        mode = _q_select("Mode:", choices=mode_choices)
        if mode is None or mode == "quit":
            break

        try:
            if mode.startswith("step"):
                temp   = float(_q_text("Temperature (0 = greedy):", default="0.0") or "0.0")
                prompt = _q_text("Prompt:")
                if not prompt:
                    console.print("[yellow]empty prompt, skipping[/yellow]")
                    continue
                console.print()
                step_mode(llm, prompt, temp, model_name=model_path.name)

            elif mode.startswith("parallel"):
                temp = float(_q_text("Temperature:", default="0.7") or "0.7")
                pa   = _q_text("Prompt A:")
                pb   = _q_text("Prompt B:")
                n    = int(_q_text("Max tokens:", default="50") or "50")
                seed = int(_q_text("Seed (-1 = random):", default="-1") or "-1")
                if not pa or not pb:
                    console.print("[yellow]both prompts required, skipping[/yellow]")
                    continue
                console.print()
                parallel_mode(llm, pa, pb, n, temp, seed=seed, server_url=server_url)

            elif mode.startswith("compare"):
                prompt = _q_text("Prompt (shared):")
                if not prompt:
                    console.print("[yellow]empty prompt, skipping[/yellow]")
                    continue
                temp_a  = float(_q_text("Config A -- temperature:", default="0.0") or "0.0")
                top_k_a = int(_q_text("Config A -- top_k:", default="1") or "1")
                temp_b  = float(_q_text("Config B -- temperature:", default="1.0") or "1.0")
                top_k_b = int(_q_text("Config B -- top_k:", default="40") or "40")
                n       = int(_q_text("Max tokens:", default="60") or "60")
                seed    = int(_q_text("Seed (-1 = random):", default="-1") or "-1")
                console.print()
                compare_mode(llm, prompt, temp_a, top_k_a, temp_b, top_k_b, n, seed=seed, server_url=server_url)

        except KeyboardInterrupt:
            console.print("\n[dim]interrupted[/dim]")
            continue

        console.print()


if __name__ == "__main__":
    main()
