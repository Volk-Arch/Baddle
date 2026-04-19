"""Ship-with-code дефолты для ui-ready контента.

Roles (персоны для /graph/think) и Templates (шаблоны для быстрых chat-
промптов) живут в data/ как user-editable JSON. При первом запуске
(файл отсутствует) пишем эти дефолты туда же — юзер может править/удалять/
добавлять. Если юзер удалил файл через /data/reset — дефолты снова
заполнят data/ на следующем старте.
"""

DEFAULT_ROLES = [
    {"name": "(none)",           "text": "",                                                                   "lang": "en"},
    {"name": "(нет)",            "text": "",                                                                   "lang": "ru"},
    {"name": "Assistant",        "text": "You are a helpful assistant. Answer concisely and to the point.",   "lang": "en"},
    {"name": "Ассистент",        "text": "Ты полезный ассистент. Отвечай кратко и по делу.",                 "lang": "ru"},
    {"name": "Brief",            "text": "Answer as briefly as possible — 1-3 sentences.",                   "lang": "en"},
    {"name": "Краткий",          "text": "Отвечай максимально кратко — 1-3 предложения.",                    "lang": "ru"},
    {"name": "Writer",           "text": "You are a writer. Continue the story.",                             "lang": "en"},
    {"name": "Писатель",         "text": "Ты писатель. Продолжи историю.",                                   "lang": "ru"},
    {"name": "Programmer",       "text": "You are an experienced programmer. Write clean, correct code.",    "lang": "en"},
    {"name": "Программист",      "text": "Ты опытный программист. Пиши чистый, корректный код.",             "lang": "ru"},
    {"name": "Translator EN→RU", "text": "Translate everything into Russian. Do not add explanations.",      "lang": "en"},
    {"name": "Переводчик RU→EN", "text": "Переводи всё на английский язык. Не добавляй пояснений.",          "lang": "ru"},
    {"name": "Analyst",          "text": "You are a systems analyst. Provide detailed, structured analysis.", "lang": "en"},
    {"name": "Аналитик",         "text": "Ты системный аналитик. Давай детальный, структурированный анализ.","lang": "ru"},
]


DEFAULT_TEMPLATES = [
    {
        "name": "Ревью кода",
        "name_en": "Code review",
        "text": "Ты — {{role}}. Проанализируй код.\nФокус: {{focus}}.\nФормат вывода: {{format}}.",
        "text_en": "You are a {{role}}. Analyze the provided code.\nFocus on: {{focus}}.\nOutput format: {{format}}.",
        "defaults": {
            "role":   "Senior Security Engineer",
            "focus":  "уязвимости, лучшие практики",
            "format": "список",
        },
    },
    {
        "name": "Переводчик",
        "name_en": "Translator",
        "text": "Переведи текст с {{source_lang}} на {{target_lang}}. Сохрани тон оригинала. Без пояснений.",
        "text_en": "Translate all text from {{source_lang}} to {{target_lang}}. Keep the original tone. Do not add explanations.",
        "defaults": {
            "source_lang": "English",
            "target_lang": "Russian",
        },
    },
    {
        "name": "Резюме",
        "name_en": "Summarizer",
        "text": "Резюмируй текст в {{length}}. Язык: {{language}}.",
        "text_en": "Summarize the following text in {{length}}. Language: {{language}}.",
        "defaults": {
            "length":   "2-3 предложения",
            "language": "как в оригинале",
        },
    },
]
