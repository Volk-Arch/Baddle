"""baddle — 14 режимов мышления как компактные пресеты.

Runtime на режимы не switch-ится (всё через NAND-зоны в tick_nand.py). MODES
нужны для:
  - UI: селектор, поля формы, placeholder, intro
  - execute_via_zones: `renderer_style` определяет карточку
  - create_horizon(mode_id): preset precision + policy weights + target_surprise

Классификация режима из сообщения юзера — `classify_intent_llm` в
assistant.py (один LLM-вызов, не keyword-эвристики).
"""

# ── Shared policy templates (common distributions across modes) ─────────────

_P_BALANCED  = {"generate": 0.25, "merge": 0.25, "elaborate": 0.25, "doubt": 0.25}
_P_GENERATE  = {"generate": 0.5,  "merge": 0.1,  "elaborate": 0.1,  "doubt": 0.3}
_P_DOUBT     = {"generate": 0.1,  "merge": 0.2,  "elaborate": 0.2,  "doubt": 0.5}
_P_ELABORATE = {"generate": 0.2,  "merge": 0.2,  "elaborate": 0.3,  "doubt": 0.3}
_P_BUILDER   = {"generate": 0.1,  "merge": 0.2,  "elaborate": 0.3,  "doubt": 0.4}
_P_PIPELINE  = {"generate": 0.1,  "merge": 0.1,  "elaborate": 0.4,  "doubt": 0.4}
_P_SCALES    = {"generate": 0.2,  "merge": 0.3,  "elaborate": 0.2,  "doubt": 0.3}
_P_RACE      = {"generate": 0.3,  "merge": 0.1,  "elaborate": 0.2,  "doubt": 0.4}
_P_HORIZON   = {"generate": 0.3,  "merge": 0.2,  "elaborate": 0.2,  "doubt": 0.3}
_P_DISPUTE   = {"generate": 0.1,  "merge": 0.1,  "elaborate": 0.2,  "doubt": 0.6}


def _preset(precision, policy, target=0.3):
    return {"precision": precision, "policy": dict(policy), "target": target}


# ── Modes ───────────────────────────────────────────────────────────────────

# Каждая запись: (name_ru, name_en, goals_count, fields, placeholder_ru,
#                 placeholder_en, intro_ru, intro_en, renderer_style, preset)
_MODES = {
    # Free (manual)
    "free": ("Свободный", "Free (manual)", 0, ["topic"],
             "Тема или мысль...", "Topic or thought...",
             "Что хочешь сделать руками?", "What do you want to do manually?",
             "ideas", _preset(0.5, _P_BALANCED, 0.3)),

    # 0 goals — scout/wander
    "scout": ("Блуждание", "Wander", 0, [],
              "Просто начни думать...", "Just start thinking...",
              "Просто блуждаю по связям. Серендипити включено.",
              "Just wandering. Serendipity mode.",
              "ideas", _preset(0.3, _P_GENERATE, 0.5)),

    # 1 goal
    "vector": ("Фокус", "Focus", 1, ["goal"],
               "Цель: что нужно достичь?", "Goal: what to achieve?",
               "Одна цель, доведём до результата.", "Single goal, we'll finish it.",
               "ideas", _preset(0.7, _P_DOUBT, 0.15)),

    "rhythm": ("Привычка", "Habit", 1, ["goal", "interval"],
               "Привычка: что делать регулярно?", "Habit: what to do regularly?",
               "Запоминаю как привычку. Отслеживаю streak.",
               "Tracking as habit. Streak activated.",
               "habit", _preset(0.5, _P_ELABORATE, 0.2)),

    "horizon": ("Исследование", "Research", 1, ["goal"],
                "Тема: что исследовать?", "Topic: what to explore?",
                "Исследую вглубь. Покажу связи которые найду.",
                "Exploring deeply. I'll show connections found.",
                "ideas", _preset(0.4, _P_HORIZON, 0.3)),

    # AND (multi-goal, all required)
    "builder": ("Сборка", "Assembly", "2+", ["goals"],
                "Подзадачи (все обязательны):", "Subtasks (all required):",
                "Все части нужны. Собираю целое.", "All parts required. Assembling.",
                "cluster", _preset(0.6, _P_BUILDER, 0.2)),

    "pipeline": ("По шагам", "Step by step", "2+", ["goals"],
                 "Шаги по порядку:", "Steps in order:",
                 "По шагам. Перечисли что нужно сделать, и в каком порядке.",
                 "Step by step. List the steps in order.",
                 "cluster", _preset(0.6, _P_PIPELINE, 0.15)),

    "cascade": ("Приоритеты", "Priorities", "2+", ["goals"],
                "Задачи по приоритету:", "Tasks by priority:",
                "Расставлю по приоритету. Срочное первым.", "Prioritizing. Urgent first.",
                "cluster", _preset(0.6, _P_BUILDER, 0.2)),

    "scales": ("Баланс", "Balance", "2+", ["goals"],
               "Между чем балансировать?", "What to balance between?",
               "Балансирую между целями. Снимаю snapshot.", "Balancing. Taking snapshot.",
               "cluster", _preset(0.5, _P_SCALES, 0.25)),

    # OR (any option)
    "race": ("Любой вариант", "Any option", "2+", ["goals"],
             "Варианты (любой подойдёт):", "Options (any will do):",
             "Ищу первый подходящий вариант.", "Finding the first match.",
             "comparative", _preset(0.5, _P_RACE, 0.3)),

    "fan": ("Мозговой штурм", "Brainstorm", "2+", ["goal"],
            "Тема для мозгового штурма:", "Topic for brainstorming:",
            "Брейншторм идей без ограничений. Поехали.",
            "Brainstorming without limits. Let's go.",
            "ideas", _preset(0.3, _P_GENERATE, 0.5)),

    # XOR (pick one)
    "tournament": ("Выбор", "Choice", "2+", ["options", "criteria"],
                   "Варианты для сравнения:", "Options to compare:",
                   "Сравню варианты и выберу лучший. Дай мне список.",
                   "I'll compare and pick the best. Give me the options.",
                   "comparative", _preset(0.7, _P_DOUBT, 0.15)),

    "dispute": ("Дебаты", "Debate", "2+", ["positions"],
                "Противоречивые позиции:", "Contradictory positions:",
                "За и против, потом синтез. Запускаю диалектику.",
                "Pros and cons, then synthesis. Running dialectic.",
                "dialectical", _preset(0.5, _P_DISPUTE, 0.25)),

    # Bayesian
    "bayes": ("Байесовский", "Bayesian", 1, ["hypothesis"],
              "Гипотеза (что проверяем?):", "Hypothesis (what to test?):",
              "Проверяю гипотезу через наблюдения. Начальная вероятность?",
              "Testing hypothesis with observations. What's the prior?",
              "bayesian", _preset(0.6, _P_DOUBT, 0.2)),
}


DEFAULT_MODE = "free"


def _as_dict(mode_id: str, entry) -> dict:
    """Convert compact tuple → flat dict (public API для consumer'ов)."""
    (name, name_en, goals, fields, ph, ph_en, intro, intro_en, renderer, preset) = entry
    return {
        "id": mode_id,
        "name": name, "name_en": name_en,
        "goals_count": goals,
        "fields": list(fields),
        "placeholder": ph, "placeholder_en": ph_en,
        "intro": intro, "intro_en": intro_en,
        "renderer_style": renderer,
        "preset": preset,
    }


def get_mode(mode_id: str) -> dict:
    """Get mode config by ID. Falls back to `free`."""
    entry = _MODES.get(mode_id) or _MODES[DEFAULT_MODE]
    return _as_dict(mode_id if mode_id in _MODES else DEFAULT_MODE, entry)


def list_modes() -> list[dict]:
    """List all modes — для UI селектора."""
    return [_as_dict(k, v) for k, v in _MODES.items()]


# ── Elaborate hint — UX flavor, не логический switch ───────────────────────

_ELABORATE_HINTS = {
    "tournament": "мы сравниваем варианты для выбора '{goal}'. Раскрой плюсы, минусы, особенности.",
    "dispute":    "диалектика вокруг '{goal}'. Сторона: покажи аргументы.",
    "builder":    "часть задачи '{goal}'. Углуби детали реализации.",
    "pipeline":   "шаг в последовательности '{goal}'. Как выполнить именно этот шаг.",
    "cascade":    "приоритетная задача в '{goal}'. Что критично здесь.",
    "scales":     "одна из сторон баланса в '{goal}'. Её доля и вклад.",
    "race":       "один из вариантов для '{goal}'. Что делает его подходящим.",
    "fan":        "идея в штурме '{goal}'. Раскрой свободно.",
    "vector":     "цель — '{goal}'. Углуби этот аспект.",
    "horizon":    "исследование '{goal}'. Расширь понимание.",
    "rhythm":     "регулярное действие '{goal}'. Конкретный шаг.",
    "bayes":      "гипотеза '{goal}'. Какое наблюдение проверяет её.",
}


def get_elaborate_hint(goal_node: dict, lang: str = "ru") -> str:
    """Контекст-подсказка для elaborate-промпта на основе режима goal-ноды."""
    mode_id = goal_node.get("mode")
    goal_text = goal_node.get("text", "")
    template = _ELABORATE_HINTS.get(mode_id or "")
    if not template:
        return ""
    if lang == "ru":
        return "Контекст: " + template.format(goal=goal_text)
    return f"Context: goal is '{goal_text}'. Elaborate accordingly."


# ── Universal stop condition via distinct zones ────────────────────────────

def should_stop(cl: dict, graph: dict, horizon, goal_node: dict = None) -> dict:
    """Universal stop condition via distinct — no primitive/goal_type switch.

    A goal is resolved when:
      1. Goal с subgoals: avg_d между ними решает AND (все нужны) vs OR (первый)
      2. `d(goal, best_verified) < τ_in` — synthesis close to goal
      3. Strong convergence: 3+ verified, avg confidence > 85%, нет pending
      4. Novelty exhaustion: precision > 0.85 и нет работы

    Работает одинаково для всех 14 режимов. Пресет mode тюнит τ_in/τ_out/γ.
    """
    if not cl["hypotheses"] or goal_node is None:
        return {"resolved": False, "reason": ""}

    nodes = graph.get("nodes", [])
    embeddings = graph.get("embeddings", [])
    goal_idx = cl.get("goal_idx")
    subgoals = goal_node.get("subgoals") or []
    tau_in = getattr(horizon, "tau_in", 0.3)
    tau_out = getattr(horizon, "tau_out", 0.7)

    # ── Case 1: subgoals — emergent AND/OR via subgoal distinct zone ──
    if subgoals:
        import numpy as np
        from .main import distinct

        sub_confidences = []
        sub_vecs = []
        for sg in subgoals:
            if 0 <= sg < len(nodes):
                sub_confidences.append(nodes[sg].get("confidence", 0.5))
                emb = embeddings[sg] if sg < len(embeddings) else None
                if emb:
                    sub_vecs.append(np.array(emb, dtype=np.float32))

        avg_d = 0.5
        if len(sub_vecs) >= 2:
            sum_d = 0.0
            n_pairs = 0
            for a in range(len(sub_vecs)):
                for b in range(a + 1, len(sub_vecs)):
                    if sub_vecs[a].size and sub_vecs[b].size:
                        sum_d += distinct(sub_vecs[a], sub_vecs[b])
                        n_pairs += 1
            if n_pairs:
                avg_d = sum_d / n_pairs

        verified_count = sum(1 for c in sub_confidences if c >= 0.8)

        if sub_confidences:
            if avg_d > tau_out:
                # OR-like: первый verified побеждает
                if verified_count >= 1:
                    return {"resolved": True,
                            "reason": f"avg_d(subgoals)={avg_d:.2f}>τ_out: first verified ({verified_count}/{len(sub_confidences)})"}
            else:
                # AND-like: все нужны
                if verified_count >= len(sub_confidences):
                    return {"resolved": True,
                            "reason": f"avg_d(subgoals)={avg_d:.2f}≤τ_out: all {verified_count} verified"}

    # ── Case 2: synthesis близко к цели ──
    if goal_idx is not None and 0 <= goal_idx < len(embeddings) and cl["verified"]:
        import numpy as np
        from .main import distinct

        goal_emb = embeddings[goal_idx]
        if goal_emb:
            goal_vec = np.array(goal_emb, dtype=np.float32)
            if goal_vec.size:
                for v_idx, _ in cl["verified"]:
                    if 0 <= v_idx < len(embeddings) and embeddings[v_idx]:
                        v_vec = np.array(embeddings[v_idx], dtype=np.float32)
                        if v_vec.size:
                            d = distinct(goal_vec, v_vec)
                            if d < tau_in:
                                return {"resolved": True,
                                        "reason": f"d(goal,#{v_idx})={d:.2f}<τ_in={tau_in:.2f}"}

    # ── Case 3: convergence ──
    if not cl["unverified"] and not cl["bare"] and len(cl["verified"]) >= 3:
        avg = sum(n.get("confidence", 0.5) for _, n in cl["hypotheses"]) / len(cl["hypotheses"])
        if avg > 0.85:
            return {"resolved": True,
                    "reason": f"Convergence: {len(cl['verified'])} verified, avg {avg:.0%}"}

    # ── Case 4: novelty exhaustion ──
    precision = getattr(horizon, "precision", 0.5)
    if precision > 0.85 and not cl["bare"] and not cl["unverified"] and cl["verified"]:
        return {"resolved": True, "reason": f"Novelty exhausted (precision={precision:.2f})"}

    return {"resolved": False, "reason": ""}
