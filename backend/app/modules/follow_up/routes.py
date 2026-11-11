"""護理師追蹤管理 API (Phase 1c). Nurses act only on follow-ups of patients currently assigned to them
(anything else → 404, nothing revealed); admins and patients get 403 on the nurse endpoints. Every write
needs an Idempotency-Key and the version the nurse saw; every write and every detail / history view is audited."""

from flask import g, request

from app.core.api import APIError, ok, query_choice, query_int
from app.core.audit import record_patient_view
from app.core.auth import ensure_can_view_patient, require_auth, resolve_patient
from app.core.idempotency import run_idempotent
from app.extensions import db
from app.models import SymptomRecord
from app.models.enums import RoleName
from app.modules.follow_up import follow_up_bp
from app.modules.follow_up.services import (
    MAX_REASON,
    SORTS,
    STATUS_FILTERS,
    _Errors,
    cancel,
    complete,
    create_follow_up,
    events_payload,
    find,
    follow_up_payload,
    list_for_nurse,
    list_for_patient,
    parse_due_at,
    reschedule,
    text_field,
)


def _body():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise APIError(400, "VALIDATION_ERROR", "Request body must be a JSON object")
    return body


def _load(public_id):
    """A follow-up of a patient this nurse currently cares for; otherwise 404 (existence never revealed)."""
    fu = find(public_id)
    if fu is None or not _can_view(fu.patient):  # not (or no longer) assigned: the same answer as a missing id
        raise APIError(404, "NOT_FOUND", "Follow-up not found")
    return fu


def _can_view(patient):
    try:
        ensure_can_view_patient(patient)
        return True
    except APIError:
        return False


def _replay(public_id):
    return {"data": follow_up_payload(find(public_id), detail=True)}


@follow_up_bp.get("/care-follow-ups")
@require_auth(RoleName.NURSE)
def list_follow_ups():
    """Query: status=open|overdue|completed|cancelled|all (default open), patient_id, sort=due_at|-due_at|-updated_at, page, per_page."""
    patient = None
    if request.args.get("patient_id"):
        patient = resolve_patient(request.args["patient_id"])
        ensure_can_view_patient(patient)
    items, meta = list_for_nurse(
        g.current_user,
        status=query_choice(request.args, "status", "open", STATUS_FILTERS),
        patient=patient,
        sort=query_choice(request.args, "sort", "due_at", tuple(SORTS)),
        page=query_int(request.args, "page", 1, 1, 10_000),
        per_page=query_int(request.args, "per_page", 20, 1, 100),
    )
    return ok(items, meta=meta)


@follow_up_bp.post("/care-follow-ups")
@require_auth(RoleName.NURSE)
def create():
    """``{symptom_record_id, due_at, reason}`` for a reviewed record → 201. One open follow-up per record (409)."""
    body = _body()
    record_id = body.get("symptom_record_id")
    if not isinstance(record_id, int) or isinstance(record_id, bool):
        raise APIError(400, "VALIDATION_ERROR", "追蹤資料有誤", [{"field": "symptom_record_id", "issue": "is required"}])
    record = db.session.get(SymptomRecord, record_id)
    if record is None or record.patient.deleted_at is not None or not _can_view(record.patient):
        raise APIError(404, "NOT_FOUND", "Symptom record not found")  # one answer: missing or not yours
    errors = _Errors()
    due_at = parse_due_at(body.get("due_at"), record.patient, errors)
    reason = text_field(body, "reason", errors, MAX_REASON)
    errors.raise_if_any()

    def do_create():
        fu = create_follow_up(record, g.current_user, due_at, reason, record.nursing_assessment)
        return fu.public_id, {"data": follow_up_payload(fu, detail=True)}, 201

    return run_idempotent(resource_type="care_follow_ups", body=body, create=do_create, replay=_replay, required=True)


@follow_up_bp.get("/care-follow-ups/<follow_up_id>")
@require_auth(RoleName.NURSE)
def detail(follow_up_id):
    fu = _load(follow_up_id)
    record_patient_view(fu.patient, resource_type="care_follow_ups", resource_id=fu.public_id)
    db.session.commit()
    return ok(follow_up_payload(fu, detail=True))


@follow_up_bp.get("/care-follow-ups/<follow_up_id>/events")
@require_auth(RoleName.NURSE)
def events(follow_up_id):
    fu = _load(follow_up_id)
    record_patient_view(fu.patient, resource_type="care_follow_up_events", resource_id=fu.public_id)
    db.session.commit()
    return ok(events_payload(fu))


def _write(follow_up_id, action):
    fu = _load(follow_up_id)
    body = _body()

    def do():
        result = action(fu, g.current_user, body)
        data = follow_up_payload(fu, detail=True)
        if isinstance(result, tuple) and result[1] is not None:  # complete → the next follow-up of the chain
            data["next_follow_up"] = follow_up_payload(result[1])
        return fu.public_id, {"data": data}, 200

    return run_idempotent(resource_type="care_follow_ups", body=body, create=do, replay=_replay, required=True)


@follow_up_bp.patch("/care-follow-ups/<follow_up_id>/reschedule")
@require_auth(RoleName.NURSE)
def reschedule_route(follow_up_id):
    """``{due_at, reason, version}`` — open follow-ups only; the old date is kept in the history."""
    return _write(follow_up_id, reschedule)


@follow_up_bp.post("/care-follow-ups/<follow_up_id>/complete")
@require_auth(RoleName.NURSE)
def complete_route(follow_up_id):
    """``{result, actions, assessment_type?, version, next_follow_up?: {due_at, reason}}`` → a signed nursing
    assessment + completed (and the next follow-up of the chain)."""
    return _write(follow_up_id, complete)


@follow_up_bp.post("/care-follow-ups/<follow_up_id>/cancel")
@require_auth(RoleName.NURSE)
def cancel_route(follow_up_id):
    """``{reason, version}`` — open follow-ups only. Nothing is deleted."""
    return _write(follow_up_id, cancel)


@follow_up_bp.get("/patients/me/care-follow-ups")
@require_auth(RoleName.PATIENT)
def my_follow_ups():
    """A patient's own follow-ups, read only: planned date and state — no nurse notes, reasons or names."""
    patient = resolve_patient("me")
    ensure_can_view_patient(patient)
    return ok(list_for_patient(patient), meta={
        "notice": "本功能為照護追蹤紀錄，並非即時監測或緊急通報系統。如有急性或嚴重不適，請依醫院指示聯繫醫療團隊；緊急情況請撥打 119。"})
