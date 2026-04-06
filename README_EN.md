# baddle

> AHI Protocol — Augmented Human Intelligence.
> Don't ask AI for an answer — control the thinking process.

**[Русская версия →](README.md)** · **[Installation & Setup →](SETUP_EN.md)**

A thought graph with Bayesian confidence, Markov transitions and dialectical synthesis. Set a goal → system automatically explores the topic, verifies hypotheses, synthesizes results. Runs locally via llama.cpp or through API. Free, on a quantized 8B model.

---

## How it works

1. Set a **goal** — "Prove that procrastination is useful"
2. Press **🔄 Run**
3. System automatically:
   - Generates 10 hypotheses
   - Gathers evidence for each
   - Verifies dialectically (thesis/antithesis → synthesis via embedding centroid)
   - Asks itself questions ("what did I miss?")
   - Collapses verified clusters
   - Repeats the cycle
4. Get a **structured essay** with argumentation

Real example: "Prove that matter is not primary" → 51 steps → three rounds of Think/Verify/Collapse → essay with arguments from quantum physics, philosophy of consciousness, emergence.

---

## Thought Graph

### Bayesian Confidence

Every node has **confidence** (0–1). Determined automatically:

- **LLM classification** — type (hypothesis/fact/question/evidence/goal/action) + initial confidence in one call
- **Auto-evidence** — Elaborate children automatically become evidence. LLM determines supports/contradicts
- **Smart DC** — dialectical check: thesis + antithesis + neutral → centroid in embedding space → confidence from cosine similarity
- **α/β model** — supports vs contradicts, progress bar, "What changes the mind?"

### Markov Transitions

Every edge has **transition_prob**. Random Walk shows where thought leads. Trap detector. Hebb: frequently used paths strengthen.

### Temporal Links

Nodes from same session linked contextually. Timestamp on every node.

### A→B Navigation

Set goal → BFS finds shortest path → system guides along it. Exploration: if obvious fails → tries less obvious. Trap avoidance.

---

## Automatic Thinking

**Two modes (🔄 Run):**

| | Fast | Deep |
|---|---|---|
| Approach | By priority: fix weakest | By phases: process all |
| Think | When < 3 hypotheses | When < 5, ×10 |
| Ask | 1 question | 3 questions |
| META | When ≥ 3 verified | When ≥ 5 verified |

**Tools (both modes):**
Think → Elaborate → Verify (Smart DC) → Ask → Rephrase → Expand → Collapse → META → Summary

**Phase marker:** "Collapse at N" — after N steps, system starts forced compression in batches of 5. Hard stop at 2N.

**Verify mode:** replace (overwrites node) or expand (adds synthesis as child, original stays).

**Output format:** essay / brief / list / none.

---

## Interface

- **Topic + Add** — above graph, with type selector (auto/hypothesis/goal/fact/...)
- **Parameters** — collapsible panel (thoughts, similarity, threshold, temp, top_k, seed, max tokens)
- **Buttons**: Select / All / Collapse(badge) | Link / Flow / Time / Layout | Auto / Run▾ | Undo / Save / Load / Reset
- **Collapse** — dropdown: auto-clusters + manual selection → Studio
- **Detail panel** — per-token heatmap, confidence, type, α/β, edges, Walk, Verify
- **Context menu** — right-click: Expand, Elaborate, Rephrase, Verify, Walk, Evidence, Chat, Edit, type, Delete
- **Generation Studio** — universal modal for all generations with batch variants

---

## Other Modes

**step** — token by token, probability distribution, heatmap
**parallel** — two prompts + compare mode
**chat** — with context sidebar (→ Chat from graph, → Graph from chat)

---

## Settings

- **Local / API / Hybrid** — generation + embeddings separately
- **Embedding model** — separate model (nomic-embed-text) for similarity and centroid
- **Heatmap** — adjustable entropy scale

> 💡 Qwen3-8B for generation + nomic-embed-text for embeddings. Both run in parallel via LM Studio.

---

📄 [Vision & Architecture](VISION.md) · 📋 [TODO](TODO.md) · 📝 [Article (AI perspective)](Article/ARTICLE_AI_VIEW.md)
