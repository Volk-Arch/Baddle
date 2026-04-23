"""Intent router — двухуровневый LLM-классификатор сообщений юзера.

Заменяет собой первичный diagnostic «что вообще хочет юзер». Вторичная
классификация (mode мышления) остаётся за `classify_intent_llm`.

**Уровень 1 — top-level kind:**
  task             — запрос/цель/задача («хочу научиться», «как сделать X»)
  fact             — свершившийся факт («поел», «купил молоко»)
  constraint_event — нарушение или установка ограничения («съел торт»)
  chat             — свободное общение («привет», «как дела»)
  command          — slash-команда (обрабатывается chat_commands, сюда не доходит)

**Уровень 2 — subtype:**
  Для task:             new_goal | new_recurring | new_constraint | question
  Для fact:             instance | activity | thought
  Для constraint_event: violation | new_constraint
  Для chat:             (нет подтипов)

**Target matching** (для instance/violation): embedding similarity между
сообщением юзера и активными recurring/constraint целями. Если есть
match > порога — возвращаем goal_id, иначе просим юзера выбрать.

Экономика: 1-2 LLM call'а на сообщение, max_tokens=15, temp=0.1.
Результаты кэшируются (LRU, 5 мин TTL) как у classify_intent_llm.
"""
import logging
import time
import re
from typing import Optional

from .graph_logic import _graph_generate

log = logging.getLogger(__name__)


# ── Кэш (LRU с TTL) ───────────────────────────────────────────────────────

_CACHE_MAX = 100
_CACHE_TTL = 300.0    # 5 минут
_cache: dict[str, tuple[float, dict]] = {}


def _cache_key(message: str) -> str:
    # Нормализуем: lower + strip + collapse whitespace
    return re.sub(r"\s+", " ", (message or "").lower().strip())[:200]


def _cache_get(key: str) -> Optional[dict]:
    entry = _cache.get(key)
    if not entry:
        return None
    ts, data = entry
    if time.time() - ts > _CACHE_TTL:
        _cache.pop(key, None)
        return None
    return dict(data)


def _cache_put(key: str, data: dict):
    if len(_cache) >= _CACHE_MAX:
        # drop oldest
        oldest = min(_cache.keys(), key=lambda k: _cache[k][0])
        _cache.pop(oldest, None)
    _cache[key] = (time.time(), dict(data))


# ── Top-level classifier ──────────────────────────────────────────────────

_TOP_KINDS = ("task", "fact", "constraint_event", "chat", "command")


def _classify_top(message: str, lang: str = "ru") -> tuple[str, float]:
    """LLM call #1: определяет kind верхнего уровня.

    Возвращает (kind, confidence). Confidence 0.5 если LLM ответил
    нераспознанным словом.
    """
    if lang == "ru":
        system = ("/no_think\nКлассифицируй сообщение юзера одним словом:\n"
                  "- task — ЗАПРОС/намерение: «хочу X», «как сделать», «помоги»\n"
                  "- fact — СВЕРШИВШЕЕСЯ действие: «поел», «купил», "
                  "«начал тренировку», «пошёл гулять», «сейчас работаю»\n"
                  "- constraint_event — нарушение ограничения: «съел сладкое "
                  "когда обещал не есть»\n"
                  "- chat — короткое общение без конкретики: «привет», "
                  "«как дела», «спасибо»\n"
                  "Ключевое различие task vs fact: task говорит о БУДУЩЕМ/"
                  "желаемом, fact — о ТЕКУЩЕМ/прошедшем.\n"
                  "Ответ ТОЛЬКО одним словом из списка по-английски.")
        user = f"Сообщение: «{message[:300]}»\nКатегория:"
    else:
        system = ("/no_think\nClassify user message with one word:\n"
                  "- task — request/goal/question\n"
                  "- fact — completed event, no request (ate, bought)\n"
                  "- constraint_event — constraint violation\n"
                  "- chat — free talk, no specific ask (hi, thanks)\n"
                  "Answer ONE word from the list.")
        user = f"Message: «{message[:300]}»\nCategory:"
    try:
        result, _ = _graph_generate(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            max_tokens=15, temp=0.1, top_k=10,
        )
    except Exception as e:
        log.warning(f"[intent_router] top classify failed: {e}")
        return ("task", 0.3)   # safe fallback
    text = (result or "").strip().lower()
    for k in _TOP_KINDS:
        if k in text:
            return (k, 0.8)
    # Fallback — LLM ответил что-то странное
    return ("task", 0.3)


# ── Subtype classifier ────────────────────────────────────────────────────

def _classify_subtype_task(message: str, lang: str) -> tuple[str, float]:
    if lang == "ru":
        system = ("/no_think\nЮзер сформулировал задачу/цель. Что это?\n"
                  "- new_goal — одноразовая цель (выучить X, закончить Y)\n"
                  "- new_recurring — хочет ввести регулярную привычку "
                  "(каждый день, каждое утро)\n"
                  "- new_constraint — хочет ввести ограничение "
                  "(перестать, избегать, не)\n"
                  "- question — хочет подумать/исследовать/получить совет\n"
                  "Ответ одним словом из списка.")
        user = f"Сообщение: «{message[:300]}»\nПодтип:"
    else:
        system = ("/no_think\nUser stated a task. What is it?\n"
                  "- new_goal | new_recurring | new_constraint | question\n"
                  "Answer ONE word.")
        user = f"Message: «{message[:300]}»\nSubtype:"
    try:
        result, _ = _graph_generate(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            max_tokens=15, temp=0.1, top_k=10,
        )
    except Exception as e:
        log.warning(f"[intent_router] task subtype failed: {e}")
        return ("question", 0.3)
    text = (result or "").strip().lower()
    for k in ("new_recurring", "new_constraint", "new_goal", "question"):
        if k in text:
            return (k, 0.7)
    return ("question", 0.3)


def extract_activity_name(message: str, lang: str = "ru") -> Optional[str]:
    """Извлечь краткое название активности из сообщения («начал тренировку»
    → «Тренировка»). Один LLM-call, max_tokens=20. Фолбэк — first-noun
    эвристика.

    Используется когда router решил `fact/activity` и мы хотим авто-запустить
    taskplayer. Юзер пишет полное предложение — Baddle превращает его
    в короткое имя задачи.
    """
    msg = (message or "").strip()
    if not msg:
        return None
    if lang == "ru":
        system = ("/no_think\nВыдели из сообщения юзера короткое название "
                  "активности (1-2 слова, существительное, с большой буквы). "
                  "Без пояснений, только название.\n"
                  "Примеры:\n"
                  "  «начал тренировку» → Тренировка\n"
                  "  «пошёл гулять» → Прогулка\n"
                  "  «начинаю писать код» → Код\n"
                  "  «на совещании» → Совещание")
        user = f"Сообщение: «{msg[:200]}»\nНазвание:"
    else:
        system = ("/no_think\nExtract a short activity name (1-2 words) "
                  "from the user message. No explanation, name only.\n"
                  "Example: «started training» → Training")
        user = f"Message: «{msg[:200]}»\nName:"
    try:
        result, _ = _graph_generate(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            max_tokens=20, temp=0.1, top_k=10,
        )
    except Exception as e:
        log.debug(f"[extract_activity_name] LLM failed: {e}")
        return None
    # Чистим: первая строка, без кавычек/точек, ≤50 символов
    text = (result or "").strip().split("\n")[0].strip(' "«»\'.,:!?-')
    if not text or len(text) > 50:
        return None
    # Capitalize первое слово чтобы отрезать «:» и fluff
    return text[:50]


def _classify_subtype_fact(message: str, lang: str,
                             recurring_list: list[dict]) -> tuple[str, float, Optional[str]]:
    """Для fact: определяет subtype + (если instance) matched goal_id.

    Возвращает (subtype, confidence, goal_id_or_None).

    Три случая:
      * instance — fact совпадает с одной из recurring-целей юзера
      * activity — fact = физическое действие в процессе (тренировка, код,
                   еда, прогулка); без match к recurring. Триггерит auto-start
                   taskplayer'а в /assist хуке.
      * thought  — просто мысль/наблюдение, не действие
    """
    # Нумерованный список recurring (может быть пуст — всё равно спрашиваем
    # LLM про activity vs thought).
    numbered = "\n".join(f"{i+1}. {g['text']}"
                          for i, g in enumerate(recurring_list[:10]))
    habits_block = (f"Привычки юзера:\n{numbered}\n\n"
                    if numbered else "Привычек нет.\n\n")
    habits_block_en = (f"Habits:\n{numbered}\n\n"
                       if numbered else "No habits configured.\n\n")

    if lang == "ru":
        system = ("/no_think\nЮзер сообщил о свершившемся действии или факте. "
                  "Классифицируй:\n"
                  "  1. Если это совпадает с одной из привычек юзера — "
                  "ответь НОМЕРОМ (1, 2, …) той привычки.\n"
                  "  2. Если это физическая активность (тренировка, код, "
                  "работа, прогулка, еда) в процессе — ответь 'activity'.\n"
                  "  3. Если это просто мысль/наблюдение без действия — "
                  "ответь 'thought'.\n"
                  "Примеры:\n"
                  "  «начал тренировку» → activity\n"
                  "  «пошёл гулять» → activity\n"
                  "  «сейчас работаю над задачей» → activity\n"
                  "  «поел» (привычка #1 про еду) → 1\n"
                  "  «мне грустно» → thought\n"
                  "  «подумал о работе» → thought\n"
                  "Ответ одним словом или цифрой.")
        user = (f"{habits_block}"
                f"Сообщение юзера: «{message[:300]}»\nОтвет:")
    else:
        system = ("/no_think\nUser reported an action or fact. Classify:\n"
                  "  1. Match to habit → habit NUMBER\n"
                  "  2. Physical activity in progress → 'activity'\n"
                  "  3. Just a thought/observation → 'thought'\n"
                  "Examples: 'started training'→activity, 'went for walk'→activity,\n"
                  "'feel sad'→thought, 'had lunch' (habit #1)→1.")
        user = (f"{habits_block_en}Message: «{message[:300]}»\nAnswer:")
    try:
        result, _ = _graph_generate(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            max_tokens=10, temp=0.1, top_k=10,
        )
    except Exception as e:
        log.warning(f"[intent_router] fact subtype failed: {e}")
        return ("thought", 0.3, None)
    text = (result or "").strip().lower()
    # Номер (instance)?
    m = re.search(r"\d+", text)
    if m and recurring_list:
        n = int(m.group(0))
        if 1 <= n <= len(recurring_list):
            return ("instance", 0.8, recurring_list[n - 1]["id"])
    if "activity" in text:
        return ("activity", 0.75, None)
    return ("thought", 0.6, None)


# ── Главная функция маршрутизации ─────────────────────────────────────────

def route(message: str, lang: str = "ru",
          use_cache: bool = True) -> dict:
    """Маршрутизация сообщения юзера в структурированный intent.

    Возвращает dict:
      {
        "kind": "task"|"fact"|"constraint_event"|"chat",
        "subtype": str|None,
        "target_goal_id": str|None,
        "confidence_top": float,
        "confidence_sub": float,
        "source": "cache"|"llm"|"fallback",
      }
    """
    key = _cache_key(message)
    if use_cache:
        cached = _cache_get(key)
        if cached:
            cached["source"] = "cache"
            return cached

    top_kind, top_conf = _classify_top(message, lang=lang)
    result = {
        "kind": top_kind,
        "subtype": None,
        "target_goal_id": None,
        "confidence_top": top_conf,
        "confidence_sub": 0.0,
        "source": "llm",
    }

    if top_kind == "chat":
        _cache_put(key, result)
        return result

    if top_kind == "task":
        sub, sub_conf = _classify_subtype_task(message, lang=lang)
        result["subtype"] = sub
        result["confidence_sub"] = sub_conf
        _cache_put(key, result)
        return result

    if top_kind == "fact":
        try:
            from .recurring import list_recurring
            recs = list_recurring(active_only=True)
        except Exception:
            recs = []
        sub, sub_conf, gid = _classify_subtype_fact(message, lang=lang,
                                                     recurring_list=recs)
        result["subtype"] = sub
        result["confidence_sub"] = sub_conf
        result["target_goal_id"] = gid
        _cache_put(key, result)
        return result

    if top_kind == "constraint_event":
        # Для нарушений используем существующий scanner (он matched constraints)
        # scanner вызовется в /assist ниже отдельно; здесь только помечаем
        result["subtype"] = "violation"
        result["confidence_sub"] = top_conf
        _cache_put(key, result)
        return result

    # Default (command и пр.)
    _cache_put(key, result)
    return result


# ── Helpers для /assist ───────────────────────────────────────────────────

def make_draft_card(kind: str, subtype: str, message: str,
                     lang: str = "ru") -> dict:
    """Сформировать карточку-предложение для юзера с кнопками подтверждения.

    Используется когда router решил `new_recurring` / `new_constraint` /
    `new_goal` — юзер видит что система хочет создать, может подтвердить
    или отредактировать.
    """
    draft = {"text": message[:200], "kind": subtype}
    if subtype == "new_recurring":
        # Дефолт: 1 раз в день, без категории
        draft["schedule"] = {"times_per_day": 1}
        draft["mode"] = "rhythm"
    elif subtype == "new_constraint":
        draft["polarity"] = "avoid"
        draft["mode"] = "free"
    elif subtype == "new_goal":
        draft["mode"] = "horizon"

    labels = {
        "new_recurring":  ("Создать привычку?", "Create habit?"),
        "new_constraint": ("Создать ограничение?", "Create constraint?"),
        "new_goal":       ("Создать цель?", "Create goal?"),
    }
    ru, en = labels.get(subtype, ("Подтвердить?", "Confirm?"))
    return {
        "type": "intent_confirm",
        "kind": subtype,
        "draft": draft,
        "title": ru if lang == "ru" else en,
        "description_ru": f"Система распознала: «{message[:150]}»",
        "description_en": f"System detected: «{message[:150]}»",
        "prompt_user": True,
    }
