# baddle

> AHI Protocol — Augmented Human Intelligence. Don't ask AI for an answer — control the thinking process.

**[Русская версия →](README.md)**

A thought graph with Bayesian confidence, Markov transitions and dialectical synthesis. Runs locally via llama.cpp or through API. Not a chatbot — here you branch ideas, verify hypotheses, and the system learns with you.

**[Installation & Setup →](SETUP_EN.md)**

---

## Graph Thinking

The key mode. Enter a topic → model generates thoughts → connections form → clusters → collapse → repeat.

**Two modes of thinking:**
- **Divergent** — Think generates ideas, Expand branches
- **Convergent** — Collapse synthesizes a cluster, Elaborate deepens

### Bayesian Confidence (Stone 1)

Every node has **confidence** (0–1). Everything is determined automatically via LLM:

- **Auto-type + auto-confidence** — LLM classifies text (hypothesis/fact/question/evidence) and rates initial confidence in one call. "Ants are better than sparrows" → hypothesis 35%. "Earth is round" → fact 95%
- **Auto-evidence** — Expand/Elaborate children automatically become evidence. LLM determines supports/contradicts and strength. Parent confidence updates via Bayes
- **α/β model** — for hypotheses: α = sum of supports, β = sum of contradicts. Progress bar + evidence list sorted by strength ("What changes the mind?")
- **Manual evidence** — "+ Evidence" button, supports/contradicts, strength slider
- **Edge types** — similarity (grey), supports (green →), contradicts (red ⇢), temporal (cyan), directed (purple →)

### Markov Transitions (Stone 2)

Every edge has **transition_prob** — probability of traversal. Normalized, directed edges get a bonus.

- **Random Walk** — 🚶 Walk button: "where does this thought lead?" 50 simulations, top-3 endpoints
- **Trap detector** — nodes with high inflow, low outflow → red outline
- **Hebb learning** — navigating between nodes strengthens transition_prob. "Neurons that fire together, wire together"
- **Edge tooltip** — P-values, similarity, relation type

### Temporal Links (Stone 3)

Every node stores `created_at` and `last_accessed`.

- **Temporal links** — auto-connections between nodes created within 5 min. Cyan, hidden by default (⏰ Time button)
- **Timestamps** in detail panel

### Smart DC — Dialectical Synthesis (Stone 4)

**Two automatic thinking modes (🔄 Run):**

**Fast** — priority-based. Fixes weakest problem first, converges when possible.

**Deep** — phase-based. Processes ALL nodes through ALL phases. Doesn't stop until everything is examined.

Both use the same tools:
1. **Think** — generate ideas (10 at a time)
2. **Elaborate** — add evidence (α/β without changing confidence)
3. **Smart DC (Verify)** — dialectical check: thesis/antithesis/neutral → synthesis → confidence from centroid distance (embeddings)
4. **Ask** — "why do I think this?" (question-node, reveals assumptions)
5. **Rephrase** — reformulate if evidence doesn't help (max 1 per node)
6. **Expand** — branch isolated nodes
7. **Collapse** — synthesize verified clusters
8. **META** — "what did I miss?" (another Think round after verification)
9. **Summary** — final text → linked to goal (essay/brief/list/none)

Common mechanisms: BFS to goal (shortest path), exploration (if obvious fails → try less obvious), trap avoidance (bypass dead ends)
- **🔄 Run** — full automatic cycle: tick → action → ... until stable. Configurable steps, stable threshold, output format (essay/brief/list/none). Prompts for goal if none exists. At stable — final document (join all thoughts + conclusions) → linked to goal. Exploration: if best path fails — tries less obvious route
- **Types: goal / action** — goal = target state (point B), action = completed action. A→B navigation: system guides from current state to goal. Goal auto-links to all hypotheses
- **Context menu** — right-click: Expand, Elaborate, Rephrase, Verify, Walk, Evidence, Chat, Edit, node type, Delete

### Generation Studio

Universal modal: Rephrase, Elaborate, Expand, Collapse, Freeform. Batch generation of N variants, compare, Apply.

### Graph Interface

- Right-click → context menu, drag → reposition, scroll → zoom, drag background → pan
- Link mode, Undo (Ctrl+Z), Delete, Esc
- → Flow / free graph, ⟳ Layout, ↓ Save / ↑ Load
- threshold — live edge recalculation
- Thought list with numbers, types [H][E][F][Q], sorted by cluster
- Detail panel: per-token heatmap, confidence slider, source tracking, connected edges with P-values

---

## Other Modes

### `step` — token-by-token generation

One token at a time. Probability distribution (top-10), editable text, heatmap.

### `parallel` — two prompts / compare

Two prompts in parallel, each with temp/top_k. **compare** checkbox — one prompt, two configs, divergence badge.

### `chat` — conversation with the model

Chat template (ChatML / Jinja2). Continue, heatmap.
**Context sidebar** — context from graph (→ Chat), from responses (→ ctx), manual. **→ graph** — send chat text to graph.

---

## Common Features

- **Confidence heatmap** — tokens colored by entropy, adjustable scale
- **Roles** — presets from `roles.json`, EN/RU switcher
- **Settings** — Local / API / Hybrid, auto-load models from API, hot-swap, `settings.json`
- **Embedding model** — separate model for embeddings (similarity + Smart DC centroid). Dropdown in Settings from available API/local models
- **Similarity** — Embedding / Jaccard / Off, auto-fallback

> 💡 **Recommended**: main model (Qwen3-8B) for generation + separate embedding model (nomic-embed-text) for similarity and Smart DC centroid. Both run in parallel via LM Studio without conflicts.

---

📄 [Vision & Architecture](VISION.md) · 📋 [TODO](TODO.md) · 📝 [Article (AI perspective)](Article/ARTICLE_AI_VIEW.md)
