# baddle

> Don't ask AI for an answer — watch how it builds one, and take control of the process.

**[Русская версия →](README.md)**

Humans don't think linearly. We throw out points — facts, hypotheses, associations —
check if they connect, and if they do — go deeper. If not — gather more points
or restructure the links.

Baddle reproduces this process through LLMs. Not a chatbot — here you branch ideas,
collapse clusters, intervene in generation at the individual token level.
Everything runs locally via llama.cpp, no cloud required.

**[Installation & Setup →](SETUP_EN.md)**

---

## Modes

### `graph` — graph thinking

![Graph mode](images/graph1.jpg)
![Graph mode](images/graph2.jpg)

The key mode. Enter a topic — the model generates a batch of short thoughts.
Connections are built between them using **cosine similarity on model embeddings**.
Similar thoughts connect, forming clusters.

Two modes of thinking, implemented literally:

- **Divergent** — Think generates a batch of ideas, Expand branches from a specific node
- **Convergent** — Collapse merges a cluster into a coherent paragraph, Elaborate deepens a specific thought

**Cycle: generate thoughts → build connections → cluster → collapse → repeat.** Each collapse raises the level of abstraction.

**Interface:**
- **Right-click** on a node → context menu (Expand / Elaborate / Edit / Delete)
- **Hover** → full thought text
- **Drag** → reposition nodes
- **Link mode** → toggle button enables linking, click two nodes → connect/disconnect (dashed lines)
- **Convex hull** — semi-transparent boundary around clusters
- **Collapsed nodes** — square shape, larger (visually distinct from regular thoughts)
- **Edges** — thickness and opacity by connection strength
- **Scroll wheel** — zoom graph, **drag background** — pan
- **Ctrl+Z** — undo, **Delete** — remove node, **Esc** — deselect
- **⟳ Layout** — recalculate node positions
- **↓ Save / ↑ Load** — export/import graph as JSON (thoughts, edges, positions, clusters)
- **temp / top_k** — tune generation parameters directly in the graph interface
- **threshold** — live recalculation of edges and clusters when adjusting the similarity threshold
- **Collapse ▾** — short (paragraph), long (detailed essay), or custom token limit
- **Collapse prompt** — custom instruction for collapse ("compare", "find contradictions", "write a plan")
- **Collapse without merging** — "keep" checkbox: generates text but keeps original nodes (for testing different collapses)
- **Node entropy** — per-token heatmap in detail panel on click
- **→ Flow** — directed flow layout: nodes in columns by depth (Topic→Think→Expand→Elaborate). Toggle free graph / flow
- **Source tracking** — selecting a node shows which thought it originated from (purple "↳ from:")
- **Thought list** — sorted by cluster, click text to select node on graph, click node to highlight cluster in list
- **Topic nodes** — root topics as full diamond-shaped nodes (depth=-1), multiple topics in one graph, directed edges to children
- **Fan layout** — in free mode clusters occupy sectors, nodes don't overlap
- **✂ Select → Collapse / → Chat** — manual selection of arbitrary nodes, then Collapse or send to chat as context
- **☐ All** — select all nodes with one button
- **→ Chat with graph context** — selected nodes are sent to chat as context (option "structure" includes full graph: clusters, edges, weights)
- **To Graph** — manually add text to graph without links, or send collapse result back to graph
- **seed** — reproducible generation in graph mode
- **Heatmap scale** — adjustable entropy scale next to the heatmap checkbox

Works only in in-process mode (without `--server`).

---

### `step` — token-by-token generation

![Step mode](images/step.jpg)

The model generates **one token at a time**. After each token you see
the probability distribution (top-10), can change temperature and top_k,
and continue generating.

Text is **editable** — `Edit` enables editing, `Sync` applies changes.
The model picks up from there.

Tokens are highlighted with a **confidence heatmap** — green (confident),
yellow, red (high entropy, guessing).

---

### `parallel` — two prompts at once

![Parallel mode](images/parallel.jpg)

Two different prompts generate in parallel. Live split-screen,
both streams updating in real time. Each stream has its own **temp** and **top_k**.

**compare** checkbox — one prompt, two parameter sets. Both streams start
from identical tokens and diverge when parameters produce different samples.
A badge shows the exact divergence step.

With the `--server` flag, both prompts are processed simultaneously on GPU.

---

### `chat` — conversation with the model

![Chat mode](images/chat.jpg)

Chat via chat template (ChatML / Jinja2). Roles, temperature, token limit.
**Continue** resumes truncated responses. Heatmap shows confidence.

**Context sidebar** — right panel with a persistent context buffer:
- Add context from graph (→ Chat), from model responses (→ ctx), or manually
- Toggle or remove individual items
- **structure** checkbox — passes full graph structure (clusters, edges, weights)
- **→ graph** — send text from chat to graph without switching tabs

---

### Hybrid mode: parallel/compare → step

The **→ Step** button on each stream switches to step mode preserving
text and KV cache. In-process mode only.

---

### Common features

- **Confidence heatmap** — across all modes, tokens colored by entropy
- **Roles** — presets from `roles.json` (prefix in step/parallel/compare, system message in chat)
- **Language** — EN/RU switcher: roles, system prompts (including graph mode) in the selected language
- **Seed** — reproducible results (parallel, compare)
- **Token counter** — used / available context tokens
- **Settings** — choose how the app runs:
  - **Local** — everything on local model (llama.cpp)
  - **API** — OpenAI-compatible API (GPT-4, Claude, etc.) for graph and chat
  - **Hybrid** — per-component routing (e.g. graph via API, embeddings locally)
  - Auto-load model list from API, hot-swap local models without restart
  - Settings persist in `settings.json`
