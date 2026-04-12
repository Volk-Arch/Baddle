"""baddle — 12 thinking modes built from 4 primitives.

Algebra of thinking:
  4 primitives: none / AND / OR / XOR
  + strategies: unordered / seq / priority / balance (over AND)
                comparative / dialectical (over XOR)
  + goal types: finite / repeatable / open
  = 12 modes

Each mode config defines:
  - primitive + strategy → how to process goals
  - goal_type → when to stop
  - fields → what UI fields to show
  - description → human-readable explanation
"""

MODES = {
    # ── Free mode ─────────────────────────────────────────────────────────────
    "free": {
        "name": "Свободный",
        "name_en": "Free (manual)",
        "primitive": None,
        "strategy": None,
        "goal_type": None,
        "goals_count": 0,
        "fields": ["topic"],
        "placeholder": "Тема или мысль...",
        "placeholder_en": "Topic or thought...",
        "description": "Ручной режим. Все инструменты доступны, autorun выключен.",
        "description_en": "Manual mode. All tools available, no autorun.",
        "tooltip": "Всё вручную, без автопилота. Все инструменты доступны",
    },

    # ── 0 goals ──────────────────────────────────────────────────────────────
    "scout": {
        "name": "Блуждание",
        "name_en": "Wander",
        "primitive": None,
        "strategy": None,
        "goal_type": None,
        "goals_count": 0,
        "fields": [],
        "placeholder": "Просто начни думать...",
        "placeholder_en": "Just start thinking...",
        "description": "Свободное блуждание без цели. Поиск неожиданных связей.",
        "description_en": "Free exploration without a goal. Serendipity search.",
        "tooltip": "Без цели. Система блуждает и ищет неожиданные связи",
    },

    # ── 1 goal ───────────────────────────────────────────────────────────────
    "vector": {
        "name": "Фокус",
        "name_en": "Focus",
        "primitive": "focus",
        "strategy": None,
        "goal_type": "finite",
        "goals_count": 1,
        "fields": ["goal"],
        "placeholder": "Цель: что нужно достичь?",
        "placeholder_en": "Goal: what to achieve?",
        "description": "Одна конечная цель. Фокус до достижения.",
        "description_en": "Single finite goal. Focus until achieved.",
        "tooltip": "Одна цель, работаем пока не достигнем",
    },
    "rhythm": {
        "name": "Привычка",
        "name_en": "Habit",
        "primitive": "focus",
        "strategy": None,
        "goal_type": "repeatable",
        "goals_count": 1,
        "fields": ["goal", "interval"],
        "placeholder": "Привычка: что делать регулярно?",
        "placeholder_en": "Habit: what to do regularly?",
        "description": "Повторяемая цель. Streak, тренд, snapshot.",
        "description_en": "Repeatable goal. Streak, trend, snapshot evaluation.",
        "tooltip": "Повторяемое действие. Отслеживает streak и тренд",
    },
    "horizon": {
        "name": "Исследование",
        "name_en": "Research",
        "primitive": "focus",
        "strategy": None,
        "goal_type": "open",
        "goals_count": 1,
        "fields": ["goal"],
        "placeholder": "Тема: что исследовать?",
        "placeholder_en": "Topic: what to explore?",
        "description": "Открытая цель. Бесконечное уточнение до исчерпания новизны.",
        "description_en": "Open-ended goal. Infinite refinement until novelty exhaustion.",
        "tooltip": "Изучаем тему вглубь, пока не исчерпаем новизну",
    },

    # ── AND ──────────────────────────────────────────────────────────────────
    "builder": {
        "name": "Сборка",
        "name_en": "Assembly",
        "primitive": "and",
        "strategy": "unordered",
        "goal_type": "finite",
        "goals_count": "2+",
        "fields": ["goals"],
        "placeholder": "Подзадачи (все обязательны):",
        "placeholder_en": "Subtasks (all required):",
        "description": "Все подцели обязательны, порядок неважен.",
        "description_en": "All subgoals required, any order.",
        "tooltip": "Все части обязательны, порядок неважен",
    },
    "pipeline": {
        "name": "По шагам",
        "name_en": "Step by step",
        "primitive": "and",
        "strategy": "seq",
        "goal_type": "finite",
        "goals_count": "2+",
        "fields": ["goals"],
        "placeholder": "Шаги по порядку:",
        "placeholder_en": "Steps in order:",
        "description": "Все подцели по порядку. Каждая после предыдущей.",
        "description_en": "All subgoals in order. Each after previous.",
        "tooltip": "Строго по порядку, каждый шаг после предыдущего",
    },
    "cascade": {
        "name": "Приоритеты",
        "name_en": "Priorities",
        "primitive": "and",
        "strategy": "priority",
        "goal_type": "finite",
        "goals_count": "2+",
        "fields": ["goals"],
        "placeholder": "Задачи по приоритету:",
        "placeholder_en": "Tasks by priority:",
        "description": "Все подцели по приоритету. Срочное первым.",
        "description_en": "All subgoals by priority. Urgent first.",
        "tooltip": "Всё нужно, но срочное и важное первым",
    },
    "scales": {
        "name": "Баланс",
        "name_en": "Balance",
        "primitive": "and",
        "strategy": "balance",
        "goal_type": "open",
        "goals_count": "2+",
        "fields": ["goals"],
        "placeholder": "Между чем балансировать?",
        "placeholder_en": "What to balance between?",
        "description": "Пропорциональное распределение между целями. Бюджет, баланс.",
        "description_en": "Proportional allocation between goals. Budget, balance.",
        "tooltip": "Распределяем внимание между несколькими целями поровну",
    },

    # ── OR ───────────────────────────────────────────────────────────────────
    "race": {
        "name": "Любой вариант",
        "name_en": "Any option",
        "primitive": "or",
        "strategy": None,
        "goal_type": "finite",
        "goals_count": "2+",
        "fields": ["goals"],
        "placeholder": "Варианты (любой подойдёт):",
        "placeholder_en": "Options (any will do):",
        "description": "Любая одна цель достаточна. Первая выигрывает.",
        "description_en": "Any one goal suffices. First to finish wins.",
        "tooltip": "Подойдёт любой вариант, берём первый найденный",
    },
    "fan": {
        "name": "Мозговой штурм",
        "name_en": "Brainstorm",
        "primitive": "or",
        "strategy": None,
        "goal_type": "open",
        "goals_count": "2+",
        "fields": ["goal"],
        "placeholder": "Тема для мозгового штурма:",
        "placeholder_en": "Topic for brainstorming:",
        "description": "Набор идей без ограничений. Мозговой штурм.",
        "description_en": "Open-ended idea generation. Brainstorm.",
        "tooltip": "Набрасываем максимум идей без ограничений",
    },

    # ── XOR ──────────────────────────────────────────────────────────────────
    "tournament": {
        "name": "Выбор",
        "name_en": "Choice",
        "primitive": "xor",
        "strategy": "comparative",
        "goal_type": "finite",
        "goals_count": "2+",
        "fields": ["options", "criteria"],
        "placeholder": "Варианты для сравнения:",
        "placeholder_en": "Options to compare:",
        "description": "Выбрать ровно одну из нескольких. Сравнение вариантов.",
        "description_en": "Pick exactly one from several. Compare options.",
        "tooltip": "Сравниваем варианты и выбираем лучший",
    },
    "dispute": {
        "name": "Дебаты",
        "name_en": "Debate",
        "primitive": "xor",
        "strategy": "dialectical",
        "goal_type": "finite",
        "goals_count": "2+",
        "fields": ["positions"],
        "placeholder": "Противоречивые позиции:",
        "placeholder_en": "Contradictory positions:",
        "description": "Противоречивые утверждения. Диалектический синтез.",
        "description_en": "Contradictory claims. Dialectical synthesis.",
        "tooltip": "Сталкиваем позиции, ищем синтез",
    },

    # ── Bayesian ────────────────────────────────────────────────────────────
    "bayes": {
        "name": "Байесовский",
        "name_en": "Bayesian",
        "primitive": "bayes",
        "strategy": None,
        "goal_type": "open",
        "goals_count": 1,
        "fields": ["hypothesis"],
        "placeholder": "Гипотеза (что проверяем?):",
        "placeholder_en": "Hypothesis (what to test?):",
        "description": "Ввод гипотезы + наблюдения → обновление вероятности по Байесу.",
        "description_en": "Enter hypothesis + observations → Bayesian probability update.",
        "tooltip": "Вводишь гипотезу, добавляешь наблюдения, смотришь как меняется вероятность",
    },
}


# Default — free mode (manual, all tools, no autorun)
DEFAULT_MODE = "free"


def get_mode(mode_id: str) -> dict:
    """Get mode config by ID. Falls back to horizon."""
    return MODES.get(mode_id, MODES[DEFAULT_MODE])


def get_elaborate_hint(goal_node: dict, lang: str = "ru") -> str:
    """Get context hint for elaborate prompt based on goal's primitive."""
    primitive = goal_node.get("primitive")
    goal_text = goal_node.get("text", "")
    if not primitive:
        return ""

    hints = {
        "xor": {
            "ru": f"Контекст: мы сравниваем варианты для выбора '{goal_text}'. Раскрой плюсы, минусы, особенности этого варианта.",
            "en": f"Context: we are comparing options to choose '{goal_text}'. Reveal pros, cons, specifics of this option.",
        },
        "and": {
            "ru": f"Контекст: это часть задачи '{goal_text}'. Углуби детали реализации.",
            "en": f"Context: this is part of task '{goal_text}'. Elaborate on implementation details.",
        },
        "or": {
            "ru": f"Контекст: это один из вариантов для '{goal_text}'. Раскрой что делает его подходящим.",
            "en": f"Context: this is one option for '{goal_text}'. Reveal what makes it suitable.",
        },
        "focus": {
            "ru": f"Контекст: цель — '{goal_text}'. Углуби этот аспект.",
            "en": f"Context: goal is '{goal_text}'. Elaborate on this aspect.",
        },
    }
    h = hints.get(primitive, {})
    return h.get(lang, h.get("ru", ""))


def list_modes() -> list[dict]:
    """List all modes with IDs for UI selector."""
    return [{"id": k, **v} for k, v in MODES.items()]


def check_stop(goal_node: dict, cl: dict, graph: dict) -> dict:
    """Check if goal is reached based on goal_type.

    Returns {"resolved": bool, "reason": str}.
    cl = classify_nodes() result from thinking.py.
    """
    goal_type = goal_node.get("goal_type")

    if goal_type == "finite":
        # All hypotheses verified → RESOLVED
        if cl["hypotheses"] and not cl["unverified"]:
            return {"resolved": True, "reason": "All hypotheses verified"}
        # High average confidence with enough verified
        if cl["hypotheses"] and cl["verified"] and len(cl["verified"]) >= 3:
            avg = sum(n.get("confidence", 0.5) for _, n in cl["hypotheses"]) / len(cl["hypotheses"])
            if avg > 0.85:
                return {"resolved": True, "reason": f"High avg confidence: {avg:.0%}"}
        return {"resolved": False, "reason": ""}

    elif goal_type == "repeatable":
        # One cycle done → snapshot, system can restart
        if cl["hypotheses"] and not cl["unverified"] and cl["verified"]:
            return {"resolved": True, "reason": "Cycle complete, snapshot"}
        return {"resolved": False, "reason": ""}

    elif goal_type == "open":
        # Novelty exhaustion — model is repeating itself
        horizon_data = graph.get("_horizon", {})
        precision = horizon_data.get("precision", 0.5)
        if precision > 0.85 and not cl["bare"] and not cl["unverified"]:
            return {"resolved": True, "reason": "Diminishing returns"}
        return {"resolved": False, "reason": ""}

    # None (free/scout) — never auto-stop
    return {"resolved": False, "reason": ""}
