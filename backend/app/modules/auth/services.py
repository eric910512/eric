"""Login logic: credential check, lockout, audit (api-design.md §3)."""

from datetime import timedelta

from flask import current_app
from flask_jwt_extended import create_access_token, get_jwt
from sqlalchemy import func, select
from werkzeug.security import check_password_hash, generate_password_hash

from app.core.api import APIError
from app.core.audit import record_auth_event, record_data_event
from app.core.passwords import password_problems
from app.core.timeutil import iso_utc
from app.extensions import db
from app.models import User
from app.models.base import utcnow
from app.models.enums import AuditAction, AuditOutcome
from app.modules.auth import sessions

# Checked when the email does not exist, so response time does not reveal which accounts exist.
_DUMMY_HASH = generate_password_hash("not-a-real-password")


def user_payload(user):
    return {
        "id": user.public_id,
        "display_name": user.display_name,
        "email": user.email,
        "role": user.role_name,
        "patient_id": user.patient_profile.public_id if user.patient_profile else None,
        # Accounts created with a system temporary password must set their own before using the API.
        "must_change_password": user.password_changed_at is None,
    }


def authenticate(email, password):
    """Return (access_token, user, refresh_token) or raise APIError; opens a server-side session.
    The refresh token goes only into the HttpOnly cookie (routes.set_refresh_cookie). Caller commits."""
    user = db.session.execute(
        select(User).where(func.lower(User.email) == email.strip().lower())
    ).scalar_one_or_none()
    now = utcnow()

    if user is None:
        check_password_hash(_DUMMY_HASH, password)
        record_auth_event(AuditAction.LOGIN_FAILED, AuditOutcome.FAILURE, identifier=email, reason="unknown_email")
        raise APIError(401, "UNAUTHENTICATED", "帳號或密碼錯誤")

    if user.locked_until and user.locked_until > now:
        record_auth_event(AuditAction.LOGIN_FAILED, AuditOutcome.FAILURE, user=user, identifier=email, reason="locked")
        raise APIError(
            423,
            "ACCOUNT_LOCKED",
            "登入失敗次數過多，帳號暫時鎖定",
            [{"field": "locked_until", "issue": iso_utc(user.locked_until)}],
        )

    if not check_password_hash(user.password_hash, password):
        user.failed_login_count = (user.failed_login_count or 0) + 1
        reason = "bad_password"
        if user.failed_login_count >= current_app.config["LOGIN_MAX_FAILED_ATTEMPTS"]:
            user.locked_until = now + timedelta(minutes=current_app.config["LOGIN_LOCKOUT_MINUTES"])
            user.failed_login_count = 0
            reason = "bad_password_locked"
        record_auth_event(AuditAction.LOGIN_FAILED, AuditOutcome.FAILURE, user=user, identifier=email, reason=reason)
        raise APIError(401, "UNAUTHENTICATED", "帳號或密碼錯誤")

    if not user.is_active:
        record_auth_event(AuditAction.LOGIN_FAILED, AuditOutcome.FAILURE, user=user, identifier=email, reason="inactive")
        raise APIError(401, "UNAUTHENTICATED", "帳號或密碼錯誤")

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    record_auth_event(AuditAction.LOGIN, AuditOutcome.SUCCESS, user=user, identifier=email)

    sid, refresh = sessions.open_session(user)
    return access_token_for(user, sid), user, refresh


def access_token_for(user, sid):
    """Short-lived access token of one session (claims: sub, role, patient_id, sid)."""
    return create_access_token(
        identity=user.public_id,
        additional_claims={"role": user.role_name, "patient_id": user_payload(user)["patient_id"], "sid": sid},
    )


def change_password(user, current_password, new_password):
    """Verify the current password, apply the policy, store the new hash. Caller commits.
    Audit records that the password changed — never a password value."""
    details = []
    if not isinstance(current_password, str) or not current_password:
        details.append({"field": "current_password", "issue": "is required"})
    elif not check_password_hash(user.password_hash, current_password):
        details.append({"field": "current_password", "issue": "is incorrect"})
    details += [{"field": "new_password", "issue": p} for p in password_problems(new_password, current_password=current_password)]
    if details:
        raise APIError(400, "VALIDATION_ERROR", "密碼設定有誤", details)
    first_change = user.password_changed_at is None
    user.password_hash = generate_password_hash(new_password)
    user.password_changed_at = utcnow()
    # session policy: every other session of this account ends; the current one keeps working
    ended = sessions.revoke_all(user, keep=get_jwt().get("sid"))
    record_data_event(AuditAction.UPDATE, "users", user.public_id,
                      changes={"password": "changed", "temporary_password_replaced": first_change, "sessions_revoked": ended})
