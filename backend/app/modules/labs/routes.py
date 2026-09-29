from flask import g, request

from app.core.api import APIError, ok, query_choice, query_int
from app.core.audit import record_data_event, record_patient_view
from app.core.auth import ensure_can_view_patient, require_auth, resolve_patient
from app.core.idempotency import run_idempotent
from app.core.timeutil import iso_utc
from app.extensions import db
from app.models import LabResult
from app.models.enums import AuditAction, RoleName
from app.modules.labs import labs_bp
from app.modules.labs.services import (
    active_test_types,
    create_lab_results,
    full_history,
    patient_summary,
    replay_payload,
    result_payload,
    serialize_triggered,
    test_type_payload,
)
from app.services.corrections import register_routes


@labs_bp.get("/test-types")
@require_auth()
def test_types():
    """Supported analytes with unit, reference and critical ranges."""
    return ok([test_type_payload(t) for t in active_test_types()])


@labs_bp.post("/results")
@require_auth(RoleName.NURSE)
def create():
    """Record a panel of lab results collected together (nurse; e.g. a CBC before chemo).

    Body: ``{patient_id, collected_at, resulted_at?, results: [{test_code, value}]}``.
    One lab_results row per analyte; each row is evaluated against lab alert rules
    (e.g. ANC) and notifications follow the shared workflow. Idempotency-Key optional.
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise APIError(400, "VALIDATION_ERROR", "Request body must be a JSON object")
    patient_id = body.get("patient_id")
    if not isinstance(patient_id, str) or not patient_id:
        raise APIError(400, "VALIDATION_ERROR", "檢驗資料內容有誤", [{"field": "patient_id", "issue": "is required"}])
    patient = resolve_patient(patient_id)
    ensure_can_view_patient(patient)
    nurse = g.current_user

    def do_create():
        rows, triggered = create_lab_results(patient, nurse, body)
        all_triggered = [t for row in rows for t in triggered[row.id]]
        record_data_event(
            AuditAction.CREATE, "lab_results", rows[0].id, patient=patient,
            changes={
                "lab_result_ids": [r.id for r in rows],
                "test_codes": [r.test_type.code for r in rows],
                "alert_rule_codes": [t.rule.code for t in all_triggered if t.notified],
            },
        )
        payload = {
            "collected_at": iso_utc(rows[0].collected_at),
            "cycle_day": rows[0].cycle_day,
            "results": [result_payload(r, nurse) for r in rows],
            "triggered_alerts": serialize_triggered(all_triggered),
        }
        # resource pointer: the panel's row ids (≤ 4 analytes, fits idempotency_records.resource_id)
        return ",".join(str(r.id) for r in rows), {"data": payload}, 201

    def replay(resource_id):
        rows = [db.session.get(LabResult, int(i)) for i in resource_id.split(",")]
        return {"data": replay_payload(rows, nurse)}

    return run_idempotent(resource_type="lab_results", body=body, create=do_create, replay=replay, required=False)


@labs_bp.get("/results/<patient_id>")
@require_auth(RoleName.NURSE, RoleName.ADMIN)
def history(patient_id):
    """Full lab data for staff. Query: days (1–365, default 90), test (WBC|ANC|HGB|PLT)."""
    patient = resolve_patient(patient_id)
    ensure_can_view_patient(patient)
    codes = [t.code for t in active_test_types()]
    data, meta = full_history(
        patient, g.current_user,
        days=query_int(request.args, "days", 90, 1, 365),
        code=query_choice(request.args, "test", "", ("", *codes)) or None,
    )
    record_patient_view(patient, resource_type="lab_results")
    db.session.commit()
    return ok(data, meta=meta)


@labs_bp.get("/summary/<patient_id>")
@require_auth()
def summary(patient_id):
    """Simplified latest results (patient view; staff may call it too). ``me`` for patients."""
    patient = resolve_patient(patient_id)
    ensure_can_view_patient(patient)
    return ok(patient_summary(patient))


# ------------------------------------------------------------------ corrections (Sprint 5) and the abnormal list


@labs_bp.get("/abnormal")
@require_auth(RoleName.NURSE)
def abnormal():
    """Results outside the reference range for the nurse's current patients (``hours`` 1–720,
    default 168), newest first, with the state of the alerts they raised."""
    from app.modules.labs.services import list_abnormal
    items, meta = list_abnormal(g.current_user, query_int(request.args, "hours", 168, 1, 720))
    return ok(items, meta=meta)



register_routes(labs_bp, "/results", "lab_results", lambda r: result_payload(r, g.current_user))
