"""Voronoi карта именованных состояний пользователя в (T, A)-пространстве.

T (emotional tone) ≈ serotonin — положительная валентность, стабильность
A (consciousness activation) ≈ mean(dopamine, norepinephrine) — arousal

Каждое состояние — точка в [0, 1]². `nearest_named_state(t, a)` находит
ближайшую метку. Список + координаты взяты из MindBalance v4 (прототип
Игоря) и соответствуют 10 регионам эмоционально-когнитивного пространства.

Использование:
    from .user_state_map import nearest_named_state
    state = nearest_named_state(t=0.85, a=0.9)   # → "flow"

Это не режим симбиоза (FLOW/REST/PROTECT/CONFESS про синхронизацию
с системой), а **состояние юзера как такового**. Разрешение выше —
10 регионов вместо 4 — чтобы различать apathy ≠ burnout ≠ disappointment.
"""

from typing import Optional

# (t, a, label_ru, advice_key)
_STATES = [
    # key,              t,    a,    label_ru,         advice
    ("flow",            0.85, 0.90, "Поток",          "Оптимальная вовлечённость. Сложные задачи сейчас."),
    ("inspiration",     0.90, 0.95, "Вдохновение",    "Творческий подъём. Генерируй идеи, записывай всё."),
    ("curiosity",       0.48, 0.82, "Любопытство",    "Активное исследование. Задавай вопросы."),
    ("gratitude",       0.64, 0.58, "Благодарность",  "Реальность превзошла ожидания. Зафиксируй это."),
    ("neutral",         0.50, 0.50, "Нейтральное",    "Баланс. Хорошая точка для выбора направления."),
    ("meditation",      0.43, 0.21, "Медитация",      "Спокойное наблюдение без реакции."),
    ("apathy",          0.35, 0.25, "Апатия",         "Низкая вовлечённость. Начни с маленькой задачи."),
    ("stress",          0.30, 0.85, "Стресс",         "Высокое напряжение. Разбей задачу на мелкие. Дыши."),
    ("disappointment",  0.26, 0.40, "Разочарование",  "Ожидания не оправдались. Пересмотри их."),
    ("burnout",         0.10, 0.40, "Выгорание",      "Критическое истощение. Отдых обязателен."),
]


def nearest_named_state(t: float, a: float) -> dict:
    """Ближайшая именованная точка к (t, a) по евклидовой дистанции.

    Возвращает {key, label, advice, distance, coord}.
    """
    t = max(0.0, min(1.0, float(t)))
    a = max(0.0, min(1.0, float(a)))
    best = None
    best_dist = float("inf")
    for key, st, sa, label, advice in _STATES:
        d = ((t - st) ** 2 + (a - sa) ** 2) ** 0.5
        if d < best_dist:
            best_dist = d
            best = (key, st, sa, label, advice)
    key, st, sa, label, advice = best
    return {
        "key": key,
        "label": label,
        "advice": advice,
        "distance": round(best_dist, 3),
        "coord": [st, sa],
    }


def list_named_states() -> list[dict]:
    """UI может читать полный список для рендера карты."""
    return [
        {"key": k, "t": t, "a": a, "label": label, "advice": advice}
        for k, t, a, label, advice in _STATES
    ]
