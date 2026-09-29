"""Patient Care Timeline (GET /api/v1/patients/<id>/timeline).

Composed at query time from the existing clinical tables; nothing is stored for the
timeline itself. Each source runs its own indexed, time-bounded query limited to
``limit + 1`` rows after the cursor, and the sources are merged in memory.

Ordering: ``occurred_at`` DESC, then a fixed per-source rank (so an alert raised by a
vital sign is listed right above it), then source id DESC. The cursor is that sort key
of the last returned event (keyset pagination: stable while new events arrive).

Privacy: patients get the patient-visible version of every event — no staff notes, no
internal risk judgement, no staff names on alert handling, and only their own copy of
each notification (same rules as the notification workflow).
"""

import base64
import binascii
import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import and_, or_, select

from app.core.api import APIError
from app.core.timeutil import iso_date, iso_utc, local_day_bounds_utc, patient_zone, to_local
from app.extensions import db
from app.models.base import utcnow
from app.models import (
    Appointment,
    LabResult,
    MedicationRecord,
    Notification,
    NursingAssessment,
    SymptomRecord,
    VitalSign,
)
from app.models.enums import (
    MedicationType,
    NotificationStatus,
    NotificationType,
    RecordStatus,
    RoleName,
    SignStatus,
    SymptomValueType,
)
from app.models.enums import TimelineEventType as T
from app.services.labs import FLAG_LEVEL, format_value as lab_value, patient_view as lab_patient_view
from app.services.vitals import VITAL_FIELDS, flags_for, format_value as vital_value

DEFAULT_LIMIT = 30
MAX_LIMIT = 100

# Tie-break at the same instant (lower first): later handling steps above the alert, the
# alert above the record that raised it.
RANK = {
    "status:resolved": 1,
    "status:in_progress": 2,
    "status:acknowledged": 3,
    T.NOTIFICATION: 4,
    T.NURSING_ASSESSMENT: 5,
    T.LAB_RESULT: 6,
    T.VITAL_SIGN: 7,
    T.SYMPTOM: 8,
    "chemo:medication": 9,
    "chemo:cycle_end": 10,
    "chemo:cycle_start": 11,
    T.APPOINTMENT: 12,
}
APPOINTMENT_TYPE_TEXT = {"chemo_infusion": "化療注射", "lab_draw": "抽血", "clinic_visit": "門診", "imaging": "影像檢查",
                         "radiotherapy": "放射治療", "education_session": "衛教", "other": "行程"}
APPOINTMENT_STATUS_TEXT = {"scheduled": "已排定", "checked_in": "已報到", "completed": "已完成", "cancelled": "已取消",
                           "no_show": "未到", "rescheduled": "已改期"}

ASSESSMENT_TYPE_TEXT = {
    "initial": "初次評估",
    "pre_chemo": "化療前評估",
    "during_infusion": "輸注中評估",
    "post_chemo": "化療後評估",
    "follow_up": "追蹤評估",
    "phone_follow_up": "電話追蹤",
}
MEDICATION_TYPE_TEXT = {MedicationType.CHEMO: "化療給藥", MedicationType.PREMEDICATION: "前置用藥", MedicationType.SUPPORTIVE: "支持性用藥"}
ADMIN_STATUS_TEXT = {"given": "已給藥", "held": "暫停給藥", "partial": "部分給藥", "refused": "拒絕給藥"}
RISK_TO_SEVERITY = {"high": "critical", "medium": "warning"}
STAFF_STEP = {
    NotificationStatus.ACKNOWLEDGED: ("acknowledged", "接手"),
    NotificationStatus.IN_PROGRESS: ("in_progress", "開始處理"),
    NotificationStatus.RESOLVED: ("resolved", "完成處理"),
}
PATIENT_STEP = {
    NotificationStatus.ACKNOWLEDGED: "護理師已接手",
    NotificationStatus.IN_PROGRESS: "護理師正在處理",
    NotificationStatus.RESOLVED: "已處理完成",
}


# ------------------------------------------------------------------ params & cursor


@dataclass
class Params:
    start: datetime | None  # naive UTC, inclusive
    end: datetime | None  # naive UTC, exclusive
    start_date: date | None
    end_date: date | None
    limit: int
    cursor: tuple | None  # (occurred_at, rank, key)


def _parse_date(raw, name, details):
    if raw in (None, ""):
        return None
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        details.append({"field": name, "issue": "must be a date (YYYY-MM-DD)"})
        return None


def encode_cursor(key):
    t, rank, ident = key
    raw = json.dumps([t.isoformat(), rank, ident], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(raw):
    try:
        t, rank, ident = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        return datetime.fromisoformat(t), int(rank), int(ident)
    except (ValueError, TypeError, binascii.Error, json.JSONDecodeError):
        raise APIError(400, "VALIDATION_ERROR", "查詢條件有誤", [{"field": "cursor", "issue": "is invalid"}]) from None


def parse_params(args, zone):
    details = []
    start_date = _parse_date(args.get("start_date"), "start_date", details)
    end_date = _parse_date(args.get("end_date"), "end_date", details)
    if start_date and end_date and start_date > end_date:
        details.append({"field": "end_date", "issue": "must not be earlier than start_date"})
    raw_limit = args.get("limit", str(DEFAULT_LIMIT))
    try:
        limit = int(raw_limit)
        if not 1 <= limit <= MAX_LIMIT:
            raise ValueError
    except (TypeError, ValueError):
        details.append({"field": "limit", "issue": f"must be an integer between 1 and {MAX_LIMIT}"})
        limit = DEFAULT_LIMIT
    if details:
        raise APIError(400, "VALIDATION_ERROR", "查詢條件有誤", details)
    cursor = _decode_cursor(args["cursor"]) if args.get("cursor") else None
    return Params(
        start=local_day_bounds_utc(start_date, zone)[0] if start_date else None,
        end=local_day_bounds_utc(end_date, zone)[1] if end_date else None,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        cursor=cursor,
    )


# ------------------------------------------------------------------ query helpers


def _window(col, id_col, rank, params):
    """Date range + keyset conditions for one source (col DESC, id DESC within the source)."""
    conds = []
    if params.start is not None:
        conds.append(col >= params.start)
    if params.end is not None:
        conds.append(col < params.end)
    if params.cursor is not None:
        t, r, k = params.cursor
        if rank > r:
            conds.append(col <= t)
        elif rank < r:
            conds.append(col < t)
        else:
            conds.append(or_(col < t, and_(col == t, id_col < k)))
    return conds


def _after_cursor(key, params):
    """Python-side version of _window for sources computed in memory."""
    t, rank, ident = key
    if params.start is not None and t < params.start:
        return False
    if params.end is not None and t >= params.end:
        return False
    if params.cursor is None:
        return True
    ct, cr, ck = params.cursor
    return t < ct or (t == ct and (rank > cr or (rank == cr and ident < ck)))


@dataclass
class Event:
    key: tuple  # (occurred_at, rank, id) — sort key and cursor
    event_type: str
    event_id: str
    title: str
    summary: str
    severity: str | None = None
    source_table: str | None = None
    source_id: int | None = None
    cycle_id: int | None = None
    cycle_day: int | None = None
    all_day: bool = False
    detail: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)

    def payload(self):
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "occurred_at": iso_utc(self.key[0]),
            "all_day": self.all_day,
            "title": self.title,
            "summary": self.summary,
            "severity": self.severity,
            "source": {"table": self.source_table, "id": self.source_id, **self.extra} if self.source_table else None,
            "source_id": self.source_id,
            "cycle_id": self.cycle_id,
            "cycle_day": self.cycle_day,
            "detail": self.detail,
        }


class Context:
    def __init__(self, patient, viewer, params):
        self.patient = patient
        self.viewer = viewer
        self.params = params
        self.staff = viewer.role_name in (RoleName.NURSE, RoleName.ADMIN)
        self.zone = patient_zone(patient.timezone)
        self.cycles = sorted(
            (c for c in patient.chemotherapy_cycles if c.deleted_at is None and c.actual_start_date),
            key=lambda c: c.actual_start_date,
        )

    def cycle_at(self, dt):
        """(cycle_id, cycle_day) for events that do not store it (e.g. notifications)."""
        day = to_local(dt, self.zone).date()
        for c in reversed(self.cycles):
            if c.actual_start_date <= day and (c.actual_end_date is None or day <= c.actual_end_date + timedelta(days=30)):
                return c.id, c.cycle_day_on(day)
        return None, None

    def local_midnight_utc(self, day):
        return datetime.combine(day, time.min, tzinfo=self.zone).astimezone(timezone.utc).replace(tzinfo=None)


def _person(user):
    return user.display_name if user else None


# ------------------------------------------------------------------ sources


def _symptoms(ctx):
    rank = RANK[T.SYMPTOM]
    rows = db.session.execute(
        select(SymptomRecord)
        .where(SymptomRecord.patient_id == ctx.patient.id, SymptomRecord.record_status == RecordStatus.FINAL,
               *_window(SymptomRecord.recorded_at, SymptomRecord.id, rank, ctx.params))
        .order_by(SymptomRecord.recorded_at.desc(), SymptomRecord.id.desc())
        .limit(ctx.params.limit + 1)
    ).scalars().all()
    events = []
    for r in rows:
        values, parts, fever, top = [], [], False, 0
        for v in r.values:
            d = v.definition
            if d.value_type == SymptomValueType.BOOLEAN:
                fever = fever or bool(v.value_boolean)
                values.append({"code": d.code, "label": d.name_zh, "value": "有" if v.value_boolean else "沒有"})
                if v.value_boolean:
                    parts.append(d.name_zh)
            else:
                score = float(v.score) if v.score is not None else None
                values.append({"code": d.code, "label": d.name_zh, "value": f"{score:g}" if score is not None else None, "score": score})
                if score is not None:
                    top = max(top, score)
        scored = sorted((x for x in values if x.get("score") is not None), key=lambda x: -x["score"])
        summary = "、".join([f"{x['label']} {x['value']}" for x in scored[:3]] + parts) or "已回報"
        detail = {"values": values, "notes": r.notes, "reviewed": r.review_status == "reviewed"}
        if ctx.staff:
            detail.update({"review_status": r.review_status, "reviewed_by": _person(r.reviewer), "reviewed_at": iso_utc(r.reviewed_at),
                           "reported_by": _person(r.reporter), "source": r.source})
        events.append(Event(
            key=(r.recorded_at, rank, r.id), event_type=T.SYMPTOM, event_id=f"{T.SYMPTOM}:{r.id}",
            title="症狀回報" if r.source == "patient_app" else "症狀紀錄（護理師）", summary=summary,
            severity="critical" if fever else "warning" if top >= 7 else None,
            source_table="symptom_records", source_id=r.id, cycle_id=r.cycle_id, cycle_day=r.cycle_day, detail=detail,
        ))
    return events


def _vitals(ctx):
    rank = RANK[T.VITAL_SIGN]
    rows = db.session.execute(
        select(VitalSign)
        .where(VitalSign.patient_id == ctx.patient.id, VitalSign.record_status == RecordStatus.FINAL,
               *_window(VitalSign.measured_at, VitalSign.id, rank, ctx.params))
        .order_by(VitalSign.measured_at.desc(), VitalSign.id.desc())
        .limit(ctx.params.limit + 1)
    ).scalars().all()
    events = []
    for v in rows:
        flags = flags_for(v)
        level = {f["field"]: f["level"] for f in flags}
        values = []
        for name, meta in VITAL_FIELDS.items():
            value = getattr(v, name)
            if value is not None:
                values.append({"field": name, "label": meta.label, "value": vital_value(name, value), "flag": level.get(name)})
        bp = next((x for x in values if x["field"] == "systolic_bp_mmhg"), None)
        short = [x for x in values if x["field"] not in ("systolic_bp_mmhg", "diastolic_bp_mmhg")]
        parts = [f"{x['label']} {x['value']}" for x in short]
        if bp and v.diastolic_bp_mmhg is not None:
            parts.insert(1 if short else 0, f"血壓 {v.systolic_bp_mmhg}/{v.diastolic_bp_mmhg}")
        detail = {"values": values, "flags": [f["message"] for f in flags],
                  "temperature_site": v.temperature_site, "bp_measure_site": v.bp_measure_site, "notes": v.notes}
        if ctx.staff:
            detail.update({"source": v.source, "recorded_by": _person(v.recorder)})
        events.append(Event(
            key=(v.measured_at, rank, v.id), event_type=T.VITAL_SIGN, event_id=f"{T.VITAL_SIGN}:{v.id}",
            title="生命徵象量測", summary="、".join(parts[:4]),
            severity=flags[0]["level"] if flags else None,
            source_table="vital_signs", source_id=v.id, cycle_id=v.cycle_id, cycle_day=v.cycle_day, detail=detail,
        ))
    return events


def _labs(ctx):
    """One event per collection time (a CBC panel is several lab_results rows)."""
    rank = RANK[T.LAB_RESULT]
    base = [LabResult.patient_id == ctx.patient.id, LabResult.record_status == RecordStatus.FINAL]
    p = ctx.params
    conds = []
    if p.start is not None:
        conds.append(LabResult.collected_at >= p.start)
    if p.end is not None:
        conds.append(LabResult.collected_at < p.end)
    if p.cursor is not None:
        t, r, _k = p.cursor
        conds.append(LabResult.collected_at <= t if rank > r else LabResult.collected_at < t)
    times = db.session.execute(
        select(LabResult.collected_at).where(*base, *conds).group_by(LabResult.collected_at)
        .order_by(LabResult.collected_at.desc()).limit(p.limit + 1)
    ).scalars().all()
    if not times:
        return []
    rows = db.session.execute(
        select(LabResult).where(*base, LabResult.collected_at.in_(times)).order_by(LabResult.id)
    ).scalars().all()
    panels = {}
    for row in rows:
        panels.setdefault(row.collected_at, []).append(row)
    order = {"WBC": 0, "ANC": 1, "HGB": 2, "PLT": 3}
    events = []
    for collected_at, panel in panels.items():
        panel.sort(key=lambda x: order.get(x.test_type.code, 99))
        first = min(x.id for x in panel)
        levels = [FLAG_LEVEL.get(x.abnormal_flag or "N") for x in panel]
        if ctx.staff:
            items = [{
                "test_code": x.test_type.code, "name": x.test_type.name_zh, "value": lab_value(x), "unit": x.unit,
                "ref_low": float(x.ref_low) if x.ref_low is not None else None,
                "ref_high": float(x.ref_high) if x.ref_high is not None else None,
                "abnormal_flag": x.abnormal_flag,
            } for x in panel]
            summary = "、".join(f"{i['test_code']} {i['value']}" + (f" {i['abnormal_flag']}" if i["abnormal_flag"] not in (None, "N") else "") for i in items)
            detail = {"items": items, "recorded_by": _person(panel[0].recorder)}
        else:
            items = [lab_patient_view(x) for x in panel]
            summary = "、".join(f"{i['label']} {i['value']}（{i['status_text']}）" for i in items)
            detail = {"items": items}
        events.append(Event(
            key=(collected_at, rank, first), event_type=T.LAB_RESULT, event_id=f"{T.LAB_RESULT}:{first}",
            title="抽血檢驗", summary=summary,
            severity="critical" if "critical" in levels else "warning" if "warning" in levels else None,
            source_table="lab_results", source_id=first, extra={"ids": [x.id for x in panel]},
            cycle_id=panel[0].cycle_id, cycle_day=panel[0].cycle_day, detail=detail,
        ))
    return events


def _notification_scope(ctx):
    """Patient: their own copies. Staff: one row per event (same rule as the notification list)."""
    from app.modules.notification.services import _event_representatives  # noqa: PLC0415 (avoid import cycle)

    base = [Notification.patient_id == ctx.patient.id, Notification.type == NotificationType.RISK_ALERT,
            (Notification.scheduled_for.is_(None)) | (Notification.scheduled_for <= utcnow())]
    if not ctx.staff:
        return base + [Notification.recipient_id == ctx.viewer.id]
    return base + [Notification.id.in_(_event_representatives(ctx.viewer, base))]


def _notifications(ctx):
    rank = RANK[T.NOTIFICATION]
    rows = db.session.execute(
        select(Notification)
        .where(*_notification_scope(ctx), *_window(Notification.created_at, Notification.id, rank, ctx.params))
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(ctx.params.limit + 1)
    ).scalars().all()
    from app.modules.notification.services import PATIENT_STATUS_TEXT, STAFF_STATUS_TEXT  # noqa: PLC0415

    events = []
    for n in rows:
        cycle_id, cycle_day = ctx.cycle_at(n.created_at)
        detail = {
            "notification_id": n.id,
            "message": n.message,
            "status": n.status,
            "status_text": (STAFF_STATUS_TEXT if ctx.staff else PATIENT_STATUS_TEXT).get(n.status),
            "source": {"table": n.source_table, "id": n.source_id} if n.source_table else None,
        }
        if ctx.staff:
            detail.update({
                "alert_rule_code": n.alert_rule.code if n.alert_rule else None,
                "recommended_action": n.alert_rule.recommended_action if n.alert_rule else None,
            })
        events.append(Event(
            key=(n.created_at, rank, n.id), event_type=T.NOTIFICATION, event_id=f"{T.NOTIFICATION}:{n.id}",
            title=n.title, summary=n.message, severity=n.severity,
            source_table="notifications", source_id=n.id, cycle_id=cycle_id, cycle_day=cycle_day, detail=detail,
        ))
    return events


def _notification_steps(ctx):
    """Handling steps from the lifecycle columns. Steps saved at the same instant (quick resolve,
    rows migrated from before the lifecycle) collapse into the latest one."""
    events = []
    steps = [
        (NotificationStatus.ACKNOWLEDGED, Notification.acknowledged_at,
         [or_(Notification.started_at.is_(None), Notification.started_at != Notification.acknowledged_at)]),
        (NotificationStatus.IN_PROGRESS, Notification.started_at,
         [or_(Notification.resolved_at.is_(None), Notification.resolved_at != Notification.started_at)]),
        (NotificationStatus.RESOLVED, Notification.resolved_at, []),
    ]
    scope = _notification_scope(ctx)
    for status, col, collapse in steps:
        key_name, verb = STAFF_STEP[status]
        rank = RANK[f"status:{key_name}"]
        rows = db.session.execute(
            select(Notification)
            .where(*scope, col.is_not(None), *collapse, *_window(col, Notification.id, rank, ctx.params))
            .order_by(col.desc(), Notification.id.desc())
            .limit(ctx.params.limit + 1)
        ).scalars().all()
        for n in rows:
            at = getattr(n, col.key)
            cycle_id, cycle_day = ctx.cycle_at(at)
            if ctx.staff:
                actor = {NotificationStatus.ACKNOWLEDGED: n.acknowledger, NotificationStatus.IN_PROGRESS: n.starter,
                         NotificationStatus.RESOLVED: n.resolver}[status]
                title = f"{verb}：{n.title}"
                summary = f"{_person(actor) or '護理人員'}{verb}"
                detail = {"notification_id": n.id, "status": status, "by": _person(actor)}
                if status == NotificationStatus.RESOLVED:
                    detail["resolution_note"] = n.resolution_note
                    summary += f"：{n.resolution_note}" if n.resolution_note else ""
            else:
                title = PATIENT_STEP[status]
                summary = f"「{n.title}」{PATIENT_STEP[status]}"
                detail = {"notification_id": n.id, "status": status}
            events.append(Event(
                key=(at, rank, n.id), event_type=T.NOTIFICATION_STATUS, event_id=f"{T.NOTIFICATION_STATUS}:{n.id}:{status}",
                title=title, summary=summary, severity=None,
                source_table="notifications", source_id=n.id, cycle_id=cycle_id, cycle_day=cycle_day, detail=detail,
            ))
    return events


def _assessments(ctx):
    rank = RANK[T.NURSING_ASSESSMENT]
    conds = [NursingAssessment.patient_id == ctx.patient.id, NursingAssessment.record_status == RecordStatus.FINAL]
    if not ctx.staff:
        conds.append(NursingAssessment.sign_status == SignStatus.SIGNED)  # drafts are internal
    rows = db.session.execute(
        select(NursingAssessment)
        .where(*conds, *_window(NursingAssessment.assessed_at, NursingAssessment.id, rank, ctx.params))
        .order_by(NursingAssessment.assessed_at.desc(), NursingAssessment.id.desc())
        .limit(ctx.params.limit + 1)
    ).scalars().all()
    events = []
    for a in rows:
        type_text = ASSESSMENT_TYPE_TEXT.get(a.assessment_type, "護理評估")
        if ctx.staff:
            text = a.plan or a.assessment or a.subjective or ""
            summary = f"{_person(a.assessor) or '護理師'}：{text[:80]}{'…' if len(text) > 80 else ''}" if text else (_person(a.assessor) or "")
            detail = {
                "assessment_type": a.assessment_type, "assessment_type_text": type_text, "assessed_by": _person(a.assessor),
                "sign_status": a.sign_status, "risk_level": a.risk_level, "ecog_status": a.ecog_status,
                "overall_condition": a.overall_condition, "chemo_readiness": a.chemo_readiness,
                "subjective": a.subjective, "objective": a.objective, "assessment": a.assessment, "plan": a.plan,
                "next_follow_up_at": iso_utc(a.next_follow_up_at),
            }
            severity = RISK_TO_SEVERITY.get(a.risk_level)
            title = f"護理評估（{type_text}）" + ("・草稿" if a.sign_status == SignStatus.DRAFT else "")
        else:
            summary = "護理師已追蹤您的狀況。" if a.assessment_type == "phone_follow_up" else "護理師已完成評估。"
            detail = {"assessment_type": a.assessment_type, "assessment_type_text": type_text}
            severity = None
            title = f"護理師{type_text}"
        events.append(Event(
            key=(a.assessed_at, rank, a.id), event_type=T.NURSING_ASSESSMENT, event_id=f"{T.NURSING_ASSESSMENT}:{a.id}",
            title=title, summary=summary, severity=severity,
            source_table="nursing_assessments", source_id=a.id, cycle_id=a.cycle_id, cycle_day=a.cycle_day, detail=detail,
        ))
    return events


def _medications(ctx):
    rank = RANK["chemo:medication"]
    rows = db.session.execute(
        select(MedicationRecord)
        .where(MedicationRecord.patient_id == ctx.patient.id, MedicationRecord.record_status == RecordStatus.FINAL,
               *_window(MedicationRecord.administered_at, MedicationRecord.id, rank, ctx.params))
        .order_by(MedicationRecord.administered_at.desc(), MedicationRecord.id.desc())
        .limit(ctx.params.limit + 1)
    ).scalars().all()
    events = []
    for m in rows:
        dose = f"{m.drug.generic_name} {float(m.dose_value):g} {m.dose_unit}" + (f" {m.route}" if m.route else "")
        detail = {
            "kind": "medication", "medication_type": m.medication_type, "drug": m.drug.generic_name,
            "dose": f"{float(m.dose_value):g} {m.dose_unit}", "route": m.route,
            "administration_status": m.administration_status,
            "administration_status_text": ADMIN_STATUS_TEXT.get(m.administration_status, m.administration_status),
            "infusion_duration_min": m.infusion_duration_min,
        }
        if ctx.staff:
            detail.update({"reaction_notes": m.reaction_notes, "administered_by": _person(m.administrator)})
        events.append(Event(
            key=(m.administered_at, rank, m.id), event_type=T.CHEMOTHERAPY, event_id=f"{T.CHEMOTHERAPY}:medication:{m.id}",
            title=MEDICATION_TYPE_TEXT.get(m.medication_type, "給藥"), summary=f"{dose}（{detail['administration_status_text']}）",
            source_table="medication_records", source_id=m.id, cycle_id=m.cycle_id, cycle_day=m.cycle_day, detail=detail,
        ))
    return events


def _cycles(ctx):
    """Cycle start / end (dates, shown as all-day events). Few rows per patient, filtered in memory."""
    events = []
    for c in ctx.cycles:
        plan = c.plan
        regimen = plan.regimen.name if plan and plan.regimen else None
        base_detail = {"kind": "cycle", "cycle_number": c.cycle_number, "regimen": regimen,
                       "scheduled_date": iso_date(c.scheduled_date), "status": c.status,
                       "total_cycles": plan.total_cycles if plan else None}
        for kind, day in (("cycle_start", c.actual_start_date), ("cycle_end", c.actual_end_date)):
            if day is None:
                continue
            key = (ctx.local_midnight_utc(day), RANK[f"chemo:{kind}"], c.id)
            if not _after_cursor(key, ctx.params):
                continue
            start = kind == "cycle_start"
            events.append(Event(
                key=key, event_type=T.CHEMOTHERAPY, event_id=f"{T.CHEMOTHERAPY}:{kind}:{c.id}",
                title=f"第 {c.cycle_number} 次化療{'開始' if start else '結束'}",
                summary=(f"{regimen}，" if regimen else "") + ("療程第 1 天" if start else "本次療程結束"),
                source_table="chemotherapy_cycles", source_id=c.id, cycle_id=c.id,
                cycle_day=1 if start else c.cycle_day_on(day), all_day=True, detail={**base_detail, "kind": kind},
            ))
    return events


def _appointments(ctx):
    """Appointments whose time has come (upcoming ones are in 今日行程 / the schedule, not in the
    history). A rescheduled appointment links to the one that replaced it and vice versa."""
    rank = RANK[T.APPOINTMENT]
    rows = db.session.execute(
        select(Appointment)
        .where(Appointment.patient_id == ctx.patient.id, Appointment.deleted_at.is_(None), Appointment.scheduled_at <= utcnow(),
               *_window(Appointment.scheduled_at, Appointment.id, rank, ctx.params))
        .order_by(Appointment.scheduled_at.desc(), Appointment.id.desc())
        .limit(ctx.params.limit + 1)
    ).scalars().all()
    events = []
    for a in rows:
        link = lambda x: {"id": x.id, "scheduled_at": iso_utc(x.scheduled_at)} if x else None  # noqa: E731
        status_text = APPOINTMENT_STATUS_TEXT.get(a.status, a.status)
        detail = {
            "kind": "appointment", "appointment_type": a.appointment_type,
            "appointment_type_text": APPOINTMENT_TYPE_TEXT.get(a.appointment_type, "行程"), "status": a.status,
            "status_text": status_text, "location": a.location, "duration_min": a.duration_min,
            "instructions": [{"instruction_type": i.instruction_type, "text": i.text, "due_at": iso_utc(i.due_at)} for i in a.instructions],
            "rescheduled_from": link(a.rescheduled_from),
            "rescheduled_to": link(a.rescheduled_to[0] if a.rescheduled_to else None),
        }
        if ctx.staff:
            detail["notes"] = a.notes
        cycle_id, cycle_day = ctx.cycle_at(a.scheduled_at)
        events.append(Event(
            key=(a.scheduled_at, rank, a.id), event_type=T.APPOINTMENT, event_id=f"{T.APPOINTMENT}:{a.id}",
            title=a.title, summary="，".join(x for x in (status_text, a.location) if x),
            source_table="appointments", source_id=a.id, cycle_id=a.cycle_id or cycle_id, cycle_day=cycle_day, detail=detail,
        ))
    return events


SOURCES = (_symptoms, _vitals, _labs, _notifications, _notification_steps, _assessments, _medications, _cycles, _appointments)


def build_timeline(patient, viewer, params):
    ctx = Context(patient, viewer, params)
    events = [e for source in SOURCES for e in source(ctx)]
    events.sort(key=lambda e: (e.key[0], -e.key[1], e.key[2]), reverse=True)  # time DESC, rank ASC, id DESC
    page = events[: params.limit]
    has_more = len(events) > params.limit
    return [e.payload() for e in page], {
        "limit": params.limit,
        "returned": len(page),
        "has_more": has_more,
        "next_cursor": encode_cursor(page[-1].key) if has_more and page else None,
        "start_date": iso_date(params.start_date),
        "end_date": iso_date(params.end_date),
        "timezone": patient.timezone,
        "event_types": list(T.ALL),
    }
