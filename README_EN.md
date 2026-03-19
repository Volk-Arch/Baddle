# baddle

> Don't ask AI for an answer — watch how it builds one, and take control of the process.

**[Русская версия →](README.md)**

A tool for experimenting with neural network text generation.
Lets you look inside the process — not just get an answer, but control it step by step.

Most LLM interfaces are a black box: you send a prompt, you get text back.
**baddle** opens that box: go token by token, see probability distributions,
edit text right in the generation window, compare how the same prompt behaves
with different settings — all in the browser.

With the `--server` flag, baddle uses the native `llama-server` from llama.cpp,
which can process **two prompts simultaneously** on the GPU — both generate
in parallel and take roughly the same time as one.

**[Installation & Setup →](SETUP_EN.md)**

---

## Modes

### `step` — token-by-token generation

![Step mode](images/step.jpg)

**Step mode** is the main research mode.
The model generates **one token at a time**. After each token you can see
the probability distribution (top-10), change temperature and top_k,
and continue generating.

The text in the generation window is **editable**. The `Edit` button enables
editing mode: you can append text (inject), trim excess, fix any fragment —
then press `Sync` so the model picks up the changes.

Tokens are highlighted with a **confidence heatmap** — green (model is confident),
yellow, red (high entropy, model is guessing).

---

### `parallel` — two prompts at once

![Parallel mode](images/parallel.jpg)

Two different prompts generate in parallel. The result is a live split-screen
with both streams updating in real time.

Try giving opposite framings — the model will confidently argue both positions.
Or two identical prompts with temp>0 — they diverge within 1–3 tokens,
the first random token determines the entire trajectory.

---

### `compare` — one prompt, two parameter sets

![Compare mode](images/compare.jpg)

One prompt, but **two different configs** (temperature, top_k). Both streams start
from identical tokens and diverge as soon as the parameters produce different samples.
The interface shows the exact divergence step.

On academic text the divergence comes late;
on creative text — immediately. This is the token where the distribution was wide enough
for different settings to make a different choice.

---

### `chat` — conversation with the model

![Chat mode](images/chat.jpg)

Chat with the model via chat template (ChatML / Jinja2). Configurable roles
(system prompt), temperature, token limit. The **Continue** button lets you
resume generation if the response was cut off by the token limit.

Responses are highlighted with a confidence heatmap — you can see where the model
is confident in its answer and where it's guessing.

---

### `graph` — graph thinking

![Graph mode](images/graph1.jpg)
![Graph mode](images/graph2.jpg)

An experimental non-linear generation mode. The model produces short thoughts
on a topic, and a graph of connections is built between them using **cosine similarity
on model embeddings**. Connected thoughts form clusters.

**Force-directed layout** — connected nodes attract, unconnected nodes repel.
The graph automatically finds a clear arrangement, visually highlighting clusters.
Nodes can be **dragged** with the mouse for manual grouping.

Clicking two nodes lets you **manually connect or disconnect** them —
manual links are shown as dashed lines.

A cluster can be **collapsed** — the model synthesizes related thoughts into a coherent
paragraph. The collapsed result becomes a new node in the graph, and the process can be
repeated, going deeper at each level.

Cycle: generation → filtering → clustering → collapse → repeat.

Works only in in-process mode (without `--server`).

---

### Hybrid mode: parallel/compare → step

After parallel or compare generation completes, a **→ Step** button appears for each
stream. It switches to step mode preserving the text and KV cache of the selected
stream. You can continue generating token by token with full control.
Works only in in-process mode (without `--server`).

---

### Common features

- **Confidence heatmap** — across all modes (step, parallel, compare, chat)
  tokens are colored by entropy: green → yellow → red.
  Shows where the model knew the answer and where it was guessing.
- **Roles** — presets from `roles.json`, shared across all modes. In step/parallel/compare
  used as prefix, in chat — as system message.
- **Seed** — for reproducibility. With the same seed and parameters the model
  produces identical results. Seed `-1` means random. Available in parallel and compare.
- **Token counter** — shows used / available context tokens.
