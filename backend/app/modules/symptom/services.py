"""Symptom forms and reports (api-design.md §6)."""

import math
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select

from app.core.api import APIError
from app.core.timeutil import iso_utc, parse_observed_at, patient_zone, to_local
from app.extensions import db
from app.models import Notification, NursingAssessment, SymptomForm, SymptomRecord, SymptomRecordValue
from app.models.base import utcnow
from app.models.enums import (
    NotificationStatus,
    ObservationSource,
    RecordStatus,
    ReviewStatus,
    RiskLevel,
    RoleName,
    SignStatus,
    SymptomValueType,
)
from app.services.alert_engine import evaluate_symptom_record, replay_symptom_record
from app.modules.notification.services import resolve_source_alerts
from app.services.treatment import active_plan_and_cycle, cycle_nadir

MAX_TEXT = 1000
MAX_NOTES = 2000

# Which request key carries the answer for each value_type.
ANSWER_KEY = {
    SymptomValueType.SCALE: "value_numeric",
    SymptomValueType.NUMERIC: "value_numeric",
    SymptomValueType.BOOLEAN: "value_boolean",
    SymptomValueType.SINGLE_CHOICE: "option_code",
    SymptomValueType.MULTI_CHOICE: "option_codes",
    SymptomValueType.TEXT: "value_text",
}


def _num(value):
    return float(value) if value is not None else None


# ------------------------------------------------------------------ forms


def get_active_form(code):
    form = db.session.execute(select(SymptomForm).filter_by(code=code, is_active=True)).scalar_one_or_none()
    if form is None:
        raise APIError(404, "NOT_FOUND", "Form not found")
    return form


def serialize_definition(d):
    data = {
        "id": d.id,
        "code": d.code,
        "name_zh": d.name_zh,
        "question_text": d.question_text,
        "help_text": d.help_text,
        "value_type": d.value_type,
        "higher_is_worse": d.higher_is_worse,
    }
    if d.value_type in (SymptomValueType.SCALE, SymptomValueType.NUMERIC):
        data.update(
            min_value=_num(d.min_value),
            max_value=_num(d.max_value),
            step=_num(d.step),
            unit=d.unit,
            min_label=d.min_label,
            max_label=d.max_label,
        )
    if d.value_type in (SymptomValueType.SINGLE_CHOICE, SymptomValueType.MULTI_CHOICE):
        data["options"] = [
            {"id": o.id, "value_code": o.value_code, "label_zh": o.label_zh, "score": _num(o.score)}
            for o in d.options
            if o.is_active
        ]
    return data


def serialize_form(form):
    return {
        "id": form.id,
        "code": form.code,
        "name": form.name,
        "description": form.description,
        "intended_for": form.intended_for,
        "availability": form.availability,
        "recall_period_hours": form.recall_period_hours,
        "version": form.version,
        "items": [
            {
                "display_order": item.display_order,
                "is_required": bool(item.is_required),
                "display_condition": item.display_condition,
                "definition": serialize_definition(item.definition),
            }
            for item in form.items
            if item.definition.is_active
        ],
    }


# ------------------------------------------------------------------ validation


class _Errors:
    def __init__(self):
        self.details = []

    def add(self, field, issue):
        self.details.append({"field": field, "issue": issue})

    def raise_if_any(self):
        if self.details:
            raise APIError(400, "VALIDATION_ERROR", "症狀回報內容有誤", self.details)


def _parse_recorded_at(raw, errors, now, correction=False):
    value, problem = parse_observed_at(raw, now, timedelta(days=3650) if correction else None)  # a correction keeps the original time
    if problem:
        errors.add("recorded_at", problem)
    return value


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate_answer(definition, item, field, errors):
    """Return (column_values, score, selected_options) or None when invalid."""
    vtype = definition.value_type
    expected = ANSWER_KEY.get(vtype)
    stray = sorted(set(item) - {"definition_code", expected})
    if stray:
        errors.add(field, f"unexpected keys for {vtype}: {', '.join(stray)}")
        return None
    if expected not in item:
        errors.add(field, f"'{expected}' is required for {vtype}")
        return None
    answer = item[expected]

    if vtype in (SymptomValueType.SCALE, SymptomValueType.NUMERIC):
        if not _is_number(answer):
            errors.add(f"{field}.value_numeric", "must be a number")
            return None
        value = Decimal(str(answer))
        lo, hi, step = definition.min_value, definition.max_value, definition.step
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            errors.add(f"{field}.value_numeric", f"must be between {_num(lo):g} and {_num(hi):g}")
            return None
        if step:
            base = lo if lo is not None else Decimal(0)
            try:
                if ((value - base) / step) % 1 != 0:
                    errors.add(f"{field}.value_numeric", f"must be in steps of {_num(step):g}")
                    return None
            except InvalidOperation:
                pass
        return {"value_numeric": value}, value, []

    if vtype == SymptomValueType.BOOLEAN:
        if not isinstance(answer, bool):
            errors.add(f"{field}.value_boolean", "must be true or false")
            return None
        return {"value_boolean": answer}, Decimal(1 if answer else 0), []

    options = {o.value_code: o for o in definition.options if o.is_active}
    if vtype == SymptomValueType.SINGLE_CHOICE:
        option = options.get(answer) if isinstance(answer, str) else None
        if option is None:
            errors.add(f"{field}.option_code", "is not a valid option")
            return None
        return {"option_id": option.id}, (Decimal(option.score) if option.score is not None else None), []

    if vtype == SymptomValueType.MULTI_CHOICE:
        if not isinstance(answer, list) or not all(isinstance(c, str) for c in answer):
            errors.add(f"{field}.option_codes", "must be a list of option codes")
            return None
        if len(set(answer)) != len(answer):
            errors.add(f"{field}.option_codes", "must not contain duplicates")
            return None
        unknown = [c for c in answer if c not in options]
        if unknown:
            errors.add(f"{field}.option_codes", f"invalid options: {', '.join(unknown)}")
            return None
        return {}, None, [options[c] for c in answer]

    if vtype == SymptomValueType.TEXT:
        if not isinstance(answer, str) or len(answer) > MAX_TEXT:
            errors.add(f"{field}.value_text", f"must be a string of at most {MAX_TEXT} characters")
            return None
        return {"value_text": answer.strip()}, None, []

    errors.add(field, f"unsupported value type {vtype}")
    return None


# ------------------------------------------------------------------ create


def create_symptom_record(patient, user, body, correction_of=None):
    """Validate and persist a symptom report, then evaluate alert rules. Caller commits.

    ``correction_of``: the record this one corrects (Sprint 5) — keeps its form, cycle (no
    recalculation), reporter, source and review state. Returns (record, triggered_alerts).
    """
    errors = _Errors()
    now = utcnow()

    form_code = body.get("form_code")
    if not isinstance(form_code, str) or not form_code:
        errors.add("form_code", "is required")
        errors.raise_if_any()
    form = correction_of.form if correction_of else db.session.execute(
        select(SymptomForm).filter_by(code=form_code, is_active=True)).scalar_one_or_none()
    if form is None:
        errors.add("form_code", "unknown or inactive form")
        errors.raise_if_any()

    recorded_at = _parse_recorded_at(body.get("recorded_at"), errors, now, correction=correction_of is not None)
    notes = body.get("notes")
    if notes is not None and (not isinstance(notes, str) or len(notes) > MAX_NOTES):
        errors.add("notes", f"must be a string of at most {MAX_NOTES} characters")

    items = {i.definition.code: i for i in form.items if i.definition.is_active}
    values = body.get("values")
    if not isinstance(values, list) or not values:
        errors.add("values", "must be a non-empty list")
        errors.raise_if_any()

    answers, seen = [], set()
    for idx, item in enumerate(values):
        field = f"values[{idx}]"
        if not isinstance(item, dict) or not isinstance(item.get("definition_code"), str):
            errors.add(field, "must be an object with 'definition_code'")
            continue
        code = item["definition_code"]
        if code in seen:
            errors.add(f"{field}.definition_code", f"'{code}' appears more than once")
            continue
        seen.add(code)
        form_item = items.get(code)
        if form_item is None:
            errors.add(f"{field}.definition_code", f"'{code}' is not part of form '{form.code}'")
            continue
        result = _validate_answer(form_item.definition, item, field, errors)
        if result is not None:
            answers.append((form_item.definition, *result))

    for code, form_item in items.items():
        # a correction may concern a report made before a question became required
        if form_item.is_required and code not in seen and correction_of is None:
            errors.add("values", f"'{code}' is required")
    errors.raise_if_any()

    # Cycle context (database-design.md §4: cycle_day is computed by the server).
    zone = patient_zone(patient.timezone)
    local_day = to_local(recorded_at, zone).date()
    cycle = correction_of.cycle if correction_of else active_plan_and_cycle(patient)[1]
    cycle_day = cycle.cycle_day_on(local_day) if cycle else None
    if cycle_day is None:
        cycle = None

    record = SymptomRecord(
        patient_id=patient.id,
        form_id=form.id,
        form_version=form.version,
        recorded_at=recorded_at,
        reported_by=correction_of.reported_by if correction_of else user.id,
        review_status=correction_of.review_status if correction_of else ReviewStatus.SUBMITTED,
        reviewed_by=correction_of.reviewed_by if correction_of else None,
        reviewed_at=correction_of.reviewed_at if correction_of else None,
        nursing_assessment_id=correction_of.nursing_assessment_id if correction_of else None,
        amends_id=correction_of.id if correction_of else None,
        notes=(notes or "").strip() or None,
        cycle=cycle,
        cycle_day=cycle_day,
        source=correction_of.source if correction_of else (
            ObservationSource.PATIENT_APP if user.role_name == RoleName.PATIENT else ObservationSource.NURSE),
        record_status=RecordStatus.FINAL,
    )
    for definition, columns, score, selected in answers:
        value = SymptomRecordValue(definition=definition, score=score, **columns)
        value.selected_options = selected
        record.values.append(value)
    db.session.add(record)
    db.session.flush()

    triggered = evaluate_symptom_record(record, patient, **_alert_context(record, patient))
    return record, triggered


def _alert_context(record, patient):
    """Inputs for rule conditions, derived from the record itself (same result on replay)."""
    local_day = to_local(record.recorded_at, patient_zone(patient.timezone)).date()
    return {
        "in_nadir": cycle_nadir(record.cycle, local_day)[0] if record.cycle else False,
        "cancer_type_ids": {d.cancer_type_id for d in patient.diagnoses if d.deleted_at is None},
    }


# ------------------------------------------------------------------ serialize


def serialize_value(v):
    data = {"definition_code": v.definition.code, "label": v.definition.name_zh}
    vtype = v.definition.value_type
    if vtype in (SymptomValueType.SCALE, SymptomValueType.NUMERIC):
        data["value_numeric"] = _num(v.value_numeric)
    elif vtype == SymptomValueType.BOOLEAN:
        data["value_boolean"] = v.value_boolean
    elif vtype == SymptomValueType.SINGLE_CHOICE:
        data["option_code"] = v.option.value_code if v.option else None
    elif vtype == SymptomValueType.MULTI_CHOICE:
        data["option_codes"] = [o.value_code for o in v.selected_options]
    elif vtype == SymptomValueType.TEXT:
        data["value_text"] = v.value_text
    data["score"] = _num(v.score)
    return data


def serialize_triggered(triggered):
    return [
        {
            "alert_rule_code": t.rule.code,
            "severity": t.rule.severity,
            "symptom": t.label,
            "message": t.patient_message,
            "notified": t.notified,
        }
        for t in triggered
    ]


def replay_triggered(record):
    """triggered_alerts for an idempotent replay: same rules, nothing sent again."""
    return serialize_triggered(replay_symptom_record(record, **_alert_context(record, record.patient)))


def serialize_record(record, triggered_alerts):
    return {
        "id": record.id,
        "patient_id": record.patient.public_id,
        "form": {"code": record.form.code, "version": record.form_version} if record.form else None,
        "recorded_at": iso_utc(record.recorded_at),
        "cycle_id": record.cycle_id,
        "cycle_day": record.cycle_day,
        "source": record.source,
        "review_status": record.review_status,
        "record_status": record.record_status,
        "amends_id": record.amends_id,  # Sprint 5: set on a corrected version
        "notes": record.notes,
        "values": [serialize_value(v) for v in record.values],
        "triggered_alerts": triggered_alerts,
    }


# ------------------------------------------------------------------ list & review (nurse workflow)

REVIEW_ASSESSMENT_TYPES = ("phone_follow_up", "follow_up")
MAX_ACTION_NOTE = 2000


def list_patient_records(patient, *, review_status, page, per_page):
    filters = [SymptomRecord.patient_id == patient.id, SymptomRecord.record_status == RecordStatus.FINAL]
    if review_status != "all":
        filters.append(SymptomRecord.review_status == review_status)
    total = db.session.execute(select(func.count()).select_from(SymptomRecord).where(*filters)).scalar()
    order = (SymptomRecord.reviewed_at.desc(),) if review_status == ReviewStatus.REVIEWED else ()
    records = db.session.execute(
        select(SymptomRecord).where(*filters)
        .order_by(*order, SymptomRecord.recorded_at.desc(), SymptomRecord.id.desc())
        .offset((page - 1) * per_page).limit(per_page)
    ).scalars().all()
    counts = dict(db.session.execute(
        select(SymptomRecord.review_status, func.count())
        .where(SymptomRecord.patient_id == patient.id, SymptomRecord.record_status == RecordStatus.FINAL)
        .group_by(SymptomRecord.review_status)
    ).all())
    meta = {
        "page": page,
        "per_page": per_page,
        "total": total,
        "submitted": counts.get(ReviewStatus.SUBMITTED, 0),
        "reviewed": counts.get(ReviewStatus.REVIEWED, 0),
    }
    return records, meta


def _record_alerts(record):
    """Alerts raised by this record, one entry per event (not per recipient)."""
    rows = db.session.execute(
        select(Notification).filter_by(source_table="symptom_records", source_id=record.id).order_by(Notification.id)
    ).scalars().all()
    events = {}
    for n in rows:
        entry = events.setdefault(n.event_key or n.id, {
            "event_key": n.event_key,
            "alert_rule_code": n.alert_rule.code if n.alert_rule else None,
            "severity": n.severity,
            "title": n.title,
            "resolved": True,
            "resolved_at": None,
            "resolved_by": None,
        })
        entry["status"] = n.status
        if n.status != NotificationStatus.RESOLVED:
            entry["resolved"] = False
        elif entry["resolved_at"] is None:
            entry["resolved_at"] = iso_utc(n.resolved_at)
            entry["resolved_by"] = n.resolver.display_name if n.resolver else None
    alerts = list(events.values())
    for a in alerts:
        if not a["resolved"]:
            a["resolved_at"] = a["resolved_by"] = None
    return sorted(alerts, key=lambda a: a["severity"] != "critical")


def record_list_payload(record, *, viewer_role):
    """A record plus its review state. Nurse notes are internal: patients see only that it was
    reviewed and whether its alerts were handled — no staff names (same rule as notifications)."""
    patient_view = viewer_role == RoleName.PATIENT
    data = serialize_record(record, triggered_alerts=None)
    data.pop("triggered_alerts")
    reporter_is_staff = record.reporter is not None and record.reporter.id != record.patient.user_id
    data["reported_by"] = (
        None if patient_view and reporter_is_staff
        else {"id": record.reporter.public_id, "display_name": record.reporter.display_name} if record.reporter else None
    )
    data["alerts"] = _record_alerts(record)
    if patient_view:
        for a in data["alerts"]:
            a["resolved_by"] = None
    data["reviewed_at"] = iso_utc(record.reviewed_at)
    data["reviewed_by"] = (
        {"id": record.reviewer.public_id, "display_name": record.reviewer.display_name}
        if record.reviewer and not patient_view else None
    )
    assessment = record.nursing_assessment
    data["review"] = (
        {
            "nursing_assessment_id": assessment.id,
            "assessment_type": assessment.assessment_type,
            "action_note": assessment.plan,
            "risk_level": assessment.risk_level,
        }
        if assessment and viewer_role != RoleName.PATIENT else None
    )
    return data


def _summary(record, zone):
    parts = []
    for v in record.values:
        if v.definition.value_type == SymptomValueType.BOOLEAN:
            if v.value_boolean:
                parts.append(f"有{v.definition.name_zh}")
        elif v.score is not None:
            parts.append(f"{v.definition.name_zh} {float(v.score):g}")
    when = to_local(record.recorded_at, zone).strftime("%m/%d %H:%M")
    return f"病人回報（{when}）：" + ("、".join(parts) or "無")


def review_record(record, nurse, body):
    """Mark a symptom record reviewed; the action note is kept in a signed nursing assessment
    linked through ``symptom_records.nursing_assessment_id``. Caller commits.

    Returns (assessment, graded_codes, resolved_notifications).
    """
    errors = _Errors()
    note = body.get("action_note")
    if not isinstance(note, str) or not note.strip() or len(note) > MAX_ACTION_NOTE:
        errors.add("action_note", f"is required (1–{MAX_ACTION_NOTE} characters)")
    assessment_type = body.get("assessment_type", "phone_follow_up")
    if assessment_type not in REVIEW_ASSESSMENT_TYPES:
        errors.add("assessment_type", f"must be one of: {', '.join(REVIEW_ASSESSMENT_TYPES)}")
    risk_level = body.get("risk_level")
    if risk_level is not None and risk_level not in RiskLevel.ALL:
        errors.add("risk_level", f"must be one of: {', '.join(RiskLevel.ALL)}")
    resolve_alerts = body.get("resolve_alerts", True)
    if not isinstance(resolve_alerts, bool):
        errors.add("resolve_alerts", "must be true or false")
    grades = body.get("ctcae_grades", [])
    values_by_code = {v.definition.code: v for v in record.values}
    parsed_grades = []
    if not isinstance(grades, list):
        errors.add("ctcae_grades", "must be a list")
    else:
        for idx, g in enumerate(grades):
            code = g.get("definition_code") if isinstance(g, dict) else None
            grade = g.get("ctcae_grade") if isinstance(g, dict) else None
            if code not in values_by_code:
                errors.add(f"ctcae_grades[{idx}].definition_code", "is not answered in this record")
            elif not isinstance(grade, int) or isinstance(grade, bool) or not 0 <= grade <= 5:
                errors.add(f"ctcae_grades[{idx}].ctcae_grade", "must be an integer 0–5")
            else:
                parsed_grades.append((values_by_code[code], grade))
    errors.raise_if_any()

    if record.record_status != RecordStatus.FINAL:
        raise APIError(422, "INVALID_STATE", "只有有效的紀錄可以審閱")
    if record.review_status == ReviewStatus.REVIEWED:
        by = record.reviewer.display_name if record.reviewer else "其他人員"
        raise APIError(422, "INVALID_STATE", f"這筆紀錄已由{by}審閱")

    patient = record.patient
    zone = patient_zone(patient.timezone)
    now = utcnow()
    today = to_local(now, zone).date()
    cycle = record.cycle
    note = note.strip()
    grade_text = "、".join(f"{v.definition.name_zh} G{grade}" for v, grade in parsed_grades)

    assessment = NursingAssessment(
        patient_id=patient.id,
        assessed_by=nurse.id,
        assessed_at=now,
        assessment_type=assessment_type,
        risk_level=risk_level,
        subjective=_summary(record, zone),
        assessment="症狀回報審閱" + (f"（CTCAE：{grade_text}）" if grade_text else ""),
        plan=note,
        sign_status=SignStatus.SIGNED,
        signed_at=now,
        cycle=cycle,
        cycle_day=cycle.cycle_day_on(today) if cycle else None,
        source=ObservationSource.NURSE,
        record_status=RecordStatus.FINAL,
    )
    db.session.add(assessment)
    db.session.flush()

    for value, grade in parsed_grades:
        value.ctcae_grade = grade
    record.review_status = ReviewStatus.REVIEWED
    record.reviewed_by = nurse.id
    record.reviewed_at = now
    record.nursing_assessment_id = assessment.id

    resolved = resolve_source_alerts("symptom_records", record.id, nurse, note) if resolve_alerts else []
    return assessment, [v.definition.code for v, _ in parsed_grades], resolved
