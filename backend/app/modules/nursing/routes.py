"""Nursing assessment API (api-design.md §9). Nurses of the patient write (drafts: the author
only); admins read; patients have no access (the timeline gives them a neutral line)."""

from flask import g, request

from app.core.api import APIError, ok
from app.core.audit import record_data_event, record_patient_view
from app.core.auth import ensure_can_view_patient, require_auth, resolve_patient
from app.core.idempotency import run_idempotent
from app.extensions import db
from app.models import NursingAssessment
from app.models.enums import AuditAction, RoleName
from app.modules.nursing import nursing_bp
from app.modules.nursing import services as s

STAFF = (RoleName.NURSE, RoleName.ADMIN)


def _body():
    body = request.get_json(silent=True)
    if body is None and not request.get_data():
        return {}
    if not isinstance(body, dict):
        raise APIError(400, "VALIDATION_ERROR", "Request body must be a JSON object")
    return body


def _load(assessment_id):
    a = s.get_assessment(assessment_id)
    ensure_can_view_patient(a.patient)  # unassigned nurse → 404
    return a


def _replay(resource_id):
    return {"data": s.assessment_payload(db.session.get(NursingAssessment, int(resource_id)))}


@nursing_bp.get("")
@require_auth(*STAFF)
def list_assessments():
    """``?patient_id=`` (one patient) — without it, the signed-in nurse's own assessments of current
    patients (e.g. drafts waiting for signature). ``sign_status``, ``from`` / ``to``, ``include_history``."""
    patient = None
    if request.args.get("patient_id"):
        patient = resolve_patient(request.args["patient_id"])
        ensure_can_view_patient(patient)
    elif g.current_user.role_name != RoleName.NURSE:
        raise APIError(400, "VALIDATION_ERROR", "patient_id is required", [{"field": "patient_id", "issue": "is required"}])
    rows = s.list_assessments(g.current_user, patient, request.args)
    if patient is not None:
        record_patient_view(patient, resource_type="nursing_assessments")
        db.session.commit()
    return ok([s.assessment_payload(a, summary=True) for a in rows])


@nursing_bp.post("")
@require_auth(RoleName.NURSE)
def create_assessment():
    """New draft. Idempotency-Key supported (a retried submission never creates a second draft)."""
    body = _body()
    patient_id = body.get("patient_id")
    if not isinstance(patient_id, str) or not patient_id:
        raise APIError(400, "VALIDATION_ERROR", "護理評估內容有誤", [{"field": "patient_id", "issue": "is required"}])
    patient = resolve_patient(patient_id)
    ensure_can_view_patient(patient)

    def do_create():
        a = s.create_draft(patient, g.current_user, body)
        record_data_event(AuditAction.CREATE, "nursing_assessments", a.id, patient=patient,
                          changes={"assessment_type": a.assessment_type, "sign_status": a.sign_status, "items": len(a.items)})
        return a.id, {"data": s.assessment_payload(a)}, 201

    return run_idempotent(resource_type="nursing_assessments", body=body, create=do_create, replay=_replay, required=False)


@nursing_bp.get("/<int:assessment_id>")
@require_auth(*STAFF)
def get_assessment(assessment_id):
    a = _load(assessment_id)
    record_patient_view(a.patient, resource_type="nursing_assessments", resource_id=str(a.id))
    db.session.commit()
    return ok(s.assessment_payload(a))


@nursing_bp.get("/<int:assessment_id>/versions")
@require_auth(*STAFF)
def get_versions(assessment_id):
    """Every version of the assessment (original → corrections), oldest first."""
    a = _load(assessment_id)
    return ok([s.assessment_payload(v) for v in s.versions(a)])


@nursing_bp.patch("/<int:assessment_id>")
@require_auth(RoleName.NURSE)
def update_assessment(assessment_id):
    """Drafts only, by their author (signed → 422 RECORD_LOCKED; use amend)."""
    a = _load(assessment_id)
    changed = s.update_draft(a, g.current_user, _body())
    if changed:
        record_data_event(AuditAction.UPDATE, "nursing_assessments", a.id, patient=a.patient, changes={"fields": changed})
    db.session.commit()
    return ok(s.assessment_payload(a))


@nursing_bp.post("/<int:assessment_id>/sign")
@require_auth(RoleName.NURSE)
def sign_assessment(assessment_id):
    a = _load(assessment_id)
    superseded = s.sign(a, g.current_user)
    record_data_event(AuditAction.SIGN, "nursing_assessments", a.id, patient=a.patient,
                      changes={"sign_status": a.sign_status, "supersedes_id": superseded.id if superseded else None})
    db.session.commit()
    return ok(s.assessment_payload(a))


@nursing_bp.post("/<int:assessment_id>/amend")
@require_auth(RoleName.NURSE)
def amend_assessment(assessment_id):
    """``{amend_reason, …fields to change}`` on a signed assessment → 201 with a new draft version.
    Signing that draft marks the original ``amended``."""
    original = _load(assessment_id)
    body = _body()

    def do_amend():
        draft, reason = s.amend(original, g.current_user, body)
        record_data_event(AuditAction.AMEND, "nursing_assessments", original.id, patient=original.patient,
                          changes={"new_version_id": draft.id, "amend_reason": reason,
                                   "fields": sorted(k for k in body if k != "amend_reason")})
        return draft.id, {"data": s.assessment_payload(draft)}, 201

    return run_idempotent(resource_type="nursing_assessments", body=body, create=do_amend, replay=_replay, required=False)


@nursing_bp.patch("/<int:assessment_id>/items/<int:item_id>")
@require_auth(RoleName.NURSE)
def update_item(assessment_id, item_id):
    """``{item_status}`` — follow-up of a problem / intervention, also after signing."""
    a = _load(assessment_id)
    item = s.update_item(a, item_id, _body())
    record_data_event(AuditAction.UPDATE, "nursing_assessment_items", item.id, patient=a.patient,
                      changes={"assessment_id": a.id, "item_status": item.item_status})
    db.session.commit()
    return ok(s.item_payload(item))
