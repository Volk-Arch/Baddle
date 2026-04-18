"""baddle — 14 режимов мышления как UI-пресеты.

Runtime на режимы не switch-ится (всё через NAND-зоны в tick_nand.py). MODES
нужны для:
  - UI: что показать в селекторе, какие поля на форме, placeholder, intro
  - execute_via_zones: `renderer_style` определяет какую карточку вернуть
  - create_horizon(mode_id): preset precision + policy weights

Классификация режима из сообщения юзера — через `classify_intent_llm`
в assistant.py (один короткий LLM-вызов, не keyword-эвристики).
"""

MODES = {
    # ── Free mode ─────────────────────────────────────────────────────────────
    "free": {
        "name": "Свободный",
        "name_en": "Free (manual)",
        "goals_count": 0,
        "fields": ["topic"],
        "placeholder": "Тема или мысль...",
        "placeholder_en": "Topic or thought...",
        "description": "Ручной режим. Все инструменты доступны, autorun выключен.",
        "description_en": "Manual mode. All tools available, no autorun.",
        "tooltip": "Всё вручную, без автопилота. Все инструменты доступны",
        "intro": "Что хочешь сделать руками?",
        "intro_en": "What do you want to do manually?",
    },

    # ── 0 goals ──────────────────────────────────────────────────────────────
    "scout": {
        "name": "Блуждание",
        "name_en": "Wander",
        "goals_count": 0,
        "fields": [],
        "placeholder": "Просто начни думать...",
        "placeholder_en": "Just start thinking...",
        "description": "Свободное блуждание без цели. Поиск неожиданных связей.",
        "description_en": "Free exploration without a goal. Serendipity search.",
        "tooltip": "Без цели. Система блуждает и ищет неожиданные связи",
        "intro": "Просто блуждаю по связям. Серендипити включено.",
        "intro_en": "Just wandering. Serendipity mode.",
    },

    # ── 1 goal ───────────────────────────────────────────────────────────────
    "vector": {
        "name": "Фокус",
        "name_en": "Focus",
        "goals_count": 1,
        "fields": ["goal"],
        "placeholder": "Цель: что нужно достичь?",
        "placeholder_en": "Goal: what to achieve?",
        "description": "Одна конечная цель. Фокус до достижения.",
        "description_en": "Single finite goal. Focus until achieved.",
        "tooltip": "Одна цель, работаем пока не достигнем",
        "intro": "Одна цель, доведём до результата.",
        "intro_en": "Single goal, we'll finish it.",
    },
    "rhythm": {
        "name": "Привычка",
        "name_en": "Habit",
        "goals_count": 1,
        "fields": ["goal", "interval"],
        "placeholder": "Привычка: что делать регулярно?",
        "placeholder_en": "Habit: what to do regularly?",
        "description": "Повторяемая цель. Streak, тренд, snapshot.",
        "description_en": "Repeatable goal. Streak, trend, snapshot evaluation.",
        "tooltip": "Повторяемое действие. Отслеживает streak и тренд",
        "intro": "Запоминаю как привычку. Отслеживаю streak.",
        "intro_en": "Tracking as habit. Streak activated.",
    },
    "horizon": {
        "name": "Исследование",
        "name_en": "Research",
        "goals_count": 1,
        "fields": ["goal"],
        "placeholder": "Тема: что исследовать?",
        "placeholder_en": "Topic: what to explore?",
        "description": "Открытая цель. Бесконечное уточнение до исчерпания новизны.",
        "description_en": "Open-ended goal. Infinite refinement until novelty exhaustion.",
        "tooltip": "Изучаем тему вглубь, пока не исчерпаем новизну",
        "intro": "Исследую вглубь. Покажу связи которые найду.",
        "intro_en": "Exploring deeply. I'll show connections found.",
    },

    # ── AND ──────────────────────────────────────────────────────────────────
    "builder": {
        "name": "Сборка",
        "name_en": "Assembly",
        "goals_count": "2+",
        "fields": ["goals"],
        "placeholder": "Подзадачи (все обязательны):",
        "placeholder_en": "Subtasks (all required):",
        "description": "Все подцели обязательны, порядок неважен.",
        "description_en": "All subgoals required, any order.",
        "tooltip": "Все части обязательны, порядок неважен",
        "intro": "Все части нужны. Собираю целое.",
        "intro_en": "All parts required. Assembling.",
    },
    "pipeline": {
        "name": "По шагам",
        "name_en": "Step by step",
        "goals_count": "2+",
        "fields": ["goals"],
        "placeholder": "Шаги по порядку:",
        "placeholder_en": "Steps in order:",
        "description": "Все подцели по порядку. Каждая после предыдущей.",
        "description_en": "All subgoals in order. Each after previous.",
        "tooltip": "Строго по порядку, каждый шаг после предыдущего",
        "intro": "По шагам. Перечисли что нужно сделать, и в каком порядке.",
        "intro_en": "Step by step. List the steps in order.",
    },
    "cascade": {
        "name": "Приоритеты",
        "name_en": "Priorities",
        "goals_count": "2+",
        "fields": ["goals"],
        "placeholder": "Задачи по приоритету:",
        "placeholder_en": "Tasks by priority:",
        "description": "Все подцели по приоритету. Срочное первым.",
        "description_en": "All subgoals by priority. Urgent first.",
        "tooltip": "Всё нужно, но срочное и важное первым",
        "intro": "Расставлю по приоритету. Срочное первым.",
        "intro_en": "Prioritizing. Urgent first.",
    },
    "scales": {
        "name": "Баланс",
        "name_en": "Balance",
        "goals_count": "2+",
        "fields": ["goals"],
        "placeholder": "Между чем балансировать?",
        "placeholder_en": "What to balance between?",
        "description": "Пропорциональное распределение между целями. Бюджет, баланс.",
        "description_en": "Proportional allocation between goals. Budget, balance.",
        "tooltip": "Распределяем внимание между несколькими целями поровну",
        "intro": "Балансирую между целями. Снимаю snapshot.",
        "intro_en": "Balancing. Taking snapshot.",
    },

    # ── OR ───────────────────────────────────────────────────────────────────
    "race": {
        "name": "Любой вариант",
        "name_en": "Any option",
        "goals_count": "2+",
        "fields": ["goals"],
        "placeholder": "Варианты (любой подойдёт):",
        "placeholder_en": "Options (any will do):",
        "description": "Любая одна цель достаточна. Первая выигрывает.",
        "description_en": "Any one goal suffices. First to finish wins.",
        "tooltip": "Подойдёт любой вариант, берём первый найденный",
        "intro": "Ищу первый подходящий вариант.",
        "intro_en": "Finding the first match.",
    },
    "fan": {
        "name": "Мозговой штурм",
        "name_en": "Brainstorm",
        "goals_count": "2+",
        "fields": ["goal"],
        "placeholder": "Тема для мозгового штурма:",
        "placeholder_en": "Topic for brainstorming:",
        "description": "Набор идей без ограничений. Мозговой штурм.",
        "description_en": "Open-ended idea generation. Brainstorm.",
        "tooltip": "Набрасываем максимум идей без ограничений",
        "intro": "Брейншторм идей без ограничений. Поехали.",
        "intro_en": "Brainstorming without limits. Let's go.",
    },

    # ── XOR ──────────────────────────────────────────────────────────────────
    "tournament": {
        "name": "Выбор",
        "name_en": "Choice",
        "goals_count": "2+",
        "fields": ["options", "criteria"],
        "placeholder": "Варианты для сравнения:",
        "placeholder_en": "Options to compare:",
        "description": "Выбрать ровно одну из нескольких. Сравнение вариантов.",
        "description_en": "Pick exactly one from several. Compare options.",
        "tooltip": "Сравниваем варианты и выбираем лучший",
        "intro": "Сравню варианты и выберу лучший. Дай мне список.",
        "intro_en": "I'll compare and pick the best. Give me the options.",
    },
    "dispute": {
        "name": "Дебаты",
        "name_en": "Debate",
        "goals_count": "2+",
        "fields": ["positions"],
        "placeholder": "Противоречивые позиции:",
        "placeholder_en": "Contradictory positions:",
        "description": "Противоречивые утверждения. Диалектический синтез.",
        "description_en": "Contradictory claims. Dialectical synthesis.",
        "tooltip": "Сталкиваем позиции, ищем синтез",
        "intro": "За и против, потом синтез. Запускаю диалектику.",
        "intro_en": "Pros and cons, then synthesis. Running dialectic.",
    },

    # ── Bayesian ────────────────────────────────────────────────────────────
    "bayes": {
        "name": "Байесовский",
        "name_en": "Bayesian",
        "goals_count": 1,
        "fields": ["hypothesis"],
        "placeholder": "Гипотеза (что проверяем?):",
        "placeholder_en": "Hypothesis (what to test?):",
        "description": "Ввод гипотезы + наблюдения → обновление вероятности по Байесу.",
        "description_en": "Enter hypothesis + observations → Bayesian probability update.",
        "tooltip": "Вводишь гипотезу, добавляешь наблюдения, смотришь как меняется вероятность",
        "intro": "Проверяю гипотезу через наблюдения. Начальная вероятность?",
        "intro_en": "Testing hypothesis with observations. What's the prior?",
    },
}


# Renderer style per mode — picks card type in execute_via_zones.
# Один источник истины: какую карточку отдаёт режим.
# Specials: "habit" (external state), "bayesian" (unique prior/posterior flow).
RENDERER_STYLE = {
    "free":       "ideas",
    "scout":      "ideas",
    "vector":     "ideas",
    "horizon":    "ideas",
    "fan":        "ideas",
    "rhythm":     "habit",
    "bayes":      "bayesian",
    "tournament": "comparative",
    "race":       "comparative",
    "dispute":    "dialectical",
    "builder":    "cluster",
    "pipeline":   "cluster",
    "cascade":    "cluster",
    "scales":     "cluster",
}

# Splice into MODES dict so get_mode() returns it
for _mid, _style in RENDERER_STYLE.items():
    if _mid in MODES:
        MODES[_mid]["renderer_style"] = _style


# Default — free mode (manual, all tools, no autorun)
DEFAULT_MODE = "free"


def get_mode(mode_id: str) -> dict:
    """Get mode config by ID. Falls back to horizon."""
    return MODES.get(mode_id, MODES[DEFAULT_MODE])


def get_elaborate_hint(goal_node: dict, lang: str = "ru") -> str:
    """Get context hint for elaborate prompt based on goal's mode (preset, not switch)."""
    mode_id = goal_node.get("mode")
    goal_text = goal_node.get("text", "")
    if not mode_id:
        return ""

    # Mode → hint is a preset (UX flavor), not a logic switch.
    hints_by_mode = {
        "tournament": ("ru", f"Контекст: мы сравниваем варианты для выбора '{goal_text}'. Раскрой плюсы, минусы, особенности."),
        "dispute":    ("ru", f"Контекст: диалектика вокруг '{goal_text}'. Сторона: покажи аргументы."),
        "builder":    ("ru", f"Контекст: часть задачи '{goal_text}'. Углуби детали реализации."),
        "pipeline":   ("ru", f"Контекст: шаг в последовательности '{goal_text}'. Как выполнить именно этот шаг."),
        "cascade":    ("ru", f"Контекст: приоритетная задача в '{goal_text}'. Что критично здесь."),
        "scales":     ("ru", f"Контекст: одна из сторон баланса в '{goal_text}'. Её доля и вклад."),
        "race":       ("ru", f"Контекст: один из вариантов для '{goal_text}'. Что делает его подходящим."),
        "fan":        ("ru", f"Контекст: идея в штурме '{goal_text}'. Раскрой свободно."),
        "vector":     ("ru", f"Контекст: цель — '{goal_text}'. Углуби этот аспект."),
        "horizon":    ("ru", f"Контекст: исследование '{goal_text}'. Расширь понимание."),
        "rhythm":     ("ru", f"Контекст: регулярное действие '{goal_text}'. Конкретный шаг."),
        "bayes":      ("ru", f"Контекст: гипотеза '{goal_text}'. Какое наблюдение проверяет её."),
    }
    entry = hints_by_mode.get(mode_id)
    if not entry:
        return ""
    _, ru_text = entry
    if lang == "ru":
        return ru_text
    # Lightweight EN shim — keep tag only, LLM handles rest
    return f"Context: goal is '{goal_text}'. Elaborate accordingly."


def list_modes() -> list[dict]:
    """List all modes with IDs for UI selector."""
    return [{"id": k, **v} for k, v in MODES.items()]


def should_stop(cl: dict, graph: dict, horizon, goal_node: dict = None) -> dict:
    """Universal stop condition via distinct — no primitive/goal_type switch.

    A goal is resolved when:
      1. Its embedding is close (d < τ_in) to a verified synthesis node.
      2. For multi-subgoal goals: distinct-zone between subgoals decides AND-vs-OR
         emergently (close cluster = all needed, distant = any wins).
      3. Strong convergence: enough verified + high avg confidence + nothing pending.

    Works the same way for all 14 modes. Mode presets tune τ_in/τ_out/γ in Horizon.

    Returns {"resolved": bool, "reason": str}.
    """
    if not cl["hypotheses"] or goal_node is None:
        return {"resolved": False, "reason": ""}

    nodes = graph.get("nodes", [])
    embeddings = graph.get("embeddings", [])
    goal_idx = cl.get("goal_idx")
    subgoals = goal_node.get("subgoals") or []
    tau_in = getattr(horizon, "tau_in", 0.3)
    tau_out = getattr(horizon, "tau_out", 0.7)

    # ── Case 1: goal with subgoals — emergent AND/OR via subgoal distinct zone ──
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

        # Pairwise avg d between subgoals → tells us whether they're CONFIRM-cluster or CONFLICT
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
                # Subgoals mutually distant → OR-like: first verified wins
                if verified_count >= 1:
                    return {"resolved": True,
                            "reason": f"avg_d(subgoals)={avg_d:.2f}>τ_out: first verified ({verified_count}/{len(sub_confidences)})"}
            else:
                # Subgoals close → AND-like: all needed
                if verified_count >= len(sub_confidences):
                    return {"resolved": True,
                            "reason": f"avg_d(subgoals)={avg_d:.2f}≤τ_out: all {verified_count} verified"}

    # ── Case 2: distinct(goal, best verified) < τ_in — synthesis close to goal ──
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

    # ── Case 3: strong convergence — all hypotheses verified + high avg ──
    if not cl["unverified"] and not cl["bare"] and len(cl["verified"]) >= 3:
        avg = sum(n.get("confidence", 0.5) for _, n in cl["hypotheses"]) / len(cl["hypotheses"])
        if avg > 0.85:
            return {"resolved": True,
                    "reason": f"Convergence: {len(cl['verified'])} verified, avg {avg:.0%}"}

    # ── Case 4: novelty exhaustion — precision saturated + nothing pending ──
    precision = getattr(horizon, "precision", 0.5)
    if precision > 0.85 and not cl["bare"] and not cl["unverified"] and cl["verified"]:
        return {"resolved": True, "reason": f"Novelty exhausted (precision={precision:.2f})"}

    return {"resolved": False, "reason": ""}


