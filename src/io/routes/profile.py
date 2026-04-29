"""Profile routes — /profile/* endpoints (W14.6b2 extract)."""
from flask import request, jsonify

from . import assistant_bp


@assistant_bp.route("/profile", methods=["GET"])
def profile_get():
    """Return full user profile."""
    from ...user_profile import load_profile, CATEGORIES, CATEGORY_LABELS_RU
    return jsonify({
        "profile": load_profile(),
        "categories": list(CATEGORIES),
        "labels_ru": CATEGORY_LABELS_RU,
    })


@assistant_bp.route("/profile/add", methods=["POST"])
def profile_add():
    """Body: {category, kind: preferences|constraints, text}"""
    from ...user_profile import add_item
    d = request.get_json(force=True) or {}
    try:
        p = add_item(d.get("category", ""), d.get("kind", ""), d.get("text", ""))
        return jsonify({"ok": True, "profile": p})
    except ValueError as e:
        return jsonify({"error": str(e)})


@assistant_bp.route("/profile/remove", methods=["POST"])
def profile_remove():
    """Body: {category, kind, text}"""
    from ...user_profile import remove_item
    d = request.get_json(force=True) or {}
    p = remove_item(d.get("category", ""), d.get("kind", ""), d.get("text", ""))
    return jsonify({"ok": True, "profile": p})


@assistant_bp.route("/profile/context", methods=["POST"])
def profile_context():
    """Body: {key, value} для свободного context-поля."""
    from ...user_profile import set_context
    d = request.get_json(force=True) or {}
    p = set_context(d.get("key", ""), d.get("value"))
    return jsonify({"ok": True, "profile": p})


@assistant_bp.route("/profile/learn", methods=["POST"])
def profile_learn():
    """Uncertainty-learning: LLM-разбор ответа юзера на profile_clarify-вопрос.

    Body: { "category": "food", "answer": "не ем орехи, люблю курицу",
            "original_message": "хочу покушать", "lang": "ru" }

    Парсит answer на preferences/constraints, сохраняет в profile[category].
    Возвращает добавленные items + сохраняет в profile автоматически.
    """
    from ...user_profile import parse_category_answer, add_item, CATEGORIES
    d = request.get_json(force=True) or {}
    cat = d.get("category")
    answer = (d.get("answer") or "").strip()
    lang = d.get("lang", "ru")
    if cat not in CATEGORIES:
        return jsonify({"error": f"unknown category: {cat}"})
    if not answer:
        return jsonify({"error": "empty answer"})

    parsed = parse_category_answer(answer, cat, lang=lang)
    for text in parsed.get("preferences", []):
        add_item(cat, "preferences", text)
    for text in parsed.get("constraints", []):
        add_item(cat, "constraints", text)

    return jsonify({
        "ok": True,
        "category": cat,
        "added": parsed,
        "original_message": d.get("original_message", ""),
    })
