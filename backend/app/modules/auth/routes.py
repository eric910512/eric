from flask import current_app, g, jsonify, make_response, request
from flask_jwt_extended import decode_token, get_jwt

from app.core.api import APIError, ok
from app.core.audit import record_auth_event, record_data_event
from app.core.auth import require_auth
from app.core.timeutil import iso_utc
from app.extensions import db
from app.modules.auth import auth_bp
from app.models.enums import AuditAction, AuditOutcome
from app.modules.auth import sessions
from app.models import User
from app.modules.auth.services import access_token_for, authenticate, change_password, user_payload


# ------------------------------------------------------------------ refresh cookie (api-design.md §1.5)


def set_refresh_cookie(response, raw, expires):
    """HttpOnly + Secure + SameSite=Strict, Path=/api/v1/auth, host-only (no Domain): the browser
    sends it only to the auth endpoints of this origin, and page JavaScript cannot read it."""
    cfg = current_app.config
    response.set_cookie(
        cfg["REFRESH_COOKIE_NAME"], raw, expires=expires, path=cfg["REFRESH_COOKIE_PATH"],
        secure=cfg["REFRESH_COOKIE_SECURE"], httponly=True, samesite=cfg["REFRESH_COOKIE_SAMESITE"],
    )
    return response


def clear_refresh_cookie(response):
    cfg = current_app.config
    response.delete_cookie(cfg["REFRESH_COOKIE_NAME"], path=cfg["REFRESH_COOKIE_PATH"], secure=cfg["REFRESH_COOKIE_SECURE"],
                           httponly=True, samesite=cfg["REFRESH_COOKIE_SAMESITE"])
    return response


def _token_body(token, user):
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": int(current_app.config["JWT_ACCESS_TOKEN_EXPIRES"].total_seconds()),
        "user": user_payload(user),
    }


def _refresh_cookie():
    return request.cookies.get(current_app.config["REFRESH_COOKIE_NAME"])


@auth_bp.post("/login")
def login():
    body = request.get_json(silent=True) or {}
    email, password = body.get("email"), body.get("password")
    details = [
        {"field": name, "issue": "is required"}
        for name, value in (("email", email), ("password", password))
        if not isinstance(value, str) or not value.strip()
    ]
    if details:
        raise APIError(400, "VALIDATION_ERROR", "請輸入帳號與密碼", details)

    try:
        token, user, refresh = authenticate(email, password)
    except APIError:
        db.session.commit()  # keep the failed-attempt counter and LOGIN_FAILED audit row
        raise
    db.session.commit()

    # the refresh token is only in the cookie, never in the body
    return set_refresh_cookie(make_response(ok(_token_body(token, user))), refresh, sessions.expiry_of(refresh))


REFRESH_MESSAGES = {
    "REFRESH_TOKEN_EXPIRED": "登入已逾時，請重新登入",
    "REFRESH_TOKEN_REVOKED": "這個登入已結束，請重新登入",
    "REFRESH_TOKEN_REUSED": "偵測到重複使用的登入憑證，為了安全已結束這個登入，請重新登入",
}


@auth_bp.post("/refresh")
def refresh():
    """New access token for the session of the refresh-token cookie; the cookie is rotated.
    Requires ``X-Requested-With: XMLHttpRequest`` (a cross-site form cannot send it; with
    SameSite=Strict this is defence in depth). Any refusal → 401 and the cookie is cleared."""
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        raise APIError(403, "FORBIDDEN", "Refresh requires the X-Requested-With: XMLHttpRequest header")
    try:
        user, sid, raw = sessions.rotate(_refresh_cookie())
    except sessions.RefreshError as e:
        if e.code == "REFRESH_TOKEN_REUSED":
            record_auth_event(AuditAction.LOGOUT, AuditOutcome.FAILURE, user=e.user, identifier=e.user.email, reason="refresh_token_reused")
        db.session.commit()
        body = {"error": {"code": e.code, "message": REFRESH_MESSAGES.get(e.code, "請重新登入"), "details": [], "request_id": g.get("request_id")}}
        response = jsonify(body)
        response.status_code = 401
        return clear_refresh_cookie(response)
    db.session.commit()
    return set_refresh_cookie(make_response(ok(_token_body(access_token_for(user, sid), user))), raw, sessions.expiry_of(raw))


@auth_bp.get("/me")
@require_auth()
def me():
    user = g.current_user
    data = user_payload(user)
    data["last_login_at"] = iso_utc(user.last_login_at)
    if user.nurse_profile:
        data["nurse_profile"] = {
            "staff_code": user.nurse_profile.staff_code,
            "department": user.nurse_profile.department,
            "title": user.nurse_profile.title,
        }
    return ok(data)


@auth_bp.put("/password")
@require_auth()
def change_password_route():
    """Set a new password (api-design.md §3). Body: {current_password, new_password} → 204.
    Also completes the first-login change of a system temporary password."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise APIError(400, "VALIDATION_ERROR", "Request body must be a JSON object")
    change_password(g.current_user, body.get("current_password"), body.get("new_password"))
    db.session.commit()
    return "", 204


def _session_to_end():
    """(user, sid) of the session a sign-out ends: the Bearer token's (a correctly signed token
    that has only expired still names its session), otherwise the refresh cookie's."""
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        try:
            claims = decode_token(header[7:], allow_expired=True)
        except Exception:  # noqa: BLE001 — a forged / malformed token ends nothing
            claims = None
        if claims and claims.get("sid"):
            user = db.session.execute(db.select(User).filter_by(public_id=claims["sub"])).scalar_one_or_none()
            if user is not None:
                return user, claims["sid"]
    return sessions.family_of(_refresh_cookie())


@auth_bp.post("/logout")
def logout():
    """Server-side sign-out → 204: ends this session (its access and refresh tokens stop working
    at once) and clears the refresh cookie. Works after the access token has expired, too."""
    user, sid = _session_to_end()
    if user is not None and sessions.revoke(user, sid):
        record_auth_event(AuditAction.LOGOUT, AuditOutcome.SUCCESS, user=user, identifier=user.email)
    db.session.commit()
    return clear_refresh_cookie(current_app.response_class(status=204))


@auth_bp.get("/sessions")
@require_auth()
def my_sessions():
    """The signed-in account's active sessions (sign-ins), the current one marked."""
    return ok(sessions.list_sessions(g.current_user, get_jwt().get("sid")))


@auth_bp.delete("/sessions/<sid>")
@require_auth()
def end_session(sid):
    """End one of your own sessions (e.g. a device you no longer use) → 204; unknown → 404."""
    user = g.current_user
    if not sessions.revoke(user, sid):
        raise APIError(404, "NOT_FOUND", "Session not found")
    record_data_event(AuditAction.UPDATE, "users", user.public_id, changes={"session_revoked": True, "current": sid == get_jwt().get("sid")})
    db.session.commit()
    return "", 204


@auth_bp.post("/sessions/revoke-others")
@require_auth()
def end_other_sessions():
    """Sign out everywhere else; this session keeps working. → {revoked}."""
    user = g.current_user
    n = sessions.revoke_all(user, keep=get_jwt().get("sid"))
    if n:
        record_data_event(AuditAction.UPDATE, "users", user.public_id, changes={"sessions_revoked": n, "kept_current": True})
    db.session.commit()
    return ok({"revoked": n})
