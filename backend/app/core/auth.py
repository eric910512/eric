"""JWT authentication and patient-scope authorization (api-design.md §1.5).

- ``require_auth`` verifies the Bearer token and puts the signed-in User on ``g.current_user``.
- ``resolve_patient`` / ``ensure_can_view_patient`` enforce the data scope:
  patient → only self, nurse → currently assigned patients, admin → all.
"""

import uuid
from functools import wraps

from flask import g, jsonify, request
from flask_jwt_extended import current_user, verify_jwt_in_request
from sqlalchemy import select

from app.core.api import APIError
from app.extensions import db
from app.models import NursePatientAssignment, PatientProfile, User
from app.models.enums import RoleName


def _jwt_error(status, code, message):
    body = {"error": {"code": code, "message": message, "details": [], "request_id": g.get("request_id")}}
    return jsonify(body), status


def init_jwt(jwt):
    """Register Flask-JWT-Extended callbacks: user loading and error responses in the API envelope."""

    @jwt.user_lookup_loader
    def _load_user(_jwt_header, jwt_data):
        """Active account and active session (``sid``): signing out, revoking a session,
        disabling the account or a password change / reset ends access immediately."""
        from app.modules.auth import sessions  # local: the auth module imports this one

        user = db.session.execute(
            select(User).filter_by(public_id=jwt_data["sub"], is_active=True)
        ).scalar_one_or_none()
        if user is None or not sessions.is_active(user.id, jwt_data.get("sid")):
            return None
        return user

    @jwt.user_lookup_error_loader
    def _user_not_found(_jwt_header, _jwt_data):
        return _jwt_error(401, "UNAUTHENTICATED", "Account is not available")

    @jwt.expired_token_loader
    def _expired(_jwt_header, _jwt_data):
        return _jwt_error(401, "TOKEN_EXPIRED", "Access token has expired")

    @jwt.invalid_token_loader
    def _invalid(reason):
        return _jwt_error(401, "UNAUTHENTICATED", "Invalid access token")

    @jwt.unauthorized_loader
    def _missing(reason):
        return _jwt_error(401, "UNAUTHENTICATED", "Authentication required")


# Endpoints an account that still has its temporary password may call (everything else → 403).
PASSWORD_CHANGE_ALLOWED = {"auth.me", "auth.change_password_route"}


def require_auth(*roles):
    """Require a valid access token; optionally restrict to the given role names.

    Usage: ``@require_auth()`` or ``@require_auth(RoleName.NURSE)``.
    An account still using a system temporary password (``password_changed_at`` NULL) may only
    read ``/auth/me`` and change its password until it has set its own.
    """

    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            verify_jwt_in_request()
            g.current_user = current_user
            if current_user.password_changed_at is None and request.endpoint not in PASSWORD_CHANGE_ALLOWED:
                raise APIError(403, "PASSWORD_CHANGE_REQUIRED", "請先設定新密碼")
            if roles and current_user.role_name not in roles:
                raise APIError(403, "FORBIDDEN", "This action is not allowed for your role")
            return view(*args, **kwargs)

        return wrapper

    return decorator


def resolve_patient(patient_id):
    """Look up a patient by public_id (UUID) or ``me``. Unknown ids -> 404."""
    user = g.current_user
    if patient_id == "me":
        patient = user.patient_profile if user.role_name == RoleName.PATIENT else None
    else:
        try:
            uuid.UUID(patient_id)
        except ValueError:
            raise APIError(404, "NOT_FOUND", "Patient not found") from None
        patient = db.session.execute(
            select(PatientProfile).filter_by(public_id=patient_id)
        ).scalar_one_or_none()
    if patient is None or patient.deleted_at is not None:
        raise APIError(404, "NOT_FOUND", "Patient not found")
    return patient


def is_assigned_nurse(user, patient):
    return (
        db.session.execute(
            select(NursePatientAssignment.id).filter_by(nurse_id=user.id, patient_id=patient.id, ended_at=None)
        ).first()
        is not None
    )


def ensure_can_view_patient(patient):
    """Out-of-scope access returns 404 so the patient's existence is not revealed."""
    user = g.current_user
    role = user.role_name
    if role == RoleName.ADMIN:
        return
    if role == RoleName.PATIENT and user.patient_profile is not None and user.patient_profile.id == patient.id:
        return
    if role == RoleName.NURSE and is_assigned_nurse(user, patient):
        return
    raise APIError(404, "NOT_FOUND", "Patient not found")
