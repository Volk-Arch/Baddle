"""8-region РГК-карта состояний по химическому профилю.

См. [planning/rgk-spec.md §5 «Карта состояний РГК»](../planning/rgk-spec.md).
Каждый регион — целевой профиль (DA, 5HT, NE, ACh, GABA) ∈ [0,1]⁵.
Nearest по L2-расстоянию в 5D пространстве.

Использование:
    from .user_state_map import nearest_named_state
    state = nearest_named_state(da=0.7, s=0.5, ne=0.4, ach=0.8, gaba=0.5)
    # → {"key": "explore", "label": "Исследование", "emoji": "🟡", ...}

Это **состояние юзера как такового** (не sync_regime FLOW/REST/PROTECT/CONFESS,
который про синхронизацию с системой). Regions взяты из РГК v1.0 spec §5
и описывают химический профиль: ↑↑↑=1.0, ↑↑=0.85, ↑=0.7, ↗=0.6, ↔=0.5,
↘=0.4, ↓=0.3, ↓↓=0.15, ↓↓↓=0.0.
"""
from typing import Optional

# (key, da, 5ht, ne, ach, gaba, emoji, label_ru, advice)
# Профили из rgk-spec.md §5:
#   🔵 ПОТОК       | DA↑, ACh↑, NE↗, 5-HT↗               | Фокус + гибкость
#   🟢 УСТОЙЧИВОСТЬ| 5-HT↑, GABA↑                        | Спокойствие, рутина
#   🟠 ФОКУС/ТРЕВОГА| NE↑↑, 5-HT↓, GABA↓                 | Туннельное внимание
#   🟡 ИССЛЕДОВАНИЕ| ACh↑, DA↗, NE↓                      | Любопытство, аналогии
#   🔴 ПЕРЕГРУЗ    | NE↑↑↑, GABA↓↓                       | Хаос, скачки
#   ⚫ ЗАСТОЙ      | DA↓↓                                | Вязкость, повторение
#   ⚪ ВЫГОРАНИЕ   | DA↓, NE↑(хрон), ACh↓                | Цинизм, автоматизм
#   ✨ ИНСАЙТ      | ACh↑↑, DA(пик)                      | Новый аттрактор
_STATES = [
    # key,        da,   5ht,  ne,   ach,  gaba, emoji, label,             advice
    ("flow",      0.70, 0.60, 0.60, 0.70, 0.50, "🔵", "Поток",          "Оптимальная вовлечённость. Сложные задачи сейчас."),
    ("stable",    0.50, 0.85, 0.40, 0.50, 0.85, "🟢", "Устойчивость",   "Спокойствие, терпение. Рутина — ОК."),
    ("focus",     0.60, 0.30, 0.90, 0.40, 0.30, "🟠", "Фокус-Тревога",  "Туннельное внимание. Выдох 8 сек, расфокусировка взгляда."),
    ("explore",   0.60, 0.50, 0.30, 0.90, 0.50, "🟡", "Исследование",   "Любопытство, аналогии. Сузить конус если распыляешься."),
    ("overload",  0.50, 0.20, 1.00, 0.50, 0.15, "🔴", "Перегруз",       "Хаос, скачки. STOP: заземление + 4-7-8 дыхание ×3."),
    ("apathy",    0.15, 0.50, 0.30, 0.40, 0.60, "⚫", "Застой",         "Вязкость, повторение. Сдвиг фазы: холод + 5 движений."),
    ("burnout",   0.30, 0.40, 0.70, 0.30, 0.50, "⚪", "Выгорание",      "Цинизм, автоматизм. Восстановление: 10 мин тишины."),
    ("insight",   0.85, 0.50, 0.40, 0.95, 0.50, "✨", "Инсайт",         "Запиши/озвучь сразу. Зафиксируй аттрактор."),
]


def nearest_named_state(da: float, s: float, ne: float,
                         ach: float = 0.5, gaba: float = 0.5) -> dict:
    """Ближайший регион РГК-карты к (da, 5ht, ne, ach, gaba) по L2-дистанции.

    Возвращает {key, label, advice, emoji, distance, coord} где coord — целевой
    профиль ближайшего региона в порядке (da, 5ht, ne, ach, gaba).

    ACh/GABA имеют default 0.5 для backward-compat когда вызывающий код
    не передаёт их (например, legacy snapshots до Phase D).
    """
    target = (
        max(0.0, min(1.0, float(da))),
        max(0.0, min(1.0, float(s))),
        max(0.0, min(1.0, float(ne))),
        max(0.0, min(1.0, float(ach))),
        max(0.0, min(1.0, float(gaba))),
    )
    best = None
    best_d = float("inf")
    for entry in _STATES:
        key, td, ts, tn, ta, tg, emoji, label, advice = entry
        candidate = (td, ts, tn, ta, tg)
        d = sum((target[i] - candidate[i]) ** 2 for i in range(5)) ** 0.5
        if d < best_d:
            best_d = d
            best = (key, label, advice, emoji, candidate)
    key, label, advice, emoji, coord = best
    return {
        "key": key,
        "label": label,
        "advice": advice,
        "emoji": emoji,
        "distance": round(best_d, 3),
        "coord": list(coord),
    }


def list_named_states() -> list[dict]:
    """UI может читать полный список для рендера карты."""
    return [
        {"key": k, "da": d, "serotonin": h, "ne": n, "ach": a, "gaba": g,
         "emoji": emoji, "label": label, "advice": advice}
        for k, d, h, n, a, g, emoji, label, advice in _STATES
    ]
