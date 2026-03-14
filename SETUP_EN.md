# Installation & Setup

**[Русская версия →](SETUP.md)**

## Requirements

- Python 3.10+
- GPU with CUDA (works on CPU too, just slower)
- GGUF model in the `models/` folder

---

## Installation

```bash
git clone https://github.com/Volk-Arch/Baddle
cd baddle
python setup.py        # detects CUDA, installs llama-cpp-python + downloads llama-server
pip install flask      # for the web UI
```

After installation, place a GGUF model in the `models/` folder (created automatically):

```
models/Qwen3-8B-Q4_K_M.gguf
```

### Which models work

Baddle works with models in **GGUF** format — the llama.cpp format for running
on CPU/GPU without PyTorch. Models can be downloaded from [Hugging Face](https://huggingface.co/models?library=gguf).

**Quantization** — models come in different sizes (quantizations):

| Quantization | Size (for 8B) | Quality | When to use |
|---|---|---|---|
| **Q4_K_M** | ~5 GB | good | default choice, balance of speed and quality |
| **Q5_K_M** | ~5.5 GB | better | if you have enough VRAM |
| **Q8_0** | ~8.5 GB | near lossless | for researching quantization quality |
| **F16** | ~16 GB | original | requires lots of VRAM, maximum quality |

**How to choose:**
- GPU with 8 GB VRAM — Q4_K_M for models up to 8B parameters
- GPU with 12 GB VRAM — Q5_K_M/Q8_0 for up to 8B, or Q4_K_M for up to 14B
- GPU with 24 GB VRAM — Q8_0 for up to 14B, or Q4_K_M for up to 32B

**Context** (`--ctx`) affects memory usage. Default is 4096 tokens.
Increasing to 8192+ requires additional VRAM.

`setup.py` automatically:
- Detects CUDA version
- Installs `llama-cpp-python` (with GPU if CUDA is available, otherwise CPU)
- Downloads the native `llama-server` binary to `llama-server/` (for parallel mode)

> **Installation takes 5–15 minutes** — this is normal.
> `llama-cpp-python` compiles from C++ sources.
> With CUDA, GPU kernels are also built.

---

## Running

```bash
python ui.py             # web interface at localhost:7860
python ui.py --server    # web interface + parallel server (recommended)
```

If there's one model in `models/` — it loads automatically. If there are several — you'll get a selection.

```bash
python ui.py -m Qwen3-8B-Q4_K_M.gguf   # choose a specific model
python ui.py --ctx 8192                 # increase context (default 4096)
python ui.py --gpu-layers 20            # partial GPU offload (default: all layers)
python ui.py --no-gpu                   # CPU only
python ui.py --port 8080                # different port
```

> These parameters are set **at launch only** — they determine how the model
> is loaded into memory and cannot be changed on the fly. To change them,
> restart `ui.py`.

| Parameter | Default | What it does |
|---|---|---|
| `-m` / `--model` | auto (only one in `models/`) | which GGUF model to load |
| `--ctx` | 4096 | context size in tokens; larger = more VRAM |
| `--gpu-layers` | -1 (all) | how many model layers to offload to GPU; -1 = all |
| `--no-gpu` | off | force CPU, ignore GPU |
| `--port` | 7860 | web interface port |
| `--server` | off | use llama-server for parallel generation |

---

## Generation parameters

Parameters are configured directly in the interface, each mode has its own:

### Temperature (temp)

Controls the "randomness" of the next token selection.

| Value | Effect |
|---|---|
| **0.0** | always picks the most probable token (greedy) |
| **0.3–0.7** | balance of predictability and diversity |
| **1.0** | standard probability distribution |
| **1.2–2.0** | unlikely tokens become viable — creative but unstable text |

Available in: step (`temp` field), parallel/compare (`temp A`/`temp B` fields), chat (`temp` field).
In step and chat you can change it on the fly — affects the next token.

### Top-K

Before choosing a token, the model keeps only the top-K most probable candidates;
the rest are discarded. Lower K = more predictable, higher = more diverse.

| Value | Effect |
|---|---|
| **1** | equivalent to temp=0 (always one option) |
| **10–40** | standard range |
| **100** | almost no filtering |

Available in: step (`top_k` field), parallel/compare (`top_k A`/`top_k B` fields).

### Seed

Fixes randomness. With the same seed and parameters the result is identical —
useful for A/B testing: change one parameter and see exactly its effect.
Value `-1` means random seed each time.

Available in: parallel, compare.

### Max tokens (chat)

Token limit per response. If the response was cut off — the **Continue** button resumes generation.
Default: 200.

### Where each parameter is available

| Parameter | Step | Parallel | Compare | Chat |
|---|---|---|---|---|
| temp | panel | A/B fields | A/B fields | panel |
| top_k | panel | A/B fields | A/B fields | — |
| seed | — | yes | yes | — |
| max tokens | Auto N | N field | N field | max field |

---

## Server mode

`--server` enables true parallelism — two prompts are processed simultaneously.

```bash
# Auto-start (server starts and stops automatically):
python ui.py --server

# Connect to an already running server:
python ui.py --server http://localhost:8080
```

`--server` without a URL will find `llama-server` in the `llama-server/` folder, start it,
and stop it on exit.

| | In-process (default) | Server mode (`--server`) |
|---|---|---|
| **How it works** | One request, then the other (sequential) | Both requests on GPU in parallel |
| **Speed** | ~2x of a single request | ~1x of a single request |
| **Step mode** | Available (logits access) | Not available |

**Step mode requires in-process** (direct logits access).
**For fast parallel/compare — use `--server`.**

#### Where llama-server comes from

`python setup.py` downloads the binary automatically from
[llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases).

If setup didn't download it (not Windows, no internet) — manually:

1. Go to https://github.com/ggml-org/llama.cpp/releases
2. Download `llama-bXXXX-bin-win-cuda-XX.X-x64.zip` (CUDA version matching yours)
3. Download `cudart-llama-bin-win-cuda-XX.X-x64.zip` (CUDA runtime DLLs)
4. Extract both into `llama-server/` inside the project

> Check your CUDA version: `nvidia-smi` → line `CUDA Version: XX.X`

---

## Controls

### Step mode

| Button | Action |
|---|---|
| **Init** | load the prompt into the model |
| **Next Token** | generate one token |
| **Auto** + N | automatically generate N tokens without stopping |
| **Stop** | stop Auto generation |
| **Edit** | enable text editing (inject, trim, fix) |
| **Sync** (Ctrl+Enter) | apply changes — model picks up the edited text |
| **Reset** | revert to the original prompt |

The **Next token probs** panel shows the top-10 probable next tokens with a histogram.
**temp** and **top_k** can be changed on the fly — they affect the next token.

### Parallel / Compare

| Button | Action |
|---|---|
| **Generate** | start generation of both streams |
| **Stop** | stop generation |
| **→ Step** | after generation — switch to step mode with the selected stream (in-process only) |

In compare mode, a badge shows the divergence step number.

### Chat

| Button | Action |
|---|---|
| **Send** | send a message |
| **Stop** | stop response generation |
| **Continue** | resume generation if the response was cut off by the token limit |
| **Reset** | clear history and reset model state |

Parameters: **temp** (temperature), **max** (token limit per response).

### Common to all modes

- **Confidence heatmap** — tokens are colored by entropy: green (confident) → yellow → red (guessing)
- **Token counter** — in the header, shows used / available (from n_ctx), turns red at >90%
- **Roles** — shared dropdown from `roles.json` for all modes. In step/parallel/compare — prefix, in chat — system message

---

## Project structure

```
baddle/
├── main.py            # engine: model, sampling, batch generation
├── ui.py              # web interface (Flask + SSE), entry point
├── server_backend.py  # HTTP client for llama-server
├── setup.py           # installer: llama-cpp-python + llama-server
├── models/            # GGUF models
└── llama-server/      # native binary (downloaded by setup.py)
```
