"""Patient & care-team management (api-design.md §4): patient profiles with system-generated
codes, login accounts with one-time temporary passwords, care alerts, diagnoses and
nurse-patient assignments.

Access control is the existing one (app.core.auth): nurses see currently assigned patients,
admins see all, patients only themselves; out-of-scope → 404. Callers commit.
No national ID is ever stored: the patient code is the identifier.
"""

import re
from datetime import date
from zoneinfo import available_timezones

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash

from app.core.api import APIError
from app.core.passwords import generate_temporary_password
from app.core.timeutil import iso_date, iso_utc, patient_zone, to_local
from app.extensions import db
from app.models import (
    CancerDiagnosis,
    CancerType,
    NursePatientAssignment,
    NurseProfile,
    PatientCareAlert,
    PatientProfile,
    Role,
    User,
)
from app.models.base import utcnow
from app.models.enums import BpMeasureSite, CareAlertSeverity, CareAlertType, DiagnosisStatus, Gender, RoleName
from app.services.treatment import active_plan_and_cycle, cycle_nadir

CODE_PATTERN = re.compile(r"^P(\d{5})$")
CODE_ATTEMPTS = 5
BLOOD_TYPES = ("A", "B", "AB", "O")
# Real identity numbers must never be stored in the demo phase (patient_code instead).
FORBIDDEN_FIELDS = {"national_id", "id_number", "identity_number", "id_card", "nid"}
PROFILE_FIELDS = ("display_name", "gender", "date_of_birth", "height_cm", "blood_type", "allergies", "baseline_ecog", "timezone")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TIMEZONES = None


def _error(details, message="資料內容有誤"):
    raise APIError(400, "VALIDATION_ERROR", message, details)


def _timezones():
    global _TIMEZONES
    if _TIMEZONES is None:
        _TIMEZONES = available_timezones()
    return _TIMEZONES


def _person(user):
    return {"id": user.public_id, "display_name": user.display_name} if user else None


# ------------------------------------------------------------------ patient code


def next_patient_code(taken=()):
    """Next P00001-style code: highest existing number + 1. Soft-deleted patients count too,
    so a code is never reused."""
    numbers = [int(m.group(1)) for code in db.session.execute(select(PatientProfile.patient_code)).scalars()
               if (m := CODE_PATTERN.match(code))]
    numbers += [int(CODE_PATTERN.match(c).group(1)) for c in taken]
    number = max(numbers, default=0) + 1
    if number > 99999:
        raise APIError(409, "CONFLICT", "病人代碼已用完（P99999）")
    return f"P{number:05d}"


# ------------------------------------------------------------------ profile validation


def _profile_values(body, *, partial):
    details = []
    for key in FORBIDDEN_FIELDS & set(body):
        details.append({"field": key, "issue": "identity numbers must not be stored (the patient code is used instead)"})
    for key in ("patient_code", "id", "is_demo", "user_id"):
        if key in body:
            details.append({"field": key, "issue": "cannot be set"})
    values = {}
    if not partial or "display_name" in body:
        name = body.get("display_name")
        if not isinstance(name, str) or not name.strip():
            details.append({"field": "display_name", "issue": "is required"})
        elif len(name.strip()) > 100:
            details.append({"field": "display_name", "issue": "must be at most 100 characters"})
        else:
            values["display_name"] = name.strip()
    if not partial or "date_of_birth" in body:
        raw = body.get("date_of_birth")
        try:
            dob = date.fromisoformat(raw) if isinstance(raw, str) else None
        except ValueError:
            dob = None
        today = utcnow().date()
        if dob is None:
            details.append({"field": "date_of_birth", "issue": "is required (YYYY-MM-DD)"})
        elif dob > today or dob.year < today.year - 120:
            details.append({"field": "date_of_birth", "issue": "must be a past date within 120 years"})
        else:
            values["date_of_birth"] = dob
    if "gender" in body:
        if body["gender"] is not None and body["gender"] not in Gender.ALL:
            details.append({"field": "gender", "issue": f"must be one of: {', '.join(Gender.ALL)}"})
        else:
            values["gender"] = body["gender"]
    if "height_cm" in body:
        h = body["height_cm"]
        if h is not None and (isinstance(h, bool) or not isinstance(h, (int, float)) or not 30 <= h <= 250):
            details.append({"field": "height_cm", "issue": "must be a number between 30 and 250"})
        else:
            values["height_cm"] = round(h, 1) if h is not None else None
    if "blood_type" in body:
        if body["blood_type"] is not None and body["blood_type"] not in BLOOD_TYPES:
            details.append({"field": "blood_type", "issue": f"must be one of: {', '.join(BLOOD_TYPES)}"})
        else:
            values["blood_type"] = body["blood_type"]
    if "allergies" in body:
        a = body["allergies"]
        if a is not None and (not isinstance(a, str) or len(a) > 500):
            details.append({"field": "allergies", "issue": "must be text of at most 500 characters"})
        else:
            values["allergies"] = a.strip() or None if isinstance(a, str) else None
    if "baseline_ecog" in body:
        e = body["baseline_ecog"]
        if e is not None and (isinstance(e, bool) or not isinstance(e, int) or not 0 <= e <= 5):
            details.append({"field": "baseline_ecog", "issue": "must be an integer between 0 and 5"})
        else:
            values["baseline_ecog"] = e
    if "timezone" in body:
        tz = body["timezone"]
        if not isinstance(tz, str) or tz not in _timezones():
            details.append({"field": "timezone", "issue": "must be an IANA timezone, e.g. Asia/Taipei"})
        else:
            values["timezone"] = tz
    unknown = set(body) - set(PROFILE_FIELDS) - FORBIDDEN_FIELDS - {"patient_code", "id", "is_demo", "user_id", "account"}
    for key in sorted(unknown):
        details.append({"field": key, "issue": "is not a patient profile field"})
    if details:
        _error(details, "病人資料有誤")
    return values


def _account_email(raw, field="account.email"):
    if not isinstance(raw, str) or not EMAIL_PATTERN.match(raw.strip()) or len(raw.strip()) > 255:
        _error([{"field": field, "issue": "must be a valid email address"}], "帳號資料有誤")
    email = raw.strip().lower()
    if db.session.execute(select(User.id).where(func.lower(User.email) == email)).first():
        raise APIError(409, "CONFLICT", "這個 email 已經有帳號", [{"field": field, "issue": "is already registered"}])
    return email


# ------------------------------------------------------------------ create / update


def create_patient(user, body):
    """New patient with the next free code (retried on a concurrent collision). Optional
    ``account: {email}`` creates the login account in the same request.
    Returns (patient, temporary_password | None)."""
    values = _profile_values(body, partial=False)
    account = body.get("account")
    if account is not None and not isinstance(account, dict):
        _error([{"field": "account", "issue": "must be an object with email"}])
    email = _account_email(account.get("email")) if account else None

    taken = []
    for _ in range(CODE_ATTEMPTS):
        code = next_patient_code(taken)
        try:
            with db.session.begin_nested():
                patient = PatientProfile(patient_code=code, is_demo=True, created_by=user.id, **{"timezone": "Asia/Taipei", **values})
                db.session.add(patient)
                db.session.flush()
            break
        except IntegrityError:
            taken.append(code)  # another request took this code first
    else:
        raise APIError(409, "CONFLICT", "無法產生病人代碼，請再試一次")
    password = create_account(patient, email) if email else None
    return patient, password


def create_account(patient, email):
    """Login account (role patient) with a system temporary password, returned once. The
    patient must set a new password on first login (password_changed_at stays NULL)."""
    if patient.user_id is not None:
        raise APIError(409, "CONFLICT", "這位病人已經有登入帳號")
    role = db.session.execute(select(Role).filter_by(name=RoleName.PATIENT)).scalar_one()
    password = generate_temporary_password()
    account = User(role=role, email=email, display_name=patient.display_name,
                   password_hash=generate_password_hash(password), is_active=True, password_changed_at=None)
    db.session.add(account)
    db.session.flush()
    patient.user_id = account.id
    return password


def update_patient(patient, body):
    """Update profile fields; returns the names of the changed fields."""
    values = _profile_values(body, partial=True)
    if "account" in body:
        _error([{"field": "account", "issue": "use POST /patients/{id}/account"}])
    changed = [k for k, v in values.items() if getattr(patient, k) != v]
    for k in changed:
        setattr(patient, k, values[k])
    if "display_name" in changed and patient.user is not None:
        patient.user.display_name = patient.display_name
    return changed


# ------------------------------------------------------------------ serialize


def _age(patient):
    today = to_local(utcnow(), patient_zone(patient.timezone)).date()
    dob = patient.date_of_birth
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _primary_diagnosis(patient):
    active = [d for d in patient.diagnoses if d.deleted_at is None]
    d = next((d for d in active if d.is_primary), active[0] if active else None)
    return {"id": d.id, "cancer_type_code": d.cancer_type.code, "name_zh": d.cancer_type.name_zh, "stage": d.stage} if d else None


def _current_cycle(patient):
    plan, cycle = active_plan_and_cycle(patient)
    if not cycle:
        return None
    today = to_local(utcnow(), patient_zone(patient.timezone)).date()
    return {"cycle_number": cycle.cycle_number, "total_cycles": plan.total_cycles if plan else None,
            "cycle_day": cycle.cycle_day_on(today), "in_nadir": cycle_nadir(cycle, today)[0]}


def _active_assignments(patient):
    return db.session.execute(
        select(NursePatientAssignment).filter_by(patient_id=patient.id, ended_at=None)
        .order_by(NursePatientAssignment.is_primary.desc(), NursePatientAssignment.assigned_at)
    ).scalars().all()


def _care_team(patient):
    rows = _active_assignments(patient)
    primary = next((a for a in rows if a.is_primary), None)
    return {
        "primary_nurse": _person(primary.nurse) if primary else None,
        "nurses": [{**_person(a.nurse), "is_primary": bool(a.is_primary), "assigned_at": iso_utc(a.assigned_at)} for a in rows],
    }


def patient_list_item(patient, viewer):
    team = _care_team(patient)
    item = {
        "id": patient.public_id,
        "patient_code": patient.patient_code,
        "display_name": patient.display_name,
        "gender": patient.gender,
        "age": _age(patient),
        "primary_diagnosis": _primary_diagnosis(patient),
        "current_cycle": _current_cycle(patient),
        "has_account": patient.user_id is not None,
        "care_team": team,
        "created_at": iso_utc(patient.created_at),
    }
    if viewer.role_name == RoleName.NURSE:
        item["is_primary_nurse"] = any(n["id"] == viewer.public_id and n["is_primary"] for n in team["nurses"])
    return item


def patient_detail(patient, viewer):
    staff = viewer.role_name in (RoleName.NURSE, RoleName.ADMIN)
    data = {
        "id": patient.public_id,
        "patient_code": patient.patient_code,
        "display_name": patient.display_name,
        "gender": patient.gender,
        "date_of_birth": iso_date(patient.date_of_birth),
        "age": _age(patient),
        "height_cm": float(patient.height_cm) if patient.height_cm is not None else None,
        "blood_type": patient.blood_type,
        "allergies": patient.allergies,
        "baseline_ecog": patient.baseline_ecog,
        "timezone": patient.timezone,
        "is_demo": bool(patient.is_demo),
        "care_alerts": [care_alert_payload(a) for a in patient.active_care_alerts],
        "diagnoses": [diagnosis_payload(d) for d in patient.diagnoses if d.deleted_at is None],
        "current_cycle": _current_cycle(patient),
    }
    if staff:  # internal: care team, account state, provenance
        from app.modules.patient.profile import contact_summary  # local: profile imports this module

        account = patient.user
        data.update({
            "care_team": _care_team(patient),
            "account": {
                "has_account": account is not None,
                "email": account.email if account else None,
                "is_active": bool(account.is_active) if account else None,
                "must_change_password": account.password_changed_at is None if account else None,
                "last_login_at": iso_utc(account.last_login_at) if account else None,
            },
            # the patient's own contact email (not the login): masked, verification and notification state only
            "notification_contact": contact_summary(patient),
            "created_at": iso_utc(patient.created_at),
            "created_by": _person(db.session.get(User, patient.created_by)) if patient.created_by else None,
        })
    return data


def created_payload(patient, temporary_password):
    """201 body: the new patient plus, once, the temporary password of its new account."""
    data = {"id": patient.public_id, "patient_code": patient.patient_code, "display_name": patient.display_name,
            "is_demo": bool(patient.is_demo), "created_at": iso_utc(patient.created_at), "account": None}
    if patient.user is not None:
        data["account"] = {"email": patient.user.email, "temporary_password": temporary_password, "must_change_password": True}
    return data


# ------------------------------------------------------------------ list


def list_patients(viewer, *, q, assigned, page, per_page, sort):
    conds = [PatientProfile.deleted_at.is_(None)]
    active = select(NursePatientAssignment.patient_id).where(NursePatientAssignment.ended_at.is_(None))
    if viewer.role_name == RoleName.NURSE:
        conds.append(PatientProfile.id.in_(active.where(NursePatientAssignment.nurse_id == viewer.id)))
    elif assigned is not None:  # admin filter
        conds.append(PatientProfile.id.in_(active) if assigned else PatientProfile.id.not_in(active))
    if q:
        like = f"%{q.strip()}%"
        conds.append(or_(PatientProfile.patient_code.ilike(like), PatientProfile.display_name.ilike(like)))
    order = {"patient_code": PatientProfile.patient_code, "display_name": PatientProfile.display_name,
             "created_at": PatientProfile.created_at.desc()}[sort]
    total = db.session.execute(select(func.count()).select_from(PatientProfile).where(*conds)).scalar()
    rows = db.session.execute(
        select(PatientProfile).where(*conds).order_by(order, PatientProfile.id).offset((page - 1) * per_page).limit(per_page)
    ).scalars().all()
    return [patient_list_item(p, viewer) for p in rows], {"page": page, "per_page": per_page, "total": total}


# ------------------------------------------------------------------ care alerts


def care_alert_payload(a):
    return {"id": a.id, "alert_type": a.alert_type, "body_site": a.body_site, "description": a.description,
            "severity": a.severity, "is_active": bool(a.is_active)}


def _care_alert_values(body, *, partial, current_type=None):
    details, values = [], {}
    if not partial or "alert_type" in body:
        if body.get("alert_type") not in CareAlertType.ALL:
            details.append({"field": "alert_type", "issue": f"must be one of: {', '.join(CareAlertType.ALL)}"})
        else:
            values["alert_type"] = body["alert_type"]
    if not partial or "description" in body:
        d = body.get("description")
        if not isinstance(d, str) or not d.strip() or len(d.strip()) > 255:
            details.append({"field": "description", "issue": "is required (at most 255 characters)"})
        else:
            values["description"] = d.strip()
    if not partial or "severity" in body:
        if body.get("severity") not in CareAlertSeverity.ALL:
            details.append({"field": "severity", "issue": f"must be one of: {', '.join(CareAlertSeverity.ALL)}"})
        else:
            values["severity"] = body["severity"]
    if "body_site" in body:
        values["body_site"] = body["body_site"]
    if "is_active" in body and partial:
        if not isinstance(body["is_active"], bool):
            details.append({"field": "is_active", "issue": "must be true or false"})
        else:
            values["is_active"] = body["is_active"]
    kind = values.get("alert_type", current_type)
    site = values.get("body_site", body.get("body_site") if not partial else None)
    if kind == CareAlertType.LIMB_RESTRICTION and ("body_site" in values or not partial) and site not in BpMeasureSite.ALL:
        details.append({"field": "body_site", "issue": f"limb restrictions need one of: {', '.join(BpMeasureSite.ALL)}"})
    elif "body_site" in values and values["body_site"] is not None and (not isinstance(values["body_site"], str) or len(values["body_site"]) > 30):
        details.append({"field": "body_site", "issue": "must be text of at most 30 characters"})
    if details:
        _error(details, "注意事項內容有誤")
    return values


def create_care_alert(patient, user, body):
    values = _care_alert_values(body, partial=False)
    alert = PatientCareAlert(patient_id=patient.id, recorded_by=user.id, is_active=True, **values)
    db.session.add(alert)
    db.session.flush()
    return alert


def update_care_alert(alert, body):
    values = _care_alert_values(body, partial=True, current_type=alert.alert_type)
    changed = [k for k, v in values.items() if getattr(alert, k) != v]
    for k in changed:
        setattr(alert, k, values[k])
    return changed


# ------------------------------------------------------------------ diagnoses


def cancer_type_payload(t):
    return {"code": t.code, "name_zh": t.name_zh, "name_en": t.name_en}


def diagnosis_payload(d):
    return {"id": d.id, "cancer_type": cancer_type_payload(d.cancer_type), "stage": d.stage,
            "diagnosis_date": iso_date(d.diagnosis_date), "status": d.status, "is_primary": bool(d.is_primary),
            "histology": d.histology, "notes": d.notes}


def _diagnosis_values(body, *, partial):
    details, values = [], {}
    if not partial or "cancer_type_code" in body:
        t = db.session.execute(select(CancerType).filter_by(code=body.get("cancer_type_code"), is_active=True)).scalar_one_or_none() \
            if isinstance(body.get("cancer_type_code"), str) else None
        if t is None:
            details.append({"field": "cancer_type_code", "issue": "must be an active cancer type code (GET /patients/cancer-types)"})
        else:
            values["cancer_type_id"] = t.id
    if not partial or "diagnosis_date" in body:
        try:
            dd = date.fromisoformat(body.get("diagnosis_date")) if isinstance(body.get("diagnosis_date"), str) else None
        except ValueError:
            dd = None
        if dd is None or dd > utcnow().date():
            details.append({"field": "diagnosis_date", "issue": "is required and cannot be in the future (YYYY-MM-DD)"})
        else:
            values["diagnosis_date"] = dd
    for key, limit in (("stage", 10), ("histology", 100), ("notes", 2000)):
        if key in body:
            v = body[key]
            if v is not None and (not isinstance(v, str) or len(v) > limit):
                details.append({"field": key, "issue": f"must be text of at most {limit} characters"})
            else:
                values[key] = v.strip() or None if isinstance(v, str) else None
    if "status" in body or not partial:
        s = body.get("status", DiagnosisStatus.ACTIVE)
        if s not in DiagnosisStatus.ALL:
            details.append({"field": "status", "issue": f"must be one of: {', '.join(DiagnosisStatus.ALL)}"})
        else:
            values["status"] = s
    if "is_primary" in body:
        if not isinstance(body["is_primary"], bool):
            details.append({"field": "is_primary", "issue": "must be true or false"})
        else:
            values["is_primary"] = body["is_primary"]
    if details:
        _error(details, "診斷資料有誤")
    return values


def _single_primary(patient, keep):
    for d in patient.diagnoses:
        if d.id != keep.id and d.deleted_at is None and d.is_primary:
            d.is_primary = False


def create_diagnosis(patient, user, body):
    values = _diagnosis_values(body, partial=False)
    values.setdefault("is_primary", not any(d.deleted_at is None for d in patient.diagnoses))
    diagnosis = CancerDiagnosis(patient_id=patient.id, created_by=user.id, **values)
    db.session.add(diagnosis)
    db.session.flush()
    db.session.refresh(patient)
    if diagnosis.is_primary:
        _single_primary(patient, diagnosis)
    return diagnosis


def update_diagnosis(patient, diagnosis, body):
    values = _diagnosis_values(body, partial=True)
    changed = [k for k, v in values.items() if getattr(diagnosis, k) != v]
    for k in changed:
        setattr(diagnosis, k, values[k])
    if values.get("is_primary"):
        _single_primary(patient, diagnosis)
    return [("cancer_type_code" if k == "cancer_type_id" else k) for k in changed]


# ------------------------------------------------------------------ assignments


def assignment_payload(a):
    return {"id": a.id, "nurse": _person(a.nurse), "is_primary": bool(a.is_primary), "assigned_at": iso_utc(a.assigned_at),
            "ended_at": iso_utc(a.ended_at), "active": a.ended_at is None,
            "assigned_by": _person(db.session.get(User, a.assigned_by)) if a.assigned_by else None}


def list_assignments(patient, include_ended=True):
    q = select(NursePatientAssignment).filter_by(patient_id=patient.id)
    if not include_ended:
        q = q.where(NursePatientAssignment.ended_at.is_(None))
    return db.session.execute(q.order_by(NursePatientAssignment.ended_at.is_(None).desc(), NursePatientAssignment.assigned_at.desc())).scalars().all()


def create_assignment(patient, admin, body):
    nurse_id = body.get("nurse_id")
    nurse = db.session.execute(select(User).filter_by(public_id=nurse_id)).scalar_one_or_none() if isinstance(nurse_id, str) else None
    if nurse is None or nurse.role_name != RoleName.NURSE or not nurse.is_active:
        _error([{"field": "nurse_id", "issue": "must be the id of an active nurse account"}], "指派資料有誤")
    is_primary = body.get("is_primary", False)
    if not isinstance(is_primary, bool):
        _error([{"field": "is_primary", "issue": "must be true or false"}], "指派資料有誤")
    active = _active_assignments(patient)
    if any(a.nurse_id == nurse.id for a in active):
        raise APIError(409, "CONFLICT", "這位護理師已經負責這位病人")
    if is_primary:
        for a in active:
            a.is_primary = False
    assignment = NursePatientAssignment(nurse_id=nurse.id, patient_id=patient.id, is_primary=is_primary,
                                        assigned_at=utcnow(), assigned_by=admin.id)
    db.session.add(assignment)
    db.session.flush()
    return assignment


def end_assignment(assignment):
    if assignment.ended_at is not None:
        raise APIError(409, "CONFLICT", "這個指派已經結束")
    assignment.ended_at = utcnow()
    assignment.is_primary = False


# ------------------------------------------------------------------ staff accounts (admin)


def create_nurse_account(body):
    """Nurse or admin account (``role``, default nurse) with a system temporary password
    (returned once; changed on first login). Patients get accounts through patient management."""
    details = []
    role_name = body.get("role", RoleName.NURSE)
    if role_name not in (RoleName.NURSE, RoleName.ADMIN):
        details.append({"field": "role", "issue": "must be 'nurse' or 'admin' (patients: POST /patients/{id}/account)"})
    name = body.get("display_name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
        details.append({"field": "display_name", "issue": "is required (at most 100 characters)"})
    if "temporary_password" in body or "password" in body:
        details.append({"field": "temporary_password", "issue": "is generated by the system"})
    profile = body.get("nurse_profile") or {}
    if not isinstance(profile, dict):
        details.append({"field": "nurse_profile", "issue": "must be an object"})
        profile = {}
    elif profile and role_name == RoleName.ADMIN:
        details.append({"field": "nurse_profile", "issue": "only for nurse accounts"})
    for key, limit in (("staff_code", 30), ("department", 100), ("title", 50)):
        v = profile.get(key)
        if v is not None and (not isinstance(v, str) or len(v) > limit):
            details.append({"field": f"nurse_profile.{key}", "issue": f"must be text of at most {limit} characters"})
    if details:
        _error(details, "帳號資料有誤")
    email = _account_email(body.get("email"), field="email")
    staff_code = (profile.get("staff_code") or "").strip() or None
    if staff_code and db.session.execute(select(NurseProfile.id).filter_by(staff_code=staff_code)).first():
        raise APIError(409, "CONFLICT", "員工編號已被使用", [{"field": "nurse_profile.staff_code", "issue": "is already used"}])
    role = db.session.execute(select(Role).filter_by(name=role_name)).scalar_one()
    password = generate_temporary_password()
    user = User(role=role, email=email, display_name=name.strip(), password_hash=generate_password_hash(password),
                is_active=True, password_changed_at=None)
    db.session.add(user)
    db.session.flush()
    if role_name == RoleName.NURSE:
        db.session.add(NurseProfile(user_id=user.id, staff_code=staff_code, department=(profile.get("department") or None),
                                    title=(profile.get("title") or None)))
    db.session.flush()
    return user, password


def staff_payload(user):
    active = db.session.execute(
        select(func.count()).select_from(NursePatientAssignment).filter_by(nurse_id=user.id, ended_at=None)
    ).scalar()
    np_ = user.nurse_profile
    return {
        "id": user.public_id, "email": user.email, "display_name": user.display_name, "role": user.role_name,
        "is_active": bool(user.is_active), "must_change_password": user.password_changed_at is None,
        "last_login_at": iso_utc(user.last_login_at), "active_patient_count": active,
        "nurse_profile": {"staff_code": np_.staff_code, "department": np_.department, "title": np_.title} if np_ else None,
    }


def list_staff(role, q):
    conds = [User.role.has(name=role)]
    if q:
        like = f"%{q.strip()}%"
        conds.append(or_(User.display_name.ilike(like), User.email.ilike(like)))
    rows = db.session.execute(select(User).where(*conds).order_by(User.display_name, User.id)).scalars().all()
    return [staff_payload(u) for u in rows]
