"""Assistant mode execution — рендерит карточки из зон distinct.

Единый путь: `execute_via_zones` генерирует N кандидатов, считает distinct
matrix, выбирает renderer по (zone × mode.renderer_style). Specials:
rhythm (external habit state) и bayes (уникальный prior/observation flow).

Response card types:
  - dialectic : FOR/AGAINST/SYNTHESIS (Smart DC)
  - comparison: options with winner (LLM-judge)
  - bayesian  : prior → observations → posterior
  - ideas_list: список идей с первым через Smart DC
  - habit     : streak + trend
  - clarify   : встречный вопрос (из /assist при ambiguous)
  - decompose_suggestion: inline предложение разбить на подзадачи
"""
import logging
import re
from typing import List, Dict, Optional

from .graph_logic import _graph, _add_node, _graph_generate, _clean_thought, _ensure_embeddings
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
        max_tokens=2000, temp=0.5, top_k=40,
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
        max_tokens=3000, temp=0.3, top_k=40,
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
        max_tokens=3000, temp=0.95, top_k=80,
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
    """Register a habit — tracks streak + snapshot history (v2 persistence).

    Each call counts as a habit tick. If today's already tracked, streak stays.
    Otherwise increments streak, appends to history, updates 7-day snapshot.
    """
    from .assistant import _load_state, _save_state
    from datetime import datetime

    state = _load_state()
    streaks = state.setdefault("streaks", {})
    habit_history = state.setdefault("habit_history", {})
    snapshots = state.setdefault("habit_snapshots", {})

    habit_key = message[:40]
    today = datetime.now().strftime("%Y-%m-%d")

    history = habit_history.setdefault(habit_key, [])
    last_entry = history[-1] if history else None
    streak = streaks.get(habit_key, 0)

    if not last_entry or last_entry.get("date") != today:
        streak += 1
        streaks[habit_key] = streak
        entry = {"date": today, "streak_at_time": streak}
        # Snapshot CognitiveState for trend viz
        try:
            from .horizon import get_global_state
            cs_metrics = get_global_state().get_metrics()
            chem = cs_metrics.get("neurochem", {})
            entry["state"] = {
                "norepinephrine": chem.get("norepinephrine"),
                "dopamine": chem.get("dopamine"),
                "serotonin": chem.get("serotonin"),
                "burnout": chem.get("burnout"),
                "hrv_coherence": (cs_metrics.get("hrv") or {}).get("coherence"),
            }
        except Exception:
            pass
        history.append(entry)
        # Bound history to ~1 year
        if len(history) > 365:
            history[:] = history[-365:]
        # 7-day snapshot
        last_7 = history[-7:]
        snapshots[habit_key] = {
            "last_update": today,
            "trend": [h.get("streak_at_time", 0) for h in last_7],
            "completion_7d": len(last_7),
        }

    _save_state(state)

    text = (f"Отмечено. Streak {streaks[habit_key]}: {habit_key}" if lang == "ru"
            else f"Registered. Streak {streaks[habit_key]}: {habit_key}")

    return {
        "text": text,
        "cards": [{
            "type": "habit",
            "habit": habit_key,
            "streak": streaks[habit_key],
            "today": history[-1].get("date") if history else today,
            "trend": snapshots.get(habit_key, {}).get("trend", []),
            "completion_7d": snapshots.get(habit_key, {}).get("completion_7d", 0),
            "message": ("7/7 дней подряд!" if snapshots.get(habit_key, {}).get("completion_7d", 0) >= 7
                       else ("Продолжай — вернись завтра." if lang == "ru"
                             else "Keep going — come back tomorrow.")),
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


def _resolve_renderer(style: str, zones: dict) -> str:
    """Zone × style → final renderer pick.

    Priority: style-preset если подходит доминирующей зоне; иначе zone-default.
    Returns one of: "dialectical" | "comparative" | "cluster" | "explore".
    """
    conflict_dom = zones["conflict"] and zones["conflict_ratio"] >= max(
        zones["confirm_ratio"], zones["explore_ratio"])
    confirm_dom = zones["confirm"] and zones["confirm_ratio"] >= zones["explore_ratio"]

    if style == "dialectical" and conflict_dom:  return "dialectical"
    if style == "comparative" and conflict_dom:  return "comparative"
    if style == "cluster"     and confirm_dom:   return "cluster"
    if conflict_dom:  return "dialectical"       # zone default
    if confirm_dom:   return "cluster"
    return "explore"


def _render_card(renderer: str, ideas: list, zones: dict, lang: str,
                 steps_base: list) -> Dict:
    """Единая точка сборки карточки. 4 renderer'а, одна диспетчеризация.

    Любая ветка возвращает {text, cards:[...], steps:[...]}.
    """
    if renderer == "dialectical":
        zones["conflict"].sort(key=lambda p: -p[2])
        i, j, d = zones["conflict"][0]
        dc = execute_dispute(f"{ideas[i]} vs {ideas[j]}", lang)
        dc["steps"] = steps_base + dc.get("steps", []) + [
            f"CONFLICT d={d:.2f} → диалектика #{i}↔#{j}" if lang == "ru"
            else f"CONFLICT d={d:.2f} → dialectic #{i}↔#{j}",
        ]
        return dc

    if renderer == "comparative":
        tn = execute_tournament(", ".join(ideas), lang)
        tn["steps"] = steps_base + tn.get("steps", []) + [
            "CONFLICT → LLM-судья выбирает" if lang == "ru"
            else "CONFLICT → LLM-judge picks",
        ]
        return tn

    if renderer == "cluster":
        return {
            "text": "Идеи собираются в одно:" if lang == "ru" else "Ideas converge:",
            "cards": [{"type": "ideas_list", "ideas": ideas, "zone": "confirm"}],
            "steps": steps_base + [
                f"CONFIRM: {len(zones['confirm'])} близких пар" if lang == "ru"
                else f"CONFIRM: {len(zones['confirm'])} close pairs",
            ],
        }

    # explore (default): ideas_list с Smart DC на первой
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
        "text": "Вот что нашёл:" if lang == "ru" else "Here's what I found:",
        "cards": [{"type": "ideas_list", "ideas": ideas,
                   "verified_first": verified_first, "zone": "explore"}],
        "steps": steps_base + [
            "Smart DC на первой идее" if lang == "ru" else "Smart DC on first idea",
        ],
    }


# ═══ Ask-gate: проверка нужны ли вводные ═══
# Некоторые режимы (tournament/race/dispute/builder/pipeline/cascade/scales)
# требуют 2+ inputs. Если юзер написал просто «сравни», не дав опций —
# возвращаем clarify card и спрашиваем конкретики. Без этого engine
# работает на пустом месте и даёт слабый результат.

MODES_NEED_OPTIONS = {
    "tournament": ("options", 2),      # сравнение — нужны ≥2 options
    "race":       ("options", 2),
    "dispute":    ("positions", 2),    # дебаты — ≥2 позиции / тезиса
    "builder":    ("subtasks", 2),     # сборка — ≥2 подзадачи
    "pipeline":   ("steps", 2),        # шаги по порядку — ≥2 шага
    "cascade":    ("tasks", 2),        # приоритеты — ≥2 задачи
    "scales":     ("dimensions", 2),   # баланс — ≥2 измерения
    "fan":        ("topic", 0),        # brainstorm — достаточно темы
}


def _need_inputs(message: str, mode_id: str, lang: str = "ru") -> Optional[Dict]:
    """Проверить достаточно ли вводных в message для данного mode.

    Возвращает None если OK (можно запускать engine) или clarify-card
    dict если требуется дополнить.
    """
    cfg = MODES_NEED_OPTIONS.get(mode_id)
    if not cfg:
        return None
    field_name, need = cfg
    if need <= 0:
        return None
    parsed = _parse_options(message, max_count=10)
    if len(parsed) >= need:
        return None

    # Недостаточно — готовим clarify
    hints_ru = {
        "tournament": "Какие варианты сравнить? Напиши через запятую, «или», или по одному в строке. Нужно минимум 2.",
        "race":       "Какие варианты рассмотреть? Перечисли через запятую или по строкам — подойдёт первый валидный.",
        "dispute":    "Какие противоречивые позиции обсудить? Минимум 2 тезиса через запятую или по строкам.",
        "builder":    "Из каких частей собрать? Перечисли подзадачи — каждая на новой строке.",
        "pipeline":   "Какие шаги в каком порядке? Перечисли по одному в строке.",
        "cascade":    "Какие задачи проставить по приоритету? Перечисли через запятую или построчно.",
        "scales":     "Между чем балансируем? Назови 2+ измерений через запятую.",
    }
    hints_en = {
        "tournament": "Which options to compare? List via commas, 'or', or one per line. At least 2.",
        "race":       "Which options to consider? Any valid one wins.",
        "dispute":    "Which positions to debate? At least 2 theses.",
        "builder":    "Which parts to assemble? List subtasks one per line.",
        "pipeline":   "Which steps in which order? One per line.",
        "cascade":    "Which tasks to prioritize? List them.",
        "scales":     "Balance between what? Name 2+ dimensions.",
    }
    q = (hints_ru if lang == "ru" else hints_en).get(mode_id,
        "Дополни запрос — мне нужно больше вводных." if lang == "ru" else "Need more inputs.")
    return {
        "text": q,
        "intro": q,
        "cards": [{
            "type": "mode_clarify",
            "mode_id": mode_id,
            "field": field_name,
            "have": len(parsed),
            "need": need,
            "question": q,
            "prompt_user": True,
        }],
        "steps": [f"Mode «{mode_id}» требует ≥{need} {field_name}, дано {len(parsed)} — спрашиваю"
                  if lang == "ru" else f"Mode '{mode_id}' needs ≥{need} {field_name}, got {len(parsed)}"],
        "awaiting_input": True,
        "graph_updated": False,
    }


# ═══ Deep execute: реальный 3-step pipeline с tools ═══
# Применяется ко всем 12 non-special modes (rhythm и bayes оставлены как
# specials из-за уникального state-flow). Создаёт реальные ноды в content
# graph, вызывает elaborate и SmartDC. Renderer финальной card адаптируется
# под mode.renderer_style.

HEAVY_MODES_DEEP = ("horizon", "dispute", "builder", "pipeline",
                     "tournament", "race", "cascade", "scales", "fan",
                     "scout", "vector", "free")


def _pairwise_diversity(idxs: list[int]) -> tuple[float | None, tuple[int, int] | None]:
    """Avg pairwise distinct distance для списка nodes + ближайшая пара.

    Возвращает (avg_d, (i, j)) где (i, j) — пара с наименьшим d (наиболее
    похожие). avg_d=None если нод без embedding'ов слишком много.
    Используется diversity guard'ом в execute_deep.
    """
    import numpy as np
    from .main import distinct
    nodes = _graph.get("nodes", [])
    vecs = []
    for i in idxs:
        if 0 <= i < len(nodes):
            emb = nodes[i].get("embedding")
            if emb:
                vecs.append((i, np.array(emb, dtype=np.float32)))
    if len(vecs) < 2:
        return None, None
    total, npairs = 0.0, 0
    best_d = 2.0
    best_pair = None
    for a in range(len(vecs)):
        for b in range(a + 1, len(vecs)):
            d = float(distinct(vecs[a][1], vecs[b][1]))
            total += d
            npairs += 1
            if d < best_d:
                best_d = d
                best_pair = (vecs[a][0], vecs[b][0])
    if npairs == 0:
        return None, None
    return total / npairs, best_pair


def _deepen_round(weak_idx: int, message: str, lang: str, system: str,
                   max_tokens: int, temp: float, top_k: int) -> dict:
    """Один раунд углубления на конкретной hypothesis: elaborate (2 evidence)
    → smartdc → confidence update. Возвращает detail dict для trace.

    Используется iterative deepening'ом execute_deep'а. Один раунд меняет
    уверенность ноды (вверх или вниз) на основе нового smartdc.
    """
    from .graph_logic import _graph_generate
    nodes = _graph.get("nodes", [])
    if not (0 <= weak_idx < len(nodes)):
        return {"skipped": "invalid_idx"}
    weak_text = nodes[weak_idx].get("text", "")
    conf_before = nodes[weak_idx].get("confidence", 0.5)

    # Elaborate
    ev_added = []
    try:
        from .graph_logic import parse_lines_clean, parse_smartdc_triple
        elab_prompt = (
            f"Гипотеза: «{weak_text}».\n"
            f"Контекст цели: {message[:150]}.\n"
            f"Дай 2 новых evidence (факты/механизмы/примеры), каждый на "
            f"своей строке, без нумерации. Избегай уже сказанного."
            if lang == "ru" else
            f"Hypothesis: «{weak_text}».\nContext: {message[:150]}.\n"
            f"Give 2 NEW concrete evidence. One per line."
        )
        res, _ = _graph_generate(
            [{"role": "system", "content": system},
             {"role": "user", "content": elab_prompt}],
            max_tokens=max_tokens, temp=temp, top_k=top_k,
        )
        lines = parse_lines_clean(res, min_len=8, max_n=2)
        directed = _graph["edges"].setdefault("directed", [])
        for et in lines:
            eidx = _add_node(et, depth=2, topic="", confidence=0.65,
                             node_type="evidence")
            _graph["nodes"][eidx]["evidence_target"] = weak_idx
            ev_added.append(eidx)
            directed.append([weak_idx, eidx])
    except Exception as e:
        return {"error": str(e)[:100], "phase": "elaborate"}

    # SmartDC → pro vs con → update confidence.
    # Confidence update — symmetric ±0.12 (асимметрия +0.12/-0.15 давала
    # upward drift). Length-heuristic признана хрупкой, но заменить её LLM-
    # judge'ем = +1 lm call per round; пока держим length как rough signal.
    try:
        dc_prompt = (
            f"Гипотеза: «{weak_text}».\n"
            f"Дай FOR/AGAINST/SYNTHESIS, три строки."
            if lang == "ru" else
            f"Hypothesis: «{weak_text}». Give FOR/AGAINST/SYNTHESIS."
        )
        dc, _ = _graph_generate(
            [{"role": "system", "content": system},
             {"role": "user", "content": dc_prompt}],
            max_tokens=max_tokens, temp=0.4, top_k=30,
        )
        fr, ag, sy = parse_smartdc_triple(dc)
        if fr and ag:
            # Symmetric conf shift: AGAINST длиннее → понижаем, иначе повышаем
            if len(ag) > len(fr) * 1.3:
                new_conf = max(0.1, conf_before - 0.12)
            else:
                new_conf = min(0.95, conf_before + 0.12)
        else:
            # Одна из сторон пустая — LLM не дал полный ответ. Не меняем conf
            # но зафиксируем warning чтобы upstream мог диагностировать.
            log.warning(f"[deepen] empty FOR/AGAINST на #{weak_idx}: "
                        f"fr={bool(fr)} ag={bool(ag)}")
            new_conf = conf_before
        _graph["nodes"][weak_idx]["confidence"] = new_conf
        return {
            "evidence_added": ev_added,
            "conf_before": round(conf_before, 2),
            "conf_after": round(new_conf, 2),
            # Без обрезки — UI сам решит layout; пусть видно весь текст
            "synthesis": sy or "",
            "thesis": fr or "",
            "antithesis": ag or "",
        }
    except Exception as e:
        return {"error": str(e)[:100], "phase": "smartdc",
                "evidence_added": ev_added}


def execute_deep(message: str, lang: str = "ru", mode_id: str = "horizon",
                 profile_hint: str = "", max_steps: int = None,
                 prev_session_indices: Optional[list[int]] = None) -> Dict:
    """Deep research через реальные tools — реальное использование
    того же engine что и в graph tab autorun, не замена одним brainstorm'ом.

    Глубина (число iteration rounds) берётся из settings per-mode:
      tournament/free/race — 2-3 (pairwise сам по себе дорогой)
      horizon — 5 (research, глубокое исследование)
      bayes — 7 (prior/observations/posterior циклы)

    Базовые шаги (всегда):
      1. Seed goal-node + brainstorm N hypotheses
      2. Diversity guard: если avg pairwise d < deep_diversity_min — pump
         между ближайшей парой для разброса (serendipity axis)
      3. Per-option evidence (pro+con) для comparative/cluster/dialectical,
         или single-hypothesis elaborate для остальных
      4. Pairwise SmartDC между options (comparative/dialectical), или
         single SmartDC на top hypothesis

    Iterative deepening (если mode_steps > 3): дополнительно раундов
    N-3, каждый = elaborate(weakest) → smartdc(weakest) → confidence update.
    Exit: max confidence > 0.85 ИЛИ stall (2 раунда без +conf) ИЛИ wall-step.

    Возвращает `deep_research` card: trace шагов + final synthesis +
    список созданных нод (юзер видит реальную работу системы).
    """
    from .prompts import _p
    from .graph_routes import _add_node as _add_node_route  # same module
    from .graph_logic import _compute_edges
    from .api_backend import get_neural_defaults, get_mode_depth

    # Thinking → cone в UI будет дышать пока идёт deep-research. Один trigger
    # покрывает и user-triggered /assist, и cognitive_loop._check_dmn_deep_research.
    try:
        from .cognitive_loop import get_cognitive_loop
        get_cognitive_loop().set_thinking("synthesize",
                                            {"mode_id": mode_id, "message": message[:80]})
    except Exception:
        pass

    _nd = get_neural_defaults()
    # Per-mode depth и infinite-режим (как в graph autorun Lab):
    #   • collapse_at — сколько «настоящих» iteration'ов до принудительного
    #     финального collapse (по умолчанию per-mode из settings)
    #   • infinite — если True, loop идёт до should_stop=STABLE, collapse_at
    #     работает как safety cap (hard_stop = collapse_at × 2)
    if max_steps is None:
        max_steps = get_mode_depth(mode_id)
    max_steps = max(1, min(200, int(max_steps)))
    try:
        from .api_backend import is_deep_infinite
        _infinite_mode = is_deep_infinite()
    except Exception:
        _infinite_mode = False
    # Эти значения override'ят hardcoded max_tokens если они больше дефолта
    _nd_maxtok = _nd.get("max_tokens") or 3000
    _nd_temp = _nd.get("temperature") or 0.7
    _nd_topk = _nd.get("top_k") or 40

    trace = []  # список шагов для UI

    # ── Step 1: Seed — goal + 5 hypotheses ──
    try:
        goal_idx = _add_node(message[:300], depth=0, topic="", confidence=0.5,
                             node_type="goal")
        _graph["nodes"][goal_idx]["mode"] = mode_id
        trace.append({"step": 1, "action": "seed_goal",
                      "detail": f"Цель добавлена как node #{goal_idx}",
                      "nodes_touched": [goal_idx]})
    except Exception as e:
        return execute_via_zones(message, lang, mode_id, profile_hint)

    # Для comparative/cluster modes: используем user-provided options как hypotheses
    # напрямую, не генерим свежие через LLM. Это сохраняет точность «сравни React,Vue,Svelte».
    system = _p(lang, "think")
    if profile_hint:
        system += "\n" + profile_hint + (
            "\nУчитывай эти предпочтения и ограничения."
            if lang == "ru" else "\nTake constraints into account.")

    try:
        mode_cfg = get_mode(mode_id) or {}
    except Exception:
        mode_cfg = {}
    style = mode_cfg.get("renderer_style", "ideas")

    user_options = _parse_options(message, max_count=7)
    use_user_options = (style in ("comparative", "cluster", "dialectical")
                        and len(user_options) >= 2)

    if use_user_options:
        ideas = [_clean_thought(o, "") for o in user_options if len(o) > 1][:7]
        log.info(f"[execute_deep] using {len(ideas)} user-provided options (style={style})")
    else:
        user_prompt = (f"{_p(lang, 'topic')}: {message}\n"
                       f"Сгенерируй 5 разных гипотез. Одна на строке. Без нумерации."
                       if lang == "ru" else
                       f"Topic: {message}\nGenerate 5 hypotheses. One per line.")
        try:
            result, _ = _graph_generate(
                [{"role": "system", "content": system},
                 {"role": "user", "content": user_prompt}],
                max_tokens=3000, temp=0.8, top_k=60,
            )
        except Exception as e:
            log.warning(f"[execute_deep] brainstorm failed: {e}")
            return execute_via_zones(message, lang, mode_id, profile_hint)
        from .graph_logic import parse_lines_clean
        ideas = parse_lines_clean(result, min_len=8, max_n=5)
    added_hyp = []
    for text in ideas:
        try:
            idx = _add_node(text, depth=1, topic="", confidence=0.5,
                            node_type="hypothesis")
            added_hyp.append(idx)
        except Exception:
            pass
    trace.append({"step": 2, "action": "brainstorm",
                  "detail": f"Сгенерировал {len(added_hyp)} гипотез",
                  "nodes_touched": added_hyp,
                  "texts": ideas[:5]})

    # Compute embeddings for distinct/similarity
    try:
        _ensure_embeddings([n.get("text", "") for n in _graph["nodes"]])
    except Exception:
        pass

    # ── Diversity guard: pairwise distinct между hypotheses ──
    # Если brainstorm вернул слипшиеся идеи (avg_d < min), synthesize
    # будет работать на иллюзии разнообразия. Запускаем pump между
    # ближайшей парой чтобы добавить альтернативную ось.
    avg_d = None
    if not use_user_options and len(added_hyp) >= 3:
        try:
            from .api_backend import get_depth_defaults
            div_min = float(get_depth_defaults().get("deep_diversity_min", 0.30))
        except Exception:
            div_min = 0.30
        try:
            avg_d, closest_pair = _pairwise_diversity(added_hyp)
        except Exception as e:
            log.debug(f"[execute_deep] diversity calc failed: {e}")
            closest_pair = None
        if avg_d is not None and avg_d < div_min and closest_pair:
            log.info(f"[execute_deep] diversity={avg_d:.2f} < {div_min} — "
                     f"pumping {closest_pair} for axis injection")
            try:
                from .pump_logic import pump
                pres = pump(closest_pair[0], closest_pair[1],
                            max_iterations=1, lang=lang,
                            temp=_nd_temp, top_k=_nd_topk)
                bridge_text = (pres or {}).get("bridge", "")
                if bridge_text and len(bridge_text) > 8:
                    bridge_idx = _add_node(bridge_text[:300], depth=1,
                                           topic="", confidence=0.55,
                                           node_type="hypothesis")
                    _graph["nodes"][bridge_idx]["diversity_seed"] = True
                    added_hyp.append(bridge_idx)
                    trace.append({
                        "step": 2.5, "action": "diversity_pump",
                        "detail": f"avg_d={avg_d:.2f} < {div_min}: добавил мост между "
                                  f"#{closest_pair[0]} и #{closest_pair[1]}",
                        "nodes_touched": [bridge_idx],
                        "pair": list(closest_pair),
                        "bridge_text": bridge_text,
                    })
                    try:
                        _ensure_embeddings([n.get("text", "") for n in _graph["nodes"]])
                    except Exception:
                        pass
            except Exception as e:
                log.debug(f"[execute_deep] diversity pump failed: {e}")

    # ── Deep branch per option (comparative/cluster/dialectical) ──
    # Каждая hypothesis получает **свой** evidence-branch: 2 pro + 2 con
    # для comparative/dialectical, или 2 "why needed" для cluster.
    # Плюс pairwise SmartDC между опциями → aggregate score per option.
    pair_dialectics = []
    option_scores = {i: 0 for i in added_hyp}
    if style in ("comparative", "cluster", "dialectical") and len(added_hyp) >= 2:
        directed = _graph["edges"].setdefault("directed", [])
        per_option_evidence: dict[int, list[int]] = {}
        for h_idx in added_hyp:
            h_text = _graph["nodes"][h_idx].get("text", "")[:200]
            per_option_evidence[h_idx] = []
            # Для cluster — evidence просто "почему важно/как делать"
            # Для comparative/dialectical — pro + con
            polarity_rounds = (
                [("почему хорошо", "pro", 0.7),
                 ("почему плохо",  "con", 0.3)]
                if style in ("comparative", "dialectical")
                else
                [("почему это важно", "why", 0.65),
                 ("как это сделать",  "how", 0.65)]
            )
            for prompt_tail, pol_label, base_conf in polarity_rounds:
                if lang == "ru":
                    p = (f"Тема: {message[:100]}\n"
                         f"Вариант: «{h_text}»\n"
                         f"Дай 2 конкретных аргумента {prompt_tail}. По одному на строке, "
                         f"без нумерации.")
                else:
                    p = (f"Topic: {message[:100]}\nOption: «{h_text}»\n"
                         f"Give 2 concrete arguments: {prompt_tail}. One per line.")
                try:
                    res, _ = _graph_generate(
                        [{"role": "system", "content": system},
                         {"role": "user", "content": p}],
                        max_tokens=1200, temp=0.6, top_k=40,
                    )
                    from .graph_logic import parse_lines_clean
                    ev_lines = parse_lines_clean(res, min_len=8, max_n=2)
                    for et in ev_lines:
                        # Маркируем тип argument через node_type=evidence +
                        # поле evidence_polarity для downstream визуализации
                        try:
                            eidx = _add_node(et, depth=2, topic="", confidence=base_conf,
                                             node_type="evidence")
                            _graph["nodes"][eidx]["evidence_polarity"] = pol_label
                            _graph["nodes"][eidx]["evidence_target"] = h_idx
                            per_option_evidence[h_idx].append(eidx)
                            directed.append([h_idx, eidx])
                        except Exception:
                            pass
                except Exception as e:
                    log.debug(f"[execute_deep] {pol_label} for #{h_idx} failed: {e}")

        total_ev = sum(len(v) for v in per_option_evidence.values())
        trace.append({
            "step": 3, "action": "elaborate_per_option",
            "detail": f"Для {len(added_hyp)} опций сгенерировал {total_ev} evidence (pro+con)",
            "nodes_touched": [e for lst in per_option_evidence.values() for e in lst],
            "per_option": {
                _graph["nodes"][h].get("text", "")[:30]: len(evs)
                for h, evs in per_option_evidence.items()
            },
        })

        # ── Pairwise SmartDC: каждая пара options → кто побеждает + reason
        if style in ("comparative", "dialectical"):
            for i, a in enumerate(added_hyp):
                for b in added_hyp[i+1:]:
                    a_t = _graph["nodes"][a].get("text", "")[:100]
                    b_t = _graph["nodes"][b].get("text", "")[:100]
                    if lang == "ru":
                        pp = (f"Сравни в контексте «{message[:80]}»:\n"
                              f"A: {a_t}\nB: {b_t}\n"
                              f"Выдай:\n"
                              f"WINNER: A или B\n"
                              f"REASON: одна строка почему")
                    else:
                        pp = (f"Compare in context «{message[:80]}»:\n"
                              f"A: {a_t}\nB: {b_t}\n"
                              f"Output:\nWINNER: A or B\nREASON: one line")
                    try:
                        pr, _ = _graph_generate(
                            [{"role": "system", "content": system},
                             {"role": "user", "content": pp}],
                            max_tokens=800, temp=0.3, top_k=20,
                        )
                        winner_letter = "?"
                        reason = ""
                        for line in pr.split("\n"):
                            L = line.strip()
                            if L.upper().startswith("WINNER:"):
                                winner_letter = L.split(":", 1)[1].strip()[:1].upper()
                            elif L.upper().startswith("REASON:") or L.upper().startswith("ПРИЧИНА:"):
                                reason = L.split(":", 1)[1].strip()
                        winner_idx = a if winner_letter == "A" else (b if winner_letter == "B" else None)
                        if winner_idx in option_scores:
                            option_scores[winner_idx] += 1
                        pair_dialectics.append({
                            "a": a, "a_text": a_t, "b": b, "b_text": b_t,
                            "winner": winner_idx, "winner_letter": winner_letter,
                            "reason": reason,
                        })
                    except Exception as e:
                        log.debug(f"[execute_deep] pair {a}x{b} failed: {e}")
            trace.append({
                "step": 4, "action": "pairwise_smartdc",
                "detail": f"Pairwise SmartDC: {len(pair_dialectics)} пар",
                "pairs": pair_dialectics,
                "scores": {_graph["nodes"][h].get("text", "")[:30]: s
                           for h, s in option_scores.items()},
            })
            # Update confidence based on pairwise wins
            total_pairs = max(1, len(added_hyp) - 1)
            for h_idx, wins in option_scores.items():
                _graph["nodes"][h_idx]["confidence"] = min(0.95, 0.3 + 0.5 * (wins / total_pairs))

    # ── Step 2 (single-hypothesis path): только для ideas-style ──
    # Для comparative/cluster/dialectical уже отработал per-option branch выше.
    skip_single_elaborate = style in ("comparative", "cluster", "dialectical") and len(added_hyp) >= 2
    if added_hyp and not skip_single_elaborate:
        # Выбираем первую из added (все conf=0.5 начальные — берём первую)
        weak_idx = added_hyp[0]
        weak_text = _graph["nodes"][weak_idx].get("text", "")
        elaborate_prompt = (
            f"Углуби мысль: «{weak_text}».\n"
            f"Дай 2 конкретных evidence (факт или механизм). По одному на строке."
            if lang == "ru" else
            f"Elaborate on: «{weak_text}».\nGive 2 concrete evidence. One per line."
        )
        try:
            ev_result, _ = _graph_generate(
                [{"role": "system", "content": system},
                 {"role": "user", "content": elaborate_prompt}],
                max_tokens=2000, temp=0.6, top_k=40,
            )
            from .graph_logic import parse_lines_clean
            ev_lines = parse_lines_clean(ev_result, min_len=8, max_n=2)
            ev_added = []
            for et in ev_lines[:2]:
                idx = _add_node(et, depth=2, topic="", confidence=0.65,
                                node_type="evidence")
                ev_added.append(idx)
            # Link evidence → hypothesis (directed edge)
            directed = _graph["edges"].get("directed", [])
            for eidx in ev_added:
                directed.append([weak_idx, eidx])
            trace.append({"step": 3, "action": "elaborate",
                          "detail": f"Углубил #{weak_idx} ({weak_text[:40]}): "
                                    f"+{len(ev_added)} evidence",
                          "nodes_touched": ev_added,
                          "parent": weak_idx,
                          "texts": ev_lines[:2]})
        except Exception as e:
            log.warning(f"[execute_deep] elaborate failed: {e}")
            trace.append({"step": 3, "action": "elaborate", "error": str(e)[:100]})

    # ── Step 3: SmartDC на top hypothesis → pro/contra/synthesis ──
    # Для comparative уже прошёл pairwise — пропускаем.
    synthesis_text = None
    confidence_t = None
    confidence_a = None
    skip_single_smartdc = style in ("comparative", "dialectical") and len(added_hyp) >= 2
    if added_hyp and not skip_single_smartdc:
        top_idx = added_hyp[0]
        top_text = _graph["nodes"][top_idx].get("text", "")
        dc_prompt = (
            f"Гипотеза: «{top_text}».\n"
            f"Выдай:\n"
            f"1) FOR (аргумент за)\n2) AGAINST (аргумент против)\n3) SYNTHESIS (что верно)\n"
            f"Формат: три строки начинающихся с FOR:/AGAINST:/SYNTHESIS:"
            if lang == "ru" else
            f"Hypothesis: «{top_text}».\nProvide FOR/AGAINST/SYNTHESIS. Three lines."
        )
        try:
            dc_result, _ = _graph_generate(
                [{"role": "system", "content": system},
                 {"role": "user", "content": dc_prompt}],
                max_tokens=3000, temp=0.4, top_k=30,
            )
            from .graph_logic import parse_smartdc_triple
            thesis, antithesis, synthesis_text = parse_smartdc_triple(dc_result)
            # Rough confidence heuristic: длины аргументов (более полный = более уверенный)
            confidence_t = min(0.9, 0.5 + len(thesis) / 200)
            confidence_a = min(0.9, 0.5 + len(antithesis) / 200)
            # Update hypothesis confidence: если synthesis ближе к thesis → повышаем
            if synthesis_text and thesis:
                # Простая эвристика: synthesis короче antithesis → thesis сильнее
                if len(antithesis) > len(thesis) * 1.3:
                    _graph["nodes"][top_idx]["confidence"] = 0.35
                else:
                    _graph["nodes"][top_idx]["confidence"] = 0.75
            trace.append({
                "step": 4, "action": "smartdc",
                "detail": f"SmartDC на #{top_idx}: FOR×{len(thesis)} AGAINST×{len(antithesis)}",
                "thesis": thesis,
                "antithesis": antithesis,
                "synthesis": synthesis_text if synthesis_text else "",
                "parent": top_idx,
            })
        except Exception as e:
            log.warning(f"[execute_deep] smartdc failed: {e}")
            trace.append({"step": 4, "action": "smartdc", "error": str(e)[:100]})

    # ── Iterative deepening (beyond base 3) ──
    # Базовые шаги (seed/brainstorm/elaborate/smartdc) считаем как ~3
    # раунда. При mode_steps=5 делаем +2 раунда углубления на weakest
    # hypothesis. Exit через `should_stop(cl, graph, horizon, goal_node)` —
    # тот же адаптивный алгоритм сходимости что в `tick_nand` STOP CHECK:
    #   (1) subgoals AND/OR, (2) synthesis близко к goal (d < τ_in),
    #   (3) convergence: 3+ verified, avg conf > 85%, нет pending,
    #   (4) novelty exhaustion: precision > 0.85 + нет работы.
    # Stall как safety net (если should_stop никогда не срабатывает).
    STALL_LIMIT = 3              # было 2 — слишком чувствительно при скачках
    STALL_DELTA = 0.015          # было 0.02 — 0.015 < типичный +0.12 но выше шума
    base_rounds = 3
    # В infinite mode: hardStop (safety cap) = max_steps×2. В limited: max_steps.
    # В обоих случаях break на should_stop=STABLE — natural convergence.
    if _infinite_mode:
        extra_rounds = max(0, max_steps * 2 - base_rounds)
    else:
        extra_rounds = max(0, max_steps - base_rounds)
    stall = 0
    def _avg_conf():
        vals = [_graph["nodes"][h].get("confidence", 0.5)
                for h in added_hyp if h < len(_graph["nodes"])]
        return sum(vals) / len(vals) if vals else 0.0
    prev_avg_conf = _avg_conf()

    # Готовим контекст для should_stop: snapshot horizon params + goal_node.
    # SNAPSHOT вместо live ссылки — чтобы background tick / DMN / другой
    # execute_deep не модифицировал tau_in/tau_out/precision пока идёт
    # наш iter loop (single-user реалистично редко, но multi-agent сценарий
    # без snapshot ломался бы). Читаем один раз → shim-объект с атрибутами.
    try:
        from .horizon import get_global_state
        from .thinking import classify_nodes
        from .modes import should_stop
        from .graph_logic import _compute_edges
        _live = get_global_state()
        class _HorizonSnap:
            __slots__ = ("tau_in", "tau_out", "precision")
        _horizon = _HorizonSnap()
        _horizon.tau_in = float(getattr(_live, "tau_in", 0.3))
        _horizon.tau_out = float(getattr(_live, "tau_out", 0.7))
        _horizon.precision = float(getattr(_live, "precision", 0.5))
        _goal_node = _graph["nodes"][goal_idx] if 0 <= goal_idx < len(_graph["nodes"]) else None
    except Exception as e:
        log.debug(f"[execute_deep] stop-check setup failed: {e}")
        _horizon = None
        _goal_node = None

    # Отмечаем как вышли из loop — natural STABLE vs hard cap.
    # В infinite mode после STABLE финальный synthesis = конвергенция.
    # В limited mode после cap = forced collapse.
    exit_reason = "max_steps_reached"
    stable_at_round = None
    for r in range(extra_rounds):
        # Per-round adaptive stop-check — использует те же 4 кейса что tick
        if _horizon is not None and _goal_node is not None:
            try:
                _edges = _compute_edges(_graph["nodes"], 0.91, "embedding")
                cl = classify_nodes(_graph["nodes"], _edges, _graph,
                                     stable_threshold=0.8)
                stop_res = should_stop(cl, _graph, _horizon, goal_node=_goal_node)
                if stop_res.get("resolved"):
                    exit_reason = "natural_convergence"
                    stable_at_round = r + 1
                    trace.append({"step": 5 + r, "action": "iterate_exit",
                                  "detail": f"should_stop: {stop_res.get('reason','')}"})
                    break
            except Exception as e:
                log.debug(f"[execute_deep] should_stop failed: {e}")

        # Pick weakest = most uncertain (conf ближе к 0.5 = максимальная
        # энтропия 50/50). Если все ноды уже solved, loop всё равно выйдет
        # на следующем should_stop или stall.
        candidates = [(h, abs(_graph["nodes"][h].get("confidence", 0.5) - 0.5))
                       for h in added_hyp if h < len(_graph["nodes"])]
        if not candidates:
            break
        candidates.sort(key=lambda p: p[1])    # min abs → ближе к 0.5 = uncertain
        weak_idx = candidates[0][0]
        round_res = _deepen_round(weak_idx, message, lang, system,
                                   max_tokens=int(_nd_maxtok),
                                   temp=float(_nd_temp),
                                   top_k=int(_nd_topk))
        ev_added = round_res.get("evidence_added") or []
        # System neurochem update от deepen: |Δconfidence| — мера новизны
        # (сильный update → высокий d → dopamine growth). Без этого система
        # почти не двигается в метриках даже при активной работе.
        try:
            conf_before = float(round_res.get("conf_before", 0.5))
            conf_after = float(round_res.get("conf_after", conf_before))
            d_val = min(1.0, abs(conf_after - conf_before) * 2.5)  # 0.12 delta → d=0.30
            if d_val > 0:
                from .horizon import get_global_state as _gs
                _gs().update_neurochem(d=d_val)
        except Exception:
            pass
        trace.append({
            "step": 5 + r, "action": "deepen",
            "detail": (f"Углубление #{r+1} на #{weak_idx}: "
                       f"{round_res.get('conf_before','?')} → "
                       f"{round_res.get('conf_after','?')}, "
                       f"+{len(ev_added)} evidence"),
            "parent": weak_idx,
            "nodes_touched": ev_added,
            "conf_before": round_res.get("conf_before"),
            "conf_after": round_res.get("conf_after"),
            "synthesis": round_res.get("synthesis", ""),
        })
        # Embeddings для новых evidence — чтобы distinct в following
        # should_stop читал с нод корректно.
        if ev_added:
            try:
                _ensure_embeddings([n.get("text", "") for n in _graph["nodes"]])
            except Exception:
                pass
        # Stall safety net — avg confidence по всем гипотезам не растёт
        # (мы deepen'им разные ноды раунд за раундом, лидер не меняется,
        # поэтому max бесполезен). Avg ловит agenda-wide progress.
        cur_avg_conf = _avg_conf()
        if cur_avg_conf <= prev_avg_conf + STALL_DELTA:
            stall += 1
        else:
            stall = 0
        prev_avg_conf = cur_avg_conf
        if stall >= STALL_LIMIT:
            exit_reason = "stall_safety"
            stable_at_round = r + 1
            trace.append({"step": 6 + r, "action": "iterate_exit",
                          "detail": f"stall safety: {stall} раунда avg_conf без progress "
                                    f"({cur_avg_conf:.2f})"})
            break
        # Если synthesis_text не был вычислен из единственного smartdc,
        # подхватываем последнюю синтезу из deepen-раунда.
        if not synthesis_text and round_res.get("synthesis"):
            synthesis_text = round_res["synthesis"]

    # ── Финальный collapse: session-ноды → один synthesis-абзац ──
    # Собираем indices созданные/затронутые в ЭТОЙ execute_deep сессии.
    # Плюс если юзер нажал «Продолжить тему» — добавляем prev_session_indices
    # (manual continuity), чтобы synthesis учитывал предыдущую беседу.
    session_indices: list[int] = []
    if prev_session_indices:
        # Фильтруем невалидные (могли быть удалены через consolidation)
        _total_nodes = len(_graph.get("nodes", []))
        session_indices.extend(
            i for i in prev_session_indices
            if isinstance(i, int) and 0 <= i < _total_nodes)
    try:
        if goal_idx not in session_indices:
            session_indices.append(goal_idx)
    except NameError:
        pass
    try:
        for h in (added_hyp or []):
            if h not in session_indices:
                session_indices.append(h)
    except NameError:
        pass
    # Все evidence-ноды созданные в trace (elaborate_per_option, deepen, ...)
    for t in trace:
        nt = t.get("nodes_touched") or []
        for idx in nt:
            if isinstance(idx, int) and idx not in session_indices:
                session_indices.append(idx)

    try:
        from .graph_logic import force_synthesize_top
        from .api_backend import get_deep_response_format, is_deep_batched
        _fmt = get_deep_response_format()
        _batched = is_deep_batched()
        # Для article format требуется больше токенов — поднимем cap
        _syn_tokens = int(_nd_maxtok)
        if _fmt == "article" and _syn_tokens < 6000:
            _syn_tokens = 6000
        final_syn = force_synthesize_top(
            n=12 if _fmt == "article" else 7,
            lang=lang, max_tokens=_syn_tokens,
            source_indices=session_indices if session_indices else None,
            fmt=_fmt, batched=_batched,
        )
        if final_syn and final_syn.get("text"):
            synthesis_text = final_syn["text"]
            src_n = len(final_syn.get("source_indices") or [])
            avg_c = final_syn.get('confidence', 0)
            # Wording зависит от того как вышли: natural convergence (STABLE)
            # vs forced (max_steps) vs stall safety. infinite-mode обычно
            # приводит к natural_convergence (или stall), limited — чаще к
            # max_steps_reached.
            if exit_reason == "natural_convergence":
                action_label = "converged"
                detail = (f"Сошлось за {stable_at_round} раундов · "
                          f"синтез из топ-{src_n} нод (средняя уверенность {avg_c:.0%})")
            elif exit_reason == "stall_safety":
                action_label = "converged"
                detail = (f"Плато достигнуто за {stable_at_round} раундов — "
                          f"дальше идеи не развиваются, пора сворачивать · "
                          f"синтез из топ-{src_n} нод")
            else:  # max_steps_reached
                action_label = "final_synthesis"
                detail = (f"Синтез по {src_n} мыслям сессии "
                          f"(средняя уверенность {avg_c:.0%})")
            trace.append({
                "step": 5 + extra_rounds + 1,
                "action": action_label,
                "detail": detail,
                "nodes_touched": [final_syn.get("node_idx")] if final_syn.get("node_idx") is not None else [],
                "synthesis": synthesis_text,
            })
    except Exception as e:
        log.debug(f"[execute_deep] final_collapse failed: {e}")

    # ── Финальная сборка карточки ── зависит от mode.renderer_style
    # Считаем все ноды трогаемые deepen/elaborate/per-option raунд'ами
    _countable_actions = ("elaborate", "elaborate_per_option", "deepen",
                           "diversity_pump")
    nodes_created = 1 + len(added_hyp) + sum(
        len(t.get("nodes_touched") or []) for t in trace
        if t.get("action") in _countable_actions)

    try:
        style = (get_mode(mode_id) or {}).get("renderer_style", "ideas")
    except Exception:
        style = "ideas"
    # Для comparative modes подменяем card type → comparison-style с pairwise evidence.
    # Для cluster modes → cluster-group. Для dialectical → dialectic с evidence.
    # Остальные → deep_research (universal trace-view).
    final_card_type = "deep_research"
    if style == "comparative":
        final_card_type = "deep_comparison"
    elif style == "cluster":
        final_card_type = "deep_cluster"
    elif style == "dialectical":
        final_card_type = "deep_dialectic"

    summary = (f"{mode_id.title()} deep: {len(trace)} шагов · {nodes_created} нод в графе"
               if lang == "ru" else
               f"{mode_id.title()} deep: {len(trace)} steps · {nodes_created} nodes")

    steps_human = []
    deepen_count = 0
    for t in trace:
        a = t.get("action", "?")
        if a == "seed_goal":
            steps_human.append(f"① Записал цель в граф")
        elif a == "brainstorm":
            steps_human.append(f"② Сгенерировал {len(t.get('nodes_touched',[]))} гипотез")
        elif a == "diversity_pump":
            steps_human.append(f"⊕ Diversity guard: добавил мост-ось "
                               f"({len(t.get('nodes_touched',[]))} новая нода)")
        elif a == "elaborate_per_option":
            steps_human.append(f"③ Pro+con на {len(t.get('per_option',{}))} опциях: "
                               f"+{len(t.get('nodes_touched',[]))} evidence")
        elif a == "elaborate":
            steps_human.append(f"③ Углубил одну слабую: +{len(t.get('nodes_touched',[]))} evidence")
        elif a == "pairwise_smartdc":
            steps_human.append(f"④ Pairwise SmartDC: {len(t.get('pairs',[]))} пар")
        elif a == "smartdc":
            steps_human.append(f"④ SmartDC thesis vs antithesis → синтез")
        elif a == "deepen":
            deepen_count += 1
            steps_human.append(f"↻ Раунд углубления #{deepen_count}: "
                               f"conf {t.get('conf_before','?')} → {t.get('conf_after','?')}")
        elif a == "iterate_exit":
            steps_human.append(f"✓ {t.get('detail','')}")

    # Winner и детальная разборка для comparative/dialectical
    winner_info = None
    if option_scores and any(option_scores.values()):
        winner_idx = max(option_scores, key=option_scores.get)
        winner_info = {
            "idx": winner_idx,
            "text": _graph["nodes"][winner_idx].get("text", ""),
            "score": option_scores[winner_idx],
            "max_score": len(added_hyp) - 1,
            "confidence": _graph["nodes"][winner_idx].get("confidence", 0.5),
        }

    # Hypothesis-level detail (для UI: опция + её evidence-трассы)
    hyp_detail = []
    for h_idx in added_hyp:
        if h_idx >= len(_graph["nodes"]):
            continue
        hnode = _graph["nodes"][h_idx]
        # Находим evidence nodes связанные с этой hypothesis через directed edges
        ev_for_h = []
        for en in _graph["nodes"]:
            if en.get("type") == "evidence" and en.get("evidence_target") == h_idx:
                ev_for_h.append({
                    "text": en.get("text", ""),
                    "polarity": en.get("evidence_polarity"),
                    "confidence": en.get("confidence", 0.5),
                })
        hyp_detail.append({
            "idx": h_idx,
            "text": hnode.get("text", ""),
            "confidence": hnode.get("confidence", 0.5),
            "evidence": ev_for_h,
            "score": option_scores.get(h_idx, 0),
        })

    # Clear thinking — deep-research завершён
    try:
        from .cognitive_loop import get_cognitive_loop
        get_cognitive_loop().clear_thinking()
    except Exception:
        pass

    return {
        "text": summary,
        "intro": summary,
        # Session indices — UI сохраняет их в localStorage и при кнопке
        # «Продолжить тему» передаёт обратно в следующий /assist вызов,
        # чтобы next synthesis учитывал мысли этой сессии.
        "session_indices": session_indices,
        "cards": [{
            "type": final_card_type,
            "mode_id": mode_id,
            "style": style,
            "trace": trace,
            "synthesis": synthesis_text or "",
            "goal_idx": goal_idx,
            "nodes_created": nodes_created,
            "hypothesis_count": len(added_hyp),
            "has_evidence": any(t.get("action", "").startswith("elaborate") and not t.get("error") for t in trace),
            "has_smartdc": any(t.get("action", "").startswith("smartdc") or t.get("action") == "pairwise_smartdc" for t in trace),
            "thesis": next((t.get("thesis") for t in trace if t.get("action") == "smartdc"), ""),
            "antithesis": next((t.get("antithesis") for t in trace if t.get("action") == "smartdc"), ""),
            "confidence_thesis": confidence_t,
            "confidence_anti": confidence_a,
            # Hypothesis texts (для comparative/cluster отрисовки как список)
            "hypotheses": [_graph["nodes"][i].get("text", "") for i in added_hyp if i < len(_graph["nodes"])],
            # Deep comparative detail
            "hypothesis_detail": hyp_detail,  # per-option evidence + score
            "pair_dialectics": pair_dialectics,  # pairwise SmartDC results
            "option_scores": {_graph["nodes"][h].get("text", "")[:40]: s for h, s in option_scores.items()},
            "winner": winner_info,
        }],
        "steps": steps_human,
        "graph_updated": True,
    }


def execute_via_zones(message: str, lang: str = "ru", mode_id: str = "horizon",
                      profile_hint: str = "") -> Dict:
    """Единый путь для всех 14 режимов.

    Алгоритм:
      1. Brainstorm N кандидатов (vector=3, остальные=MAX_IDEAS)
      2. Distinct-matrix → зоны CONFIRM / EXPLORE / CONFLICT
      3. `_resolve_renderer(style, zones)` → выбор финального рендерера
      4. `_render_card(renderer, ideas, zones, lang)` → карточка

    profile_hint — preferences/constraints из user_profile в текущей
    категории (food/work/health/...). Инжектится в brainstorm-промпт
    чтобы LLM не предлагал неподходящее.

    Specials (rhythm, bayes) не проходят сюда — они диспатчатся в execute().
    """
    from .prompts import _p
    from .modes import get_mode

    mode = get_mode(mode_id)
    style = mode.get("renderer_style", "ideas")

    # Comparative style + explicit options → tournament напрямую
    if style == "comparative":
        explicit = _parse_options(message)
        if len(explicit) >= 2:
            return execute_tournament(message, lang)

    n_ideas = 3 if mode_id == "vector" else MAX_IDEAS
    system = _p(lang, "think")
    if profile_hint:
        system = system + "\n" + profile_hint + (
            "\nУчитывай эти предпочтения и ограничения в ответе."
            if lang == "ru" else
            "\nTake these preferences and constraints into account."
        )
    user_prompt = (f"{_p(lang, 'topic')}: {message}\n"
                   f"Сгенерируй {n_ideas} разных идей/аспектов. Одна идея = одна строка. Без нумерации."
                   if lang == "ru" else
                   f"Topic: {message}\nGenerate {n_ideas} different ideas/aspects. One per line.")

    result, _ = _graph_generate(
        [{"role": "system", "content": system},
         {"role": "user", "content": user_prompt}],
        max_tokens=3000, temp=0.8, top_k=60,
    )
    lines = [l.strip(" -•*1234567890.") for l in result.split("\n") if l.strip()]
    ideas = [_clean_thought(l, "") for l in lines if len(l) > 5][:n_ideas]

    if len(ideas) < 2:
        return {
            "text": "Вот что нашёл:" if lang == "ru" else "Here's what I found:",
            "cards": [{"type": "ideas_list", "ideas": ideas}],
            "steps": [f"Сгенерировал {len(ideas)} идей" if lang == "ru"
                      else f"Generated {len(ideas)} ideas"],
        }

    zones = _classify_zones(ideas)
    steps_base = [
        f"Сгенерировал {len(ideas)} идей" if lang == "ru" else f"Generated {len(ideas)} ideas",
        (f"Зоны: confirm={zones['confirm_ratio']:.0%} explore={zones['explore_ratio']:.0%} "
         f"conflict={zones['conflict_ratio']:.0%} · style={style}"),
    ]
    renderer = _resolve_renderer(style, zones)
    return _render_card(renderer, ideas, zones, lang, steps_base)


# ═══ Dispatcher ═════════════════════════════════════════════════════

def execute(mode_id: str, message: str, lang: str = "ru",
            profile_hint: str = "",
            prev_session_indices: Optional[list[int]] = None) -> Dict:
    """Единый dispatcher. Все режимы → execute_via_zones кроме двух specials.

    Specials остались как pre-hooks потому что они трогают внешнее состояние
    (habit history в user_state.json) или имеют уникальный multi-step flow
    (bayes: prior → observations → posterior). Для этих двух renderer-dispatch
    нерелевантен — у них собственная логика.

    profile_hint — user_profile summary в релевантной категории;
    прокидывается в execute_via_zones. Specials его игнорируют.
    """
    try:
        # Specials: external state or unique flow
        if mode_id == "rhythm":
            return execute_rhythm(message, lang)
        if mode_id == "bayes":
            return execute_bayes(message, lang)

        # Ask-gate: если mode требует 2+ вводных и message не даёт их —
        # возвращаем clarify card. Сила baddle в глубине, не в быстрой
        # отработке пустого запроса.
        clarify = _need_inputs(message, mode_id, lang)
        if clarify is not None:
            return clarify

        # Остальные 12 non-special modes → deep pipeline. Renderer под style.
        if mode_id in HEAVY_MODES_DEEP:
            return execute_deep(message, lang, mode_id,
                                 profile_hint=profile_hint,
                                 prev_session_indices=prev_session_indices)
        # Fallback (если новый mode добавят в будущем без deep-интеграции)
        return execute_via_zones(message, lang, mode_id, profile_hint=profile_hint)
    except Exception as e:
        log.warning(f"[assist_exec] {mode_id} failed: {e}")
        return {
            "text": (f"Ошибка выполнения: {e}" if lang == "ru" else f"Execution error: {e}"),
            "cards": [],
            "steps": [],
            "error": str(e),
        }
