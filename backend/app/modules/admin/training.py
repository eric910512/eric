"""Training (demo) accounts for teaching: numbered pairs of a nurse account and a patient with a login
account, each nurse the patient's primary nurse — strictly one to one.

    POST /admin/training-accounts/preview   → what would happen per number; creates nothing
    POST /admin/training-accounts           → creates up to 10 pairs per request (Render Free / Gunicorn)

Rules
- Admin only (route) and only where synthetic demo data is allowed (``ALLOW_DEMO_SEED``: development,
  testing, staging). Production refuses (403 TRAINING_ACCOUNTS_DISABLED).
- The admin types the shared initial password; the normal password policy applies (8–128 characters,
  letters and digits). It is stored only as a hash per account — never in a response, log or audit
  entry. Training accounts get ``password_changed_at`` at creation, so students are not forced to set a
  new password on first sign-in. Every other way of creating accounts is unchanged (temporary password,
  forced change on first sign-in).
- Each pair (nurse account + patient + patient login + primary assignment) is one atomic unit: it is
  committed on its own, and any failure rolls the whole pair back. A batch can therefore be re-run:
  - ``exists``: both accounts exist and are already a complete pair → skipped, nothing changed (no
    password reset);
  - ``conflict``: anything else already uses the pair's accounts / name / staff code → nothing created;
  - ``created`` / ``failed`` (rolled back).
- Nothing outside the new pairs is touched: no existing account, patient or assignment is modified.
"""

import re
from datetime import date

from flask import current_app
from sqlalchemy import func, select
from werkzeug.security import generate_password_hash

from app.core.api import APIError
from app.core.audit import record_data_event
from app.core.passwords import password_problems
from app.extensions import db
from app.models import NursePatientAssignment, NurseProfile, PatientProfile, Role, User
from app.models.base import utcnow
from app.models.enums import AuditAction, RoleName
from app.modules.patient import management as m

MAX_PREVIEW = 50  # pairs in one batch
MAX_PER_REQUEST = 10  # pairs created per request (password hashing is slow on small instances)
MAX_NUMBER = 999
DATE_OF_BIRTH = date(1970, 1, 1)  # synthetic
PREFIX = re.compile(r"^[a-z][a-z0-9]{0,29}$")
DOMAIN = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
FIELDS = ("start", "count", "patient_prefix", "nurse_prefix", "domain", "patient_name_prefix", "nurse_name_prefix")


def ensure_allowed():
    if not current_app.config.get("ALLOW_DEMO_SEED"):
        raise APIError(403, "TRAINING_ACCOUNTS_DISABLED", "此環境不允許建立教學帳號（僅限開發 / staging）")


def _error(details):
    raise APIError(400, "VALIDATION_ERROR", "教學帳號設定有誤", details)


def parse_spec(body, *, creating):
    details = []
    allowed = set(FIELDS) | ({"password", "confirm"} if creating else set())
    for key in sorted(set(body) - allowed):
        details.append({"field": key, "issue": "is not a supported field"})
    limit = MAX_PER_REQUEST if creating else MAX_PREVIEW
    spec = {}
    for key, lo, hi in (("start", 1, MAX_NUMBER), ("count", 1, limit)):
        v = body.get(key)
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            details.append({"field": key, "issue": f"must be an integer between {lo} and {hi}"})
        else:
            spec[key] = v
    if "start" in spec and "count" in spec and spec["start"] + spec["count"] - 1 > MAX_NUMBER:
        details.append({"field": "count", "issue": f"numbers must stay within {MAX_NUMBER}"})
    for key in ("patient_prefix", "nurse_prefix"):
        v = body.get(key)
        if not isinstance(v, str) or not PREFIX.match(v):
            details.append({"field": key, "issue": "must be lowercase letters / digits starting with a letter (at most 30)"})
        else:
            spec[key] = v
    if spec.get("patient_prefix") and spec.get("patient_prefix") == spec.get("nurse_prefix"):
        details.append({"field": "nurse_prefix", "issue": "must differ from patient_prefix"})
    domain = body.get("domain", "demo.local")
    if not isinstance(domain, str) or not DOMAIN.match(domain) or len(domain) > 100:
        details.append({"field": "domain", "issue": "must be a domain such as demo.local"})
    else:
        spec["domain"] = domain
    for key in ("patient_name_prefix", "nurse_name_prefix"):
        v = body.get(key)
        if not isinstance(v, str) or not v.strip() or len(v.strip()) > 40:
            details.append({"field": key, "issue": "is required (at most 40 characters)"})
        else:
            spec[key] = v.strip()
    if creating:
        if body.get("confirm") is not True:
            details.append({"field": "confirm", "issue": "must be true"})
        password = body.get("password")
        problems = password_problems(password)
        details += [{"field": "password", "issue": p} for p in problems]
        if not problems:
            spec["password"] = password
    if details:
        _error(details)
    return spec


def _plan(spec, n):
    nn = f"{n:03d}"
    return {
        "number": nn,
        "patient_email": f"{spec['patient_prefix']}{nn}@{spec['domain']}",
        "nurse_email": f"{spec['nurse_prefix']}{nn}@{spec['domain']}",
        "patient_name": f"{spec['patient_name_prefix']} {nn}",
        "nurse_name": f"{spec['nurse_name_prefix']} {nn}",
        "staff_code": f"{spec['nurse_prefix']}{nn}".upper(),
    }


def _user(email):
    return db.session.execute(select(User).where(func.lower(User.email) == email)).scalar_one_or_none()


def _classify(plan):
    """('new' | 'exists' | 'conflict', reason, patient) for one planned pair."""
    nurse, patient_user = _user(plan["nurse_email"]), _user(plan["patient_email"])
    if nurse is None and patient_user is None:
        if db.session.execute(select(PatientProfile.id).filter_by(display_name=plan["patient_name"], deleted_at=None)).first():
            return "conflict", f"已有名為「{plan['patient_name']}」的病人", None
        if db.session.execute(select(NurseProfile.id).filter_by(staff_code=plan["staff_code"])).first():
            return "conflict", f"員工編號 {plan['staff_code']} 已被使用", None
        return "new", None, None
    if nurse is None or patient_user is None:
        return "conflict", "只有其中一個帳號已存在", None
    patient = patient_user.patient_profile
    if nurse.role_name != RoleName.NURSE or patient_user.role_name != RoleName.PATIENT or patient is None:
        return "conflict", "帳號已被其他角色使用", None
    active = db.session.execute(
        select(NursePatientAssignment).filter_by(patient_id=patient.id, ended_at=None)
    ).scalars().all()
    if len(active) == 1 and active[0].nurse_id == nurse.id and active[0].is_primary:
        return "exists", None, patient
    return "conflict", "兩個帳號都存在，但照護關係不是這一組一對一主責", patient


def _row(plan, status, reason=None, patient=None):
    return {
        "number": plan["number"],
        "patient_email": plan["patient_email"],
        "patient_name": plan["patient_name"],
        "patient_code": patient.patient_code if patient else None,
        "nurse_email": plan["nurse_email"],
        "nurse_name": plan["nurse_name"],
        "primary_assignment": status in ("created", "exists"),
        "status": status,
        "reason": reason,
    }


def _summary(rows, keys):
    return {k: sum(1 for r in rows if r["status"] == k) for k in keys}


def preview(body):
    """What a batch would do, number by number. Read-only."""
    spec = parse_spec(body, creating=False)
    rows = []
    for n in range(spec["start"], spec["start"] + spec["count"]):
        plan = _plan(spec, n)
        status, reason, patient = _classify(plan)
        rows.append(_row(plan, "will_create" if status == "new" else status, reason, patient))
    return {"rows": rows, "summary": _summary(rows, ("will_create", "exists", "conflict"))}


def _create_pair(plan, admin, password):
    """One atomic unit (caller commits / rolls back): nurse account, patient, patient login, primary assignment."""
    now = utcnow()
    roles = {r.name: r for r in db.session.execute(select(Role).where(Role.name.in_((RoleName.NURSE, RoleName.PATIENT)))).scalars()}
    nurse = User(role=roles[RoleName.NURSE], email=plan["nurse_email"], display_name=plan["nurse_name"],
                 password_hash=generate_password_hash(password), is_active=True, password_changed_at=now)
    db.session.add(nurse)
    db.session.flush()
    db.session.add(NurseProfile(user_id=nurse.id, staff_code=plan["staff_code"], department="教學帳號", title=None))
    patient, _ = m.create_patient(admin, {"display_name": plan["patient_name"], "date_of_birth": DATE_OF_BIRTH.isoformat()})
    account = User(role=roles[RoleName.PATIENT], email=plan["patient_email"], display_name=plan["patient_name"],
                   password_hash=generate_password_hash(password), is_active=True, password_changed_at=now)
    db.session.add(account)
    db.session.flush()
    patient.user_id = account.id
    assignment = m.create_assignment(patient, admin, {"nurse_id": nurse.public_id, "is_primary": True})
    # audit: what was created, never the password
    marker = {"training_account": True, "password_set_by_admin": True}
    record_data_event(AuditAction.CREATE, "users", nurse.public_id, changes={"role": RoleName.NURSE, **marker})
    record_data_event(AuditAction.CREATE, "patient_profiles", patient.public_id, patient=patient,
                      changes={"patient_code": patient.patient_code, "training_account": True})
    record_data_event(AuditAction.CREATE, "users", account.public_id, patient=patient, changes={"role": RoleName.PATIENT, **marker})
    record_data_event(AuditAction.ASSIGN, "nurse_patient_assignments", assignment.id, patient=patient,
                      changes={"nurse_id": nurse.public_id, "is_primary": True, "training_account": True})
    return patient


def create(body, admin):
    """Create up to MAX_PER_REQUEST pairs; each pair is committed on its own (atomic per pair)."""
    spec = parse_spec(body, creating=True)
    password = spec.pop("password")
    rows = []
    for n in range(spec["start"], spec["start"] + spec["count"]):
        plan = _plan(spec, n)
        status, reason, patient = _classify(plan)
        if status != "new":
            rows.append(_row(plan, status, reason, patient))
            continue
        try:
            patient = _create_pair(plan, admin, password)
            db.session.commit()
            rows.append(_row(plan, "created", None, patient))
        except Exception as exc:  # noqa: BLE001 - the pair is rolled back as a whole; report and go on
            db.session.rollback()
            current_app.logger.warning("training pair %s failed: %s", plan["number"], type(exc).__name__)
            rows.append(_row(plan, "failed", "建立失敗，這一組已完整復原（沒有留下部分資料）"))
    summary = _summary(rows, ("created", "exists", "conflict", "failed"))
    record_data_event(AuditAction.CREATE, "training_accounts",
                      f"{spec['patient_prefix']}/{spec['nurse_prefix']}:{spec['start']:03d}-{spec['start'] + spec['count'] - 1:03d}",
                      changes={"batch": True, **summary, "numbers": [r["number"] for r in rows]})
    db.session.commit()
    return {"rows": rows, "summary": summary}
