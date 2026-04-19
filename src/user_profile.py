"""User profile — персистентная статика о пользователе.

Структура (пять категорий + context):

  food / work / health / social / learning — каждая содержит
    preferences: list[str]  — что любит / предпочитает
    constraints: list[str]  — чего избегать / нельзя

  context — произвольный key-value (profession, tz, wake/sleep hour, ...)

Файл: `user_profile.json`. Читается при загрузке ассистента, инжектится
в LLM-промпты классификации и execute (добавляет ограничения в контекст).

Если категория пустая при request'е на эту тему — uncertainty-driven
learning: система спрашивает юзера, парсит ответ, сохраняет.
"""
import json
import logging
import time
from typing import Optional

log = logging.getLogger(__name__)

from .paths import USER_PROFILE_FILE as _PROFILE_FILE

CATEGORIES = ("food", "work", "health", "social", "learning")
CATEGORY_LABELS_RU = {
    "food":     "Еда / питание",
    "work":     "Работа",
    "health":   "Здоровье / тело",
    "social":   "Социальное",
    "learning": "Обучение",
}


def _empty_profile() -> dict:
    return {
        "categories": {c: {"preferences": [], "constraints": []} for c in CATEGORIES},
        "context": {},     # profession, tz, wake_hour, sleep_hour, ...
        "updated_at": None,
    }


def load_profile() -> dict:
    """Load profile from disk. Returns empty skeleton if file missing/broken."""
    if _PROFILE_FILE.exists():
        try:
            data = json.loads(_PROFILE_FILE.read_text(encoding="utf-8"))
            # Нормализация: гарантируем что все категории присутствуют
            cats = data.setdefault("categories", {})
            for c in CATEGORIES:
                entry = cats.setdefault(c, {})
                entry.setdefault("preferences", [])
                entry.setdefault("constraints", [])
            data.setdefault("context", {})
            return data
        except Exception as e:
            log.warning(f"[user_profile] load failed: {e}")
    return _empty_profile()


def save_profile(profile: dict):
    """Atomic write to disk."""
    profile["updated_at"] = time.time()
    try:
        tmp = _PROFILE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_PROFILE_FILE)
    except Exception as e:
        log.warning(f"[user_profile] save failed: {e}")


# ── Helpers ────────────────────────────────────────────────────────────────

def get_category(cat: str, profile: Optional[dict] = None) -> dict:
    """Возвращает {preferences: [...], constraints: [...]} для категории."""
    p = profile or load_profile()
    return p.get("categories", {}).get(cat) or {"preferences": [], "constraints": []}


def add_item(cat: str, kind: str, text: str) -> dict:
    """kind ∈ {preferences, constraints}. Dedup по strip.lower()."""
    if cat not in CATEGORIES:
        raise ValueError(f"unknown category: {cat}")
    if kind not in ("preferences", "constraints"):
        raise ValueError(f"unknown kind: {kind}")
    text = (text or "").strip()
    if not text:
        return load_profile()
    profile = load_profile()
    arr = profile["categories"][cat].setdefault(kind, [])
    existing = {a.strip().lower() for a in arr}
    if text.strip().lower() not in existing:
        arr.append(text)
    save_profile(profile)
    return profile


def remove_item(cat: str, kind: str, text: str) -> dict:
    """Удалить запись по exact match (case-insensitive)."""
    profile = load_profile()
    arr = profile["categories"].get(cat, {}).get(kind, [])
    new_arr = [a for a in arr if a.strip().lower() != (text or "").strip().lower()]
    profile["categories"][cat][kind] = new_arr
    save_profile(profile)
    return profile


def set_context(key: str, value) -> dict:
    """Установить произвольное поле context."""
    profile = load_profile()
    profile.setdefault("context", {})[key] = value
    save_profile(profile)
    return profile


def is_category_empty(cat: str, profile: Optional[dict] = None) -> bool:
    """Нет ни preferences, ни constraints в категории — нужно спрашивать."""
    entry = get_category(cat, profile)
    return not entry.get("preferences") and not entry.get("constraints")


# ── Prompt injection summary ──────────────────────────────────────────────

def profile_summary_for_prompt(
    cats: Optional[list[str]] = None,
    lang: str = "ru",
    profile: Optional[dict] = None,
) -> str:
    """Короткий текстовый summary для LLM-промпта.

    Возвращает compact string что юзер любит/не любит в указанных категориях.
    Пустая строка если нечего говорить. Используется в execute_via_zones,
    classify_intent_llm и др. — инжектится в system/user prompts.

    Пример (lang="ru", cats=["food"]):
        "Профиль юзера: ест=[здоровое питание, завтрак обязательно],
         не ест=[орехи, молочное]"
    """
    p = profile or load_profile()
    cats = cats or list(CATEGORIES)

    parts: list[str] = []
    for cat in cats:
        if cat not in CATEGORIES:
            continue
        entry = p.get("categories", {}).get(cat) or {}
        prefs = entry.get("preferences") or []
        cons = entry.get("constraints") or []
        if not prefs and not cons:
            continue
        label = CATEGORY_LABELS_RU.get(cat, cat) if lang == "ru" else cat
        bits = [f"[{label}]"]
        if prefs:
            bits.append(("нрав.: " if lang == "ru" else "likes: ") + ", ".join(prefs))
        if cons:
            bits.append(("избег.: " if lang == "ru" else "avoids: ") + ", ".join(cons))
        parts.append(" · ".join(bits))

    if not parts:
        return ""

    ctx = p.get("context") or {}
    ctx_bits = []
    if ctx.get("profession"):
        ctx_bits.append(("проф: " if lang == "ru" else "job: ") + str(ctx["profession"]))
    if ctx.get("wake_hour") is not None:
        ctx_bits.append(f"wake {ctx['wake_hour']}:00")
    if ctx.get("sleep_hour") is not None:
        ctx_bits.append(f"sleep {ctx['sleep_hour']}:00")
    if ctx_bits:
        parts.insert(0, " · ".join(ctx_bits))

    header = "Профиль юзера: " if lang == "ru" else "User profile: "
    return header + " | ".join(parts)


# ── LLM-assisted parsing ───────────────────────────────────────────────────

def parse_category_answer(text: str, cat: str, lang: str = "ru") -> dict:
    """LLM-разбор ответа юзера на уточняющий вопрос о категории.

    Вход: свободный текст ("не ем орехи, люблю курицу и овсянку").
    Выход: {preferences: [...], constraints: [...]}.

    Если LLM недоступна — очень простая heuristic: запятые → все в preferences.
    """
    from .graph_logic import _graph_generate

    text = (text or "").strip()
    if not text:
        return {"preferences": [], "constraints": []}

    if lang == "ru":
        system = ("/no_think\nРазбери ответ юзера про категорию "
                  f"«{cat}». Формат вывода (по строке на пункт, только prefix):\n"
                  "PREF: люблю X\n"
                  "AVOID: не переношу Y\n"
                  "Без вступления, без объяснений, только строки.")
    else:
        system = ("/no_think\nParse user answer about category "
                  f"'{cat}'. Output format (one per line, prefix only):\n"
                  "PREF: likes X\n"
                  "AVOID: avoids Y\n"
                  "No preamble, just lines.")

    try:
        out, _ = _graph_generate(
            [{"role": "system", "content": system},
             {"role": "user", "content": text}],
            max_tokens=200, temp=0.2, top_k=10,
        )
    except Exception:
        out = ""

    prefs: list[str] = []
    cons: list[str] = []
    for line in (out or "").split("\n"):
        ln = line.strip().lstrip("-•* \t").rstrip()
        if not ln:
            continue
        upper = ln.upper()
        if upper.startswith("PREF:"):
            item = ln.split(":", 1)[1].strip()
            if len(item) > 1:
                prefs.append(item)
        elif upper.startswith("AVOID:"):
            item = ln.split(":", 1)[1].strip()
            if len(item) > 1:
                cons.append(item)

    # Fallback если LLM не дала ни одного prefix-line
    if not prefs and not cons:
        pieces = [p.strip() for p in text.replace("\n", ",").split(",") if p.strip()]
        neg_markers = ("не ", "без ", "not ", "no ", "avoid ", "without ")
        for p in pieces:
            lower = p.lower()
            if any(lower.startswith(m) for m in neg_markers):
                cons.append(p)
            else:
                prefs.append(p)

    return {"preferences": prefs[:5], "constraints": cons[:5]}
