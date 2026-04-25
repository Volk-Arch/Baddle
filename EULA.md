# End User License Agreement (EULA)

**Baddle v1.0 — Code Drop Edition**
**Copyright (c) 2026 Igor Kriusov <kriusovia@gmail.com>**

> Version 1. General terms. For commercial deals final jurisdiction
> and governing law are set in the purchase agreement.

---

## Important — Read Before Use

By downloading, installing, running or using Baddle (the "Software"),
you agree to the terms below. If you do not agree, do not use the Software.

This EULA applies **to both** the AGPL and Commercial license variants
(see [LICENSE](LICENSE)).

---

## 1. "AS-IS" Notice

**THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NON-INFRINGEMENT.**

The Licensor shall not be liable for any claim, damages or other
liability, whether in an action of contract, tort or otherwise, arising
from, out of or in connection with the Software or the use or other
dealings in the Software.

---

## 2. No Obligation of Updates or Support

Baddle is released as a **v1.0 code snapshot**. You receive exactly
what was built at time of release. No roadmap. No guaranteed patches.
No scheduled future versions.

The Licensor has **zero obligation** to:

- Fix bugs reported after release
- Address security vulnerabilities
- Provide technical assistance or consultation
- Respond to feature requests
- Maintain compatibility with future third-party systems (APIs,
  libraries, OS versions, LLM backends)
- Keep any hosted infrastructure online (community forum, etc.)

Community-driven support may exist (forum / Discord / forks), but
Licensor is under no obligation to participate.

---

## 3. Optional Paid Sessions

If you need hands-on setup or consultation, Licensor **may** offer
one-off paid sessions (rate by request, subject to availability).
These sessions are voluntary, not guaranteed, and do **not** transfer
any warranty or support obligation.

Contact: `kriusovia@gmail.com`.

---

## 4. Data & Privacy

Baddle runs fully **locally** by default — all user data (graphs,
state, HRV, goals, profile) is stored in `data/` and `graphs/` on
your machine. Licensor has no access to this data and no obligation
to protect it beyond what is documented in [docs/storage.md](docs/storage.md).

If you configure Baddle to use a cloud LLM provider (OpenAI, Anthropic,
etc.) via `settings.json`, **your data is sent to that third party
according to their terms** — Licensor is not responsible for this.

---

## 5. Third-Party Components

Baddle depends on third-party libraries (Flask, NumPy, etc.) — see
`requirements.txt`. Each is subject to its own license. Licensor
makes no warranty regarding third-party components.

LLM providers (LM Studio, llama.cpp, OpenAI-compatible APIs) are
not included and not warranted.

---

## 6. Changes to EULA

This EULA corresponds to the v1.0 release. Any future releases
(if made) may carry a different EULA. You agreed to the EULA
version shipped with the code you are using.

---

## 7. Governing Law

For non-commercial (AGPLv3) use, this EULA is interpreted alongside
the AGPLv3 license text and applicable local consumer-protection laws.

For commercial deals, specific governing law and jurisdiction are
set individually in the purchase agreement.

If any provision of this EULA is held unenforceable, the remainder
remains in full force.

---

## 8. Acceptance

You accept this EULA by any of:
- downloading the Software
- running `ui.py` or any Baddle binary
- embedding Baddle in your product
- purchasing a Commercial License

---

**Questions on commercial use:** kriusovia@gmail.com

---

*Baddle is a v1.0 code drop. The author has released it and moved on.
You own the code. Use it well, fork it, improve it, sell it — but
don't expect the author to fix your problems.*
