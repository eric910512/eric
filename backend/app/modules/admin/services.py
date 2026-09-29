"""Admin console (api-design.md §13, Sprint 7): overview, accounts of every role and their
status, audit log search, read-only system settings, alert rules and symptom form composition.

Admins manage accounts, care teams and configuration; they do not get write access to clinical
records (those stay with the patient's nurses). Every change is audited. Callers commit.
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation

from flask import current_app
from sqlalchemy import func, or_, select

from app.core.api import APIError
from app.core.passwords import MAX_LENGTH, MIN_LENGTH
from app.core.timeutil import iso_utc
from app.extensions import db
from app.models import (
    AlertRule,
    AuditLog,
    LabTestType,
    Notification,
    NursePatientAssignment,
    NurseProfile,
    PatientProfile,
    SymptomDefinition,
    SymptomForm,
    SymptomFormItem,
    SymptomRecord,
    User,
)
from app.models.base import utcnow
from app.models.enums import AlertSeverity, AlertSourceType, NotificationStatus, NotificationType, RecordStatus, ReviewStatus, RoleName
from app.modules.auth import sessions
from app.modules.chemotherapy.services import Fields, _invalid
from app.services.vitals import VITAL_FIELDS

ROLES = (RoleName.NURSE, RoleName.PATIENT, RoleName.ADMIN)
OPERATORS = (">=", "<=", ">", "<", "==")
CONDITION_KEYS = ("within_nadir", "outside_nadir", "consecutive_records", "value_above")


def _person(u):
    return {"id": u.public_id, "display_name": u.display_name} if u else None


# ------------------------------------------------------------------ overview


def overview():
    now = utcnow()
    active = select(NursePatientAssignment.patient_id).where(NursePatientAssignment.ended_at.is_(None))
    patients = select(func.count()).select_from(PatientProfile).where(PatientProfile.deleted_at.is_(None))
    count = lambda q: db.session.execute(q).scalar()  # noqa: E731
    users = lambda role, *c: count(select(func.count()).select_from(User).where(User.role.has(name=role), *c))  # noqa: E731
    open_events = select(func.count(func.distinct(Notification.event_key))).where(
        Notification.type == NotificationType.RISK_ALERT, Notification.status.in_(NotificationStatus.OPEN))
    return {
        "patients": {"total": count(patients), "unassigned": count(patients.where(PatientProfile.id.not_in(active))),
                     "without_account": count(patients.where(PatientProfile.user_id.is_(None)))},
        "nurses": {"total": users(RoleName.NURSE), "active": users(RoleName.NURSE, User.is_active.is_(True)),
                   "must_change_password": users(RoleName.NURSE, User.password_changed_at.is_(None))},
        "accounts": {"inactive": count(select(func.count()).select_from(User).where(User.is_active.is_not(True))),
                     "locked": count(select(func.count()).select_from(User).where(User.locked_until > now))},
        "assignments": {"active": count(select(func.count()).select_from(NursePatientAssignment).where(NursePatientAssignment.ended_at.is_(None)))},
        "alerts": {"open": count(open_events),
                   "critical": count(open_events.where(Notification.severity == AlertSeverity.CRITICAL))},
        "symptom_reviews": {"pending": count(select(func.count()).select_from(SymptomRecord).where(
            SymptomRecord.review_status == ReviewStatus.SUBMITTED, SymptomRecord.record_status == RecordStatus.FINAL))},
        "generated_at": iso_utc(now),
    }


# ------------------------------------------------------------------ accounts


def user_payload(u):
    now = utcnow()
    np_ = u.nurse_profile
    data = {
        "id": u.public_id, "email": u.email, "display_name": u.display_name, "role": u.role_name,
        "is_active": bool(u.is_active), "must_change_password": u.password_changed_at is None,
        "locked": bool(u.locked_until and u.locked_until > now), "locked_until": iso_utc(u.locked_until) if u.locked_until and u.locked_until > now else None,
        "last_login_at": iso_utc(u.last_login_at), "created_at": iso_utc(u.created_at),
        "nurse_profile": {"staff_code": np_.staff_code, "department": np_.department, "title": np_.title} if np_ else None,
        "patient": {"id": u.patient_profile.public_id, "patient_code": u.patient_profile.patient_code} if u.patient_profile else None,
        "active_sessions": sessions.active_count(u),
    }
    if u.role_name == RoleName.NURSE:
        data["active_patient_count"] = db.session.execute(
            select(func.count()).select_from(NursePatientAssignment).filter_by(nurse_id=u.id, ended_at=None)).scalar()
    return data


def list_users(role, q, status):
    conds = [] if role == "all" else [User.role.has(name=role)]
    if q:
        like = f"%{q.strip()}%"
        conds.append(or_(User.display_name.ilike(like), User.email.ilike(like)))
    now = utcnow()
    if status == "active":
        conds.append(User.is_active.is_(True))
    elif status == "inactive":
        conds.append(User.is_active.is_not(True))
    elif status == "locked":
        conds.append(User.locked_until > now)
    rows = db.session.execute(select(User).where(*conds).order_by(User.display_name, User.id).limit(500)).scalars().all()
    return rows


def get_user(public_id):
    u = db.session.execute(select(User).filter_by(public_id=public_id)).scalar_one_or_none() if isinstance(public_id, str) else None
    if u is None:
        raise APIError(404, "NOT_FOUND", "User not found")
    return u


def update_user(u, admin, body):
    """``is_active`` (disable / enable — a disabled account's tokens stop working at once),
    ``unlock: true`` (clear a login lockout), ``display_name``, ``nurse_profile``. Returns changed fields."""
    f = Fields(body, "帳號資料有誤")
    if f.has("is_active"):
        f.values["is_active"] = f.boolean("is_active")
    unlock = f.boolean("unlock", False) if f.has("unlock") else False
    if f.has("display_name"):
        f.text("display_name", 100, required=True)
    profile = body.get("nurse_profile")
    if profile is not None and (not isinstance(profile, dict) or u.role_name != RoleName.NURSE):
        f.error("nurse_profile", "only for nurse accounts (object with staff_code / department / title)")
    for key in ("role", "email", "password", "temporary_password"):
        if key in body:
            f.error(key, "cannot be changed here")
    f.unknown(("is_active", "unlock", "display_name", "nurse_profile", "role", "email", "password", "temporary_password"))
    values = f.done()
    changed = []
    if values.get("is_active") is False and u.is_active:
        if u.id == admin.id:
            raise APIError(409, "CONFLICT", "不能停用自己的帳號")
        if u.role_name == RoleName.ADMIN and db.session.execute(select(func.count()).select_from(User).where(
                User.role.has(name=RoleName.ADMIN), User.is_active.is_(True))).scalar() <= 1:
            raise APIError(409, "CONFLICT", "至少要保留一個啟用中的管理者帳號")
    if "is_active" in values and bool(u.is_active) != values["is_active"]:
        u.is_active = values["is_active"]
        changed.append("is_active")
        if not u.is_active and sessions.revoke_all(u):
            changed.append("sessions_revoked")  # re-enabling does not bring old sessions back
    if unlock and (u.locked_until or u.failed_login_count):
        u.locked_until = None
        u.failed_login_count = 0
        changed.append("unlocked")
    if "display_name" in values and values["display_name"] != u.display_name:
        u.display_name = values["display_name"]
        if u.patient_profile is not None:
            u.patient_profile.display_name = u.display_name
        changed.append("display_name")
    if profile is not None:
        np_ = u.nurse_profile or NurseProfile(user_id=u.id)
        pf = Fields(profile, "帳號資料有誤")
        for key, limit in (("staff_code", 30), ("department", 100), ("title", 50)):
            if key in profile:
                pf.text(key, limit)
        pf.unknown(("staff_code", "department", "title"))
        pv = pf.done()
        code = pv.get("staff_code")
        if code and code != np_.staff_code and db.session.execute(select(NurseProfile.id).filter_by(staff_code=code)).first():
            raise APIError(409, "CONFLICT", "員工編號已被使用", [{"field": "nurse_profile.staff_code", "issue": "is already used"}])
        for k, v in pv.items():
            if getattr(np_, k) != v:
                setattr(np_, k, v)
                changed.append(f"nurse_profile.{k}")
        db.session.add(np_)
    return changed


# ------------------------------------------------------------------ audit log


def _date(raw, field, details):
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        details.append({"field": field, "issue": "must be a date (YYYY-MM-DD)"})
        return None


TPE = timezone(timedelta(hours=8))


def search_audit(args):
    """Filters: patient_id (public), actor_id (public), action, resource_type, category, outcome,
    from / to (dates, Asia/Taipei); newest first; page / per_page (≤ 200)."""
    details = []
    start, end = _date(args.get("from"), "from", details), _date(args.get("to"), "to", details)
    if start and end and start > end:
        details.append({"field": "to", "issue": "must not be earlier than from"})
    try:
        page = max(1, int(args.get("page", 1)))
        per_page = min(200, max(1, int(args.get("per_page", 50))))
    except ValueError:
        details.append({"field": "page", "issue": "must be a positive integer"})
        page, per_page = 1, 50
    if details:
        _invalid(details, "查詢條件有誤")
    conds = []
    if args.get("patient_id"):
        p = db.session.execute(select(PatientProfile).filter_by(public_id=args["patient_id"])).scalar_one_or_none()
        conds.append(AuditLog.patient_id == (p.id if p else -1))
    if args.get("actor_id"):
        a = db.session.execute(select(User).filter_by(public_id=args["actor_id"])).scalar_one_or_none()
        conds.append(AuditLog.actor_user_id == (a.id if a else -1))
    for key, col in (("action", AuditLog.action), ("resource_type", AuditLog.resource_type), ("category", AuditLog.category), ("outcome", AuditLog.outcome)):
        if args.get(key):
            conds.append(col == args[key])
    if start:
        conds.append(AuditLog.occurred_at >= datetime.combine(start, time.min, tzinfo=TPE).astimezone(timezone.utc).replace(tzinfo=None))
    if end:
        conds.append(AuditLog.occurred_at < datetime.combine(end + timedelta(days=1), time.min, tzinfo=TPE).astimezone(timezone.utc).replace(tzinfo=None))
    total = db.session.execute(select(func.count()).select_from(AuditLog).where(*conds)).scalar()
    rows = db.session.execute(select(AuditLog).where(*conds).order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
                              .offset((page - 1) * per_page).limit(per_page)).scalars().all()
    patients = {p.id: p for p in db.session.execute(select(PatientProfile).where(PatientProfile.id.in_({r.patient_id for r in rows if r.patient_id}))).scalars()}
    return [audit_payload(r, patients.get(r.patient_id)) for r in rows], {"page": page, "per_page": per_page, "total": total}


def audit_payload(r, patient):
    return {
        "id": r.id, "occurred_at": iso_utc(r.occurred_at), "category": r.category, "action": r.action,
        "actor": {**(_person(r.actor) or {}), "role": r.actor_role} if r.actor else ({"identifier": r.actor_identifier} if r.actor_identifier else None),
        "resource_type": r.resource_type, "resource_id": r.resource_id,
        "patient": {"id": patient.public_id, "patient_code": patient.patient_code} if patient else None,
        "outcome": r.outcome, "reason": r.reason, "changes": r.changes, "request_id": r.request_id,
        "http_method": r.http_method, "endpoint": r.endpoint,
    }


# ------------------------------------------------------------------ settings (read-only in Phase 1)


def settings():
    cfg = current_app.config
    return {
        "institution": cfg["INSTITUTION"],
        "security": {
            "access_token_minutes": int(cfg["JWT_ACCESS_TOKEN_EXPIRES"].total_seconds() // 60) if cfg.get("JWT_ACCESS_TOKEN_EXPIRES") else None,
            "login_lockout": {"max_failed_attempts": cfg["LOGIN_MAX_FAILED_ATTEMPTS"], "lock_minutes": cfg["LOGIN_LOCKOUT_MINUTES"]},
            "password_policy": {"min_length": MIN_LENGTH, "max_length": MAX_LENGTH, "letters_and_digits": True},
        },
        "source": "config_file",
    }


# ------------------------------------------------------------------ alert rules


def rule_payload(r):
    target = None
    if r.symptom_definition_id:
        d = db.session.get(SymptomDefinition, r.symptom_definition_id)
        target = {"type": "symptom", "code": d.code, "label": d.name_zh}
    elif r.vital_field:
        target = {"type": "vital_sign", "code": r.vital_field, "label": VITAL_FIELDS[r.vital_field].label if r.vital_field in VITAL_FIELDS else r.vital_field}
    elif r.lab_test_type_id:
        t = db.session.get(LabTestType, r.lab_test_type_id)
        target = {"type": "lab", "code": t.code, "label": t.name_zh}
    return {
        "id": r.id, "code": r.code, "name": r.name, "source_type": r.source_type, "target": target,
        "operator": r.operator, "threshold_value": float(r.threshold_value), "extra_conditions": r.extra_conditions or {},
        "severity": r.severity, "message_template": r.message_template, "recommended_action": r.recommended_action,
        "notify_patient": bool(r.notify_patient), "notify_nurse": bool(r.notify_nurse), "cooldown_minutes": r.cooldown_minutes or 0,
        "is_active": bool(r.is_active),
    }


def get_rule(rule_id):
    r = db.session.get(AlertRule, rule_id)
    if r is None:
        raise APIError(404, "NOT_FOUND", "Alert rule not found")
    return r


def _conditions(f, raw):
    if raw is None:
        f.values["extra_conditions"] = None
        return
    if not isinstance(raw, dict) or set(raw) - set(CONDITION_KEYS):
        f.error("extra_conditions", f"must be an object with keys among: {', '.join(CONDITION_KEYS)}")
        return
    if raw.get("within_nadir") and raw.get("outside_nadir"):
        f.error("extra_conditions", "within_nadir and outside_nadir cannot both be set")
    f.values["extra_conditions"] = raw


def _rule_values(body, partial, current=None):
    f = Fields(body, "風險規則內容有誤")
    if not partial:
        f.text("code", 50, required=True)
    if not partial or f.has("name"):
        f.text("name", 100, required=True)
    if not partial or f.has("operator"):
        f.choice("operator", OPERATORS, required=True)
    if not partial or f.has("threshold_value"):
        v = body.get("threshold_value")
        try:
            d = Decimal(str(v)) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        except InvalidOperation:
            d = None
        if d is None or not d.is_finite():
            f.error("threshold_value", "is required (number)")
        else:
            f.values["threshold_value"] = d
    if not partial or f.has("severity"):
        f.choice("severity", (AlertSeverity.CRITICAL, AlertSeverity.WARNING), required=True)
    for key, limit in (("message_template", 500), ("recommended_action", 500)):
        if f.has(key):
            f.text(key, limit)
    for key in ("notify_patient", "notify_nurse", "is_active"):
        if f.has(key):
            f.values[key] = f.boolean(key)
    f.integer("cooldown_minutes", 0, 10080, allow_null=False)
    if f.has("extra_conditions"):
        _conditions(f, body["extra_conditions"])
    if not partial:
        src = body.get("source_type")
        if src not in (AlertSourceType.VITAL_SIGN, AlertSourceType.SYMPTOM, AlertSourceType.LAB):
            f.error("source_type", "must be vital_sign, symptom or lab")
        elif src == AlertSourceType.VITAL_SIGN:
            if body.get("vital_field") not in VITAL_FIELDS:
                f.error("vital_field", f"must be one of: {', '.join(VITAL_FIELDS)}")
            else:
                f.values["vital_field"] = body["vital_field"]
        elif src == AlertSourceType.SYMPTOM:
            d = db.session.execute(select(SymptomDefinition).filter_by(code=body.get("symptom_code"), is_active=True)).scalar_one_or_none() if isinstance(body.get("symptom_code"), str) else None
            if d is None:
                f.error("symptom_code", "must be an active symptom definition code")
            else:
                f.values["symptom_definition_id"] = d.id
        else:
            t = db.session.execute(select(LabTestType).filter_by(code=body.get("test_code"))).scalar_one_or_none() if isinstance(body.get("test_code"), str) else None
            if t is None:
                f.error("test_code", "must be a lab test code")
            else:
                f.values["lab_test_type_id"] = t.id
        if src:
            f.values["source_type"] = src
    allowed = ("name", "operator", "threshold_value", "severity", "message_template", "recommended_action", "notify_patient", "notify_nurse",
               "cooldown_minutes", "extra_conditions", "is_active")
    f.unknown(allowed if partial else allowed + ("code", "source_type", "vital_field", "symptom_code", "test_code"))
    values = f.done()
    code = values.get("code")
    if code and db.session.execute(select(AlertRule.id).filter_by(code=code)).first():
        raise APIError(409, "CONFLICT", "規則代碼已存在", [{"field": "code", "issue": "is already used"}])
    return values


def create_rule(body):
    r = AlertRule(notify_patient=True, notify_nurse=True, cooldown_minutes=0, is_active=True, **_rule_values(body, partial=False))
    db.session.add(r)
    db.session.flush()
    return r


def update_rule(r, body):
    values = _rule_values(body, partial=True, current=r)
    changed = [k for k, v in values.items() if getattr(r, k) != v]
    for k in changed:
        setattr(r, k, values[k])
    return changed


def test_rule(r, body):
    """Dry run: would ``value`` meet the rule's comparison (and ``in_nadir`` its nadir condition)?
    Nothing is created or sent."""
    from app.services.alert_engine import OPERATORS as OPS
    v = body.get("value")
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        _invalid([{"field": "value", "issue": "is required (number)"}], "試跑資料有誤")
    in_nadir = body.get("in_nadir", False)
    if not isinstance(in_nadir, bool):
        _invalid([{"field": "in_nadir", "issue": "must be true or false"}], "試跑資料有誤")
    cond = r.extra_conditions or {}
    compare = OPS[r.operator](Decimal(str(v)), r.threshold_value)
    nadir_ok = not ((cond.get("within_nadir") and not in_nadir) or (cond.get("outside_nadir") and in_nadir))
    above_ok = cond.get("value_above") is None or Decimal(str(v)) > Decimal(str(cond["value_above"]))
    return {"matches": bool(compare and nadir_ok and above_ok), "comparison": bool(compare), "conditions_met": bool(nadir_ok and above_ok),
            "consecutive_records": cond.get("consecutive_records"), "is_active": bool(r.is_active), "sent": False}


# ------------------------------------------------------------------ symptom forms


def form_payload(form):
    return {
        "code": form.code, "name": form.name, "version": form.version, "intended_for": form.intended_for, "is_active": bool(form.is_active),
        "items": [{"definition_code": i.definition.code, "label": i.definition.name_zh, "value_type": i.definition.value_type,
                   "display_order": i.display_order, "is_required": bool(i.is_required)} for i in sorted(form.items, key=lambda i: i.display_order)],
    }


def list_forms():
    return db.session.execute(select(SymptomForm).order_by(SymptomForm.code)).scalars().all()


def update_form(form, body):
    """``{name?, items: [{definition_code, is_required}]}`` — composition only (definitions are
    locked once used); the order is the list order. ``version`` +1 when anything changed."""
    f = Fields(body, "量表內容有誤")
    if f.has("name"):
        f.text("name", 100, required=True)
    raw = body.get("items")
    items = None
    if raw is not None:
        if not isinstance(raw, list) or not raw or len(raw) > 30:
            f.error("items", "must be a non-empty list of at most 30 items")
        else:
            items, seen = [], set()
            for n, it in enumerate(raw):
                code = it.get("definition_code") if isinstance(it, dict) else None
                d = db.session.execute(select(SymptomDefinition).filter_by(code=code, is_active=True)).scalar_one_or_none() if isinstance(code, str) else None
                if d is None:
                    f.error(f"items[{n}].definition_code", "must be an active symptom definition code")
                elif d.id in seen:
                    f.error(f"items[{n}].definition_code", "appears more than once")
                else:
                    seen.add(d.id)
                    req = it.get("is_required", False)
                    if not isinstance(req, bool):
                        f.error(f"items[{n}].is_required", "must be true or false")
                    items.append((d, n + 1, req is True))
    f.unknown(("name", "items"))
    values = f.done()
    changed = []
    if "name" in values and values["name"] != form.name:
        form.name = values["name"]
        changed.append("name")
    if items is not None:
        before = [(i.definition_id, i.display_order, bool(i.is_required)) for i in sorted(form.items, key=lambda i: i.display_order)]
        after = [(d.id, order, req) for d, order, req in items]
        if before != after:
            existing = {i.definition_id: i for i in form.items}
            keep = {d.id for d, _, _ in items}
            for i in list(form.items):
                if i.definition_id not in keep:
                    form.items.remove(i)
            db.session.flush()
            for d, order, req in items:
                i = existing.get(d.id) or SymptomFormItem(definition=d, display_order=order)
                i.display_order, i.is_required = order, req
                if d.id not in existing:
                    form.items.append(i)
            changed.append("items")
    if changed:
        form.version = (form.version or 1) + 1
    return changed


# ------------------------------------------------------------------ sessions and password reset (Authentication Hardening)


def revoke_sessions(u, admin):
    """Force sign-out: end every session of ``u`` (not your own — use 登出 / 登入裝置)."""
    if u.id == admin.id:
        raise APIError(409, "CONFLICT", "要結束自己的登入請使用「登出」或「登入裝置」")
    return sessions.revoke_all(u)


def reset_password(u, admin):
    """Issue a new system temporary password (returned once, never stored or audited in clear):
    the account must set its own at the next sign-in, the login lockout is cleared and every
    session ends. Returns (temporary_password, sessions ended)."""
    from werkzeug.security import generate_password_hash

    from app.modules.patient.management import generate_temporary_password

    if u.id == admin.id:
        raise APIError(409, "CONFLICT", "請用「修改密碼」變更自己的密碼")
    password = generate_temporary_password()
    u.password_hash = generate_password_hash(password)
    u.password_changed_at = None  # first sign-in flow: must set a new password
    u.failed_login_count = 0
    u.locked_until = None
    return password, sessions.revoke_all(u)
