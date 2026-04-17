"""Assistant mode execution — actually run the graph for user messages.

Each primitive has an executor that takes user message and returns
a structured response with cards.

Response cards types:
  - text      : plain markdown text
  - dialectic : FOR/AGAINST/SYNTHESIS (from Smart DC)
  - comparison: options with winner (from XOR tournament)
  - bayesian  : prior → observations → posterior
  - synthesis : final essay
  - steps     : what the system did (visible thinking)
"""
import logging
import re
from typing import List, Dict, Optional

from .graph_logic import (
    _graph, _add_node, _graph_generate, _clean_thought, _ensure_embeddings,
    _bayesian_update_distinct, _d_from_relation,
)
from .modes import get_mode

log = logging.getLogger(__name__)

# Safety: cap number of items we'll process in a single /assist call
MAX_OPTIONS = 5
MAX_IDEAS = 5


def _parse_options(message: str, max_count: int = MAX_OPTIONS) -> List[str]:
    """Extract comparable options from message.

    Patterns:
      - "A или B или C"
      - "A, B, C"
      - "A vs B"
      - "A\nB\nC" (multiline)
    """
    # Multiline
    lines = [l.strip(" -•*1234567890.") for l in message.split("\n") if l.strip()]
    if len(lines) >= 2:
        return [l for l in lines if len(l) > 1][:max_count]

    # "или" / "vs" / ","
    text = message
    for sep in [" или ", " or ", " vs ", " versus "]:
        if sep in text.lower():
            parts = re.split(sep, text, flags=re.IGNORECASE)
            opts = [p.strip(" ?.!,") for p in parts if p.strip()]
            # Clean first part from question intro like "что лучше", "какую ..."
            if opts and any(kw in opts[0].lower() for kw in ["лучше", "какую", "какой", "what's", "which"]):
                # Only the object words after intro keyword
                m = re.search(r"(?:лучше|какую|какой|what's better|which)[\s:]+(.+)", opts[0], re.IGNORECASE)
                if m:
                    opts[0] = m.group(1).strip()
            return opts[:max_count]
    # Comma
    if "," in text:
        opts = [p.strip(" ?.!") for p in text.split(",") if p.strip()]
        if len(opts) >= 2:
            return opts[:max_count]

    return []


# ═══ Dispute (XOR dialectical) ═══════════════════════════════════════

def execute_dispute(message: str, lang: str = "ru") -> Dict:
    """Run Smart DC on the message — thesis/antithesis/synthesis."""
    from .dialectic import dialectic_flow
    result = dialectic_flow(message, lang=lang, temp=0.7, top_k=40, concise=True)

    intro_text = "Диалектический анализ:" if lang == "ru" else "Dialectical analysis:"
    return {
        "text": intro_text,
        "cards": [
            {
                "type": "dialectic",
                "thesis": result["thesis"],
                "antithesis": result["antithesis"],
                "neutral": result["neutral"],
                "synthesis": result["synthesis"],
                "confidence": 0.75,  # placeholder — smartdc route computes via embeddings
            }
        ],
        "steps": [
            "Сгенерировал тезис (за)" if lang == "ru" else "Generated thesis (for)",
            "Сгенерировал антитезис (против)" if lang == "ru" else "Generated antithesis (against)",
            "Нейтральный контекст" if lang == "ru" else "Neutral context",
            "Синтез трёх позиций" if lang == "ru" else "Synthesis of three perspectives",
        ],
    }


# ═══ Tournament (XOR comparative) ════════════════════════════════════

def execute_tournament(message: str, lang: str = "ru") -> Dict:
    """Compare options and pick winner via LLM-judge."""
    options = _parse_options(message)

    if len(options) < 2:
        # Not enough options to compare — ask user for them
        intro = ("Чтобы сравнить варианты, мне нужен список. "
                 "Напиши через запятую или одним списком." if lang == "ru"
                 else "To compare, I need a list. Write comma-separated or as lines.")
        return {
            "text": intro,
            "cards": [],
            "steps": [],
            "awaiting_input": True,
        }

    # Build comparison prompt (reuse /graph/compare logic directly)
    options_text = "\n".join(f"{i+1}. {o}" for i, o in enumerate(options))
    if lang == "ru":
        system = ("/no_think\nТы судья. Сравни варианты, выбери лучший. "
                  "Ответь СТРОГО в формате:\nЛучший: [номер]\n"
                  "Почему: [объяснение 2-3 предложения]\n"
                  "Risk: [главный риск выбора, одно предложение]")
    else:
        system = ("/no_think\nYou are a judge. Compare, pick best. "
                  "Format:\nBest: [number]\nWhy: [2-3 sentences]\nRisk: [main risk]")

    result, _ = _graph_generate(
        [{"role": "system", "content": system},
         {"role": "user", "content": options_text}],
        max_tokens=250, temp=0.5, top_k=40,
    )

    # Parse
    winner_num = None
    why = ""
    risk = ""
    for line in result.split("\n"):
        ls = line.strip().lower()
        if ls.startswith("лучший:") or ls.startswith("best:"):
            try:
                digits = "".join(c for c in line.split(":")[1] if c.isdigit())
                if digits:
                    winner_num = int(digits)
            except (ValueError, IndexError):
                pass
        elif ls.startswith("почему:") or ls.startswith("why:"):
            why = line.split(":", 1)[1].strip()
        elif ls.startswith("risk:") or ls.startswith("риск:"):
            risk = line.split(":", 1)[1].strip()

    winner_text = options[winner_num - 1] if winner_num and 1 <= winner_num <= len(options) else options[0]

    intro = "Сравнил варианты:" if lang == "ru" else "Compared options:"

    return {
        "text": intro,
        "cards": [
            {
                "type": "comparison",
                "options": options,
                "winner_idx": (winner_num - 1) if winner_num else 0,
                "winner_text": winner_text,
                "reason": why,
                "risk": risk,
            }
        ],
        "steps": [
            f"Нашёл {len(options)} вариантов" if lang == "ru" else f"Found {len(options)} options",
            "Сравнил по критериям" if lang == "ru" else "Compared by criteria",
            "LLM-судья выбрал лучший" if lang == "ru" else "LLM-judge picked winner",
        ],
    }


# ═══ Bayesian ═════════════════════════════════════════════════════════

def execute_bayes(message: str, lang: str = "ru") -> Dict:
    """Bayesian: estimate initial prior, ask for observations."""
    if lang == "ru":
        system = ("/no_think\nОцени начальную вероятность гипотезы (0.01-0.99) "
                  "на основе общих знаний, без наблюдений.\n"
                  "Ответь СТРОГО в формате:\nprior: число\nпочему: одно предложение")
    else:
        system = ("/no_think\nEstimate initial probability (0.01-0.99).\n"
                  "Format:\nprior: number\nwhy: one sentence")

    result, _ = _graph_generate(
        [{"role": "system", "content": system},
         {"role": "user", "content": f"Гипотеза: {message}"}],
        max_tokens=80, temp=0.3, top_k=40,
    )

    prior = 0.5
    why = ""
    for line in result.split("\n"):
        ls = line.strip().lower()
        if ls.startswith("prior:"):
            try:
                prior = float(ls.split(":")[1].strip())
                prior = max(0.01, min(0.99, prior))
            except ValueError:
                pass
        elif ls.startswith("почему:") or ls.startswith("why:"):
            why = line.split(":", 1)[1].strip()

    intro = ("Начальная оценка. Добавь наблюдения — буду обновлять вероятность."
             if lang == "ru" else "Initial estimate. Add observations to update.")

    return {
        "text": intro,
        "cards": [
            {
                "type": "bayesian",
                "hypothesis": message,
                "prior": prior,
                "prior_reason": why,
                "observations": [],
                "posterior": prior,
            }
        ],
        "steps": [
            "Оценил начальный prior" if lang == "ru" else "Estimated initial prior",
        ],
        "awaiting_observations": True,
    }


# ═══ Fan (brainstorm) ═══════════════════════════════════════════════

def execute_brainstorm(message: str, lang: str = "ru") -> Dict:
    """Pure idea generation, no verification."""
    from .prompts import _p

    system = _p(lang, "think")
    user_prompt = (f"{_p(lang, 'topic')}: {message}\n"
                   f"Сгенерируй 7 разных идей. Одна идея = одна строка. Без нумерации. Будь креативен."
                   if lang == "ru" else
                   f"Topic: {message}\nGenerate 7 different ideas. One per line. Be creative.")

    result, _ = _graph_generate(
        [{"role": "system", "content": system},
         {"role": "user", "content": user_prompt}],
        max_tokens=500, temp=0.95, top_k=80,
    )

    lines = [l.strip(" -•*1234567890.") for l in result.split("\n") if l.strip()]
    ideas = [_clean_thought(l, "") for l in lines if len(l) > 5][:7]

    intro = "Мозговой штурм:" if lang == "ru" else "Brainstorm:"

    return {
        "text": intro,
        "cards": [{"type": "ideas_list", "ideas": ideas}],
        "steps": [f"Сгенерировал {len(ideas)} идей" if lang == "ru" else f"Generated {len(ideas)} ideas"],
    }


# ═══ Rhythm (habit) ═════════════════════════════════════════════════

def execute_rhythm(message: str, lang: str = "ru") -> Dict:
    """Register a habit — simple tracking."""
    # Use user_state.json for streak tracking
    from .assistant import _load_state, _save_state
    state = _load_state()
    streaks = state.setdefault("streaks", {})
    # Take first meaningful word as habit key (simplified)
    habit_key = message[:40]
    streak = streaks.get(habit_key, 0)

    text = (f"Отмечено. Привычка: {habit_key}" if lang == "ru"
            else f"Registered. Habit: {habit_key}")

    return {
        "text": text,
        "cards": [{
            "type": "habit",
            "habit": habit_key,
            "streak": streak,
            "message": ("Запускай Run каждый день чтобы вести streak."
                        if lang == "ru" else "Run daily to track streak."),
        }],
        "steps": [],
    }


# ═══ Via-zones (generic: generate → distinct matrix → zone → render) ═════════

def _classify_zones(candidates: List[str], tau_in: float = 0.3, tau_out: float = 0.7) -> Dict:
    """Compute pairwise distinct() on candidates, classify into CONFIRM/EXPLORE/CONFLICT.

    Returns dict with pair lists per zone and dominance metrics.
    If embeddings unavailable, returns all-EXPLORE by default (neutral).
    """
    import numpy as np
    from .main import distinct
    from .api_backend import api_get_embedding

    confirm, explore, conflict = [], [], []
    vectors = []
    for c in candidates:
        emb = api_get_embedding(c)
        vectors.append(np.array(emb, dtype=np.float32) if emb else None)

    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            va, vb = vectors[i], vectors[j]
            if va is None or vb is None or va.size == 0 or vb.size == 0:
                continue
            d = distinct(va, vb)
            if d < tau_in:
                confirm.append((i, j, d))
            elif d > tau_out:
                conflict.append((i, j, d))
            else:
                explore.append((i, j, d))

    n_pairs = len(confirm) + len(explore) + len(conflict)
    total = max(1, n_pairs)
    return {
        "confirm": confirm, "explore": explore, "conflict": conflict,
        "confirm_ratio": len(confirm) / total,
        "explore_ratio": len(explore) / total,
        "conflict_ratio": len(conflict) / total,
        "has_data": n_pairs > 0,
    }


def execute_via_zones(message: str, lang: str = "ru", mode_id: str = "horizon") -> Dict:
    """Generic executor: brainstorm candidates, classify by distinct zones, render.

    No primitive switching — rendering picked by detected zone density:
      CONFIRM dense  → convergent synthesis card
      CONFLICT dense → dialectic card on furthest pair
      EXPLORE dense  → ideas_list with first-idea Smart DC
    """
    from .prompts import _p

    n_ideas = 3 if mode_id == "vector" else MAX_IDEAS
    system = _p(lang, "think")
    user_prompt = (f"{_p(lang, 'topic')}: {message}\n"
                   f"Сгенерируй {n_ideas} разных идей/аспектов. Одна идея = одна строка. Без нумерации."
                   if lang == "ru" else
                   f"Topic: {message}\nGenerate {n_ideas} different ideas/aspects. One per line.")

    result, _ = _graph_generate(
        [{"role": "system", "content": system},
         {"role": "user", "content": user_prompt}],
        max_tokens=400, temp=0.8, top_k=60,
    )
    lines = [l.strip(" -•*1234567890.") for l in result.split("\n") if l.strip()]
    ideas = [_clean_thought(l, "") for l in lines if len(l) > 5][:n_ideas]

    if len(ideas) < 2:
        # Not enough to classify — fall back to plain list
        return {
            "text": ("Вот что нашёл:" if lang == "ru" else "Here's what I found:"),
            "cards": [{"type": "ideas_list", "ideas": ideas}],
            "steps": [f"Сгенерировал {len(ideas)} идей" if lang == "ru" else f"Generated {len(ideas)} ideas"],
        }

    zones = _classify_zones(ideas)
    steps_base = [
        f"Сгенерировал {len(ideas)} идей" if lang == "ru" else f"Generated {len(ideas)} ideas",
        (f"Зоны: confirm={zones['confirm_ratio']:.0%} explore={zones['explore_ratio']:.0%} "
         f"conflict={zones['conflict_ratio']:.0%}"),
    ]

    # CONFLICT dense → pick furthest pair, run dialectic
    if zones["conflict"] and zones["conflict_ratio"] >= zones["confirm_ratio"] \
       and zones["conflict_ratio"] >= zones["explore_ratio"]:
        zones["conflict"].sort(key=lambda p: -p[2])
        i, j, d = zones["conflict"][0]
        dc = execute_dispute(f"{ideas[i]} vs {ideas[j]}", lang)
        dc["steps"] = steps_base + dc.get("steps", []) + [
            f"CONFLICT[d={d:.2f}] → диалектика между #{i} и #{j}"
            if lang == "ru" else f"CONFLICT[d={d:.2f}] → dialectic #{i} vs #{j}",
        ]
        return dc

    # CONFIRM dense → convergent synthesis
    if zones["confirm"] and zones["confirm_ratio"] >= zones["explore_ratio"]:
        agree_text = ("Идеи сходятся — они об одном:" if lang == "ru"
                      else "Ideas converge — they agree:")
        return {
            "text": agree_text,
            "cards": [{"type": "ideas_list", "ideas": ideas, "zone": "confirm"}],
            "steps": steps_base + [
                f"CONFIRM-кластер: {len(zones['confirm'])} пар близких"
                if lang == "ru" else f"CONFIRM cluster: {len(zones['confirm'])} close pairs",
            ],
        }

    # Default: EXPLORE dominant → ideas_list with Smart DC on first
    verified_first = None
    try:
        dc_result = execute_dispute(ideas[0], lang)
        verified_first = {
            "text": ideas[0],
            "synthesis": dc_result["cards"][0].get("synthesis", "") if dc_result.get("cards") else "",
        }
    except Exception as e:
        log.warning(f"[via_zones] DC on first idea failed: {e}")

    return {
        "text": ("Вот что нашёл:" if lang == "ru" else "Here's what I found:"),
        "cards": [{
            "type": "ideas_list",
            "ideas": ideas,
            "verified_first": verified_first,
            "zone": "explore",
        }],
        "steps": steps_base + [
            "Smart DC на первой идее" if lang == "ru" else "Smart DC on first idea",
        ],
    }


# ═══ Dispatcher ═════════════════════════════════════════════════════

def execute(mode_id: str, message: str, lang: str = "ru") -> Dict:
    """Mode → renderer map. Mode is a preset selector, not a primitive switch.

    Each mode_id maps directly to a renderer. Behavior inside renderers is
    distinct-based (zones), not primitive-branched.
    """
    try:
        if mode_id == "dispute":
            return execute_dispute(message, lang)
        if mode_id == "tournament":
            return execute_tournament(message, lang)
        if mode_id == "bayes":
            return execute_bayes(message, lang)
        if mode_id == "fan":
            return execute_brainstorm(message, lang)
        if mode_id == "rhythm":
            return execute_rhythm(message, lang)
        # General case: generate candidates, classify via distinct zones, render by zone
        if mode_id in ("horizon", "vector", "scout", "free", "scales",
                       "builder", "cascade", "pipeline", "race"):
            return execute_via_zones(message, lang, mode_id)
    except Exception as e:
        log.warning(f"[assist_exec] {mode_id} failed: {e}")
        return {
            "text": (f"Ошибка выполнения: {e}" if lang == "ru" else f"Execution error: {e}"),
            "cards": [],
            "steps": [],
            "error": str(e),
        }

    # Fallback: just the intro (no execution)
    return {
        "text": None,
        "cards": [],
        "steps": [],
    }
