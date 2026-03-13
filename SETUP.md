# Установка и запуск

## Требования

- Python 3.10+
- GPU с CUDA (работает и на CPU, медленнее)
- GGUF-модель в папке `models/`

Проверено на Qwen3-8B-Q4_K_M.gguf.

---

## Установка

```bash
git clone https://github.com/Volk-Arch/Baddle
cd baddle
python setup.py        # определит CUDA, установит llama-cpp-python + скачает llama-server
pip install flask      # только для веб-UI
```

После установки положи GGUF-модель в папку `models/` (создаётся автоматически):

```
models/Qwen3-8B-Q4_K_M.gguf
```

`setup.py` автоматически:
- Определит версию CUDA
- Установит `llama-cpp-python` (с GPU если есть CUDA, иначе CPU)
- Скачает нативный `llama-server` в папку `llama-server/` (для параллельного режима)

> **Установка занимает 5–15 минут** — это нормально.
> `llama-cpp-python` компилируется из C++ исходников.
> При наличии CUDA дополнительно собираются GPU-ядра, это занимает ещё больше.
> Просто подожди пока не появится `Successfully installed llama-cpp-python`.

---

## Запуск

```bash
python main.py           # CLI-интерфейс с меню
python main.py --server  # CLI + параллельный сервер
python ui.py             # веб-интерфейс на localhost:7860
python ui.py --server    # веб-интерфейс + параллельный сервер (рекомендуется)
```

Если в `models/` одна модель — загрузится автоматически. Если несколько — появится выбор.

```bash
python main.py -m Qwen3-8B-Q4_K_M.gguf   # выбрать модель явно
python main.py --ctx 8192                 # увеличить контекст
python main.py --no-gpu                   # только CPU
python ui.py --port 8080
python main.py --server --seed 42         # фиксированный seed для воспроизводимости
```

---

## Server mode

`--server` включает настоящий параллелизм — два промпта обрабатываются одновременно.

```bash
# Автозапуск (сервер запустится и остановится сам):
python ui.py --server
python main.py --server

# Подключение к уже запущенному серверу:
python ui.py --server http://localhost:8080
```

`--server` без URL найдёт `llama-server` в папке `llama-server/`, запустит его
и остановит при выходе.

| | In-process (по умолчанию) | Server mode (`--server`) |
|---|---|---|
| **Как работает** | Один запрос, потом второй (последовательно) | Оба запроса на GPU параллельно |
| **Скорость** | ~2x от одного запроса | ~1x от одного запроса |
| **Step mode** | Есть (доступ к logits) | Нет |

**Для step mode нужен in-process** (прямой доступ к logits).
**Для быстрого parallel/compare — `--server`.**

#### Откуда берётся llama-server

`python setup.py` скачивает бинарник автоматически с
[llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases).

Если setup не скачал (не Windows, нет интернета) — вручную:

1. Зайди на https://github.com/ggml-org/llama.cpp/releases
2. Скачай `llama-bXXXX-bin-win-cuda-XX.X-x64.zip` (версия CUDA как у тебя)
3. Скачай `cudart-llama-bin-win-cuda-XX.X-x64.zip` (CUDA runtime DLL)
4. Распакуй оба в `llama-server/` внутри проекта

> Узнать свою версию CUDA: `nvidia-smi` → строка `CUDA Version: XX.X`

---

## Команды step mode

| Команда | Действие |
|---|---|
| `[Enter]` | следующий токен |
| `top N` | N самых вероятных следующих токенов с вероятностями и гистограммой |
| `inject <текст>` | вбросить текст вместо следующего токена |
| `auto N` | автоматически N токенов без остановки |
| `temp 0.8` | изменить температуру прямо в процессе |
| `show` | вывести весь накопленный текст |
| `save session.json` | сохранить сессию в файл |
| `load session.json` | воспроизвести сохранённую сессию |
| `reset` | откатиться к исходному промпту |
| `q` | выйти |

`↑↓` — история команд,  `Tab` — автодополнение

---

## Структура проекта

```
baddle/
├── main.py            # CLI: step, parallel, compare
├── ui.py              # веб-сервер (Flask + SSE)
├── server_backend.py  # HTTP-клиент для llama-server
├── setup.py           # установщик: llama-cpp-python + llama-server
├── models/            # GGUF-модели
└── llama-server/      # нативный бинарник (скачивается setup.py)
```
