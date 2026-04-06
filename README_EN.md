# baddle

> AHI Protocol — Augmented Human Intelligence.
> Don't ask AI for an answer — control the thinking process.

**[Russian version](README.md)** | **[Installation](SETUP_EN.md)** | **[Vision](VISION.md)** | **[TODO](TODO.md)**

A thought graph with Bayesian confidence, Markov transitions and dialectical synthesis. Set a goal, press Run — system automatically explores the topic, verifies hypotheses, synthesizes results. Runs locally via llama.cpp or through API. Free, on a quantized 8B model.

---

## How it works

1. Set a **goal** — "Prove that procrastination is useful"
2. Press **Run**
3. System automatically:
   - Generates hypotheses
   - Gathers evidence for each
   - Verifies dialectically (thesis/antithesis/synthesis via embedding centroid)
   - Asks itself questions ("what did I miss?")
   - Collapses verified clusters
   - Repeats the cycle
4. Get a **structured essay** with argumentation

---

## Graph Operations

### Think
Generates N short independent thoughts on a topic. Each thought is a separate attempt, not a continuation. LLM auto-classifies type (hypothesis, fact, question, goal, action) and initial confidence.

### Elaborate
Deepens a specific thought. Unpacks a detail, consequence, or mechanism. Children automatically become **evidence** — LLM determines whether they support or contradict the parent hypothesis.

### Expand
Branches from a thought in a new direction. Not deepening, but a different angle on the same subject. Creates siblings, not children.

### Collapse
Synthesizes a cluster of thoughts into one node. From 5 separate ideas — one coherent paragraph. Two modes: merge (removes originals) and no-merge (adds synthesis as child, originals stay).

### Rephrase
Reformulates a thought. Preserves meaning, changes form. Useful when a hypothesis is weak due to poor phrasing, not content.

### Verify (Smart DC)
Dialectical check — the key mechanism. Three parallel generations:
1. **Thesis** — strongest argument FOR
2. **Antithesis** — strongest argument AGAINST
3. **Neutral** — context and conditions

Three texts -> three vectors in embedding space -> **centroid** (point of balance) -> synthesis closest to centroid. Confidence = cosine similarity of synthesis to centroid. Forced antithesis breaks echo chambers.

### Walk
Random Walk through the graph from a selected node. Uses transition probabilities on edges. Shows where a thought leads — which nodes most often end up as endpoints.

---

## Mathematical Foundation

### Bayesian Confidence

Every node has **confidence** (0-1):

```
P(H|E) = P(E|H) * P(H) / [P(E|H)*P(H) + P(E|~H)*(1-P(H))]
```

When evidence is added (supports/contradicts), confidence updates via Bayes. Alpha/Beta model tracks the balance of supporting and contradicting evidence.

### Markov Transitions

Every edge has **transition_prob**, normalized per outgoing:
- Cosine similarity of embeddings -> base weight
- Directed edges get bonus x1.5
- Hebb: on user navigation `tp += lr * (1 - tp)` — frequently used paths strengthen
- Trap detector: nodes with high incoming and low outgoing tp

### Temporal Links

Nodes created within 5 minutes get temporal links. Weight depends on time proximity (0.3-0.6).

### Clustering

Connected components on edges above threshold. Used for group collapse, hull visualization, and identifying isolated nodes.

---

## Tick Cycle — Automatic Thinking

`thinking.py` — autonomous thinking engine. On each step, analyzes the graph and decides what action to perform next.

### Classification

Input: all nodes, edges, directed children. Output:
- **hypotheses** — unverified claims
- **weak** — confidence <= 0.5
- **unverified** — confidence < stable_threshold
- **verified** — confidence >= stable_threshold
- **no_evidence** — hypotheses without child evidence
- **isolated** — nodes without edges

### Goal Navigation

`pick_toward_goal()` — BFS from each candidate to goal. Picks closest. Trap avoidance: skips traps. Exploration: if best was already tried — picks less obvious.

### Deep mode (by phases)

```
EXPLORE    if hypotheses < 5    -> Think (build mass)
DEEPEN     if bare hypotheses   -> Elaborate (add evidence)
REPHRASE   if 2+ children, weak -> Rephrase (reformulate)
VERIFY     if unverified exist  -> Smart DC (check)
META       if verified >= 5     -> Think ("what did I miss?")
COLLAPSE   if verified >= 5     -> Collapse (synthesize clusters)
EXPAND     if isolated exist    -> Expand (connect)
ASK        if questions < 3     -> Ask (probe)
SYNTHESIZE all verified         -> Stable (ready for final essay)
```

### Fast mode (by priority)

Same tools, but by priority instead of phases: fixes weakest first, converges when possible.

### Force Collapse

After N steps (configurable) — forced compression in batches of 5. Hard stop at 2N.

**Output:** essay / brief / list / none.

---

## Interface

Light theme (Notion-style). Four modes: **Graph** (primary), **Chat**, **Step**, **Parallel**.

### Graph mode
- **Topic + Think/Add** — enter topic, automatic thought generation
- **Save/Load** — server-side graph persistence, auto-save after every action
- **Actions bar** — Select, Collapse, Link, Flow, Time, Layout, Auto, Run, Undo, Save, Reset
- **SVG visualization** — drag, zoom, pan, color-coded by type and confidence
- **Detail panel** — per-token heatmap, confidence, type, alpha/beta, Walk, Verify
- **Context menu** — Expand, Elaborate, Rephrase, Verify, Walk, Evidence, Chat, Edit, Delete
- **Generation Studio** — batch variants for rephrase/elaborate/expand/collapse

### Chat mode
- Conversation with model, context sidebar (graph -> chat, chat -> graph)

### Step mode
- Token by token, top-10 candidates with probabilities, heatmap

### Parallel mode
- Two prompts side-by-side, compare mode (same prompt, different parameters)

---

## Settings

- **Local / API / Hybrid** — generation and embeddings separately
- **Embedding model** — separate model for similarity and centroid (nomic-embed-text)
- **Heatmap** — adjustable entropy scale

> Qwen3-8B for generation + nomic-embed-text for embeddings. Both run in parallel via LM Studio.

---

## Project Structure

```
baddle/
  ui.py                    # entry point (Flask)
  setup.py                 # installation
  src/
    main.py                # model, sampling, embeddings
    thinking.py            # autonomous thinking (tick, phases, BFS)
    graph_logic.py         # graph logic (nodes, edges, Bayes, SmartDC)
    graph_routes.py        # Flask routes for graph
    prompts.py             # system prompts (EN/RU)
    chat.py                # chat mode
    step.py                # step mode
    parallel.py            # parallel mode
    api_backend.py         # API client (OpenAI-compatible)
    server_backend.py      # llama-server
  static/
    css/style.css          # Notion-style theme
    js/                    # 6 JS modules (graph, chat, step, parallel, modes, settings)
  templates/index.html     # HTML markup
  graphs/                  # saved graphs
  models/                  # GGUF models
```

---

## Quick Start

```bash
git clone https://github.com/Volk-Arch/Baddle.git
cd Baddle
python setup.py        # installs dependencies + downloads llama-server
python ui.py           # opens http://localhost:7860
```

Details: [SETUP_EN.md](SETUP_EN.md)
