"""Lab result entry and queries (database-design.md §7 J; api-design.md Phase 2 labs)."""

import math
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select

from app.core.api import APIError
from app.core.timeutil import iso_utc, parse_observed_at, patient_zone, to_local
from app.extensions import db
from app.models import LabResult, LabTestType, Notification
from app.models.base import utcnow
from app.models.enums import NotificationStatus, ObservationSource, RecordStatus
from app.services.alert_engine import evaluate_lab_result, replay_lab_result
from app.services.labs import FLAG_LEVEL, INPUT_RANGES, abnormal_flag, format_value, patient_view
from app.services.treatment import active_plan_and_cycle, cycle_nadir

MAX_BACKDATE = timedelta(days=30)  # results are often entered days after collection
SUMMARY_WINDOW = timedelta(days=30)


def _error(details):
    raise APIError(400, "VALIDATION_ERROR", "檢驗資料內容有誤", details)


def active_test_types():
    return db.session.execute(
        select(LabTestType).filter_by(is_active=True).order_by(LabTestType.display_order, LabTestType.code)
    ).scalars().all()


def test_type_payload(t):
    num = lambda v: float(v) if v is not None else None  # noqa: E731
    return {
        "code": t.code,
        "loinc_code": t.loinc_code,
        "name_zh": t.name_zh,
        "unit": t.unit,
        "ref_low": num(t.ref_low),
        "ref_high": num(t.ref_high),
        "critical_low": num(t.critical_low),
        "critical_high": num(t.critical_high),
    }


# ------------------------------------------------------------------ create


def create_lab_results(patient, user, body, correction_of=None):
    """Validate a panel of results collected together and persist one row per analyte.
    Each row is evaluated against lab alert rules. Caller commits.

    ``correction_of``: the (single-analyte) row this one corrects (Sprint 5) — keeps its cycle,
    recorder and may keep its time. Returns (rows, triggered_by_row_id).
    """
    now = utcnow()
    details = []
    backdate = timedelta(days=3650) if correction_of else MAX_BACKDATE  # a correction keeps the original time
    collected_at, problem = parse_observed_at(body.get("collected_at"), now, backdate)
    if body.get("collected_at") is None:
        details.append({"field": "collected_at", "issue": "is required"})
    elif problem:
        details.append({"field": "collected_at", "issue": problem})
    resulted_at = None
    if body.get("resulted_at") is not None:
        resulted_at, problem = parse_observed_at(body.get("resulted_at"), now, backdate)
        if problem:
            details.append({"field": "resulted_at", "issue": problem})
        elif resulted_at < collected_at:
            details.append({"field": "resulted_at", "issue": "cannot be earlier than collected_at"})

    types = {t.code: t for t in active_test_types()}
    items = body.get("results")
    parsed, seen = [], set()
    if not isinstance(items, list) or not items:
        details.append({"field": "results", "issue": "must be a non-empty list"})
        items = []
    for idx, item in enumerate(items):
        field = f"results[{idx}]"
        code = item.get("test_code") if isinstance(item, dict) else None
        value = item.get("value") if isinstance(item, dict) else None
        if code not in types:
            details.append({"field": f"{field}.test_code", "issue": f"must be one of: {', '.join(types)}"})
            continue
        if code in seen:
            details.append({"field": f"{field}.test_code", "issue": f"'{code}' appears more than once"})
            continue
        seen.add(code)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            details.append({"field": f"{field}.value", "issue": "must be a number"})
            continue
        low, high, decimals = INPUT_RANGES.get(code, (Decimal("0"), Decimal("100000"), 3))
        number = Decimal(str(value))
        if not low <= number <= high:
            details.append({"field": f"{field}.value", "issue": f"must be between {low} and {high}"})
            continue
        parsed.append((types[code], number.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)))
    if details:
        _error(details)

    zone = patient_zone(patient.timezone)
    local_day = to_local(collected_at, zone).date()
    cycle = correction_of.cycle if correction_of else active_plan_and_cycle(patient)[1]
    cycle_day = cycle.cycle_day_on(local_day) if cycle else None
    if cycle_day is None:
        cycle = None

    rows = []
    for test_type, number in parsed:
        row = LabResult(
            patient_id=patient.id,
            lab_test_type_id=test_type.id,
            collected_at=collected_at,
            resulted_at=resulted_at or now,
            value_numeric=number,
            unit=test_type.unit,
            ref_low=test_type.ref_low,
            ref_high=test_type.ref_high,
            abnormal_flag=abnormal_flag(test_type, number),
            recorded_by=correction_of.recorded_by if correction_of else user.id,
            amends_id=correction_of.id if correction_of else None,
            cycle=cycle,
            cycle_day=cycle_day,
            source=ObservationSource.NURSE,
            record_status=RecordStatus.FINAL,
        )
        row.test_type = test_type
        db.session.add(row)
        rows.append(row)
    db.session.flush()

    context = alert_context(patient, collected_at, cycle)
    triggered = {row.id: evaluate_lab_result(row, patient, **context) for row in rows}
    return rows, triggered


def alert_context(patient, collected_at, cycle):
    local_day = to_local(collected_at, patient_zone(patient.timezone)).date()
    return {
        "in_nadir": cycle_nadir(cycle, local_day)[0] if cycle else False,
        "cancer_type_ids": {d.cancer_type_id for d in patient.diagnoses if d.deleted_at is None},
    }


# ------------------------------------------------------------------ serialize


def serialize_triggered(triggered):
    return [
        {
            "alert_rule_code": t.rule.code,
            "severity": t.rule.severity,
            "test_code": t.code,
            "label": t.label,
            "value": t.value_text,
            "message": t.patient_message,
            "notified": t.notified,
        }
        for t in triggered
    ]


def _alerts_for(row, viewer):
    rows = db.session.execute(
        select(Notification).filter_by(source_table="lab_results", source_id=row.id).order_by(Notification.id)
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
        if viewer is not None and n.recipient_id == viewer.id:
            e["my_notification_id"] = n.id
    return list(events.values())


def result_payload(row, viewer=None, include_alerts=True):
    """Full result for staff: value, unit + reference range snapshot, flag, provenance."""
    t = row.test_type
    data = {
        "id": row.id,
        "test_code": t.code,
        "name_zh": t.name_zh,
        "value": format_value(row),
        "unit": row.unit,
        "ref_low": float(row.ref_low) if row.ref_low is not None else None,
        "ref_high": float(row.ref_high) if row.ref_high is not None else None,
        "critical_low": float(t.critical_low) if t.critical_low is not None else None,
        "critical_high": float(t.critical_high) if t.critical_high is not None else None,
        "abnormal_flag": row.abnormal_flag,
        "level": FLAG_LEVEL.get(row.abnormal_flag or "N"),
        "collected_at": iso_utc(row.collected_at),
        "resulted_at": iso_utc(row.resulted_at),
        "cycle_id": row.cycle_id,
        "cycle_day": row.cycle_day,
        "source": row.source,
        "record_status": row.record_status,
        "recorded_by": {"id": row.recorder.public_id, "display_name": row.recorder.display_name} if row.recorder else None,
    }
    if include_alerts:
        data["alerts"] = _alerts_for(row, viewer)
    return data


def replay_payload(rows, viewer):
    patient = rows[0].patient
    context = alert_context(patient, rows[0].collected_at, rows[0].cycle)
    triggered = [t for row in rows for t in replay_lab_result(row, **context)]
    return {
        "collected_at": iso_utc(rows[0].collected_at),
        "cycle_day": rows[0].cycle_day,
        "results": [result_payload(r, viewer) for r in rows],
        "triggered_alerts": serialize_triggered(triggered),
    }


# ------------------------------------------------------------------ queries


def _final_results(patient, *, since=None, code=None):
    q = (
        select(LabResult)
        .join(LabTestType, LabResult.lab_test_type_id == LabTestType.id)
        .where(LabResult.patient_id == patient.id, LabResult.record_status == RecordStatus.FINAL)
    )
    if since is not None:
        q = q.where(LabResult.collected_at >= since)
    if code:
        q = q.where(LabTestType.code == code)
    return db.session.execute(q.order_by(LabResult.collected_at.desc(), LabResult.id.desc())).scalars().all()


def latest_by_test(patient, since=None):
    latest = {}
    for row in _final_results(patient, since=since):
        latest.setdefault(row.test_type.code, row)
    order = {t.code: i for i, t in enumerate(active_test_types())}
    return dict(sorted(latest.items(), key=lambda kv: order.get(kv[0], 99)))


def full_history(patient, viewer, *, days, code):
    """Staff view: latest per test, per-test series (oldest→newest) and all rows."""
    since = utcnow() - timedelta(days=days)
    rows = _final_results(patient, since=since, code=code)
    latest = {}
    series = {}
    for row in rows:
        latest.setdefault(row.test_type.code, row)
        series.setdefault(row.test_type.code, []).append(
            {"collected_at": iso_utc(row.collected_at), "value": format_value(row), "abnormal_flag": row.abnormal_flag}
        )
    for points in series.values():
        points.reverse()
    return {
        "test_types": [test_type_payload(t) for t in active_test_types()],
        "latest": {code_: result_payload(r, viewer) for code_, r in latest.items()},
        "series": series,
        "results": [result_payload(r, viewer, include_alerts=False) for r in rows],
    }, {"days": days, "total": len(rows)}


def patient_summary(patient):
    """Simplified view for the patient: latest value per test within 30 days, plain wording,
    no reference-range numbers or staff details."""
    latest = latest_by_test(patient, since=utcnow() - SUMMARY_WINDOW)
    items = [{**patient_view(r), "collected_at": iso_utc(r.collected_at)} for r in latest.values()]
    abnormal = [i for i in items if i["status"] != "normal"]
    if not items:
        message = "最近 30 天還沒有檢驗結果。"
    elif any(i["level"] == "critical" for i in items):
        message = "有檢驗數值需要特別注意，請依下方說明照顧自己，護理團隊會與您聯繫。"
    elif abnormal:
        message = "有部分數值不在正常範圍，請參考下方說明。"
    else:
        message = "最近一次檢驗數值都在正常範圍。"
    return {
        "last_collected_at": max((i["collected_at"] for i in items), default=None),
        "message": message,
        "items": items,
    }


def list_abnormal(nurse, hours):
    """Flagged results (not ``N``) of the nurse's current patients within ``hours``, newest first."""
    from app.models import NursePatientAssignment
    since = utcnow() - timedelta(hours=hours)
    patient_ids = db.session.execute(
        select(NursePatientAssignment.patient_id).filter_by(nurse_id=nurse.id, ended_at=None)
    ).scalars().all()
    rows = db.session.execute(
        select(LabResult).where(LabResult.patient_id.in_(patient_ids), LabResult.record_status == RecordStatus.FINAL,
                                LabResult.collected_at >= since, LabResult.abnormal_flag.is_not(None), LabResult.abnormal_flag != "N")
        .order_by(LabResult.collected_at.desc(), LabResult.id.desc()).limit(100)
    ).scalars().all()
    items = []
    for r in rows:
        if r.patient.deleted_at is not None:
            continue
        p = r.patient
        items.append({**result_payload(r, nurse),
                      "patient": {"id": p.public_id, "patient_code": p.patient_code, "display_name": p.display_name}})
    meta = {"hours": hours, "total": len(items),
            "critical": sum(i["level"] == "critical" for i in items),
            "unresolved_alerts": sum(1 for i in items for a in i["alerts"] if not a["resolved"])}
    return items, meta
