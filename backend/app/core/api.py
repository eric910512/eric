"""Response envelope, JSON errors and request IDs (api-design.md §1.2, §1.3, §1.8)."""

import uuid

from flask import current_app, g, jsonify, request
from werkzeug.exceptions import HTTPException

API_V1_PREFIX = "/api/v1"

_HTTP_ERROR_CODES = {
    400: "VALIDATION_ERROR",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "INVALID_STATE",
    429: "RATE_LIMITED",
}


class APIError(Exception):
    def __init__(self, status, code, message, details=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or []


def ok(data, meta=None, status=200):
    body = {"data": data}
    if meta:
        body["meta"] = meta
    return jsonify(body), status


def _error_response(status, code, message, details=None):
    body = {
        "error": {
            "code": code,
            "message": message,
            "details": details or [],
            "request_id": g.get("request_id"),
        }
    }
    return jsonify(body), status


def init_api(app):
    # Keep Chinese text readable and preserve the designed key order in responses.
    app.json.ensure_ascii = False
    app.json.sort_keys = False

    @app.before_request
    def _assign_request_id():
        incoming = request.headers.get("X-Request-ID", "")
        g.request_id = incoming if 0 < len(incoming) <= 64 else str(uuid.uuid4())

    @app.after_request
    def _echo_request_id(response):
        if g.get("request_id"):
            response.headers["X-Request-ID"] = g.request_id
        return response

    @app.errorhandler(APIError)
    def _handle_api_error(exc):
        return _error_response(exc.status, exc.code, exc.message, exc.details)

    @app.errorhandler(HTTPException)
    def _handle_http_error(exc):
        if not request.path.startswith("/api/"):
            return exc
        return _error_response(exc.code, _HTTP_ERROR_CODES.get(exc.code, "HTTP_ERROR"), exc.description)

    @app.errorhandler(Exception)
    def _handle_unexpected(exc):
        current_app.logger.exception("Unhandled error (request_id=%s)", g.get("request_id"))
        return _error_response(500, "INTERNAL_ERROR", "Internal server error")


def query_int(args, name, default, minimum, maximum):
    """Parse an integer query parameter or raise 400 VALIDATION_ERROR."""
    raw = args.get(name)
    if raw is None or raw == "":
        return default
    if not raw.lstrip("-").isdigit() or not minimum <= int(raw) <= maximum:
        raise APIError(400, "VALIDATION_ERROR", "Invalid query parameter",
                       [{"field": name, "issue": f"must be an integer between {minimum} and {maximum}"}])
    return int(raw)


def query_choice(args, name, default, choices):
    """Parse an enum-like query parameter or raise 400 VALIDATION_ERROR."""
    value = args.get(name) or default
    if value not in choices:
        raise APIError(400, "VALIDATION_ERROR", "Invalid query parameter",
                       [{"field": name, "issue": f"must be one of: {', '.join(choices)}"}])
    return value


def required_text(body, name, max_length):
    """A non-empty string field from a JSON body (stripped) or raise 400."""
    value = body.get(name)
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise APIError(400, "VALIDATION_ERROR", "輸入資料格式錯誤",
                       [{"field": name, "issue": f"is required (1–{max_length} characters)"}])
    return value.strip()
