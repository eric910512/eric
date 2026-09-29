"""Audit helpers (database-design.md §6.B). Callers commit together with their own work."""

from flask import g, request

from app.extensions import db
from app.models import AuditLog
from app.models.enums import AuditAction, AuditCategory, AuditOutcome


def _request_fields():
    return {
        "request_id": g.get("request_id"),
        "http_method": request.method,
        "endpoint": request.path[:255],
        "ip_address": (request.remote_addr or "")[:45] or None,
        "user_agent": (request.user_agent.string or "")[:255] or None,
    }


def record_patient_view(patient, resource_type="patient_profiles", resource_id=None):
    user = g.get("current_user")
    db.session.add(
        AuditLog(
            actor_user_id=user.id if user else None,
            actor_role=user.role_name if user else None,
            category=AuditCategory.ACCESS,
            action=AuditAction.VIEW,
            resource_type=resource_type,
            resource_id=resource_id or patient.public_id,
            patient_id=patient.id,
            outcome=AuditOutcome.SUCCESS,
            **_request_fields(),
        )
    )


def record_auth_event(action, outcome, user=None, identifier=None, reason=None):
    """LOGIN / LOGIN_FAILED / LOGOUT. ``identifier`` is the attempted email (never the password)."""
    db.session.add(
        AuditLog(
            actor_user_id=user.id if user else None,
            actor_role=user.role_name if user else None,
            actor_identifier=(identifier or "")[:255] or None,
            category=AuditCategory.AUTH,
            action=action,
            resource_type="users",
            resource_id=user.public_id if user else None,
            outcome=outcome,
            reason=reason,
            **_request_fields(),
        )
    )


def record_data_event(action, resource_type, resource_id, patient=None, changes=None):
    """CREATE / UPDATE / AMEND / MARK_ERROR on clinical data. ``changes`` must not hold secrets."""
    user = g.get("current_user")
    db.session.add(
        AuditLog(
            actor_user_id=user.id if user else None,
            actor_role=user.role_name if user else None,
            category=AuditCategory.DATA,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id),
            patient_id=patient.id if patient else None,
            changes=changes,
            outcome=AuditOutcome.SUCCESS,
            **_request_fields(),
        )
    )
