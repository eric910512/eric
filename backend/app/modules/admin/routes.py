"""Admin API (api-design.md §13): overview, accounts (nurses / admins created here; patients through
patient management), account status, audit log search, read-only settings. Admin only."""

from flask import g, request

from app.core.api import APIError, ok, query_choice
from app.core.audit import record_data_event
from app.core.auth import require_auth
from app.extensions import db
from app.models import AuditLog
from app.models.enums import AuditAction, AuditCategory, AuditOutcome, RoleName
from app.modules.admin import admin_bp
from app.modules.admin import services as s
from app.modules.auth import sessions
from app.modules.patient import management as m


def _body():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise APIError(400, "VALIDATION_ERROR", "Request body must be a JSON object")
    return body


@admin_bp.get("/overview")
@require_auth(RoleName.ADMIN)
def overview():
    """Admin dashboard numbers: patients (unassigned, without account), nurses, account status,
    active assignments, open alerts, pending symptom reviews."""
    return ok(s.overview())


@admin_bp.get("/users")
@require_auth(RoleName.ADMIN)
def list_users():
    """Accounts. Query: role=nurse (default) | patient | admin | all, q (name or email),
    status=any | active | inactive | locked."""
    role = query_choice(request.args, "role", RoleName.NURSE, (*s.ROLES, "all"))
    status = query_choice(request.args, "status", "any", ("any", "active", "inactive", "locked"))
    return ok([s.user_payload(u) for u in s.list_users(role, request.args.get("q") or None, status)])


@admin_bp.post("/users")
@require_auth(RoleName.ADMIN)
def create_user():
    """Create a nurse or admin account: {email, display_name, role: "nurse" | "admin", nurse_profile?}.
    The system generates the temporary password, returned once; it is changed on first sign-in."""
    body = _body()
    user, password = m.create_nurse_account(body)
    record_data_event(AuditAction.CREATE, "users", user.public_id, changes={"role": user.role_name, "temporary_password_issued": True})
    db.session.commit()
    return ok({**s.user_payload(user), "temporary_password": password}, status=201)


@admin_bp.get("/users/<user_id>")
@require_auth(RoleName.ADMIN)
def get_user(user_id):
    return ok(s.user_payload(s.get_user(user_id)))


@admin_bp.patch("/users/<user_id>")
@require_auth(RoleName.ADMIN)
def update_user(user_id):
    """``{is_active}`` disable / enable (a disabled account's tokens stop working immediately),
    ``{unlock: true}`` clear a login lockout, ``display_name``, ``nurse_profile``."""
    u = s.get_user(user_id)
    changed = s.update_user(u, g.current_user, _body())
    if changed:
        record_data_event(AuditAction.UPDATE, "users", u.public_id, patient=u.patient_profile,
                          changes={"fields": changed, **({"is_active": bool(u.is_active)} if "is_active" in changed else {})})
    db.session.commit()
    return ok(s.user_payload(u))


@admin_bp.get("/users/<user_id>/sessions")
@require_auth(RoleName.ADMIN)
def user_sessions(user_id):
    """Active sessions of an account (sign-in time, device)."""
    return ok(sessions.list_sessions(s.get_user(user_id)))


@admin_bp.post("/users/<user_id>/revoke-sessions")
@require_auth(RoleName.ADMIN)
def revoke_sessions(user_id):
    """Force sign-out everywhere → {revoked}. The account can sign in again."""
    u = s.get_user(user_id)
    n = s.revoke_sessions(u, g.current_user)
    record_data_event(AuditAction.UPDATE, "users", u.public_id, patient=u.patient_profile, changes={"sessions_revoked": n, "forced": True})
    db.session.commit()
    return ok({"revoked": n})


@admin_bp.post("/users/<user_id>/password-reset")
@require_auth(RoleName.ADMIN)
def password_reset(user_id):
    """New system temporary password, shown once → {…account, temporary_password, sessions_revoked}.
    The account must set its own password at the next sign-in."""
    u = s.get_user(user_id)
    password, n = s.reset_password(u, g.current_user)
    record_data_event(AuditAction.UPDATE, "users", u.public_id, patient=u.patient_profile,
                      changes={"password": "reset", "temporary_password_issued": True, "sessions_revoked": n})
    db.session.commit()
    return ok({**s.user_payload(u), "temporary_password": password, "sessions_revoked": n})


@admin_bp.get("/audit-logs")
@require_auth(RoleName.ADMIN)
def audit_logs():
    """Search the audit log (the search itself is audited). Query: patient_id, actor_id, action,
    resource_type, category, outcome, from / to (dates), page, per_page (≤200)."""
    items, meta = s.search_audit(request.args)
    user = g.current_user
    db.session.add(AuditLog(actor_user_id=user.id, actor_role=user.role_name, category=AuditCategory.ACCESS, action=AuditAction.VIEW,
                            resource_type="audit_logs", resource_id=None, outcome=AuditOutcome.SUCCESS,
                            changes={"filters": {k: v for k, v in request.args.items() if k not in ("page", "per_page")}, "total": meta["total"]}))
    db.session.commit()
    return ok(items, meta=meta)


@admin_bp.get("/settings")
@require_auth(RoleName.ADMIN)
def settings():
    """Institution information and security policy (read-only in Phase 1: from the config file)."""
    return ok(s.settings())

