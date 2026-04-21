"""HTTP helpers: JSON response envelope + error wrapping.

Единая форма ответа Flask-endpoint'а:

    @json_endpoint
    def some_route():
        if not valid_input:
            raise APIError("empty topic")
        result = do_work()
        return {"ok": True, "result": result}

- `APIError("msg")` → `jsonify({"error": msg}), status` (default 400).
- Любое другое исключение → `jsonify({"error": str(e), "endpoint": fn.__name__}), 500` + log.exception.
- dict → jsonify автоматически. Flask Response / tuple — пропускаются как есть.

Цель: убрать boilerplate `return jsonify({"error": str(e)})` в catch-all
блоках (их в graph_routes.py 7 штук), и дать единую точку для новых
endpoint'ов. Декоратор применяется ПОСЛЕ @app.route, ближе к функции.
"""
import functools
import logging

from flask import Response, jsonify

log = logging.getLogger(__name__)


class APIError(Exception):
    """Ошибка уровня endpoint'а: сообщение → клиент, status code выбирается.

    Используется вместо `return jsonify({"error": ...})` чтобы позволить
    raise'ить валидацию из любого вложенного места внутри handler'а.
    """

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def json_endpoint(fn):
    """Декоратор: dict-return → jsonify, APIError → json-error, Exception → 500.

    Оставляет Response/tuple return-value нетронутым (для cases где
    endpoint хочет свой status code или отдаёт файл).
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            result = fn(*args, **kwargs)
        except APIError as ae:
            return jsonify({"error": ae.message}), ae.status
        except Exception as e:
            log.exception(f"[{fn.__name__}] unhandled")
            return jsonify({"error": str(e), "endpoint": fn.__name__}), 500
        if isinstance(result, dict):
            return jsonify(result)
        if isinstance(result, (Response, tuple)):
            return result
        return result
    return wrapper
