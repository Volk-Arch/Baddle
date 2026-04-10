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
        "name_en": "Free",
        "primitive": None,
        "strategy": None,
        "goal_type": None,
        "goals_count": 0,
        "fields": ["topic"],
        "placeholder": "Тема или мысль...",
        "placeholder_en": "Topic or thought...",
        "description": "Ручной режим. Все инструменты доступны, autorun выключен.",
        "description_en": "Manual mode. All tools available, no autorun.",
        "tooltip": "ручной · без autorun · все инструменты",
    },

    # ── 0 goals ──────────────────────────────────────────────────────────────
    "scout": {
        "name": "Разведка",
        "name_en": "Scout",
        "primitive": None,
        "strategy": None,
        "goal_type": None,
        "goals_count": 0,
        "fields": [],
        "placeholder": "Просто начни думать...",
        "placeholder_en": "Just start thinking...",
        "description": "Свободное блуждание без цели. Поиск неожиданных связей.",
        "description_en": "Free exploration without a goal. Serendipity search.",
        "tooltip": "0 целей · бесконечный · без стоп-условия",
    },

    # ── 1 goal ───────────────────────────────────────────────────────────────
    "vector": {
        "name": "Вектор",
        "name_en": "Vector",
        "primitive": "focus",
        "strategy": None,
        "goal_type": "finite",
        "goals_count": 1,
        "fields": ["goal"],
        "placeholder": "Цель: что нужно достичь?",
        "placeholder_en": "Goal: what to achieve?",
        "description": "Одна конечная цель. Фокус до достижения.",
        "description_en": "Single finite goal. Focus until achieved.",
        "tooltip": "1 цель · конечная · стоп: confidence ≥ threshold",
    },
    "rhythm": {
        "name": "Ритм",
        "name_en": "Rhythm",
        "primitive": "focus",
        "strategy": None,
        "goal_type": "repeatable",
        "goals_count": 1,
        "fields": ["goal", "interval"],
        "placeholder": "Привычка: что делать регулярно?",
        "placeholder_en": "Habit: what to do regularly?",
        "description": "Повторяемая цель. Streak, тренд, snapshot.",
        "description_en": "Repeatable goal. Streak, trend, snapshot evaluation.",
        "tooltip": "1 цель · повторяемая · streak + тренд · бесконечный",
    },
    "horizon": {
        "name": "Горизонт",
        "name_en": "Horizon",
        "primitive": "focus",
        "strategy": None,
        "goal_type": "open",
        "goals_count": 1,
        "fields": ["goal"],
        "placeholder": "Тема: что исследовать?",
        "placeholder_en": "Topic: what to explore?",
        "description": "Открытая цель. Бесконечное уточнение до исчерпания новизны.",
        "description_en": "Open-ended goal. Infinite refinement until novelty exhaustion.",
        "tooltip": "1 цель · открытая · стоп: исчерпание новизны",
    },

    # ── AND ──────────────────────────────────────────────────────────────────
    "builder": {
        "name": "Конструктор",
        "name_en": "Builder",
        "primitive": "and",
        "strategy": "unordered",
        "goal_type": "finite",
        "goals_count": "2+",
        "fields": ["goals"],
        "placeholder": "Подзадачи (все обязательны):",
        "placeholder_en": "Subtasks (all required):",
        "description": "Все подцели обязательны, порядок неважен.",
        "description_en": "All subgoals required, any order.",
        "tooltip": "AND · все обязательны · любой порядок · конечная",
    },
    "pipeline": {
        "name": "Конвейер",
        "name_en": "Pipeline",
        "primitive": "and",
        "strategy": "seq",
        "goal_type": "finite",
        "goals_count": "2+",
        "fields": ["goals"],
        "placeholder": "Шаги по порядку:",
        "placeholder_en": "Steps in order:",
        "description": "Все подцели по порядку. Каждая после предыдущей.",
        "description_en": "All subgoals in order. Each after previous.",
        "tooltip": "AND · последовательно · по зависимостям · конечная",
    },
    "cascade": {
        "name": "Каскад",
        "name_en": "Cascade",
        "primitive": "and",
        "strategy": "priority",
        "goal_type": "finite",
        "goals_count": "2+",
        "fields": ["goals"],
        "placeholder": "Задачи по приоритету:",
        "placeholder_en": "Tasks by priority:",
        "description": "Все подцели по приоритету. Срочное первым.",
        "description_en": "All subgoals by priority. Urgent first.",
        "tooltip": "AND · по приоритету · срочное первым · конечная",
    },
    "scales": {
        "name": "Весы",
        "name_en": "Scales",
        "primitive": "and",
        "strategy": "balance",
        "goal_type": "open",
        "goals_count": "2+",
        "fields": ["goals"],
        "placeholder": "Между чем балансировать?",
        "placeholder_en": "What to balance between?",
        "description": "Пропорциональное распределение между целями. Бюджет, баланс.",
        "description_en": "Proportional allocation between goals. Budget, balance.",
        "tooltip": "AND · пропорции · бесконечный · snapshot",
    },

    # ── OR ───────────────────────────────────────────────────────────────────
    "race": {
        "name": "Гонка",
        "name_en": "Race",
        "primitive": "or",
        "strategy": None,
        "goal_type": "finite",
        "goals_count": "2+",
        "fields": ["goals"],
        "placeholder": "Варианты (любой подойдёт):",
        "placeholder_en": "Options (any will do):",
        "description": "Любая одна цель достаточна. Первая выигрывает.",
        "description_en": "Any one goal suffices. First to finish wins.",
        "tooltip": "OR · любой достаточен · конечная",
    },
    "fan": {
        "name": "Веер",
        "name_en": "Fan",
        "primitive": "or",
        "strategy": None,
        "goal_type": "open",
        "goals_count": "2+",
        "fields": ["goal"],
        "placeholder": "Тема для мозгового штурма:",
        "placeholder_en": "Topic for brainstorming:",
        "description": "Набор идей без ограничений. Мозговой штурм.",
        "description_en": "Open-ended idea generation. Brainstorm.",
        "tooltip": "OR · открытый · стоп: исчерпание новизны",
    },

    # ── XOR ──────────────────────────────────────────────────────────────────
    "tournament": {
        "name": "Турнир",
        "name_en": "Tournament",
        "primitive": "xor",
        "strategy": "comparative",
        "goal_type": "finite",
        "goals_count": "2+",
        "fields": ["options", "criteria"],
        "placeholder": "Варианты для сравнения:",
        "placeholder_en": "Options to compare:",
        "description": "Выбрать ровно одну из нескольких. Сравнение вариантов.",
        "description_en": "Pick exactly one from several. Compare options.",
        "tooltip": "XOR · выбрать одну · сравнение · конечная",
    },
    "dispute": {
        "name": "Диспут",
        "name_en": "Dispute",
        "primitive": "xor",
        "strategy": "dialectical",
        "goal_type": "finite",
        "goals_count": "2+",
        "fields": ["positions"],
        "placeholder": "Противоречивые позиции:",
        "placeholder_en": "Contradictory positions:",
        "description": "Противоречивые утверждения. Диалектический синтез.",
        "description_en": "Contradictory claims. Dialectical synthesis.",
        "tooltip": "XOR · диалектика · синтез · конечная",
    },
}


# Default — free mode (manual, all tools, no autorun)
DEFAULT_MODE = "free"


def get_mode(mode_id: str) -> dict:
    """Get mode config by ID. Falls back to horizon."""
    return MODES.get(mode_id, MODES[DEFAULT_MODE])


def list_modes() -> list[dict]:
    """List all modes with IDs for UI selector."""
    return [{"id": k, **v} for k, v in MODES.items()]
