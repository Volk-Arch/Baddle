#!/usr/bin/env python3
"""baddle — interactive neural token experiment CLI"""

import sys
import json
import dataclasses
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

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
except ImportError:
    sys.exit("[error] llama-cpp-python not found. Run: python setup.py")

console = Console()
MODELS_DIR = Path(__file__).parent / "models"


# ── config dataclass ──────────────────────────────────────────────────────────

@dataclasses.dataclass
class StreamCfg:
    label: str
    temp:  float = 0.7
    top_k: int   = 40
    color: str   = "cyan"


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
    choice = questionary.select("Select model:", choices=choices).ask()
    if choice is None:
        sys.exit(0)
    return models[choices.index(choice)]


def load_model(path: Path, gpu_layers: int, n_ctx: int) -> Llama:
    gl = "all" if gpu_layers == -1 else gpu_layers
    with console.status(f"Loading [bold]{path.name}[/bold]  (gpu_layers={gl}, ctx={n_ctx})..."):
        llm = Llama(model_path=str(path), n_gpu_layers=gpu_layers, n_ctx=n_ctx, verbose=False)
    console.print("[green]✓[/green] Model ready\n")
    return llm


# ── sampling ──────────────────────────────────────────────────────────────────

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

    logits = np.array(llm.scores[llm.n_tokens - 1], dtype=np.float32)
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
    Raises RuntimeError/AttributeError if batch API unavailable.
    """
    import llama_cpp as lc

    ctx     = llm._ctx.ctx          # raises AttributeError on old versions
    n_vocab = llm.n_vocab()
    n_ctx   = lc.llama_n_ctx(ctx)

    ta = llm.tokenize(pa.encode())
    tb = llm.tokenize(pb.encode())

    needed = len(ta) + len(tb) + max_tokens * 2
    if needed > n_ctx:
        raise RuntimeError(
            f"Context too small ({n_ctx} < {needed}). Restart with --ctx {needed + 256}."
        )

    lc.llama_kv_cache_clear(ctx)
    batch = lc.llama_batch_init(max(len(ta), len(tb)) + max_tokens + 4, 0, 2)

    try:
        # Prefill both sequences in one decode
        for pos, tok in enumerate(ta):
            lc.llama_batch_add(batch, tok, pos, [0], pos == len(ta) - 1)
        for pos, tok in enumerate(tb):
            lc.llama_batch_add(batch, tok, pos, [1], pos == len(tb) - 1)

        if lc.llama_decode(ctx, batch) != 0:
            raise RuntimeError("llama_decode failed during prefill")

        ga, gb         = list(ta), list(tb)
        cur_pos_a      = len(ta)
        cur_pos_b      = len(tb)
        da = db        = False
        text_a, text_b = pa, pb

        for step in range(max_tokens):
            # Sample from logits produced by previous decode
            out_idx = 0

            if not da:
                raw      = lc.llama_get_logits_ith(ctx, out_idx)
                tok_a    = _sample_logits(np.array(raw[:n_vocab], dtype=np.float32), cfg_a.temp, cfg_a.top_k)
                ga.append(tok_a)
                text_a   = llm.detokenize(ga).decode("utf-8", errors="replace")
                out_idx += 1
                if tok_a == llm.token_eos():
                    da = True

            if not db:
                raw   = lc.llama_get_logits_ith(ctx, out_idx)
                tok_b = _sample_logits(np.array(raw[:n_vocab], dtype=np.float32), cfg_b.temp, cfg_b.top_k)
                gb.append(tok_b)
                text_b = llm.detokenize(gb).decode("utf-8", errors="replace")
                if tok_b == llm.token_eos():
                    db = True

            yield text_a, text_b, step, da, db

            if da and db:
                break

            # Build next batch with the sampled tokens and decode
            lc.llama_batch_clear(batch)
            if not da:
                lc.llama_batch_add(batch, tok_a, cur_pos_a, [0], True)
                cur_pos_a += 1
            if not db:
                lc.llama_batch_add(batch, tok_b, cur_pos_b, [1], True)
                cur_pos_b += 1

            if lc.llama_decode(ctx, batch) != 0:
                raise RuntimeError(f"llama_decode failed at step {step}")

    finally:
        lc.llama_batch_free(batch)


def _interleaved_generate_iter(
    llm: Llama, pa: str, pb: str, max_tokens: int, cfg_a: StreamCfg, cfg_b: StreamCfg
) -> Iterator[Tuple[str, str, int, bool, bool]]:
    """
    Fallback: interleaved via KV-cache save/restore (sequential on GPU).
    Raises RuntimeError if save_state/load_state unavailable.
    """
    if not (hasattr(llm, "save_state") and hasattr(llm, "load_state")):
        raise RuntimeError("save_state/load_state not available")

    ta = llm.tokenize(pa.encode())
    tb = llm.tokenize(pb.encode())

    llm.reset(); llm.eval(ta); state_a = llm.save_state()
    llm.reset(); llm.eval(tb); state_b = llm.save_state()
    ga, gb         = list(ta), list(tb)
    da = db        = False
    text_a, text_b = pa, pb

    for step in range(max_tokens):
        if not da:
            llm.load_state(state_a)
            tok = _sample(llm, cfg_a.temp)
            llm.eval([tok]); state_a = llm.save_state()
            ga.append(tok)
            text_a = llm.detokenize(ga).decode("utf-8", errors="replace")
            if tok == llm.token_eos():
                da = True

        if not db:
            llm.load_state(state_b)
            tok = _sample(llm, cfg_b.temp)
            llm.eval([tok]); state_b = llm.save_state()
            gb.append(tok)
            text_b = llm.detokenize(gb).decode("utf-8", errors="replace")
            if tok == llm.token_eos():
                db = True

        yield text_a, text_b, step, da, db
        if da and db:
            break


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
    llm: Llama, pa: str, pb: str, max_tokens: int,
    cfg_a: StreamCfg, cfg_b: StreamCfg, track_diverge: bool = False,
):
    """Shared runner for parallel and compare modes. Tries batch, falls back to interleaved."""

    def _gen():
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

def parallel_mode(llm: Llama, pa: str, pb: str, max_tokens: int, temp: float):
    console.rule("[bold magenta]PARALLEL MODE[/bold magenta]")
    console.print(f"  [cyan]A:[/cyan] {repr(pa)}\n  [magenta]B:[/magenta] {repr(pb)}\n")
    cfg_a = StreamCfg(label="Stream A", temp=temp, color="cyan")
    cfg_b = StreamCfg(label="Stream B", temp=temp, color="magenta")
    _run_dual_streams(llm, pa, pb, max_tokens, cfg_a, cfg_b)


# ── COMPARE mode ──────────────────────────────────────────────────────────────

def compare_mode(
    llm: Llama, prompt: str,
    temp_a: float, top_k_a: int,
    temp_b: float, top_k_b: int,
    max_tokens: int,
):
    console.rule("[bold yellow]COMPARE MODE[/bold yellow]")
    console.print(
        f"  Prompt: {repr(prompt)}\n"
        f"  [cyan]A:[/cyan] temp={temp_a}  top_k={top_k_a}\n"
        f"  [magenta]B:[/magenta] temp={temp_b}  top_k={top_k_b}\n"
    )
    cfg_a = StreamCfg(label=f"temp={temp_a}  top_k={top_k_a}", temp=temp_a, top_k=top_k_a, color="cyan")
    cfg_b = StreamCfg(label=f"temp={temp_b}  top_k={top_k_b}", temp=temp_b, top_k=top_k_b, color="magenta")
    _run_dual_streams(llm, prompt, prompt, max_tokens, cfg_a, cfg_b, track_diverge=True)


# ── MAIN MENU ─────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="baddle — neural token experiment CLI")
    parser.add_argument("-m", "--model",    help="model path or filename in models/")
    parser.add_argument("--no-gpu",         action="store_true")
    parser.add_argument("--gpu-layers",     type=int, default=-1)
    parser.add_argument("--ctx",            type=int, default=4096,
                        help="context size — 4096+ recommended for parallel/compare")
    args = parser.parse_args()

    console.print("\n[bold]baddle[/bold] — neural token experiment\n", justify="center")

    model_path = pick_model(args.model)
    gpu_layers = 0 if args.no_gpu else args.gpu_layers
    llm        = load_model(model_path, gpu_layers, args.ctx)

    mode = questionary.select(
        "Mode:",
        choices=[
            "step     — interactive token-by-token",
            "parallel — two different prompts, one forward pass",
            "compare  — one prompt, two sampling configs side-by-side",
        ],
    ).ask()
    if mode is None:
        sys.exit(0)

    if mode.startswith("step"):
        temp   = float(questionary.text("Temperature (0 = greedy):", default="0.0").ask() or "0.0")
        prompt = questionary.text("Prompt:").ask()
        if not prompt:
            sys.exit("empty prompt")
        console.print()
        step_mode(llm, prompt, temp, model_name=model_path.name)

    elif mode.startswith("parallel"):
        temp = float(questionary.text("Temperature:", default="0.7").ask() or "0.7")
        pa   = questionary.text("Prompt A:").ask()
        pb   = questionary.text("Prompt B:").ask()
        n    = int(questionary.text("Max tokens:", default="50").ask() or "50")
        if not pa or not pb:
            sys.exit("both prompts required")
        console.print()
        parallel_mode(llm, pa, pb, n, temp)

    else:  # compare
        prompt = questionary.text("Prompt (shared):").ask()
        if not prompt:
            sys.exit("empty prompt")
        temp_a = float(questionary.text("Config A — temperature:", default="0.0").ask() or "0.0")
        top_k_a = int(questionary.text("Config A — top_k:", default="1").ask() or "1")
        temp_b = float(questionary.text("Config B — temperature:", default="1.0").ask() or "1.0")
        top_k_b = int(questionary.text("Config B — top_k:", default="40").ask() or "40")
        n = int(questionary.text("Max tokens:", default="60").ask() or "60")
        console.print()
        compare_mode(llm, prompt, temp_a, top_k_a, temp_b, top_k_b, n)


if __name__ == "__main__":
    main()
