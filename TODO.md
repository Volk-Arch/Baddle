# TODO

## Planned features

---

### logit lens

Показывать как меняется распределение вероятностей по слоям трансформера
(что модель «думала» на слое 5, 10, 20... перед финальным ответом).

**Суть:**
Вместо одного финального распределения токенов — N распределений (по числу слоёв).
Показывает как мнение модели «созревает» по мере прохождения через слои.

**Что нужно:**
- Доступ к промежуточным активациям через llama.cpp hooks / GGML callback
- llama-cpp-python пока не экспонирует layer hooks напрямую — нужно либо патчить,
  либо ждать поддержки в API
- Альтернатива: использовать transformers + bitsandbytes для этого режима отдельно
- Команда `lens` в step-режиме, показывает таблицу top-5 по каждому слою

---

## Известные проблемы

### Batch path (multi-seq) не работает в llama-cpp-python 0.3.x

**Проблема:**
Unified KV cache в текущей llama.cpp не поддерживает multi-sequence операции.
Любое использование `seq_id >= 1` (через `llama_decode`) или `llama_kv_self_seq_cp`
вызывает crash (access violation / GGML_ASSERT "seq_cp() is only supported for
full KV buffers"). Даже probe при загрузке модели корраптит context pointer,
после чего все последующие вызовы падают.

Batch path полностью отключён — compare и parallel работают через interleaved
(save_state/load_state). Compare: 1 prefill + save/load. Parallel: 2 prefill.

**Влияние:**
- Compare mode: минимальное — один prefill, разница только в sampling (быстро)
- Parallel mode: 2x медленнее теоретического максимума (два полных прохода вместо одного)

**Возможное решение:**
- Ждать llama-cpp-python с поддержкой split KV cache (не unified) — тогда
  `seq_id >= 1` и `seq_cp` будут работать, batch path можно включить обратно
- Или использовать `llama_kv_cache_type_k/v = LLAMA_KV_CACHE_TYPE_SPLIT`
  (если появится в API) при создании контекста
- Или перейти на llama.cpp server mode (HTTP API) — там multi-seq работает
  через встроенный scheduler
- Probe в `_probe_batch_support()` готов к восстановлению — достаточно
  заменить `_batch_seq1_ok = False` на реальный тест когда библиотека обновится

---

## Done

- [x] step mode с пошаговой генерацией
- [x] `top N` — таблица вероятностей с гистограммой
- [x] `inject` — вброс произвольных токенов в поток
- [x] `auto N` — автогенерация N токенов
- [x] температура на лету (`temp`)
- [x] parallel mode — interleaved (save_state/load_state), batch path заготовлен
- [x] fallback к interleaved если batch API недоступен (сейчас всегда interleaved)
- [x] split-screen live display (`rich.Layout`)
- [x] история команд + Tab-автодополнение (`prompt_toolkit`)
- [x] GPU-aware installer (`setup.py`)
- [x] compare mode — один промпт, два набора параметров, детекция точки расхождения
- [x] session saving — `save <file>` / `load <file>` в step mode, JSON-формат
- [x] web UI (`ui.py`) — parallel и compare в браузере, SSE-стриминг, тёмная тема