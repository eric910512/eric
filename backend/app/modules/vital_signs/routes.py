from flask import g, request

from app.core.api import APIError, ok, query_int
from app.core.audit import record_data_event
from app.core.auth import ensure_can_view_patient, require_auth, resolve_patient
from app.core.idempotency import run_idempotent
from app.extensions import db
from app.models import VitalSign
from app.models.enums import AuditAction, RoleName
from app.modules.vital_signs import vital_signs_bp
from app.modules.vital_signs.services import (
    create_vital_sign,
    list_abnormal,
    replay_payload,
    serialize_triggered,
    serialize_vital,
)
from app.services.vitals import VITAL_FIELDS, reference_ranges
from app.services.corrections import register_routes


@vital_signs_bp.post("")
@require_auth(RoleName.PATIENT, RoleName.NURSE)
def create():
    """Record one set of vital signs (api-design.md §7).

    Same workflow as symptom reports: patients must send an Idempotency-Key; in one
    transaction the reading, an audit CREATE row and notifications for matching alert rules.
    The response carries reference-range ``flags`` and limb-restriction ``warnings``.
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise APIError(400, "VALIDATION_ERROR", "Request body must be a JSON object")
    patient_id = body.get("patient_id")
    if not isinstance(patient_id, str) or not patient_id:
        raise APIError(400, "VALIDATION_ERROR", "生命徵象內容有誤", [{"field": "patient_id", "issue": "is required"}])
    patient = resolve_patient(patient_id)
    ensure_can_view_patient(patient)

    def do_create():
        vital, flags, triggered, warnings = create_vital_sign(patient, g.current_user, body)
        record_data_event(
            AuditAction.CREATE, "vital_signs", vital.id, patient=patient,
            changes={
                "fields": [f for f in VITAL_FIELDS if getattr(vital, f) is not None],
                "alert_rule_codes": [t.rule.code for t in triggered if t.notified],
                "warnings": [w["code"] for w in warnings],
            },
        )
        payload = serialize_vital(vital, flags=flags, triggered=serialize_triggered(triggered), warnings=warnings)
        return vital.id, {"data": payload}, 201

    def replay(resource_id):
        return {"data": replay_payload(db.session.get(VitalSign, int(resource_id)))}

    return run_idempotent(
        resource_type="vital_signs",
        body=body,
        create=do_create,
        replay=replay,
        required=g.current_user.role_name == RoleName.PATIENT,
    )


@vital_signs_bp.get("/abnormal")
@require_auth(RoleName.NURSE)
def abnormal():
    """Readings outside the reference ranges for the nurse's assigned patients, with the
    state of the alerts they raised. Query: hours (1–168, default 72).
    """
    items, meta = list_abnormal(g.current_user, query_int(request.args, "hours", 72, 1, 168))
    return ok(items, meta=meta)


@vital_signs_bp.get("/reference-ranges")
@require_auth()
def ranges():
    """Thresholds used for flags (input hints / colouring). Phase 1–2: from config."""
    return ok(reference_ranges(), meta={"source": "config_file"})


# ------------------------------------------------------------------ corrections (Sprint 5)


register_routes(vital_signs_bp, "", "vital_signs", replay_payload)


@vital_signs_bp.get("/patient/<patient_id>")
@require_auth(RoleName.NURSE, RoleName.ADMIN)
def patient_readings(patient_id):
    """Readings of one patient for staff, newest first (``days`` 1–365, default 30;
    ``include_history=true`` adds corrected / erroneous ones)."""
    from app.modules.vital_signs.services import list_patient_vitals
    patient = resolve_patient(patient_id)
    ensure_can_view_patient(patient)
    items = list_patient_vitals(patient, query_int(request.args, "days", 30, 1, 365), request.args.get("include_history") == "true")
    return ok(items, meta={"total": len(items), "timezone": patient.timezone})
