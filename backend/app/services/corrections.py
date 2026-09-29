"""Corrections of observations (Sprint 5; api-design.md §1.6): symptom records, vital signs and
lab results are append-only.

- ``amend``: a new record built from the original plus the corrected fields, created through
  the module's own create function (same validation, same alert engine), keeping the
  original's cycle (no cycle_day recalculation), author, source and — for symptoms — review
  state. The original becomes ``amended``; ``amends_id`` links them.
- ``mark-error``: the record becomes ``entered_in_error`` (kept, no longer used).
- Alerts of the original (existing Notification Workflow, resolve step only):
  amend — an open alert is resolved when the corrected values no longer meet its rule, or
  when the corrected record raised it again; it stays open when the rule still matches and a
  new alert was held back by the cooldown (so a true alert is never lost).
  mark-error — every open alert of the record is resolved with the reason.
- Nurses of the patient only. The reason is kept in the audit log.
"""

from flask import g, request
from sqlalchemy import select

from app.core.api import APIError, ok
from app.core.audit import record_data_event
from app.core.auth import ensure_can_view_patient, require_auth
from app.core.idempotency import run_idempotent
from app.core.timeutil import iso_utc
from app.extensions import db
from app.models import LabResult, Notification, SymptomRecord, VitalSign
from app.models.base import utcnow
from app.models.enums import AuditAction, NotificationStatus, NotificationType, RecordStatus, RoleName
from app.services.vitals import VITAL_FIELDS

REASON_MAX = 500


def _person(user):
    return {"id": user.public_id, "display_name": user.display_name} if user else None


def _reason(body, field="amend_reason"):
    v = body.get(field)
    if not isinstance(v, str) or not v.strip() or len(v.strip()) > REASON_MAX:
        raise APIError(400, "VALIDATION_ERROR", "請填寫原因", [{"field": field, "issue": f"is required (at most {REASON_MAX} characters)"}])
    return v.strip()


def _ensure_final(record):
    if record.record_status != RecordStatus.FINAL:
        raise APIError(409, "INVALID_STATE", "這筆紀錄已被更正或標示為錯誤，請對最新的紀錄操作")


# ------------------------------------------------------------------ kinds


def _symptom_body(r):
    from app.modules.symptom.services import serialize_value
    values = [{k: v for k, v in serialize_value(x).items() if k not in ("label", "score", "ctcae_grade")} for x in r.values]
    return {"form_code": r.form.code if r.form else None, "recorded_at": iso_utc(r.recorded_at), "notes": r.notes, "values": values}


def _symptom_create(patient, nurse, body, original):
    from app.modules.symptom.services import create_symptom_record
    return create_symptom_record(patient, nurse, body, correction_of=original)


def _symptom_payload(r):
    from app.modules.symptom.services import serialize_value
    return {"recorded_at": iso_utc(r.recorded_at), "notes": r.notes, "review_status": r.review_status,
            "values": [serialize_value(v) for v in r.values], "reported_by": _person(r.reporter)}


VITAL_EXTRA = ("temperature_site", "bp_measure_site", "notes")


def _vital_body(v):
    from app.modules.vital_signs.services import _values
    data = {k: val for k, val in _values(v).items() if val is not None}
    return {**data, "measured_at": iso_utc(v.measured_at), **({"notes": v.notes} if v.notes else {})}


def _vital_create(patient, nurse, body, original):
    from app.modules.vital_signs.services import create_vital_sign
    vital, _flags, triggered, _warnings = create_vital_sign(patient, nurse, body, correction_of=original)
    return vital, triggered


def _vital_payload(v):
    from app.modules.vital_signs.services import _values
    return {"measured_at": iso_utc(v.measured_at), **_values(v), "notes": v.notes, "recorded_by": _person(v.recorder)}


def _lab_body(r):
    return {"collected_at": iso_utc(r.collected_at), "resulted_at": iso_utc(r.resulted_at),
            "results": [{"test_code": r.test_type.code, "value": float(r.value_numeric)}]}


def _lab_create(patient, nurse, body, original):
    from app.modules.labs.services import create_lab_results
    rows, triggered = create_lab_results(patient, nurse, body, correction_of=original)
    return rows[0], triggered[rows[0].id]


def _lab_payload(r):
    from app.modules.labs.services import format_value
    return {"collected_at": iso_utc(r.collected_at), "test_code": r.test_type.code, "value": format_value(r), "unit": r.unit,
            "abnormal_flag": r.abnormal_flag, "recorded_by": _person(r.recorder)}


def _lab_merge(original, base, body):
    """A lab correction changes one analyte: ``value`` and / or ``collected_at``."""
    merged = dict(base)
    if "value" in body:
        merged["results"] = [{"test_code": original.test_type.code, "value": body["value"]}]
    if "collected_at" in body:
        merged["collected_at"] = body["collected_at"]
    return merged


def _default_merge(original, base, body):
    return {**base, **body}


KINDS = {
    "symptom_records": {"model": SymptomRecord, "base": _symptom_body, "create": _symptom_create, "payload": _symptom_payload,
                        "merge": _default_merge, "fields": ("values", "recorded_at", "notes")},
    "vital_signs": {"model": VitalSign, "base": _vital_body, "create": _vital_create, "payload": _vital_payload,
                    "merge": _default_merge, "fields": (*VITAL_FIELDS, *VITAL_EXTRA, "measured_at")},
    "lab_results": {"model": LabResult, "base": _lab_body, "create": _lab_create, "payload": _lab_payload,
                    "merge": _lab_merge, "fields": ("value", "collected_at")},
}


# ------------------------------------------------------------------ alerts of the original


def _open_alerts(table, record_id):
    return db.session.execute(
        select(Notification).where(Notification.source_table == table, Notification.source_id == record_id,
                                   Notification.type == NotificationType.RISK_ALERT,
                                   Notification.status.in_(NotificationStatus.OPEN))
    ).scalars().all()


def _close(rows, nurse, note):
    from app.modules.notification.services import _fast_forward, mark_read
    now = utcnow()
    for row in rows:
        _fast_forward(row, nurse, note, now)
        if row.recipient_id == nurse.id:
            mark_read(row)


def reconcile_after_amend(table, original, triggered, nurse, reason):
    """Returns the alert rule codes that were closed on the original."""
    matched = {t.rule.id: t for t in triggered}
    closed = []
    by_rule = {}
    for n in _open_alerts(table, original.id):
        by_rule.setdefault(n.alert_rule_id, []).append(n)
    for rule_id, rows in by_rule.items():
        t = matched.get(rule_id)
        if t is None:
            note = f"原始紀錄已更正（{reason}）：更正後的數值已不符合此警示條件"
        elif t.notified:
            note = f"原始紀錄已更正（{reason}）：已依更正後的紀錄重新通知"
        else:
            continue  # still valid; the corrected record's alert was held back by the cooldown
        _close(rows, nurse, note)
        closed.append(rows[0].alert_rule.code if rows[0].alert_rule else str(rule_id))
    return closed


# ------------------------------------------------------------------ operations


def amend(table, original, nurse, body):
    kind = KINDS[table]
    _ensure_final(original)
    reason = _reason(body)
    corrections = {k: v for k, v in body.items() if k != "amend_reason"}
    unknown = sorted(set(corrections) - set(kind["fields"]))
    if unknown:
        raise APIError(400, "VALIDATION_ERROR", "更正內容有誤", [{"field": k, "issue": "is not a correctable field"} for k in unknown])
    base = kind["base"](original)
    merged = kind["merge"](original, base, corrections)
    merged = {k: v for k, v in merged.items() if v is not None}
    if merged == {k: v for k, v in base.items() if v is not None}:
        raise APIError(400, "VALIDATION_ERROR", "更正內容有誤", [{"field": "amend_reason", "issue": "nothing changed: send the corrected fields"}])
    new, triggered = kind["create"](original.patient, nurse, merged, original)
    original.record_status = RecordStatus.AMENDED
    closed = reconcile_after_amend(table, original, triggered, nurse, reason)
    return new, triggered, reason, sorted(corrections), closed


def mark_error(table, record, nurse, body):
    _ensure_final(record)
    reason = _reason(body, "reason")
    record.record_status = RecordStatus.ENTERED_IN_ERROR
    rows = _open_alerts(table, record.id)
    _close(rows, nurse, f"紀錄已標示為錯誤：{reason}")
    return reason, sorted({r.alert_rule.code for r in rows if r.alert_rule})


def history(table, record):
    """Correction chain of a record, oldest first (staff)."""
    first = record
    while first.amends is not None:
        first = first.amends
    chain, cur = [], first
    while cur is not None:
        chain.append(cur)
        cur = cur.amendments[0] if cur.amendments else None
    payload = KINDS[table]["payload"]
    return [{"id": r.id, "record_status": r.record_status, "amends_id": r.amends_id,
             "amended_by_id": r.amendments[0].id if r.amendments else None, "cycle_id": r.cycle_id, "cycle_day": r.cycle_day,
             "source": r.source, "created_at": iso_utc(r.created_at), **payload(r)} for r in chain]


# ------------------------------------------------------------------ routes (registered by each module)


def register_routes(bp, prefix, table, payload_of):
    """``POST {prefix}/<id>/amend``, ``POST {prefix}/<id>/mark-error``, ``GET {prefix}/<id>/history``.
    ``payload_of(record)`` is the module's usual response for the corrected record."""
    model = KINDS[table]["model"]
    name = table.replace("_", "-")

    def load(record_id):
        record = db.session.get(model, record_id)
        if record is None:
            raise APIError(404, "NOT_FOUND", "Record not found")
        ensure_can_view_patient(record.patient)  # unassigned nurse → 404
        return record

    def body_of():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise APIError(400, "VALIDATION_ERROR", "Request body must be a JSON object")
        return body

    @require_auth(RoleName.NURSE)
    def amend_view(record_id):
        original = load(record_id)
        body = body_of()

        def do():
            new, triggered, reason, fields, closed = amend(table, original, g.current_user, body)
            record_data_event(AuditAction.AMEND, table, original.id, patient=original.patient,
                              changes={"new_record_id": new.id, "amend_reason": reason, "fields": fields,
                                       "alert_rule_codes": [t.rule.code for t in triggered if t.notified], "closed_alerts": closed})
            return new.id, {"data": payload_of(new)}, 201

        def replay(resource_id):
            return {"data": payload_of(db.session.get(model, int(resource_id)))}

        return run_idempotent(resource_type=table, body=body, create=do, replay=replay, required=False)

    @require_auth(RoleName.NURSE)
    def mark_error_view(record_id):
        record = load(record_id)
        reason, closed = mark_error(table, record, g.current_user, body_of())
        record_data_event(AuditAction.MARK_ERROR, table, record.id, patient=record.patient,
                          changes={"reason": reason, "closed_alerts": closed})
        db.session.commit()
        return ok({"id": record.id, "record_status": record.record_status, "closed_alerts": closed})

    @require_auth(RoleName.NURSE, RoleName.ADMIN)
    def history_view(record_id):
        return ok(history(table, load(record_id)))

    bp.add_url_rule(f"{prefix}/<int:record_id>/amend", f"{name}_amend", amend_view, methods=["POST"])
    bp.add_url_rule(f"{prefix}/<int:record_id>/mark-error", f"{name}_mark_error", mark_error_view, methods=["POST"])
    bp.add_url_rule(f"{prefix}/<int:record_id>/history", f"{name}_history", history_view, methods=["GET"])
