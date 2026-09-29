"""Vital-sign submission and the nurse's abnormal-readings list (api-design.md §7)."""

import math
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select

from app.core.api import APIError
from app.core.timeutil import iso_utc, parse_observed_at, patient_zone, to_local
from app.extensions import db
from app.models import Notification, NursePatientAssignment, VitalSign
from app.models.base import utcnow
from app.models.enums import BpMeasureSite, CareAlertType, NotificationStatus, ObservationSource, RecordStatus, RoleName, TemperatureSite
from app.services.alert_engine import evaluate_vital_sign, replay_vital_sign
from app.services.treatment import active_plan_and_cycle, cycle_nadir
from app.services.vitals import VITAL_FIELDS, flags_for

MAX_NOTES = 1000
ABNORMAL_LIMIT = 100
SITE_LABEL = {"left_arm": "左手", "right_arm": "右手", "leg": "腳"}

CORRECTION_BACKDATE = timedelta(days=3650)  # a correction keeps the original time


def _error(details):
    raise APIError(400, "VALIDATION_ERROR", "生命徵象內容有誤", details)


def _parse_measurements(body):
    """Validate the numeric fields and sites. Returns (column values, details)."""
    details, values = [], {}
    for field, meta in VITAL_FIELDS.items():
        raw = body.get(field)
        if raw is None:
            continue
        if not isinstance(raw, (int, float)) or isinstance(raw, bool) or not math.isfinite(raw):
            details.append({"field": field, "issue": "must be a number"})
            continue
        if meta.decimals == 0 and float(raw) != int(raw):
            details.append({"field": field, "issue": "must be a whole number"})
            continue
        if not meta.minimum <= raw <= meta.maximum:
            details.append({"field": field, "issue": f"must be between {meta.minimum:g} and {meta.maximum:g}"})
            continue
        if meta.decimals:
            values[field] = Decimal(str(raw)).quantize(Decimal(1).scaleb(-meta.decimals), rounding=ROUND_HALF_UP)
        else:
            values[field] = int(raw)

    systolic, diastolic = values.get("systolic_bp_mmhg"), values.get("diastolic_bp_mmhg")
    sbp_sent, dbp_sent = body.get("systolic_bp_mmhg") is not None, body.get("diastolic_bp_mmhg") is not None
    if sbp_sent != dbp_sent:
        details.append({"field": "systolic_bp_mmhg" if dbp_sent else "diastolic_bp_mmhg", "issue": "blood pressure needs both values"})
    elif systolic is not None and diastolic is not None and systolic <= diastolic:
        details.append({"field": "systolic_bp_mmhg", "issue": "must be greater than diastolic"})

    for field, allowed, needs in (
        ("temperature_site", TemperatureSite.ALL, "temperature_c"),
        ("bp_measure_site", BpMeasureSite.ALL, "systolic_bp_mmhg"),
    ):
        site = body.get(field)
        if site is None:
            continue
        if site not in allowed:
            details.append({"field": field, "issue": f"must be one of: {', '.join(allowed)}"})
        elif body.get(needs) is None:
            details.append({"field": field, "issue": f"only allowed together with {needs}"})
        else:
            values[field] = site
    return values, details


def create_vital_sign(patient, user, body, correction_of=None):
    """Validate and persist one reading, then evaluate alert rules. Caller commits.

    ``correction_of``: the record this one corrects (Sprint 5) — keeps its cycle (no
    recalculation), author and source, and may keep its time even if it is older than the
    usual backdate limit. Returns (vital, flags, triggered, warnings).
    """
    now = utcnow()
    values, details = _parse_measurements(body)
    if not any(field in values for field in VITAL_FIELDS) and not details:
        details.append({"field": "values", "issue": "at least one measurement is required"})
    measured_at, problem = parse_observed_at(body.get("measured_at"), now, CORRECTION_BACKDATE if correction_of else None)
    if problem:
        details.append({"field": "measured_at", "issue": problem})
    notes = body.get("notes")
    if notes is not None and (not isinstance(notes, str) or len(notes) > MAX_NOTES):
        details.append({"field": "notes", "issue": f"must be a string of at most {MAX_NOTES} characters"})
    if details:
        _error(details)

    zone = patient_zone(patient.timezone)
    local_day = to_local(measured_at, zone).date()
    cycle = correction_of.cycle if correction_of else active_plan_and_cycle(patient)[1]
    cycle_day = cycle.cycle_day_on(local_day) if cycle else None
    if cycle_day is None:
        cycle = None

    vital = VitalSign(
        patient_id=patient.id,
        measured_at=measured_at,
        recorded_by=correction_of.recorded_by if correction_of else user.id,
        amends_id=correction_of.id if correction_of else None,
        notes=(notes or "").strip() or None,
        cycle=cycle,
        cycle_day=cycle_day,
        source=correction_of.source if correction_of else (
            ObservationSource.PATIENT_APP if user.role_name == RoleName.PATIENT else ObservationSource.NURSE),
        record_status=RecordStatus.FINAL,
        **values,
    )
    db.session.add(vital)
    db.session.flush()

    context = _alert_context(vital, patient)
    triggered = evaluate_vital_sign(vital, patient, **context)
    return vital, flags_for(vital, in_nadir=context["in_nadir"]), triggered, _limb_warnings(vital, patient)


def _alert_context(vital, patient):
    local_day = to_local(vital.measured_at, patient_zone(patient.timezone)).date()
    return {
        "in_nadir": cycle_nadir(vital.cycle, local_day)[0] if vital.cycle else False,
        "cancer_type_ids": {d.cancer_type_id for d in patient.diagnoses if d.deleted_at is None},
    }


def _limb_warnings(vital, patient):
    """Blood pressure taken on a limb the care team marked as restricted: accept (it was
    measured), but tell the user and let the caller audit it."""
    if not vital.bp_measure_site:
        return []
    return [
        {
            "code": "LIMB_RESTRICTION",
            "message": f"注意：{alert.description}。本次血壓量測部位為{SITE_LABEL.get(vital.bp_measure_site, vital.bp_measure_site)}。",
        }
        for alert in patient.active_care_alerts
        if alert.alert_type == CareAlertType.LIMB_RESTRICTION and alert.body_site == vital.bp_measure_site
    ]


# ------------------------------------------------------------------ serialize


def _values(vital):
    data = {}
    for field, meta in VITAL_FIELDS.items():
        value = getattr(vital, field)
        data[field] = (float(value) if meta.decimals else int(value)) if value is not None else None
    data["temperature_site"] = vital.temperature_site
    data["bp_measure_site"] = vital.bp_measure_site
    return data


def serialize_triggered(triggered):
    return [
        {
            "alert_rule_code": t.rule.code,
            "severity": t.rule.severity,
            "field": t.code,
            "label": t.label,
            "value": t.value_text,
            "message": t.patient_message,
            "notified": t.notified,
        }
        for t in triggered
    ]


def serialize_vital(vital, *, flags, triggered, warnings):
    return {
        "id": vital.id,
        "patient_id": vital.patient.public_id,
        "measured_at": iso_utc(vital.measured_at),
        "cycle_id": vital.cycle_id,
        "cycle_day": vital.cycle_day,
        "source": vital.source,
        "record_status": vital.record_status,
        **_values(vital),
        "notes": vital.notes,
        "flags": flags,
        "triggered_alerts": triggered,
        "warnings": warnings,
    }


def replay_payload(vital):
    """Response for an idempotent replay: same flags / alerts / warnings, nothing re-sent."""
    context = _alert_context(vital, vital.patient)
    return serialize_vital(
        vital,
        flags=flags_for(vital, in_nadir=context["in_nadir"]),
        triggered=serialize_triggered(replay_vital_sign(vital, **context)),
        warnings=_limb_warnings(vital, vital.patient),
    )


# ------------------------------------------------------------------ nurse: abnormal readings


def _alerts_for(vital, nurse):
    rows = db.session.execute(
        select(Notification).filter_by(source_table="vital_signs", source_id=vital.id).order_by(Notification.id)
    ).scalars().all()
    events = {}
    for n in rows:
        e = events.setdefault(n.event_key or n.id, {
            "event_key": n.event_key,
            "alert_rule_code": n.alert_rule.code if n.alert_rule else None,
            "severity": n.severity,
            "title": n.title,
            "status": n.status,
            "resolved": n.status == NotificationStatus.RESOLVED,
            "resolved_by": n.resolver.display_name if n.resolver else None,
            "resolution_note": n.resolution_note,
            "my_notification_id": None,
        })
        if n.recipient_id == nurse.id:
            e["my_notification_id"] = n.id  # lets the nurse resolve it inline
    return sorted(events.values(), key=lambda a: a["severity"] != "critical")


def list_abnormal(nurse, hours):
    """Flagged readings of the nurse's currently assigned patients within ``hours``, newest first."""
    since = utcnow() - timedelta(hours=hours)
    patient_ids = db.session.execute(
        select(NursePatientAssignment.patient_id).filter_by(nurse_id=nurse.id, ended_at=None)
    ).scalars().all()
    vitals = db.session.execute(
        select(VitalSign)
        .where(
            VitalSign.patient_id.in_(patient_ids),
            VitalSign.record_status == RecordStatus.FINAL,
            VitalSign.measured_at >= since,
        )
        .order_by(VitalSign.measured_at.desc(), VitalSign.id.desc())
    ).scalars().all()

    items = []
    for vital in vitals:
        patient = vital.patient
        if patient.deleted_at is not None:
            continue
        in_nadir = _alert_context(vital, patient)["in_nadir"]
        flags = flags_for(vital, in_nadir=in_nadir)
        if not flags:
            continue
        items.append({
            "id": vital.id,
            "patient": {"id": patient.public_id, "patient_code": patient.patient_code, "display_name": patient.display_name},
            "measured_at": iso_utc(vital.measured_at),
            "cycle_day": vital.cycle_day,
            "in_nadir": in_nadir,
            "source": vital.source,
            **_values(vital),
            "flags": flags,
            "severity": flags[0]["level"],
            "alerts": _alerts_for(vital, nurse),
        })
        if len(items) >= ABNORMAL_LIMIT:
            break
    meta = {
        "hours": hours,
        "total": len(items),
        "critical": sum(i["severity"] == "critical" for i in items),
        "warning": sum(i["severity"] == "warning" for i in items),
        "unresolved_alerts": sum(1 for i in items for a in i["alerts"] if not a["resolved"]),
    }
    return items, meta


def list_patient_vitals(patient, days, include_history=False):
    """Staff list of one patient's readings (Sprint 5: the records a nurse may correct)."""
    conds = [VitalSign.patient_id == patient.id, VitalSign.measured_at >= utcnow() - timedelta(days=days)]
    if not include_history:
        conds.append(VitalSign.record_status == RecordStatus.FINAL)
    rows = db.session.execute(select(VitalSign).where(*conds).order_by(VitalSign.measured_at.desc(), VitalSign.id.desc()).limit(200)).scalars().all()
    return [{
        "id": v.id, "measured_at": iso_utc(v.measured_at), "cycle_id": v.cycle_id, "cycle_day": v.cycle_day, "source": v.source,
        "record_status": v.record_status, "amends_id": v.amends_id, **_values(v), "notes": v.notes,
        "flags": flags_for(v, in_nadir=_alert_context(v, patient)["in_nadir"]),
        "recorded_by": {"id": v.recorder.public_id, "display_name": v.recorder.display_name} if v.recorder else None,
    } for v in rows]
