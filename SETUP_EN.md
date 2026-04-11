# Installation & Running

**[Русская версия →](SETUP.md)**

## Requirements

- Python 3.10+
- An OpenAI-compatible LLM endpoint (local or remote)

Baddle doesn't load models itself — all generation goes through an API. This
makes installation trivial and lets you use any endpoint: LM Studio, llama-server,
Ollama, OpenAI, Groq, Together, etc.

---

## Installation

```bash
git clone https://github.com/Volk-Arch/Baddle
cd Baddle
python setup.py        # installs flask + numpy
```

Or manually:

```bash
pip install -r requirements.txt
```

---

## Configuring an LLM endpoint

You need an OpenAI-compatible server. The easiest option is **LM Studio**:

1. Download [LM Studio](https://lmstudio.ai)
2. In LM Studio go to **Discover** and load a model, e.g. `Qwen3-8B` (Q4_K_M)
3. Load an embedding model: `text-embedding-nomic-embed-text-v1.5`
4. On the **Local Server** tab press **Start Server**. URL is typically `http://localhost:1234`
5. Launch Baddle, open **Settings** and set:
   - `api_url`: `http://localhost:1234`
   - `api_model`: chat model name from LM Studio (e.g. `qwen/qwen3-8b`)
   - `embedding_model`: embedding model name (if loaded)

### Alternative endpoints

| Endpoint | api_url |
|----------|---------|
| **LM Studio** | `http://localhost:1234` |
| **llama-server** | `http://localhost:8080` |
| **Ollama** (OpenAI-compat mode) | `http://localhost:11434` |
| **OpenAI** | `https://api.openai.com` (+ `api_key`) |
| **Groq** | `https://api.groq.com/openai` (+ `api_key`) |

For cloud endpoints — fill in `api_key`. For local ones — not needed.

### Choosing a model

**Chat models (GGUF for local use):**

| Model | Size (Q4) | Quality | When |
|--------|-----------|---------|------|
| Qwen3-8B | ~5 GB | good | primary choice, great balance |
| Qwen3-14B | ~9 GB | better | if you have VRAM |
| Llama-3.1-8B | ~5 GB | good | alternative to Qwen |
| Gemma-2-9B | ~5.5 GB | good | good for analysis |

**Embedding model** (for graph edges, SmartDC centroid, novelty check):

- `nomic-embed-text-v1.5` — 140 MB, fast, high quality
- `bge-large-en-v1.5` — 1.3 GB, better for English
- Any other with `/v1/embeddings` support

---

## Running

```bash
python ui.py                  # web UI at http://localhost:7860
python ui.py --port 8080      # different port
```

Browser opens automatically.

How to use → [README](README.md)

---

## Project structure

```
baddle/
├── ui.py                # Flask entry point
├── setup.py             # install (pip install flask numpy)
├── requirements.txt
├── src/
│   ├── main.py          # utilities: StreamCfg, cosine_similarity
│   ├── thinking.py      # autonomous thinking (tick, phases)
│   ├── graph_logic.py   # graph, Bayes, SmartDC, API generation
│   ├── graph_routes.py  # Flask routes for graph
│   ├── chat.py          # chat mode
│   ├── api_backend.py   # OpenAI-compatible HTTP client
│   └── prompts.py       # system prompts (EN/RU)
├── static/
│   ├── css/style.css
│   └── js/              # graph, chat, modes, settings
├── templates/index.html
├── graphs/              # saved graphs
└── settings.json        # API endpoint config
```

---

## Troubleshooting

**"API not configured"** — open Settings in the UI, fill in api_url and api_model, save.

**"Connection refused"** — make sure LM Studio is running and the server is on
(Local Server → Start).

**Embeddings not working** — you need a loaded embedding model. In LM Studio:
Discover → search "embed" → Load. Then in Settings set `embedding_model`.

**Slow generation** — in LM Studio check GPU offload (all layers on GPU if VRAM
allows). Baddle doesn't affect speed — it's just an HTTP client.
