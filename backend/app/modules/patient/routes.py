from flask import g, request

from sqlalchemy import select

from app.core.api import APIError, ok, query_choice, query_int
from app.core.audit import record_data_event, record_patient_view
from app.core.auth import ensure_can_view_patient, require_auth, resolve_patient
from app.core.timeutil import patient_zone
from app.extensions import db
from app.modules.patient import patient_bp
from app.models import CancerDiagnosis, CancerType, NursePatientAssignment, PatientCareAlert
from app.models.enums import AuditAction, RoleName
from app.modules.patient import management as m
from app.modules.patient import profile
from app.modules.patient.timeline import build_timeline, parse_params

STAFF = (RoleName.NURSE, RoleName.ADMIN)


def _body():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise APIError(400, "VALIDATION_ERROR", "Request body must be a JSON object")
    return body


def _patient(patient_id):
    """The patient, if the signed-in user may access them (existing access control: 404 otherwise)."""
    patient = resolve_patient(patient_id)
    ensure_can_view_patient(patient)
    return patient


def _child(model, patient, child_id, name):
    row = m.db.session.get(model, child_id)
    if row is None or row.patient_id != patient.id or getattr(row, "deleted_at", None) is not None:
        raise APIError(404, "NOT_FOUND", f"{name} not found")
    return row


# ------------------------------------------------------------------ patients


@patient_bp.get("")
@require_auth(*STAFF)
def list_patients():
    """Nurse: currently assigned patients. Admin: all patients (``assigned=true|false`` filter).
    Query: q (code or name), sort=patient_code|display_name|created_at, page, per_page (≤100)."""
    args = request.args
    assigned_raw = query_choice(args, "assigned", "any", ("any", "true", "false"))
    items, meta = m.list_patients(
        g.current_user,
        q=args.get("q") or None,
        assigned=None if assigned_raw == "any" else assigned_raw == "true",
        page=query_int(args, "page", 1, 1, 10_000),
        per_page=query_int(args, "per_page", 20, 1, 100),
        sort=query_choice(args, "sort", "patient_code", ("patient_code", "display_name", "created_at")),
    )
    return ok(items, meta=meta)


@patient_bp.post("")
@require_auth(*STAFF)
def create_patient():
    """New patient; the code (P00001…) is generated. Optional ``account: {email}`` also creates
    the login account and returns its temporary password once (never stored in audit logs)."""
    body = _body()
    patient, password = m.create_patient(g.current_user, body)
    record_data_event(AuditAction.CREATE, "patient_profiles", patient.public_id, patient=patient,
                      changes={"patient_code": patient.patient_code, "fields": sorted(k for k in body if k in m.PROFILE_FIELDS)})
    if password:
        record_data_event(AuditAction.CREATE, "users", patient.user.public_id, patient=patient,
                          changes={"role": RoleName.PATIENT, "temporary_password_issued": True})
    m.db.session.commit()
    return ok(m.created_payload(patient, password), status=201)


@patient_bp.get("/cancer-types")
@require_auth()
def cancer_types():
    rows = m.db.session.execute(select(CancerType).filter_by(is_active=True).order_by(CancerType.code)).scalars().all()
    return ok([m.cancer_type_payload(t) for t in rows])


@patient_bp.get("/<patient_id>")
@require_auth()
def get_patient(patient_id):
    """Profile, care alerts, diagnoses; staff also see the care team and account state. ``me`` for patients."""
    patient = _patient(patient_id)
    record_patient_view(patient, resource_type="patient_profiles")
    m.db.session.commit()
    return ok(m.patient_detail(patient, g.current_user))


@patient_bp.patch("/<patient_id>")
@require_auth(*STAFF)
def update_patient(patient_id):
    patient = _patient(patient_id)
    changed = m.update_patient(patient, _body())
    if changed:
        record_data_event(AuditAction.UPDATE, "patient_profiles", patient.public_id, patient=patient, changes={"fields": changed})
    m.db.session.commit()
    return ok(m.patient_detail(patient, g.current_user))


@patient_bp.post("/<patient_id>/account")
@require_auth(*STAFF)
def create_account(patient_id):
    """Login account for an existing patient: ``{email}`` → temporary password, shown once."""
    patient = _patient(patient_id)
    email = m._account_email(_body().get("email"), field="email")
    password = m.create_account(patient, email)
    record_data_event(AuditAction.CREATE, "users", patient.user.public_id, patient=patient,
                      changes={"role": RoleName.PATIENT, "temporary_password_issued": True})
    m.db.session.commit()
    return ok({"patient_id": patient.public_id, "email": patient.user.email, "temporary_password": password,
               "must_change_password": True}, status=201)


# ------------------------------------------------------------------ basic data maintained by the patient


@patient_bp.get("/<patient_id>/profile")
@require_auth()
def get_profile(patient_id):
    """Basic data: height, latest weight, BMI (computed), contact email and its verification /
    notification state. ``me`` for patients. Staff (assigned nurse, admin) get the email masked only."""
    patient = _patient(patient_id)
    if g.current_user.role_name in STAFF:
        record_patient_view(patient, resource_type="patient_profile")
        db.session.commit()
    return ok(profile.profile_payload(patient, g.current_user))


@patient_bp.patch("/<patient_id>/profile")
@require_auth(RoleName.PATIENT)
def update_profile(patient_id):
    """The patient's own basic data: ``{email?, height_cm?, email_notification_enabled?}``. Any other
    patient → 404 (existing scope rule); nurses / admins cannot use it (403). A new email must be
    verified again and switches email notifications off; notifications need a verified email (422)."""
    patient = _patient(patient_id)
    changed = profile.update_profile(patient, _body())
    for table, fields in changed.items():
        changes = {"fields": fields, "by": "patient"}  # field names only, never the email address
        if "email" in fields:
            changes["email_verification_reset"] = True
        record_data_event(AuditAction.UPDATE, table, patient.public_id, patient=patient, changes=changes)
    db.session.commit()
    return ok(profile.profile_payload(patient, g.current_user))


@patient_bp.get("/<patient_id>/weights")
@require_auth()
def weights(patient_id):
    """Weight history (final ``vital_signs`` with a weight), newest first; ``limit`` 1–100 (default 30).
    ``source``: patient_app (entered by the patient) / nurse. New weights: ``POST /vital-signs``."""
    patient = _patient(patient_id)
    items = profile.weight_history(patient, g.current_user, query_int(request.args, "limit", 30, 1, profile.MAX_WEIGHTS))
    return ok(items, meta={"total": len(items)})


@patient_bp.post("/<patient_id>/email-verification")
@require_auth(RoleName.PATIENT)
def request_email_verification(patient_id):
    """Send a one-time verification link (24 h) to the contact email; earlier links stop working.
    ``{delivery: {status: sent|failed, error_code}, profile}``. 422 ``EMAIL_NOT_CONFIGURED`` while no
    email provider is configured; 429 within 60 s of the previous link."""
    patient = _patient(patient_id)
    raw, address, now = profile.request_verification(patient)
    record_data_event(AuditAction.UPDATE, "patient_contacts", patient.public_id, patient=patient,
                      changes={"email_verification_requested": True})  # never the link or the token
    db.session.commit()
    result = profile.send_verification(patient, raw, address, now)
    delivery = {"status": result.status, "error_code": result.error_code}
    return ok({"delivery": delivery, "profile": profile.profile_payload(patient, g.current_user)})


@patient_bp.post("/<patient_id>/email-verification/confirm")
@require_auth(RoleName.PATIENT)
def confirm_email_verification(patient_id):
    """``{token}`` from the verification link, by the signed-in patient it was sent to. One use only;
    expired / replaced / used links → 422. Email notifications are switched on separately."""
    patient = _patient(patient_id)
    profile.confirm_verification(patient, _body().get("token"))
    record_data_event(AuditAction.UPDATE, "patient_contacts", patient.public_id, patient=patient,
                      changes={"fields": ["email_verified_at"], "email_verified": True})
    db.session.commit()
    return ok(profile.profile_payload(patient, g.current_user))


# ------------------------------------------------------------------ care alerts


@patient_bp.get("/<patient_id>/care-alerts")
@require_auth()
def list_care_alerts(patient_id):
    patient = _patient(patient_id)
    include_inactive = request.args.get("include_inactive") == "true" and g.current_user.role_name in STAFF
    rows = [a for a in patient.care_alerts if a.is_active or include_inactive]
    return ok([m.care_alert_payload(a) for a in sorted(rows, key=lambda a: a.id)])


@patient_bp.post("/<patient_id>/care-alerts")
@require_auth(RoleName.NURSE)
def create_care_alert(patient_id):
    patient = _patient(patient_id)
    alert = m.create_care_alert(patient, g.current_user, _body())
    record_data_event(AuditAction.CREATE, "patient_care_alerts", alert.id, patient=patient, changes={"alert_type": alert.alert_type})
    m.db.session.commit()
    return ok(m.care_alert_payload(alert), status=201)


@patient_bp.patch("/<patient_id>/care-alerts/<int:alert_id>")
@require_auth(RoleName.NURSE)
def update_care_alert(patient_id, alert_id):
    patient = _patient(patient_id)
    alert = _child(PatientCareAlert, patient, alert_id, "Care alert")
    changed = m.update_care_alert(alert, _body())
    if changed:
        record_data_event(AuditAction.UPDATE, "patient_care_alerts", alert.id, patient=patient, changes={"fields": changed})
    m.db.session.commit()
    return ok(m.care_alert_payload(alert))


# ------------------------------------------------------------------ diagnoses


@patient_bp.get("/<patient_id>/diagnoses")
@require_auth()
def list_diagnoses(patient_id):
    patient = _patient(patient_id)
    return ok([m.diagnosis_payload(d) for d in patient.diagnoses if d.deleted_at is None])


@patient_bp.post("/<patient_id>/diagnoses")
@require_auth(RoleName.NURSE)
def create_diagnosis(patient_id):
    patient = _patient(patient_id)
    diagnosis = m.create_diagnosis(patient, g.current_user, _body())
    record_data_event(AuditAction.CREATE, "cancer_diagnoses", diagnosis.id, patient=patient,
                      changes={"cancer_type_code": diagnosis.cancer_type.code})
    m.db.session.commit()
    return ok(m.diagnosis_payload(diagnosis), status=201)


@patient_bp.patch("/<patient_id>/diagnoses/<int:diagnosis_id>")
@require_auth(RoleName.NURSE)
def update_diagnosis(patient_id, diagnosis_id):
    patient = _patient(patient_id)
    diagnosis = _child(CancerDiagnosis, patient, diagnosis_id, "Diagnosis")
    changed = m.update_diagnosis(patient, diagnosis, _body())
    if changed:
        record_data_event(AuditAction.UPDATE, "cancer_diagnoses", diagnosis.id, patient=patient, changes={"fields": changed})
    m.db.session.commit()
    return ok(m.diagnosis_payload(diagnosis))


# ------------------------------------------------------------------ nurse assignments


@patient_bp.get("/<patient_id>/nurse-assignments")
@require_auth(*STAFF)
def list_assignments(patient_id):
    """Care team history (active first). Nurses: only for patients they are currently assigned to."""
    patient = _patient(patient_id)
    return ok([m.assignment_payload(a) for a in m.list_assignments(patient)])


@patient_bp.post("/<patient_id>/nurse-assignments")
@require_auth(RoleName.ADMIN)
def create_assignment(patient_id):
    """Admin assigns a nurse: ``{nurse_id, is_primary?}``. The nurse can see the patient immediately."""
    patient = _patient(patient_id)
    assignment = m.create_assignment(patient, g.current_user, _body())
    record_data_event(AuditAction.ASSIGN, "nurse_patient_assignments", assignment.id, patient=patient,
                      changes={"nurse_id": assignment.nurse.public_id, "is_primary": bool(assignment.is_primary)})
    m.db.session.commit()
    return ok(m.assignment_payload(assignment), status=201)


@patient_bp.post("/<patient_id>/nurse-assignments/<int:assignment_id>/end")
@require_auth(RoleName.ADMIN)
def end_assignment(patient_id, assignment_id):
    """Admin ends an assignment: from now on that nurse gets 404 for this patient's data."""
    patient = _patient(patient_id)
    assignment = _child(NursePatientAssignment, patient, assignment_id, "Assignment")
    m.end_assignment(assignment)
    record_data_event(AuditAction.UPDATE, "nurse_patient_assignments", assignment.id, patient=patient,
                      changes={"nurse_id": assignment.nurse.public_id, "ended": True})
    m.db.session.commit()
    return ok(m.assignment_payload(assignment))


@patient_bp.get("/<patient_id>/timeline")
@require_auth()
def timeline(patient_id):
    """Patient Care Timeline, newest first. ``me`` for patients.

    Query: start_date / end_date (YYYY-MM-DD, patient's local dates, inclusive), limit
    (1–100, default 30), cursor (``meta.next_cursor`` of the previous page).
    Patient: own timeline, patient-visible content only. Nurse: assigned patients.
    Admin: all.
    """
    patient = resolve_patient(patient_id)
    ensure_can_view_patient(patient)
    params = parse_params(request.args, patient_zone(patient.timezone))
    events, meta = build_timeline(patient, g.current_user, params)
    if g.current_user.role_name != "patient":
        record_patient_view(patient, resource_type="patient_timeline")
        db.session.commit()
    return ok(events, meta=meta)
