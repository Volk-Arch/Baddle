"""Misc routes — patterns, sensor, debug, decompose, graph/assist (W14.6b5).

Patterns: weekday × category × outcome detector (cognitive heuristic).
Sensor: polymorphic body sensors stream (HRV, manual, simulator).
Debug: trigger-all _check_* test harness.
Decompose: goal → 3-bucket subtasks (AND/XOR/RESEARCH).
Graph/assist: dialogical loop (third control circuit) — clarifying questions.
"""
import logging
import time

from flask import request, jsonify

from . import assistant_bp

log = logging.getLogger(__name__)


@assistant_bp.route("/patterns", methods=["GET"])
def patterns_list():
    """Recent detected patterns (weekday × category × outcome).

    Query: ?today=1 — only today's weekday, ?hours=N — lookback (default 36).
    """
    from ...patterns import read_recent_patterns, patterns_for_today
    only_today = (request.args.get("today", "0") in ("1", "true"))
    if only_today:
        return jsonify({"patterns": patterns_for_today()})
    try:
        hours = int(request.args.get("hours", 36))
    except ValueError:
        hours = 36
    return jsonify({"patterns": read_recent_patterns(hours=hours)})


@assistant_bp.route("/patterns/run", methods=["POST"])
def patterns_run():
    """Manual trigger — запускает детектор сейчас (полезно для тестов и
    когда night_cycle ещё не отработал).
    """
    from ...patterns import detect_all
    d = request.get_json(silent=True) or {}
    try:
        days = int(d.get("days_back", 21))
    except (TypeError, ValueError):
        days = 21
    found = detect_all(days_back=days)
    return jsonify({"ok": True, "detected": len(found), "patterns": found})


# ── Dialogical loop (third control circuit) ──────────────────────────

@assistant_bp.route("/graph/assist", methods=["POST"])
def graph_assist():
    """Dialogical loop (third control circuit).

    Given current graph state, LLM asks ONE clarifying question whose answer
    would most reduce uncertainty. Optionally takes an answer to a prior
    question and materializes it as the appropriate node type:
      - mode=bayes      → evidence node on prior hypothesis
      - goal+subgoals   → new subgoal under goal (AND-like filling)
      - otherwise       → seed hypothesis

    Closes the third loop: system asks → user answers → graph grows.

    Request:
      { "lang": "ru" }                          # fresh question
      { "lang": "ru", "answer": "...",          # materialize an answer
        "question": "...", "mode": "bayes" }
    """
    from ...graph_logic import _graph, _graph_generate, _add_node
    d = request.get_json(force=True) or {}
    lang = d.get("lang", "ru")
    answer = (d.get("answer") or "").strip()
    requested_mode = d.get("mode")

    nodes = _graph["nodes"]
    goal_nodes = [(i, n) for i, n in enumerate(nodes)
                  if n.get("type") == "goal" and n.get("depth", 0) >= 0]
    goal_idx, goal_node = goal_nodes[0] if goal_nodes else (None, None)
    mode_id = requested_mode or (goal_node.get("mode") if goal_node else None) or \
              _graph.get("meta", {}).get("mode", "horizon")

    # NE spike on any /graph/assist activity (dialogical loop is engagement too)
    from ...substrate.horizon import get_global_state
    cs = get_global_state()
    cs.inject_ne(0.3)
    # Answer = модель угадала запрос → низкое d = подтверждение
    if answer:
        cs.update_neurochem(d=0.2)

    # ── Materialize path: user answered, add node of appropriate type ──
    if answer:
        directed = _graph["edges"]["directed"]
        manual_links = _graph["edges"]["manual_links"]

        if mode_id == "bayes" and goal_idx is not None:
            # Answer → evidence on the hypothesis (goal). Auto-classify support vs contradict.
            from ...graph_logic import _auto_evidence_relation, _bayesian_update_distinct, _d_from_relation
            rel, strength = _auto_evidence_relation(goal_node["text"], answer)
            d_val = _d_from_relation(rel, strength)
            old_conf = goal_node["confidence"]
            goal_node["confidence"] = _bayesian_update_distinct(old_conf, d_val)
            new_idx = _add_node(answer, depth=goal_node.get("depth", 0) + 1,
                                topic=goal_node.get("topic", ""),
                                confidence=strength, node_type="evidence")
            nodes[new_idx]["evidence_relation"] = rel
            nodes[new_idx]["evidence_strength"] = strength
            nodes[new_idx]["evidence_target"] = goal_idx
            directed.append([goal_idx, new_idx])
            pair = [min(goal_idx, new_idx), max(goal_idx, new_idx)]
            if pair not in manual_links:
                manual_links.append(pair)
            return jsonify({
                "ok": True, "node_idx": new_idx, "kind": "evidence",
                "relation": rel, "strength": strength,
                "prior": old_conf, "posterior": goal_node["confidence"],
            })

        elif goal_node is not None and (goal_node.get("subgoals") or
                                         mode_id in ("builder", "pipeline", "cascade", "scales", "tournament", "race")):
            # Answer → new subgoal under the goal
            subgoals = goal_node.setdefault("subgoals", [])
            new_idx = _add_node(answer, depth=goal_node.get("depth", 0),
                                topic=goal_node.get("topic", ""),
                                node_type="hypothesis")
            subgoals.append(new_idx)
            directed.append([goal_idx, new_idx])
            pair = [min(goal_idx, new_idx), max(goal_idx, new_idx)]
            if pair not in manual_links:
                manual_links.append(pair)
            return jsonify({"ok": True, "node_idx": new_idx, "kind": "subgoal"})

        else:
            # Answer → seed hypothesis (loose context)
            new_idx = _add_node(answer, depth=0, topic="", node_type="hypothesis")
            if goal_idx is not None:
                directed.append([new_idx, goal_idx])
                pair = [min(goal_idx, new_idx), max(goal_idx, new_idx)]
                if pair not in manual_links:
                    manual_links.append(pair)
            return jsonify({"ok": True, "node_idx": new_idx, "kind": "seed"})

    # ── Question path: generate one clarifying question ──
    # Build graph snapshot context for LLM
    context_lines = []
    if goal_node:
        context_lines.append(f"Цель: {goal_node['text'][:100]}")
    hypotheses = [n for n in nodes if n.get("type") in ("hypothesis", "thought")
                  and n.get("depth", 0) >= 0][:5]
    if hypotheses:
        context_lines.append("Текущие гипотезы:")
        for h in hypotheses:
            context_lines.append(f"- {h['text'][:80]} (conf={h.get('confidence', 0.5):.0%})")

    if lang == "ru":
        system = ("/no_think\nТы задаёшь ОДИН короткий уточняющий вопрос, "
                  "ответ на который сильнее всего уменьшит неопределённость в графе. "
                  "Без вступления. Максимум 20 слов. Один вопрос.")
        fallback_q = "Что важнее всего уточнить прямо сейчас?"
    else:
        system = ("/no_think\nAsk ONE short clarifying question whose answer "
                  "would most reduce graph uncertainty. No preamble. Max 20 words.")
        fallback_q = "What's most important to clarify right now?"

    ctx_text = "\n".join(context_lines) if context_lines else (
        "Граф пуст. Задай вопрос чтобы начать." if lang == "ru"
        else "Graph is empty. Ask to start.")
    try:
        q_text, _ = _graph_generate(
            [{"role": "system", "content": system},
             {"role": "user", "content": ctx_text}],
            max_tokens=60, temp=0.5, top_k=40,
        )
        q = (q_text or fallback_q).strip().split("\n")[0].strip()
        if not q:
            q = fallback_q
    except Exception as e:
        log.warning(f"[graph_assist] question gen failed: {e}")
        q = fallback_q

    # Hint on what the answer will become (helps UI preview)
    if mode_id == "bayes":
        answer_kind = "evidence"
    elif goal_node and (goal_node.get("subgoals") or
                        mode_id in ("builder", "pipeline", "cascade", "scales", "tournament", "race")):
        answer_kind = "subgoal"
    else:
        answer_kind = "seed"

    return jsonify({
        "question": q,
        "mode": mode_id,
        "answer_kind": answer_kind,
        "goal_idx": goal_idx,
        "graph_size": len(nodes),
    })


# ── Goal decomposition ──────────────────────────────────────────────────

_DECOMPOSE_MODE_SUGGESTION = {
    "and": "builder",           # все обязательны, порядок не строгий
    "xor": "tournament",        # выбор одного
    "research": "horizon",      # открытое исследование
}


def _parse_decompose_groups(text: str) -> dict:
    """Разобрать структурированный вывод LLM на 3 группы подзадач.

    Ожидаемый формат:
      AND: подзадача 1
      AND: подзадача 2
      XOR: вариант A
      XOR: вариант B
      RESEARCH: направление исследования

    Префикс case-insensitive, может быть с двоеточием, тире или пробелом.
    Строки без префикса → в AND (самый нейтральный bucket).
    """
    groups = {"and": [], "xor": [], "research": []}
    for raw_line in text.split("\n"):
        # lstrip только bullets/numbering (не трогаем трейлинг — там могут быть
        # значимые цифры типа «шаг 1»). rstrip пробельные.
        line = raw_line.lstrip(" \t-•*1234567890.)]:").rstrip()
        if not line or len(line) < 3:
            continue
        lower = line.lower()
        bucket = "and"  # default fallback
        content = line
        for prefix, key in [("research:", "research"), ("xor:", "xor"),
                            ("and:", "and"), ("research ", "research"),
                            ("xor ", "xor"), ("and ", "and")]:
            if lower.startswith(prefix):
                bucket = key
                content = line[len(prefix):].strip(" :-")
                break
        if content and len(content) > 2:
            groups[bucket].append(content)
    return groups


@assistant_bp.route("/assist/decompose", methods=["POST"])
def assist_decompose():
    """Goal decomposition → **подграфы разных режимов** (не плоский список).

    LLM классифицирует каждую подзадачу по трём bucket'ам:
      - AND      — все обязательны (сборка, шаги, баланс) → mode=builder
      - XOR      — выбор одного (сравнение вариантов) → mode=tournament
      - RESEARCH — открытое исследование (без финала) → mode=horizon

    UI может создать три раздельных subgraph'а с соответствующими
    пресетами precision/policy вместо одного плоского goal'а.

    Response:
      {
        "groups": {"and": [...], "xor": [...], "research": [...]},
        "mode_suggestions": {"and": "builder", ...},
        "subgoals": [...],     # backward compat: concat всех групп
        "raw": "..."
      }
    """
    from ...graph_logic import _graph_generate
    d = request.get_json(force=True)
    message = d.get("message", "")
    lang = d.get("lang", "ru")
    temp = float(d.get("temp", 0.5))
    top_k = int(d.get("top_k", 40))

    if not message:
        return jsonify({"error": "empty message"})

    if lang == "ru":
        system = (
            "/no_think\nРазбей задачу на подзадачи, класифицируя каждую в одну "
            "из трёх категорий:\n"
            "  AND      — все обязательны (части сборки, шаги плана, баланс)\n"
            "  XOR      — выбор одного варианта (сравнение альтернатив)\n"
            "  RESEARCH — открытое исследование без финала\n"
            "Формат вывода: каждая строка начинается с префикса + двоеточие.\n"
            "Пример:\n"
            "  AND: купить продукты\n"
            "  AND: приготовить блюдо\n"
            "  XOR: какое именно блюдо\n"
            "  RESEARCH: диетические ограничения гостей\n"
            "Не все категории обязательны. Без вступления, без нумерации, "
            "3-7 строк всего."
        )
    else:
        system = (
            "/no_think\nSplit task into subtasks, classifying each into one of "
            "three categories:\n"
            "  AND      — all required (assembly parts, pipeline steps, balance)\n"
            "  XOR      — pick one option (compare alternatives)\n"
            "  RESEARCH — open-ended exploration, no final state\n"
            "Format: each line starts with prefix + colon.\n"
            "Example:\n"
            "  AND: buy groceries\n"
            "  AND: cook dish\n"
            "  XOR: which dish to cook\n"
            "  RESEARCH: guests' dietary restrictions\n"
            "Not all categories required. No preamble, no numbering, "
            "3-7 lines total."
        )

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": message},
    ]
    try:
        text, _ = _graph_generate(messages, max_tokens=300, temp=temp, top_k=top_k)
    except Exception as e:
        return jsonify({"error": str(e)})

    groups = _parse_decompose_groups(text)
    # Backward compat: flat list (сохраняем порядок AND → XOR → RESEARCH)
    flat = groups["and"] + groups["xor"] + groups["research"]
    # Clamp: не больше 7 чтобы UI не перегружать
    flat = flat[:7]

    mode_suggestions = {
        key: _DECOMPOSE_MODE_SUGGESTION[key]
        for key in ("and", "xor", "research") if groups[key]
    }

    return jsonify({
        "groups": groups,
        "mode_suggestions": mode_suggestions,
        "subgoals": flat,
        "raw": text,
    })


# ── Sensor stream (polymorphic body sensors) ──────────────────────────
# Unified поток от любого источника: simulator, Polar, Apple Watch,
# manual check-in. UserState читает агрегат отсюда (не из конкретного
# HRVManager). См. docs/alerts-and-cycles.md + src/sensors/stream.py

@assistant_bp.route("/sensor/readings", methods=["GET"])
def sensor_readings():
    """Последние readings по kind/source за окно (секунды).

    Query: ?kind=hrv_snapshot&since=300&source=simulator
    """
    from ...sensors.stream import get_stream
    kind = request.args.get("kind")
    source = request.args.get("source")
    try:
        since = float(request.args.get("since", 300))
    except ValueError:
        since = 300.0
    readings = get_stream().recent(
        kinds=[kind] if kind else None,
        sources=[source] if source else None,
        since_seconds=since,
    )
    return jsonify({
        "count": len(readings),
        "active_sources": get_stream().active_sources(),
        "readings": [
            {"ts": r.ts, "source": r.source, "kind": r.kind,
             "metrics": r.metrics, "confidence": r.confidence}
            for r in readings
        ],
    })


@assistant_bp.route("/sensor/aggregate", methods=["GET"])
def sensor_aggregate():
    """Weighted HRV aggregate за окно (время-decay × confidence).

    Query: ?window=180
    """
    from ...sensors.stream import get_stream
    try:
        window = float(request.args.get("window", 180))
    except ValueError:
        window = 180.0
    agg = get_stream().latest_hrv_aggregate(window_s=window)
    activity = get_stream().recent_activity(window_s=60)
    return jsonify({
        "aggregate": agg,
        "activity_magnitude": activity,
        "active_sources": get_stream().active_sources(),
    })


# ── Debug: test harness для всех _check_* в cognitive_loop ────────────
# Прогоняет каждую `_check_*` функцию с force-сбросом throttle. Полезно
# чтобы видеть: какой alert реально emit'ится когда условия выполнены,
# какой молчит (условие/данные не дают), какой падает с ошибкой.

# Тяжёлые check'и (LLM-цикл, pump, REM) пропускаются по default чтобы
# один вызов не занимал минуту. ?include_heavy=1 — прогнать всё.
_HEAVY_CHECKS = {
    "_check_night_cycle",           # REM + Scout + Consolidation
    "_check_dmn_deep_research",     # full execute_deep pipeline
    "_check_dmn_converge",          # autorun loop
    "_check_dmn_continuous",        # pump-bridge LLM
}


@assistant_bp.route("/debug/alerts/trigger-all", methods=["POST", "GET"])
def debug_alerts_trigger_all():
    """Прогоняет все `_check_*` методы cognitive_loop с force-сбросом
    throttle, возвращает отчёт: что emitted alert / silent / error.

    Query: ?include_heavy=1 — включить pump/night/dmn_converge (долго!).
    """
    from ...process.cognitive_loop import get_cognitive_loop
    include_heavy = request.args.get("include_heavy") in ("1", "true", "yes")

    cl = get_cognitive_loop()

    # Monkey-patch _throttled чтобы всегда пропускал (и обнулял timer'ы)
    original_throttled = cl._throttled
    def force_throttled(attr, interval_s):
        try:
            setattr(cl, attr, 0.0)
        except Exception:
            pass
        return original_throttled(attr, interval_s)
    cl._throttled = force_throttled

    # Находим все _check_* методы
    check_names = sorted(
        m for m in dir(cl)
        if m.startswith("_check_") and callable(getattr(cl, m, None))
    )

    results = []
    try:
        for name in check_names:
            entry = {"name": name, "heavy": name in _HEAVY_CHECKS}
            if name in _HEAVY_CHECKS and not include_heavy:
                entry["status"] = "skipped_heavy"
                results.append(entry)
                continue
            try:
                from ...memory import workspace
                before_ts = time.time()
                fn = getattr(cl, name)
                t0 = time.time()
                fn()
                entry["elapsed_s"] = round(time.time() - t0, 3)
                # W14.5c-2: alerts читаются из графа (since_ts cursor) вместо
                # in-memory queue.
                new_alerts = workspace.list_recent_alerts(since_ts=before_ts)
                if new_alerts:
                    entry["status"] = "alert_emitted"
                    entry["alerts"] = [
                        {"type": n.get("action_kind"),
                         "severity": n.get("severity"),
                         "text": (n.get("text") or "")[:140]}
                        for n in new_alerts
                    ]
                else:
                    entry["status"] = "silent_ok"
            except Exception as e:
                entry["status"] = "error"
                entry["error"] = str(e)[:200]
            results.append(entry)
    finally:
        cl._throttled = original_throttled

    summary = {
        "total": len(results),
        "alert_emitted": sum(1 for r in results if r["status"] == "alert_emitted"),
        "silent_ok":     sum(1 for r in results if r["status"] == "silent_ok"),
        "error":         sum(1 for r in results if r["status"] == "error"),
        "skipped_heavy": sum(1 for r in results if r["status"] == "skipped_heavy"),
        "include_heavy": include_heavy,
    }
    return jsonify({"summary": summary, "results": results})
