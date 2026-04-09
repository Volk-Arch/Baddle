# Установка и запуск

**[English version →](SETUP_EN.md)**

## Требования

- Python 3.10+
- OpenAI-совместимый LLM endpoint (локальный или удалённый)

Baddle сам не загружает модели — вся генерация через API. Это делает установку
тривиальной и позволяет использовать любой endpoint: LM Studio, llama-server,
Ollama, OpenAI, Groq, Together и т.д.

---

## Установка

```bash
git clone https://github.com/Volk-Arch/Baddle
cd Baddle
python setup.py        # установит flask + numpy
```

Или вручную:

```bash
pip install -r requirements.txt
```

---

## Настройка LLM endpoint

Нужен OpenAI-совместимый сервер. Самый простой вариант — **LM Studio**:

1. Скачай [LM Studio](https://lmstudio.ai)
2. В LM Studio зайди в **Discover** и загрузи модель, например `Qwen3-8B` (Q4_K_M)
3. Загрузи embedding-модель: `text-embedding-nomic-embed-text-v1.5`
4. Во вкладке **Local Server** нажми **Start Server**. URL обычно `http://localhost:1234`
5. Запусти Baddle, зайди в **Settings** и укажи:
   - `api_url`: `http://localhost:1234`
   - `api_model`: имя чат-модели из LM Studio (например `qwen/qwen3-8b`)
   - `embedding_model`: имя embedding-модели (если загружена)

### Альтернативные endpoints

| Endpoint | api_url |
|----------|---------|
| **LM Studio** | `http://localhost:1234` |
| **llama-server** | `http://localhost:8080` |
| **Ollama** (нужен `OLLAMA_MODELS` и OpenAI-режим) | `http://localhost:11434` |
| **OpenAI** | `https://api.openai.com` (+ `api_key`) |
| **Groq** | `https://api.groq.com/openai` (+ `api_key`) |

Для облачных — заполни `api_key`. Для локальных — не нужен.

### Выбор модели

**Чат-модели (GGUF для локального использования):**

| Модель | Размер (Q4) | Качество | Когда |
|--------|-------------|---------|-------|
| Qwen3-8B | ~5 GB | хорошее | основной выбор, отличный баланс |
| Qwen3-14B | ~9 GB | лучше | если хватает VRAM |
| Llama-3.1-8B | ~5 GB | хорошее | альтернатива Qwen |
| Gemma-2-9B | ~5.5 GB | хорошее | хорош для аналитики |

**Embedding-модель** (для графовых связей, SmartDC centroid, novelty check):

- `nomic-embed-text-v1.5` — 140 MB, быстрая, качественная
- `bge-large-en-v1.5` — 1.3 GB, лучше для английского
- Любой другой с поддержкой `/v1/embeddings`

---

## Запуск

```bash
python ui.py                  # веб-интерфейс на http://localhost:7860
python ui.py --port 8080      # другой порт
```

Браузер откроется автоматически.

Как использовать → [README](README.md)

---

## Структура проекта

```
baddle/
├── ui.py                # точка входа Flask
├── setup.py             # установка (pip install flask numpy)
├── requirements.txt
├── src/
│   ├── main.py          # утилиты: StreamCfg, cosine_similarity
│   ├── thinking.py      # автономное мышление (tick, фазы)
│   ├── graph_logic.py   # граф, Байес, SmartDC, генерация через API
│   ├── graph_routes.py  # Flask роуты графа
│   ├── chat.py          # чат-режим
│   ├── api_backend.py   # OpenAI-совместимый HTTP клиент
│   └── prompts.py       # системные промпты (EN/RU)
├── static/
│   ├── css/style.css
│   └── js/              # graph, chat, modes, settings
├── templates/index.html
├── graphs/              # сохранённые графы
└── settings.json        # конфиг API endpoint
```

---

## Устранение проблем

**"API not configured"** — открой Settings в UI, заполни api_url и api_model, сохрани.

**"Connection refused"** — проверь что LM Studio запущен и сервер включён
(Local Server → Start).

**Embeddings не работают** — нужна загруженная embedding-модель. В LM Studio:
Discover → поиск "embed" → Load. Потом в Settings укажи `embedding_model`.

**Медленная генерация** — в LM Studio проверь GPU offload (все слои на GPU если
есть VRAM). Baddle на скорость не влияет — просто HTTP-клиент.
