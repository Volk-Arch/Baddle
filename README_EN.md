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

`thinking.py` — autonomous thinking engine. Models how humans think: brainstorm ideas, group similar ones, deepen each unique one, challenge them, repeat.

### Principle

Four tools, four phases. Each phase runs to completion before the next begins:

```
GENERATE    few ideas?              -> Think (build mass)
MERGE       similar nodes?          -> Collapse (merge BEFORE deepening)
ELABORATE   bare hypotheses?        -> Elaborate (add evidence)
DOUBT       unverified?             -> Smart DC (challenge)
GENERATE+   all verified?           -> Think ("what did I miss?", with context)
SYNTHESIZE  nothing new             -> Stable (final essay)
```

### Why this order

1. **GENERATE** first — need mass of diverse ideas before working with them
2. **MERGE** before elaborate — don't waste work on duplicates. "8B is cheaper" and "8B reduces costs" merge into one node BEFORE both get evidence
3. **ELABORATE** before doubt — a bare claim "8B is cheaper" verifies worse than a developed "8B is cheaper → inference $0.001 vs $0.03 → consumer GPU sufficient"
4. **DOUBT** last — verify what's already substantiated. SmartDC (thesis/antithesis/synthesis) works better on developed ideas
5. **GENERATE+** — after verifying everything, look for gaps. Model sees list of verified thoughts and generates missing aspects

### Classification

Every tick, all nodes are classified:
- **bare** — hypotheses with no evidence and not yet verified (need elaborate)
- **unverified** — confidence < stable_threshold (need doubt)
- **verified** — confidence >= stable_threshold (ready for merge or final)

Synthesis nodes (from collapse and SmartDC) are not considered bare — they're already processed.

### Convergence

The cycle converges naturally through three mechanisms:
- **Novelty check** — when generating new thoughts, embedding similarity is checked against existing nodes. If > 0.92, the thought is rejected. When the model exhausts the topic — all rejections → stop
- **Lineage tracking** — each collapse node stores `collapsed_from` (list of source indices). Merge won't collapse a node with its own ancestor — no re-grinding the same material
- **Phase completion** — each phase fully completes before the next. No infinite interleaving

### Infinite mode

No step limit. Cycle runs until the model exhausts the topic:
```
generate 10 → merge 6 into 3 → elaborate 3 → doubt 3 → verified →
generate+ ("what did I miss?") → 5 new → merge → elaborate → doubt →
generate+ → novelty reject → EXHAUSTED → final synthesis
```

Stronger model = more unique thoughts = longer cycle = deeper result.

### Force Collapse

With step limit: after N steps — forced compression in batches of 5. Hard stop at 2N.

**Output:** essay / brief / list / none. Essay supports batched mode (sections → final text).

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
