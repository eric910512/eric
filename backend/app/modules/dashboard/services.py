"""Builders for the patient dashboard. Each builder returns the payload of one widget,
shaped like the corresponding widget data endpoint in api-design.md v1.1 §8.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from flask import current_app
from sqlalchemy import func, select

from app.core.timeutil import iso_date, iso_utc, local_day_bounds_utc, patient_zone, to_local
from app.extensions import db
from app.models import (
    Appointment,
    Notification,
    NursePatientAssignment,
    NursingAssessment,
    SymptomDefinition,
    SymptomForm,
    SymptomRecord,
    SymptomRecordValue,
    VitalSign,
)
from app.models.base import utcnow
from app.services.treatment import active_plan_and_cycle as _active_plan_and_cycle
from app.services.treatment import cycle_nadir as _cycle_nadir
from app.modules.labs.services import latest_by_test, patient_summary as lab_patient_summary
from app.modules.notification.services import PATIENT_STATUS_TEXT
from app.services.labs import format_value as format_lab_value
from app.services.vitals import flag as _flag
from app.services.vitals import flags_for as _vital_flags
from app.models.enums import (
    AlertSeverity,
    AppointmentStatus,
    CycleStatus,
    NotificationStatus,
    NotificationType,
    RecordStatus,
    ReviewStatus,
    RiskLevel,
    RoleName,
    SignStatus,
    SymptomValueType,
)

NOTIFICATION_LIMIT = 5
REVIEW_LIST_LIMIT = 5
VITALS_LOOKBACK = 100  # most recent final rows scanned for "latest per field"
RISK_VITALS_WINDOW = timedelta(hours=72)
RISK_SYMPTOM_WINDOW = timedelta(hours=48)
RISK_SYMPTOM_THRESHOLD = 7
NO_REPORT_WARNING = timedelta(hours=48)
LAB_RISK_WINDOW = timedelta(days=7)
# abnormal_flag → risk level per analyte (flags come from lab_test_types ranges)
LAB_RISK_RULES = {
    "ANC": {"LL": RiskLevel.HIGH, "L": RiskLevel.MEDIUM},
    "PLT": {"LL": RiskLevel.HIGH, "L": RiskLevel.MEDIUM},
    "HGB": {"LL": RiskLevel.HIGH},
    "WBC": {"LL": RiskLevel.MEDIUM},
}
LAB_RISK_LABELS = {"ANC": "ANC", "PLT": "血小板", "HGB": "血色素", "WBC": "白血球"}
LAB_FLAG_TEXT = {"LL": "嚴重偏低", "L": "偏低", "H": "偏高", "HH": "嚴重偏高"}
TRENDABLE_TYPES = (
    SymptomValueType.SCALE,
    SymptomValueType.NUMERIC,
    SymptomValueType.SINGLE_CHOICE,
)
FLAG_RANK = {None: 0, "warning": 1, "critical": 2}
RISK_RANK = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}


def _num(value):
    """Decimal -> float for JSON; ints stay ints."""
    return float(value) if isinstance(value, Decimal) else value


def _visible_now(now):
    return (Notification.scheduled_for.is_(None)) | (Notification.scheduled_for <= now)


@dataclass
class DashboardContext:
    patient: object
    zone: object
    now: datetime
    today: date
    plan: object
    cycle: object


def build_patient_dashboard(patient, trend_days=14, viewer_role=None):
    """All dashboard widgets for one patient.

    ``nurse-view`` (internal: pending reviews, assessments, care-team risk detail) is only
    included for staff; patients get ``risk-summary``, which presents the same risk-engine
    result in patient language.
    """
    zone = patient_zone(patient.timezone)
    now = utcnow()
    plan, cycle = _active_plan_and_cycle(patient)
    ctx = DashboardContext(patient, zone, now, to_local(now, zone).date(), plan, cycle)

    latest_vitals = latest_vitals_widget(ctx)
    nurse_view = nurse_view_widget(ctx, latest_vitals)
    widgets = {
        "patient-summary": patient_summary_widget(ctx),
        "risk-summary": risk_summary_widget(ctx, nurse_view["risk"]),
        "today-schedule": today_schedule_widget(ctx),
        "treatment-progress": treatment_progress_widget(ctx),
        "latest-vitals": latest_vitals,
        "lab-summary": lab_patient_summary(patient),
        "symptom-trend": symptom_trend_widget(ctx, trend_days),
        "symptom-quick-report": symptom_quick_report_widget(ctx),
        "notifications": notifications_widget(ctx),
    }
    if viewer_role != RoleName.PATIENT:
        widgets["nurse-view"] = nurse_view
    return {"patient_id": patient.public_id, "generated_at": iso_utc(now), "widgets": widgets}


# ---------------------------------------------------------------- shared lookups


# ---------------------------------------------------------------- widgets


def patient_summary_widget(ctx):
    p = ctx.patient
    dob = p.date_of_birth
    age = ctx.today.year - dob.year - ((ctx.today.month, ctx.today.day) < (dob.month, dob.day))
    return {
        "patient_id": p.public_id,
        "patient_code": p.patient_code,
        "display_name": p.display_name,
        "age": age,
        "gender": p.gender,
        "care_alerts": [
            {
                "alert_type": a.alert_type,
                "body_site": a.body_site,
                "description": a.description,
                "severity": a.severity,
            }
            for a in p.active_care_alerts
        ],
    }


def today_schedule_widget(ctx):
    start, end = local_day_bounds_utc(ctx.today, ctx.zone)
    appointments = db.session.execute(
        select(Appointment)
        .where(
            Appointment.patient_id == ctx.patient.id,
            Appointment.deleted_at.is_(None),
            Appointment.scheduled_at >= start,
            Appointment.scheduled_at < end,
            Appointment.status.notin_([AppointmentStatus.CANCELLED, AppointmentStatus.RESCHEDULED]),
        )
        .order_by(Appointment.scheduled_at)
    ).scalars().all()

    instructions = sorted(
        (i for a in appointments for i in a.instructions if i.is_highlighted),
        key=lambda i: (i.due_at is None, i.due_at or datetime.max),
    )
    contacts = current_app.config["INSTITUTION"].get("contacts", [])
    return {
        "date": iso_date(ctx.today),
        "server_time": iso_utc(ctx.now),
        "appointments": [
            {
                "id": a.id,
                "appointment_type": a.appointment_type,
                "title": a.title,
                "scheduled_at": iso_utc(a.scheduled_at),
                "location": a.location,
                "status": a.status,
            }
            for a in appointments
        ],
        "highlight_instructions": [
            {
                "appointment_id": i.appointment_id,
                "instruction_type": i.instruction_type,
                "due_at": iso_utc(i.due_at),
                "text": i.text,
            }
            for i in instructions
        ],
        "quick_contact": next((c for c in contacts if c.get("key") == "leave"), None),
    }


def treatment_progress_widget(ctx):
    plan, cycle = ctx.plan, ctx.cycle
    if plan is None:
        return None
    cycles = [c for c in plan.cycles if c.deleted_at is None]
    completed = sum(c.status == CycleStatus.COMPLETED for c in cycles)

    current = None
    if cycle is not None:
        in_nadir, _, nadir_end = _cycle_nadir(cycle, ctx.today)
        current = {
            "cycle_number": cycle.cycle_number,
            "cycle_day": cycle.cycle_day_on(ctx.today),
            "in_nadir": in_nadir,
            "nadir_end_date": iso_date(nadir_end),
        }

    # Next cycle: an already-scheduled later cycle, otherwise estimated from the regimen length.
    reference_number = cycle.cycle_number if cycle else completed
    upcoming = [
        c for c in cycles
        if c.cycle_number > reference_number and c.status in (CycleStatus.SCHEDULED, CycleStatus.DELAYED)
    ]
    next_date, estimated = None, False
    if upcoming:
        next_date = min(upcoming, key=lambda c: c.cycle_number).scheduled_date
    elif (
        cycle is not None
        and cycle.actual_start_date
        and plan.regimen
        and plan.regimen.cycle_length_days
        and (plan.total_cycles or 0) > cycle.cycle_number
    ):
        next_date = cycle.actual_start_date + timedelta(days=plan.regimen.cycle_length_days)
        estimated = True

    return {
        "plan_id": plan.id,
        "diagnosis_name": plan.diagnosis.cancer_type.name_zh if plan.diagnosis else None,
        "regimen_name": plan.regimen.name if plan.regimen else plan.plan_name,
        "attending_physician_name": plan.attending_physician_name,
        "completed_cycles": completed,
        "total_cycles": plan.total_cycles,
        "current_cycle": current,
        "next_cycle_date": iso_date(next_date),
        "next_cycle_date_estimated": estimated,
        "disclaimer_key": "treatment_progress_disclaimer",
    }


def latest_vitals_widget(ctx):
    rows = db.session.execute(
        select(VitalSign)
        .filter_by(patient_id=ctx.patient.id, record_status=RecordStatus.FINAL)
        .order_by(VitalSign.measured_at.desc())
        .limit(VITALS_LOOKBACK)
    ).scalars().all()

    def latest(attr):
        return next((r for r in rows if getattr(r, attr) is not None), None)

    def simple(field):
        row = latest(field)
        if row is None:
            return None
        value = _num(getattr(row, field))
        return {"value": value, "measured_at": iso_utc(row.measured_at), "flag": _flag(field, value)}

    bp_row = latest("systolic_bp_mmhg")
    blood_pressure = None
    if bp_row is not None:
        blood_pressure = {
            "systolic": bp_row.systolic_bp_mmhg,
            "diastolic": bp_row.diastolic_bp_mmhg,
            "measure_site": bp_row.bp_measure_site,
            "measured_at": iso_utc(bp_row.measured_at),
            "flag": _flag("systolic_bp_mmhg", bp_row.systolic_bp_mmhg),
        }

    weight_row = latest("weight_kg")
    weight = None
    if weight_row is not None:
        baseline = next(
            (r for r in rows
             if r.weight_kg is not None and r.measured_at <= weight_row.measured_at - timedelta(days=7)),
            None,
        )
        change = None
        if baseline is not None and baseline.weight_kg:
            change = round((float(weight_row.weight_kg) - float(baseline.weight_kg)) / float(baseline.weight_kg) * 100, 1)
        weight = {
            "value": _num(weight_row.weight_kg),
            "measured_at": iso_utc(weight_row.measured_at),
            "change_pct_7d": change,
            "flag": _flag("weight_change_pct_7d", change),
        }

    return {
        "temperature_c": simple("temperature_c"),
        "heart_rate_bpm": simple("heart_rate_bpm"),
        "blood_pressure": blood_pressure,
        "respiratory_rate": simple("respiratory_rate"),
        "spo2_pct": simple("spo2_pct"),
        "weight_kg": weight,
    }


def _default_form():
    code = current_app.config.get("DEFAULT_PATIENT_SYMPTOM_FORM")
    return db.session.execute(select(SymptomForm).filter_by(code=code, is_active=True)).scalar_one_or_none()


def _patient_cycles(patient):
    return sorted(
        (c for c in patient.chemotherapy_cycles if c.deleted_at is None and c.actual_start_date),
        key=lambda c: c.actual_start_date,
    )


def symptom_trend_widget(ctx, days):
    first_day = ctx.today - timedelta(days=days - 1)
    start, _ = local_day_bounds_utc(first_day, ctx.zone)
    _, end = local_day_bounds_utc(ctx.today, ctx.zone)

    rows = db.session.execute(
        select(SymptomRecordValue, SymptomRecord)
        .join(SymptomRecord, SymptomRecordValue.symptom_record_id == SymptomRecord.id)
        .where(
            SymptomRecord.patient_id == ctx.patient.id,
            SymptomRecord.record_status == RecordStatus.FINAL,
            SymptomRecord.recorded_at >= start,
            SymptomRecord.recorded_at < end,
            SymptomRecordValue.score.is_not(None),
        )
    ).all()

    # max score per (definition, local date)
    daily = {}
    for value, record in rows:
        key = (value.definition_id, to_local(record.recorded_at, ctx.zone).date())
        score = float(value.score)
        if key not in daily or score > daily[key]:
            daily[key] = score

    # Series: items of the default form first, then any other definition that has data.
    form = _default_form()
    ordered_ids = [i.definition_id for i in form.items] if form else []
    ordered_ids += sorted({def_id for def_id, _ in daily} - set(ordered_ids))
    definitions = {
        d.id: d for d in db.session.execute(
            select(SymptomDefinition).where(SymptomDefinition.id.in_(ordered_ids))
        ).scalars()
    }

    cycles = _patient_cycles(ctx.patient)

    def cycle_context(day):
        started = [c for c in cycles if c.actual_start_date <= day]
        if not started:
            return None, None
        c = started[-1]
        return c.cycle_number, c.cycle_day_on(day)

    dates = [first_day + timedelta(days=n) for n in range(days)]
    context = {d: cycle_context(d) for d in dates}
    series = []
    for def_id in ordered_ids:
        definition = definitions.get(def_id)
        if definition is None or definition.value_type not in TRENDABLE_TYPES:
            continue
        series.append({
            "key": definition.code,
            "label": definition.name_zh,
            "higher_is_worse": definition.higher_is_worse,
            "points": [
                {
                    "date": iso_date(d),
                    "cycle_number": context[d][0],
                    "cycle_day": context[d][1],
                    "value": daily.get((def_id, d)),
                }
                for d in dates
            ],
        })

    markers = []
    for c in cycles:
        if c.actual_start_date > ctx.today:
            continue
        _, nadir_start, nadir_end = _cycle_nadir(c, c.actual_start_date)
        if (nadir_end or c.actual_start_date) < first_day:
            continue
        markers.append({
            "cycle_number": c.cycle_number,
            "start_date": iso_date(c.actual_start_date),
            "nadir_start_date": iso_date(nadir_start),
            "nadir_end_date": iso_date(nadir_end),
        })

    return {
        "bucket": "day",
        "agg": "max",
        "from": iso_date(first_day),
        "to": iso_date(ctx.today),
        "series": series,
        "cycle_markers": markers,
    }


def symptom_quick_report_widget(ctx):
    form = _default_form()
    if form is None:
        return {"form": None, "reported_today": False, "today_record_id": None, "last_report": None}

    last = db.session.execute(
        select(SymptomRecord)
        .filter_by(patient_id=ctx.patient.id, form_id=form.id, record_status=RecordStatus.FINAL)
        .order_by(SymptomRecord.recorded_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    reported_today = last is not None and to_local(last.recorded_at, ctx.zone).date() == ctx.today
    return {
        "form": {"code": form.code, "name": form.name, "version": form.version, "item_count": len(form.items)},
        "reported_today": reported_today,
        "today_record_id": last.id if reported_today else None,
        "last_report": (
            {"id": last.id, "recorded_at": iso_utc(last.recorded_at), "cycle_day": last.cycle_day}
            if last else None
        ),
    }


def notifications_widget(ctx):
    """The patient's own notifications (my-notifications)."""
    user_id = ctx.patient.user_id
    if user_id is None:
        return {"items": [], "unread_count": 0}
    base = [Notification.recipient_id == user_id, _visible_now(ctx.now)]
    items = db.session.execute(
        select(Notification).where(*base).order_by(Notification.created_at.desc()).limit(NOTIFICATION_LIMIT)
    ).scalars().all()
    unread = db.session.execute(
        select(func.count()).select_from(Notification).where(*base, Notification.is_read.is_not(True))
    ).scalar()
    return {
        "items": [
            {
                "id": n.id,
                "type": n.type,
                "severity": n.severity,
                "title": n.title,
                "message": n.message,
                "status": n.status if n.type == NotificationType.RISK_ALERT else None,
                "status_text": PATIENT_STATUS_TEXT.get(n.status) if n.type == NotificationType.RISK_ALERT else None,
                "is_read": bool(n.is_read),
                "created_at": iso_utc(n.created_at),
            }
            for n in items
        ],
        "unread_count": unread,
    }


# ---------------------------------------------------------------- patient risk summary

# Patient-facing wording for the risk engine's level. The level itself is never recomputed here.
_PATIENT_STATUS = {
    "urgent": ("請立即聯絡醫療團隊", "今天有需要馬上處理的狀況。請撥打照護專線；如果無法接通，請直接前往急診。"),
    "handled": ("護理團隊已處理，請持續觀察", "請依照護理師的指示照顧自己；如果情況變差，請立即聯絡醫療團隊。"),
    "attention": ("今天有些狀況需要留意", "請多休息並持續記錄，護理團隊會追蹤您的狀況。"),
    "stable": ("今天狀況穩定", "請繼續每天記錄症狀和生命徵象，有不舒服隨時回報。"),
}
_INTERNAL_REASON_PREFIXES = ("護理評估風險",)


def _patient_reason(reason):
    if reason.startswith("未處理警示："):
        return f"{reason.removeprefix('未處理警示：')}（護理團隊處理中）"
    return reason


def risk_summary_widget(ctx, risk):
    """Today's health risk summary for the patient home.

    ``risk`` is the risk engine result (``_assess_risk`` via nurse-view): its level and
    reasons are passed through unchanged. Today's context comes from existing rows:
    notifications (alerts raised today and whether the care team handled them),
    symptom_records and vital_signs (what was recorded today).
    """
    p = ctx.patient
    start, end = local_day_bounds_utc(ctx.today, ctx.zone)

    symptoms_today = db.session.execute(
        select(SymptomRecord)
        .where(
            SymptomRecord.patient_id == p.id,
            SymptomRecord.record_status == RecordStatus.FINAL,
            SymptomRecord.recorded_at >= start,
            SymptomRecord.recorded_at < end,
        )
        .order_by(SymptomRecord.recorded_at.desc())
    ).scalars().all()
    vitals_today = db.session.execute(
        select(VitalSign)
        .where(
            VitalSign.patient_id == p.id,
            VitalSign.record_status == RecordStatus.FINAL,
            VitalSign.measured_at >= start,
            VitalSign.measured_at < end,
        )
        .order_by(VitalSign.measured_at.desc())
    ).scalars().all()
    alerts_today = []
    if p.user_id:
        alerts_today = db.session.execute(
            select(Notification)
            .where(
                Notification.recipient_id == p.user_id,
                Notification.type == NotificationType.RISK_ALERT,
                Notification.created_at >= start,
                Notification.created_at < end,
                _visible_now(ctx.now),
            )
            .order_by(Notification.created_at.desc())
        ).scalars().all()

    # highest symptom scores reported today (worst answer per symptom)
    worst = {}
    for record in symptoms_today:
        for v in record.values:
            if v.score is None or not v.definition.higher_is_worse or v.definition.value_type == SymptomValueType.BOOLEAN:
                continue
            code = v.definition.code
            if code not in worst or v.score > worst[code][1]:
                worst[code] = (v.definition.name_zh, v.score)
    symptoms = sorted(({"label": label, "score": float(score)} for label, score in worst.values()), key=lambda x: -x["score"])

    in_nadir = _cycle_nadir(ctx.cycle, ctx.today)[0]
    latest_vital = vitals_today[0] if vitals_today else None
    vital_flags = _vital_flags(latest_vital, in_nadir=in_nadir) if latest_vital else []

    open_alerts = [n for n in alerts_today if n.status in NotificationStatus.OPEN]
    open_critical = [n for n in open_alerts if n.severity == AlertSeverity.CRITICAL]
    level = risk["level"]
    # Presentation only: the level comes from the risk engine and is not changed here.
    if level == RiskLevel.HIGH:
        if open_critical or not alerts_today:
            status = "urgent"  # an unhandled critical alert, or high risk with no alert handled today
        elif open_alerts:
            status = "attention"  # critical alerts handled, warnings still with the care team
        else:
            status = "handled"
    elif level == RiskLevel.MEDIUM:
        status = "attention"
    else:
        status = "stable"
    title, message = _PATIENT_STATUS[status]

    actions = []
    if status == "urgent":
        actions.append({"code": "call_hotline", "label": "撥打照護專線"})
    if not symptoms_today:
        actions.append({"code": "report_symptoms", "label": "回報今天的症狀"})
    if not vitals_today:
        actions.append({"code": "measure_vitals", "label": "量測並記錄生命徵象"})

    return {
        "date": iso_date(ctx.today),
        "level": level,
        "status": status,
        "title": title,
        "message": message,
        "reasons": [_patient_reason(r) for r in risk["reasons"] if not r.startswith(_INTERNAL_REASON_PREFIXES)],
        "in_nadir": in_nadir,
        "today": {
            "symptom_reported": bool(symptoms_today),
            "last_symptom_report_at": iso_utc(symptoms_today[0].recorded_at) if symptoms_today else None,
            "symptoms": symptoms[:3],
            "vitals_recorded": bool(vitals_today),
            "last_vitals_at": iso_utc(latest_vital.measured_at) if latest_vital else None,
            "vital_flags": vital_flags,
            "alerts": {
                "total": len(alerts_today),
                "open": len(open_alerts),
                "critical": sum(n.severity == AlertSeverity.CRITICAL for n in alerts_today),
                "items": [
                    {
                        "id": n.id,
                        "title": n.title,
                        "severity": n.severity,
                        "created_at": iso_utc(n.created_at),
                        "status": n.status,
                        "status_text": PATIENT_STATUS_TEXT.get(n.status),
                        "resolved": n.status == NotificationStatus.RESOLVED,
                        "resolved_at": iso_utc(n.resolved_at),
                    }
                    for n in alerts_today[:5]
                ],
            },
        },
        "actions": actions,
    }


# ---------------------------------------------------------------- nurse view


def _pending_review_item(record):
    values = sorted(
        (v for v in record.values if v.score is not None), key=lambda v: float(v.score), reverse=True
    )
    return {
        "id": record.id,
        "recorded_at": iso_utc(record.recorded_at),
        "cycle_day": record.cycle_day,
        "summary": "、".join(
            f"{v.definition.name_zh}：{v.option.label_zh}" if v.definition.value_type == SymptomValueType.SINGLE_CHOICE and v.option
            else f"{v.definition.name_zh} {float(v.score):g}" for v in values),
        "max_score": float(values[0].score) if values else None,
    }


def nurse_view_widget(ctx, latest_vitals):
    p = ctx.patient

    assignments = db.session.execute(
        select(NursePatientAssignment)
        .filter_by(patient_id=p.id, ended_at=None)
        .order_by(NursePatientAssignment.is_primary.desc(), NursePatientAssignment.assigned_at)
    ).scalars().all()

    pending_q = select(SymptomRecord).filter_by(
        patient_id=p.id, review_status=ReviewStatus.SUBMITTED, record_status=RecordStatus.FINAL
    )
    pending_count = db.session.execute(select(func.count()).select_from(pending_q.subquery())).scalar()
    pending = db.session.execute(
        pending_q.order_by(SymptomRecord.recorded_at.desc()).limit(REVIEW_LIST_LIMIT)
    ).scalars().all()

    alerts = db.session.execute(
        select(Notification)
        .where(
            Notification.patient_id == p.id,
            Notification.type == NotificationType.RISK_ALERT,
            Notification.status.in_(NotificationStatus.OPEN),
            _visible_now(ctx.now),
        )
        .order_by(Notification.created_at.desc())
    ).scalars().all()
    unique_alerts, seen = [], set()
    for n in alerts:  # one row per recipient; show each event once
        key = n.event_key or f"notification:{n.id}"
        if key not in seen:
            seen.add(key)
            unique_alerts.append(n)

    drafts = db.session.execute(
        select(NursingAssessment)
        .filter_by(patient_id=p.id, sign_status=SignStatus.DRAFT, record_status=RecordStatus.FINAL)
        .order_by(NursingAssessment.assessed_at.desc())
    ).scalars().all()
    latest_assessment = db.session.execute(
        select(NursingAssessment)
        .filter_by(patient_id=p.id, record_status=RecordStatus.FINAL)
        .order_by(NursingAssessment.assessed_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    last_symptom = db.session.execute(
        select(func.max(SymptomRecord.recorded_at)).filter_by(patient_id=p.id, record_status=RecordStatus.FINAL)
    ).scalar()
    last_vital = db.session.execute(
        select(func.max(VitalSign.measured_at)).filter_by(patient_id=p.id, record_status=RecordStatus.FINAL)
    ).scalar()
    last_report_at = max((t for t in (last_symptom, last_vital) if t), default=None)
    hours_since = int((ctx.now - last_report_at).total_seconds() // 3600) if last_report_at else None

    return {
        "care_team": [
            {
                "nurse_id": a.nurse.public_id,
                "display_name": a.nurse.display_name,
                "is_primary": bool(a.is_primary),
                "assigned_at": iso_utc(a.assigned_at),
            }
            for a in assignments
        ],
        "risk": _assess_risk(ctx, latest_vitals, unique_alerts, latest_assessment, last_report_at),
        "last_report_at": iso_utc(last_report_at),
        "hours_since_last_report": hours_since,
        "pending_symptom_reviews": {
            "count": pending_count,
            "items": [_pending_review_item(r) for r in pending],
        },
        "unacknowledged_alerts": {
            "count": len(unique_alerts),
            "items": [
                {
                    "id": n.id,
                    "event_key": n.event_key,
                    "severity": n.severity,
                    "title": n.title,
                    "message": n.message,
                    "status": n.status,
                    "created_at": iso_utc(n.created_at),
                }
                for n in unique_alerts
            ],
        },
        "pending_assessment_signoff": {
            "count": len(drafts),
            "items": [
                {
                    "id": a.id,
                    "assessment_type": a.assessment_type,
                    "assessed_at": iso_utc(a.assessed_at),
                    "assessed_by": {"id": a.assessor.public_id, "display_name": a.assessor.display_name},
                }
                for a in drafts
            ],
        },
        "latest_assessment": (
            {
                "id": latest_assessment.id,
                "assessment_type": latest_assessment.assessment_type,
                "assessed_at": iso_utc(latest_assessment.assessed_at),
                "risk_level": latest_assessment.risk_level,
                "overall_condition": latest_assessment.overall_condition,
                "sign_status": latest_assessment.sign_status,
            }
            if latest_assessment else None
        ),
    }


_VITAL_LABELS = {
    "temperature_c": ("體溫", "°C"),
    "heart_rate_bpm": ("心跳", " bpm"),
    "respiratory_rate": ("呼吸", "/min"),
    "spo2_pct": ("血氧", "%"),
}


def _assess_risk(ctx, latest_vitals, alerts, latest_assessment, last_report_at):
    """Phase 1 rule-based risk (no AI). Highest matching level wins.

    high:   recent critical vital flag, unacknowledged critical alert, assessment risk=high,
            or a critical lab within 7 days (ANC / platelets / hemoglobin critically low)
    medium: recent warning flag, symptom score >= 7 in the last 48h, unacknowledged warning
            alert, assessment risk=medium, no report for 48h during an active cycle,
            ANC or platelets below range, or WBC critically low (labs within 7 days)
    """
    level, reasons = RiskLevel.LOW, []

    def raise_to(new_level, reason):
        nonlocal level
        reasons.append(reason)
        if RISK_RANK[new_level] > RISK_RANK[level]:
            level = new_level

    recent_cutoff = ctx.now - RISK_VITALS_WINDOW
    in_nadir = _cycle_nadir(ctx.cycle, ctx.today)[0]
    for field, (label, unit) in _VITAL_LABELS.items():
        item = latest_vitals.get(field)
        if item and item["flag"] and item["measured_at"] >= iso_utc(recent_cutoff):
            text = f"{label} {item['value']:g}{unit}"
            if field == "temperature_c" and item["flag"] == "critical" and in_nadir:
                text = f"骨髓抑制期發燒 {item['value']:g}°C"
            raise_to(RiskLevel.HIGH if item["flag"] == "critical" else RiskLevel.MEDIUM, text)
    bp = latest_vitals.get("blood_pressure")
    if bp and bp["flag"] and bp["measured_at"] >= iso_utc(recent_cutoff):
        raise_to(RiskLevel.MEDIUM, f"血壓 {bp['systolic']}/{bp['diastolic']}")
    weight = latest_vitals.get("weight_kg")
    if weight and weight["flag"]:
        raise_to(RiskLevel.MEDIUM, f"7 天體重變化 {weight['change_pct_7d']:g}%")

    high_scores = db.session.execute(
        select(SymptomDefinition.name_zh, func.max(SymptomRecordValue.score))
        .join(SymptomRecordValue, SymptomRecordValue.definition_id == SymptomDefinition.id)
        .join(SymptomRecord, SymptomRecordValue.symptom_record_id == SymptomRecord.id)
        .where(
            SymptomRecord.patient_id == ctx.patient.id,
            SymptomRecord.record_status == RecordStatus.FINAL,
            SymptomRecord.recorded_at >= ctx.now - RISK_SYMPTOM_WINDOW,
            SymptomDefinition.higher_is_worse.is_(True),
            SymptomRecordValue.score >= RISK_SYMPTOM_THRESHOLD,
        )
        .group_by(SymptomDefinition.name_zh)
    ).all()
    for name, score in high_scores:
        raise_to(RiskLevel.MEDIUM, f"{name} {float(score):g}/10")

    for n in alerts:
        if n.severity == AlertSeverity.CRITICAL:
            raise_to(RiskLevel.HIGH, f"未處理警示：{n.title}")
        elif n.severity == AlertSeverity.WARNING:
            raise_to(RiskLevel.MEDIUM, f"未處理警示：{n.title}")

    for code, row in latest_by_test(ctx.patient, since=ctx.now - LAB_RISK_WINDOW).items():
        level_for = LAB_RISK_RULES.get(code, {}).get(row.abnormal_flag)
        if level_for:
            label, text = LAB_RISK_LABELS[code], LAB_FLAG_TEXT[row.abnormal_flag]
            raise_to(level_for, f"{label} {format_lab_value(row)} {row.unit}（{text}）")

    if latest_assessment and latest_assessment.risk_level in (RiskLevel.MEDIUM, RiskLevel.HIGH):
        raise_to(latest_assessment.risk_level, f"護理評估風險：{latest_assessment.risk_level}")

    if ctx.cycle is not None and (last_report_at is None or ctx.now - last_report_at > NO_REPORT_WARNING):
        raise_to(RiskLevel.MEDIUM, "治療期間超過 48 小時未回報")

    return {"level": level, "reasons": reasons}


# ---------------------------------------------------------------- nurse caseload

def _hours_desc(i):
    """Longest without a report first; never reported counts as longest."""
    return -(i["hours_since_last_report"] if i["hours_since_last_report"] is not None else 10**6)


def _sort_by_risk(items):
    # Stable sorts applied from the lowest-priority key to the highest.
    items.sort(key=lambda i: i["patient_code"])
    items.sort(key=_hours_desc)
    items.sort(key=lambda i: i["latest_alert_at"] or "", reverse=True)  # newest open alert first
    items.sort(key=lambda i: (-RISK_RANK[i["risk_level"]], -i["unacknowledged_critical_count"], -i["unacknowledged_alert_count"]))


CASELOAD_SORTS = {
    "risk": _sort_by_risk,
    "last_report": lambda items: items.sort(key=lambda i: (_hours_desc(i), i["patient_code"])),
    "name": lambda items: items.sort(key=lambda i: i["patient_code"]),
}



def build_nurse_caseload(nurse, sort="risk"):
    """caseload widget: one row per currently assigned patient.

    Risk levels come from the same risk engine as nurse-view (not recomputed). ``sort``:
    - ``risk`` (default): level, then open critical alerts, open alerts, newest alert,
      longest without a report
    - ``last_report``: longest without a report first
    - ``name``: patient code
    """
    assignments = db.session.execute(
        select(NursePatientAssignment).filter_by(nurse_id=nurse.id, ended_at=None)
    ).scalars().all()

    items = []
    for assignment in assignments:
        patient = assignment.patient
        if patient.deleted_at is not None:
            continue
        zone = patient_zone(patient.timezone)
        now = utcnow()
        plan, cycle = _active_plan_and_cycle(patient)
        ctx = DashboardContext(patient, zone, now, to_local(now, zone).date(), plan, cycle)
        nurse_view = nurse_view_widget(ctx, latest_vitals_widget(ctx))
        items.append({
            "patient_id": patient.public_id,
            "patient_code": patient.patient_code,
            "display_name": patient.display_name,
            "risk_level": nurse_view["risk"]["level"],
            "risk_reasons": nurse_view["risk"]["reasons"],
            "cycle": (
                {"cycle_number": cycle.cycle_number, "cycle_day": cycle.cycle_day_on(ctx.today)} if cycle else None
            ),
            "last_report_at": nurse_view["last_report_at"],
            "hours_since_last_report": nurse_view["hours_since_last_report"],
            "pending_review_count": nurse_view["pending_symptom_reviews"]["count"],
            "unacknowledged_alert_count": nurse_view["unacknowledged_alerts"]["count"],
            "unacknowledged_critical_count": sum(
                a["severity"] == AlertSeverity.CRITICAL for a in nurse_view["unacknowledged_alerts"]["items"]
            ),
            "latest_alert_at": max((a["created_at"] for a in nurse_view["unacknowledged_alerts"]["items"]), default=None),
            "care_alert_types": [a.alert_type for a in patient.active_care_alerts],
            "is_primary_nurse": bool(assignment.is_primary),
        })

    CASELOAD_SORTS[sort](items)
    for rank, item in enumerate(items, start=1):
        item["priority_rank"] = rank
    meta = {"total": len(items), "sort": sort}
    for level in (RiskLevel.HIGH, RiskLevel.MEDIUM, RiskLevel.LOW):
        meta[level] = sum(i["risk_level"] == level for i in items)
    return items, meta


def build_pending_reviews(nurse):
    """Submitted, final symptom records of the nurse's current patients, oldest first."""
    patient_ids = db.session.execute(
        select(NursePatientAssignment.patient_id).filter_by(nurse_id=nurse.id, ended_at=None)
    ).scalars().all()
    rows = db.session.execute(
        select(SymptomRecord).where(SymptomRecord.patient_id.in_(patient_ids), SymptomRecord.record_status == RecordStatus.FINAL,
                                    SymptomRecord.review_status == ReviewStatus.SUBMITTED)
        .order_by(SymptomRecord.recorded_at, SymptomRecord.id).limit(200)
    ).scalars().all()
    items = []
    for r in rows:
        p = r.patient
        if p.deleted_at is not None:
            continue
        open_alerts = db.session.execute(
            select(func.count(func.distinct(Notification.event_key))).where(
                Notification.source_table == "symptom_records", Notification.source_id == r.id,
                Notification.status.in_(NotificationStatus.OPEN))
        ).scalar()
        items.append({**_pending_review_item(r), "open_alert_count": open_alerts,
                      "patient": {"id": p.public_id, "patient_code": p.patient_code, "display_name": p.display_name}})
    return items
