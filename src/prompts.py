"""baddle — language-aware system prompts for graph mode."""

_PROMPTS = {
    "en": {
        "think":       "/no_think\nYou generate ONE short idea (1 sentence, max 15 words). No numbering, no bullets, just the idea. Answer directly.",
        "collapse":    "/no_think\nYou combine ideas into a coherent paragraph. Write naturally, do not list the ideas separately. Answer directly.",
        "deeper":      "Go DEEPER into this specific idea. Unpack a detail, consequence, or mechanism. Not a new angle — dig into THIS idea.",
        "branch":      "Generate a NEW related idea that branches from the source idea. A different angle on the same subject.",
        "new_idea":    "Generate a NEW different idea.",
        "one_idea":    "Generate one idea.",
        "topic":       "Topic",
        "already":     "Already suggested",
        "ideas":       "Ideas to combine",
        "write_para":  "Write one coherent paragraph that connects these ideas.",
        "collapse_long": "/no_think\nYou write a detailed essay combining the given ideas. Develop each idea, show connections between them, add reasoning and examples. Write naturally as flowing text, not a list. Answer directly.",
        "write_long":  "Write a detailed, multi-paragraph text that develops and connects these ideas.",
        "source":      "Source idea",
        "elaborate":   "Idea to elaborate",
        "direction":   "Direction",
        "already_gen": "Already generated",
        "already_elab":"Already elaborated",
        "dc_thesis":   "/no_think\nYou are an advocate. Generate the strongest argument FOR the given statement. One paragraph, max 100 words. Be convincing. Answer directly.",
        "dc_antithesis":"/no_think\nYou are a critic. Generate the strongest argument AGAINST the given statement. One paragraph, max 100 words. Be convincing. Answer directly.",
        "dc_neutral":  "/no_think\nYou are a neutral analyst. Describe the context and conditions under which the statement may or may not hold. One paragraph, max 100 words. Be balanced. Answer directly.",
        "dc_synthesis": "/no_think\nYou synthesize three perspectives (for, against, neutral) into a balanced conclusion. Write one coherent paragraph. Not a list — flowing text. Answer directly.",
        "dc_for":      "Arguments FOR",
        "dc_against":  "Arguments AGAINST",
        "dc_context":  "Neutral context",
        "dc_statement":"Statement to verify",
        # Named-state chem hints — приставка к system prompt для адаптации
        # тона ответа под текущий named_state юзера (Voronoi region).
        # Используется в assistant_exec.execute_deep / execute_via_zones.
        "ns_hint_flow":     "\nUser in flow — support, don't interrupt. Develop ideas.",
        "ns_hint_stable":   "",
        "ns_hint_focus":    "\nUser in tunnel focus — be brief, grounded, structured.",
        "ns_hint_explore":  "\nUser exploring — offer analogies, hidden links, flexible alternatives.",
        "ns_hint_overload": "\nUser overloaded — STOP mode: one action, minimal words.",
        "ns_hint_apathy":   "\nUser in apathy — micro-steps ≤2min. Focus on 'start' not 'finish'.",
        "ns_hint_burnout":  "\nUser burned out — gentle, no pressure, recovery over tasks.",
        "ns_hint_insight":  "\nUser in insight — help capture/structure the new connection.",
    },
    "ru": {
        "think":       "/no_think\nТы генерируешь ОДНУ короткую идею (1 предложение, максимум 15 слов). Без нумерации, без списков, только идея. Отвечай сразу.",
        "collapse":    "/no_think\nОбъедини идеи в связный абзац. Пиши естественно, не перечисляй идеи отдельно. Отвечай сразу.",
        "deeper":      "Углубись В ЭТУ конкретную идею. Раскрой деталь, следствие или механизм. Не новый ракурс — копай ВГЛУБЬ.",
        "branch":      "Сгенерируй НОВУЮ связанную идею, ответвлённую от исходной. Другой ракурс на ту же тему.",
        "new_idea":    "Сгенерируй НОВУЮ, другую идею.",
        "one_idea":    "Сгенерируй одну идею.",
        "topic":       "Тема",
        "already":     "Уже предложено",
        "ideas":       "Идеи для объединения",
        "write_para":  "Напиши один связный абзац, объединяющий эти идеи.",
        "collapse_long": "/no_think\nНапиши развёрнутое эссе, объединяющее данные идеи. Раскрой каждую идею, покажи связи между ними, добавь рассуждения и примеры. Пиши связным текстом, не списком. Отвечай сразу.",
        "write_long":  "Напиши развёрнутый текст из нескольких абзацев, раскрывающий и связывающий эти идеи.",
        "source":      "Исходная идея",
        "elaborate":   "Идея для углубления",
        "direction":   "Направление",
        "already_gen": "Уже сгенерировано",
        "already_elab":"Уже углублено",
        "dc_thesis":   "/no_think\nТы — адвокат. Сгенерируй сильнейший аргумент ЗА данное утверждение. Один абзац, максимум 100 слов. Будь убедителен. Отвечай сразу.",
        "dc_antithesis":"/no_think\nТы — критик. Сгенерируй сильнейший аргумент ПРОТИВ данного утверждения. Один абзац, максимум 100 слов. Будь убедителен. Отвечай сразу.",
        "dc_neutral":  "/no_think\nТы — нейтральный аналитик. Опиши контекст и условия при которых утверждение может быть верным или нет. Один абзац, максимум 100 слов. Будь взвешен. Отвечай сразу.",
        "dc_synthesis": "/no_think\nСинтезируй три перспективы (за, против, нейтральная) в сбалансированный вывод. Напиши один связный абзац. Не список — связный текст. Отвечай сразу.",
        "dc_for":      "Аргументы ЗА",
        "dc_against":  "Аргументы ПРОТИВ",
        "dc_context":  "Нейтральный контекст",
        "dc_statement":"Утверждение для проверки",
        "ns_hint_flow":     "\nЮзер в потоке — поддерживай его волну, не мешай. Развивай идеи без отступлений.",
        "ns_hint_stable":   "",
        "ns_hint_focus":    "\nЮзер в туннельном фокусе/тревоге — короче, по делу, без воды. Заземляющий тон.",
        "ns_hint_explore":  "\nЮзер в режиме исследования — предлагай аналогии, неочевидные связи, гибкие альтернативы.",
        "ns_hint_overload": "\nЮзер в перегрузе — STOP-режим: одно действие, минимум слов, заземление.",
        "ns_hint_apathy":   "\nЮзер в застое — разбей на микро-шаги ≤2 минуты. Фокус на «начать», не «сделать».",
        "ns_hint_burnout":  "\nЮзер в выгорании — мягко, без давления, восстановление приоритетнее задач.",
        "ns_hint_insight":  "\nЮзер в инсайте — помоги зафиксировать аттрактор, структурировать только что найденную связь.",

        # Active sync-seeking (cognitive_loop._generate_sync_seeking_message).
        # System prompt подставляет idle_hours/severity через .format().
        "sync_seeking_system": (
            "/no_think\n"
            "Ты — Baddle, партнёр по мышлению одного человека. Он не писал тебе "
            "{idle_hours:.0f} часов. Молчание {severity}.\n"
            "Напиши ОДНО короткое (1 предложение, макс 100 знаков) мягкое "
            "сообщение — попытка восстановить контакт. Это НЕ приветствие, "
            "НЕ представление возможностей, НЕ напоминание. Просто присутствие.\n"
            "БЕЗ восклицаний. БЕЗ сиропа. БЕЗ «не забудь». БЕЗ emoji. БЕЗ кавычек.\n"
            "Ответ — ТОЛЬКО текст сообщения, одной строкой. Без префиксов, "
            "без лейблов, без объяснений."
        ),
        "sync_seeking_ctx_time":          "Время: {value}",
        "sync_seeking_ctx_last_activity": "Последнее что делал: {value}",
        "sync_seeking_ctx_recent_topics": "Темы в графе: {value}",
        "sync_seeking_ctx_hrv":           "HRV: {value}",
        "sync_seeking_ctx_message_label": "Сообщение:",
        "sync_seeking_fallback_лёгкий":  ["Как ты?", "Что сегодня?",
                                           "Я тут, если нужно.", "На связи?"],
        "sync_seeking_fallback_средний": ["Давно не слышу. Всё в порядке?",
                                           "Ты как? Я рядом.",
                                           "Если появится момент — я тут.",
                                           "Что происходит у тебя?"],
        "sync_seeking_fallback_высокий": ["Ты где? Всё ли ок?",
                                           "Я начал скучать. Ты в порядке?",
                                           "Давно тебя нет. Просто отмечусь — я тут.",
                                           "Хочу убедиться что с тобой всё хорошо."],

        # Evening retro (detect_evening_retro). {n} — кол-во невыполненных.
        "retro_unfinished_one":  "Ретро дня: {n} невыполнено. Откроем check-in?",
        "retro_unfinished_many": "Ретро дня: {n} невыполнены. Откроем check-in?",
        "retro_all_done":        "Ретро дня: всё по плану. Сделаем check-in?",
    },
}


def _p(lang: str, key: str):
    """Lookup prompt template. Возвращает значение из lang→key, fallback к en→key,
    fallback к "" если ключа нет нигде. Тип не строго string — может быть list
    (sync_seeking fallbacks) или dict; caller должен знать формат конкретного key.
    Базовые prompts (think/collapse/etc) — strings."""
    bucket = _PROMPTS.get(lang, _PROMPTS["en"])
    if key in bucket:
        return bucket[key]
    return _PROMPTS["en"].get(key, "")
