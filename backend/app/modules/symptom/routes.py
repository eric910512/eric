from flask import g, request

from app.core.api import APIError, ok, query_choice, query_int
from app.core.audit import record_patient_view
from app.core.audit import record_data_event
from app.core.auth import ensure_can_view_patient, require_auth, resolve_patient
from app.core.idempotency import run_idempotent
from app.extensions import db
from app.models import SymptomRecord
from app.models.enums import AuditAction, ReviewStatus, RoleName
from app.modules.admin.services import form_payload, list_forms as admin_forms, update_form as admin_update_form
from app.modules.symptom import symptom_bp
from app.modules.symptom.services import (
    create_symptom_record,
    get_active_form,
    list_patient_records,
    record_list_payload,
    review_record,
    serialize_form,
    serialize_record,
    serialize_triggered,
    replay_triggered,
)
from app.services.corrections import register_routes


@symptom_bp.get("/forms/<code>")
@require_auth()
def get_form(code):
    """Form definition; the frontend renders the questionnaire from this (api-design.md §6.1)."""
    return ok(serialize_form(get_active_form(code)))


@symptom_bp.post("/records")
@require_auth(RoleName.PATIENT, RoleName.NURSE)
def create_record():
    """Submit a symptom report (api-design.md §6.2).

    Patients must send an Idempotency-Key header. In one transaction: the record and its
    values, an audit CREATE row, and notifications for any alert rules that match.
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise APIError(400, "VALIDATION_ERROR", "Request body must be a JSON object")
    patient_id = body.get("patient_id")
    if not isinstance(patient_id, str) or not patient_id:
        raise APIError(400, "VALIDATION_ERROR", "症狀回報內容有誤", [{"field": "patient_id", "issue": "is required"}])

    patient = resolve_patient(patient_id)
    ensure_can_view_patient(patient)

    def create():
        record, triggered = create_symptom_record(patient, g.current_user, body)
        record_data_event(
            AuditAction.CREATE,
            "symptom_records",
            record.id,
            patient=patient,
            changes={
                "form_code": record.form.code,
                "definition_codes": [v.definition.code for v in record.values],
                "alert_rule_codes": [t.rule.code for t in triggered if t.notified],
            },
        )
        return record.id, {"data": serialize_record(record, serialize_triggered(triggered))}, 201

    def replay(resource_id):
        record = db.session.get(SymptomRecord, int(resource_id))
        return {"data": serialize_record(record, replay_triggered(record))}

    return run_idempotent(
        resource_type="symptom_records",
        body=body,
        create=create,
        replay=replay,
        required=g.current_user.role_name == RoleName.PATIENT,
    )


@symptom_bp.get("/records/<patient_id>")
@require_auth()
def patient_records(patient_id):
    """A patient's symptom reports with review state (newest first).

    ``patient_id`` is a public id or ``me``. Query: review_status=all|submitted|reviewed, page, per_page.
    Patients see their own records without the nurse's internal note.
    """
    patient = resolve_patient(patient_id)
    ensure_can_view_patient(patient)
    records, meta = list_patient_records(
        patient,
        review_status=query_choice(request.args, "review_status", "all", ("all", ReviewStatus.SUBMITTED, ReviewStatus.REVIEWED)),
        page=query_int(request.args, "page", 1, 1, 10_000),
        per_page=query_int(request.args, "per_page", 20, 1, 100),
    )
    record_patient_view(patient, resource_type="symptom_records")
    db.session.commit()
    role = g.current_user.role_name
    return ok([record_list_payload(r, viewer_role=role) for r in records], meta=meta)


@symptom_bp.post("/records/<int:record_id>/review")
@require_auth(RoleName.NURSE)
def review(record_id):
    """Nurse review: sets reviewed_by / reviewed_at, stores the action note in a signed nursing
    assessment linked to the record, optionally grades CTCAE, and (by default) resolves the
    record's open alerts — all in one transaction, each step audited.
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise APIError(400, "VALIDATION_ERROR", "Request body must be a JSON object")
    record = db.session.get(SymptomRecord, record_id)
    if record is None:
        raise APIError(404, "NOT_FOUND", "Symptom record not found")
    ensure_can_view_patient(record.patient)

    assessment, graded, resolved = review_record(record, g.current_user, body)
    patient = record.patient
    record_data_event(
        AuditAction.CREATE, "nursing_assessments", assessment.id, patient=patient,
        changes={"symptom_record_id": record.id, "assessment_type": assessment.assessment_type, "sign_status": "signed"},
    )
    record_data_event(
        AuditAction.UPDATE, "symptom_records", record.id, patient=patient,
        changes={
            "review_status": {"old": ReviewStatus.SUBMITTED, "new": ReviewStatus.REVIEWED},
            "nursing_assessment_id": {"old": None, "new": assessment.id},
            "ctcae_graded": graded,
        },
    )
    if resolved:
        record_data_event(
            AuditAction.ACKNOWLEDGE, "notifications", resolved[0].id, patient=patient,
            changes={"source": f"symptom_records:{record.id}", "notification_ids": [n.id for n in resolved]},
        )
    db.session.commit()
    data = record_list_payload(record, viewer_role=RoleName.NURSE)
    data["resolved_notifications"] = len(resolved)
    return ok(data)


# ------------------------------------------------------------------ corrections (Sprint 5)


register_routes(symptom_bp, "/records", "symptom_records", lambda r: record_list_payload(r, viewer_role=RoleName.NURSE))


# ------------------------------------------------------------------ form composition (admin, Sprint 7)


@symptom_bp.get("/forms")
@require_auth(RoleName.ADMIN)
def list_forms():
    return ok([form_payload(f) for f in admin_forms()])


@symptom_bp.put("/forms/<code>")
@require_auth(RoleName.ADMIN)
def update_form(code):
    """``{name?, items: [{definition_code, is_required}]}`` in display order → ``version`` +1.
    Symptom definitions themselves are not edited here (they are locked once used)."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise APIError(400, "VALIDATION_ERROR", "Request body must be a JSON object")
    form = get_active_form(code)
    changed = admin_update_form(form, body)
    if changed:
        record_data_event(AuditAction.UPDATE, "symptom_forms", form.id,
                          changes={"code": form.code, "fields": changed, "version": form.version,
                                   "items": [i["definition_code"] for i in form_payload(form)["items"]]})
    db.session.commit()
    return ok(form_payload(form))
