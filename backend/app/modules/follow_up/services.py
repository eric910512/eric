"""護理師追蹤管理 (Phase 1c; docs/skin-follow-up-implementation.md).

Life cycle: open → completed | cancelled (final; another check = a new follow-up linked to the previous
one). Every change is one transaction with a conditional update on (status = 'open', version = the
version the nurse saw): two nurses acting at once → one wins, the other gets 409 (VERSION_CONFLICT or
FOLLOW_UP_CLOSED) and nothing of theirs is written. A completion is a new **signed nursing assessment**
(the review's assessment is never changed); if writing it fails, the follow-up stays open.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import exists, func, select, update
from sqlalchemy.exc import IntegrityError

from app.core.api import APIError
from app.core.audit import record_data_event
from app.core.timeutil import iso_utc, local_day_bounds_utc, patient_zone, to_local, to_ms
from app.extensions import db
from app.models import CareFollowUp, CareFollowUpEvent, NursePatientAssignment, NursingAssessment, PatientProfile, SymptomRecord
from app.models.base import utcnow
from app.models.enums import AuditAction, FollowUpEventType, FollowUpStatus, ObservationSource, RecordStatus, ReviewStatus, SignStatus

MAX_REASON = 1000
MAX_NOTE = 2000
MAX_AHEAD = timedelta(days=180)
COMPLETION_TYPES = ("phone_follow_up", "follow_up")
COMPLETION_MARK = "【追蹤結案】"  # first words of a completion assessment (also linked by care_follow_ups.completion_assessment_id)
STATUS_FILTERS = ("open", "overdue", "completed", "cancelled", "all")
SORTS = {"due_at": (CareFollowUp.due_at, CareFollowUp.id), "-due_at": (CareFollowUp.due_at.desc(), CareFollowUp.id.desc()),
         "-updated_at": (CareFollowUp.updated_at.desc(), CareFollowUp.id.desc())}


class _Errors:
    def __init__(self):
        self.details = []

    def add(self, field, issue):
        self.details.append({"field": field, "issue": issue})

    def raise_if_any(self):
        if self.details:
            raise APIError(400, "VALIDATION_ERROR", "追蹤資料有誤", self.details)


def parse_due_at(raw, patient, errors, field="due_at"):
    """ISO 8601 with timezone → naive UTC. From the start of the patient's local today (Asia/Taipei) up to 180 days ahead."""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00")) if isinstance(raw, str) else None
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        errors.add(field, "must be an ISO 8601 datetime with timezone")
        return None
    value = to_ms(parsed.astimezone(timezone.utc).replace(tzinfo=None))
    now = utcnow()
    zone = patient_zone(patient.timezone)
    start_of_today = local_day_bounds_utc(to_local(now, zone).date(), zone)[0]
    if value < start_of_today:
        errors.add(field, "cannot be before today")
        return None
    if value > now + MAX_AHEAD:
        errors.add(field, "cannot be more than 180 days ahead")
        return None
    return value


def text_field(body, name, errors, maximum, required=True):
    raw = body.get(name)
    if raw is None and not required:
        return None
    if not isinstance(raw, str) or not raw.strip() or len(raw) > maximum:
        errors.add(name, f"is required (1–{maximum} characters)")
        return None
    return raw.strip()


def version_field(body, errors):
    v = body.get("version")
    if not isinstance(v, int) or isinstance(v, bool) or v < 1:
        errors.add("version", "is required (the version you saw)")
        return None
    return v


# ------------------------------------------------------------------ payloads


def _person(user):
    return {"id": user.public_id, "display_name": user.display_name} if user else None


def follow_up_payload(fu, now=None, detail=False):
    now = now or utcnow()
    zone = patient_zone(fu.patient.timezone)
    record = fu.record
    data = {
        "id": fu.public_id,
        "status": fu.status,
        "overdue": fu.is_overdue(now),
        "due_at": iso_utc(fu.due_at),
        "due_local_date": to_local(fu.due_at, zone).date().isoformat(),
        "reason": fu.reason,
        "version": fu.version,
        "patient": {"id": fu.patient.public_id, "patient_code": fu.patient.patient_code, "display_name": fu.patient.display_name},
        "symptom_record": {"id": record.id, "recorded_at": iso_utc(record.recorded_at), "form_code": record.form.code if record.form else None},
        "created_by": _person(fu.creator),
        "created_at": iso_utc(fu.created_at),
        "updated_at": iso_utc(fu.updated_at),
        "completed_by": _person(fu.completer),
        "completed_at": iso_utc(fu.completed_at),
        "cancelled_by": _person(fu.canceller),
        "cancelled_at": iso_utc(fu.cancelled_at),
        "cancellation_reason": fu.cancellation_reason,
        "previous_follow_up_id": fu.previous.public_id if fu.previous else None,
        "next_follow_up_ids": [n.public_id for n in fu.next_follow_ups],
    }
    if detail:
        a = fu.completion_assessment
        data["completion"] = ({"nursing_assessment_id": a.id, "assessment_type": a.assessment_type, "result": a.assessment,
                               "actions": a.plan, "sign_status": a.sign_status, "signed_at": iso_utc(a.signed_at)} if a else None)
        data["review_assessment_id"] = fu.review_assessment_id
    return data


def patient_payload(fu):
    """What a patient sees: that a follow-up is planned, its date and its state — no nurse notes, names or reasons."""
    zone = patient_zone(fu.patient.timezone)
    return {
        "id": fu.public_id,
        "status": fu.status,
        "due_local_date": to_local(fu.due_at, zone).date().isoformat(),
        "symptom_record": {"id": fu.record.id, "recorded_at": iso_utc(fu.record.recorded_at)},
        "completed_at": iso_utc(fu.completed_at),
        "cancelled_at": iso_utc(fu.cancelled_at),
    }


def record_summary(record):
    """For the nurse's review card: the record's open follow-up, else its latest one (None without any)."""
    rows = db.session.execute(select(CareFollowUp).filter_by(symptom_record_id=record.id)
                              .order_by((CareFollowUp.status == FollowUpStatus.OPEN).desc(), CareFollowUp.id.desc()).limit(1)).scalars().all()
    if not rows:
        return None
    fu = rows[0]
    return {"id": fu.public_id, "status": fu.status, "due_at": iso_utc(fu.due_at), "overdue": fu.is_overdue(), "version": fu.version}


def open_follow_up_exists():
    """SQL condition on SymptomRecord: it has an open follow-up (the 需追蹤 list of the review panel)."""
    return exists().where(CareFollowUp.symptom_record_id == SymptomRecord.id, CareFollowUp.status == FollowUpStatus.OPEN)


def any_follow_up_exists():
    """SQL condition on SymptomRecord: it has a follow-up item in any state."""
    return exists().where(CareFollowUp.symptom_record_id == SymptomRecord.id)


def legacy_follow_up_at(record):
    """A Phase 1a review date without any care follow-up yet (shown as "排定但尚未建立追蹤事項"), else None."""
    a = record.nursing_assessment
    if a is None or a.next_follow_up_at is None:
        return None
    has_item = db.session.execute(select(CareFollowUp.id).filter_by(symptom_record_id=record.id).limit(1)).scalar()
    return None if has_item else iso_utc(a.next_follow_up_at)


def events_payload(fu):
    rows = db.session.execute(select(CareFollowUpEvent).filter_by(follow_up_id=fu.id).order_by(CareFollowUpEvent.id)).scalars().all()
    return [{"event_type": e.event_type, "occurred_at": iso_utc(e.occurred_at), "actor": _person(e.actor), "old_due_at": iso_utc(e.old_due_at),
             "new_due_at": iso_utc(e.new_due_at), "reason": e.reason, "related_assessment_id": e.related_assessment_id,
             "related_follow_up_id": db.session.get(CareFollowUp, e.related_follow_up_id).public_id if e.related_follow_up_id else None}
            for e in rows]


# ------------------------------------------------------------------ write


def _event(fu, event_type, actor, *, old_due=None, new_due=None, reason=None, assessment_id=None, related=None):
    db.session.add(CareFollowUpEvent(follow_up_id=fu.id, event_type=event_type, actor_id=actor.id, occurred_at=utcnow(), old_due_at=old_due,
                                     new_due_at=new_due, reason=reason, related_assessment_id=assessment_id,
                                     related_follow_up_id=related.id if related else None))


def create_follow_up(record, nurse, due_at, reason, assessment, previous=None):
    """A new open follow-up of a reviewed, final record (caller commits). 409 when one is already open."""
    if record.record_status != RecordStatus.FINAL:
        raise APIError(422, "INVALID_STATE", "只有有效的症狀紀錄可以建立追蹤")
    if record.review_status != ReviewStatus.REVIEWED or assessment is None:
        raise APIError(409, "RECORD_NOT_REVIEWED", "請先完成審閱，再建立追蹤")
    current = db.session.execute(select(CareFollowUp).filter_by(symptom_record_id=record.id, status=FollowUpStatus.OPEN)).scalar_one_or_none()
    if current is not None:
        raise APIError(409, "FOLLOW_UP_EXISTS", "這筆症狀紀錄已有進行中的追蹤", [{"field": "symptom_record_id", "issue": "an open follow-up exists",
                                                                         "follow_up_id": current.public_id}])
    fu = CareFollowUp(patient_id=record.patient_id, symptom_record_id=record.id, review_assessment_id=assessment.id, due_at=due_at,
                      reason=reason, status=FollowUpStatus.OPEN, version=1, created_by=nurse.id, previous_follow_up_id=previous.id if previous else None)
    try:
        with db.session.begin_nested():  # a concurrent create of the same record hits the partial unique index
            db.session.add(fu)
            db.session.flush()
    except IntegrityError:
        raise APIError(409, "FOLLOW_UP_EXISTS", "這筆症狀紀錄已有進行中的追蹤") from None
    _event(fu, FollowUpEventType.CREATED, nurse, new_due=due_at, reason=reason, assessment_id=assessment.id, related=previous)
    record_data_event(AuditAction.CREATE, "care_follow_ups", fu.public_id, patient=record.patient,
                      changes={"symptom_record_id": record.id, "due_at": iso_utc(due_at), "review_assessment_id": assessment.id,
                               **({"previous_follow_up_id": previous.public_id} if previous else {})})
    return fu


def _transition(fu, version, **values):
    """Conditional update: only an open follow-up at the version the nurse saw. Raises 409 otherwise."""
    now = utcnow()
    result = db.session.execute(
        update(CareFollowUp).where(CareFollowUp.id == fu.id, CareFollowUp.status == FollowUpStatus.OPEN, CareFollowUp.version == version)
        .values(version=CareFollowUp.version + 1, updated_at=now, **values).execution_options(synchronize_session=False))
    db.session.refresh(fu)
    if result.rowcount == 1:
        return now
    if fu.status != FollowUpStatus.OPEN:
        raise APIError(409, "FOLLOW_UP_CLOSED", "這筆追蹤已經結案或取消，不能再修改", [{"field": "status", "issue": fu.status}])
    raise APIError(409, "VERSION_CONFLICT", "這筆追蹤剛被其他人更新，請重新載入後再操作", [{"field": "version", "issue": f"current version is {fu.version}"}])


def reschedule(fu, nurse, body):
    errors = _Errors()
    due_at = parse_due_at(body.get("due_at"), fu.patient, errors)
    reason = text_field(body, "reason", errors, MAX_REASON)
    version = version_field(body, errors)
    errors.raise_if_any()
    old_due = fu.due_at
    _transition(fu, version, due_at=due_at)
    _event(fu, FollowUpEventType.RESCHEDULED, nurse, old_due=old_due, new_due=due_at, reason=reason)
    record_data_event(AuditAction.UPDATE, "care_follow_ups", fu.public_id, patient=fu.patient,
                      changes={"due_at": {"old": iso_utc(old_due), "new": iso_utc(due_at)}, "version": fu.version})
    return fu


def complete(fu, nurse, body):
    """Close with a traceable nursing record: a new signed assessment (result, actions); optionally the next
    follow-up (a new row linked to this one). One transaction: nothing is completed if any step fails."""
    errors = _Errors()
    result_text = text_field(body, "result", errors, MAX_NOTE)
    actions = text_field(body, "actions", errors, MAX_NOTE)
    version = version_field(body, errors)
    assessment_type = body.get("assessment_type", "phone_follow_up")
    if assessment_type not in COMPLETION_TYPES:
        errors.add("assessment_type", f"must be one of: {', '.join(COMPLETION_TYPES)}")
    nxt = body.get("next_follow_up")
    next_due = next_reason = None
    if nxt is not None:
        if not isinstance(nxt, dict):
            errors.add("next_follow_up", "must be an object {due_at, reason}")
        else:
            next_due = parse_due_at(nxt.get("due_at"), fu.patient, errors, "next_follow_up.due_at")
            next_reason = text_field(nxt, "reason", errors, MAX_REASON)
    errors.raise_if_any()
    if fu.status != FollowUpStatus.OPEN:
        raise APIError(409, "FOLLOW_UP_CLOSED", "這筆追蹤已經結案或取消，不能再修改", [{"field": "status", "issue": fu.status}])

    now = utcnow()
    zone = patient_zone(fu.patient.timezone)
    record = fu.record
    cycle = record.cycle
    assessment = NursingAssessment(
        patient_id=fu.patient_id, assessed_by=nurse.id, assessed_at=now, assessment_type=assessment_type,
        subjective=f"{COMPLETION_MARK}（預定 {to_local(fu.due_at, zone).strftime('%m/%d %H:%M')}）：{fu.reason}",
        assessment=result_text, plan=actions, next_follow_up_at=next_due,
        sign_status=SignStatus.SIGNED, signed_at=now, cycle=cycle,
        cycle_day=cycle.cycle_day_on(to_local(now, zone).date()) if cycle else None,
        source=ObservationSource.NURSE, record_status=RecordStatus.FINAL,
    )
    db.session.add(assessment)
    db.session.flush()
    _transition(fu, version, status=FollowUpStatus.COMPLETED, completed_by=nurse.id, completed_at=now, completion_assessment_id=assessment.id)
    _event(fu, FollowUpEventType.COMPLETED, nurse, old_due=fu.due_at, reason=result_text, assessment_id=assessment.id)
    record_data_event(AuditAction.CREATE, "nursing_assessments", assessment.id, patient=fu.patient,
                      changes={"care_follow_up_id": fu.public_id, "assessment_type": assessment_type, "sign_status": "signed"})
    record_data_event(AuditAction.UPDATE, "care_follow_ups", fu.public_id, patient=fu.patient,
                      changes={"status": {"old": FollowUpStatus.OPEN, "new": FollowUpStatus.COMPLETED}, "completion_assessment_id": assessment.id})
    nxt_fu = create_follow_up(record, nurse, next_due, next_reason, assessment, previous=fu) if next_due else None
    return fu, nxt_fu


def cancel(fu, nurse, body):
    errors = _Errors()
    reason = text_field(body, "reason", errors, MAX_REASON)
    version = version_field(body, errors)
    errors.raise_if_any()
    now = _transition(fu, version, status=FollowUpStatus.CANCELLED, cancelled_by=nurse.id, cancelled_at=utcnow(), cancellation_reason=reason)
    _event(fu, FollowUpEventType.CANCELLED, nurse, old_due=fu.due_at, reason=reason)
    record_data_event(AuditAction.UPDATE, "care_follow_ups", fu.public_id, patient=fu.patient,
                      changes={"status": {"old": FollowUpStatus.OPEN, "new": FollowUpStatus.CANCELLED}, "at": iso_utc(now)})
    return fu


# ------------------------------------------------------------------ read


def find(public_id):
    if not isinstance(public_id, str) or len(public_id) != 36:
        return None
    return db.session.execute(select(CareFollowUp).filter_by(public_id=public_id)).scalar_one_or_none()


def list_for_nurse(nurse, *, status, patient=None, sort="due_at", page=1, per_page=20):
    """Follow-ups of the patients currently assigned to this nurse (an ended assignment drops them at once)."""
    assigned = select(NursePatientAssignment.patient_id).where(NursePatientAssignment.nurse_id == nurse.id, NursePatientAssignment.ended_at.is_(None))
    base = [CareFollowUp.patient_id.in_(assigned), CareFollowUp.patient.has(PatientProfile.deleted_at.is_(None))]
    if patient is not None:
        base.append(CareFollowUp.patient_id == patient.id)
    now = utcnow()
    by_status = {
        "open": [CareFollowUp.status == FollowUpStatus.OPEN],
        "overdue": [CareFollowUp.status == FollowUpStatus.OPEN, CareFollowUp.due_at < now],
        "completed": [CareFollowUp.status == FollowUpStatus.COMPLETED],
        "cancelled": [CareFollowUp.status == FollowUpStatus.CANCELLED],
        "all": [],
    }
    filters = base + by_status[status]
    total = db.session.execute(select(func.count()).select_from(CareFollowUp).where(*filters)).scalar()
    rows = db.session.execute(select(CareFollowUp).where(*filters).order_by(*SORTS[sort])
                              .offset((page - 1) * per_page).limit(per_page)).scalars().all()
    counts = {k: db.session.execute(select(func.count()).select_from(CareFollowUp).where(*base, *v)).scalar() for k, v in by_status.items()}
    return [follow_up_payload(fu, now) for fu in rows], {"page": page, "per_page": per_page, "total": total, "counts": counts}


def list_for_patient(patient):
    rows = db.session.execute(select(CareFollowUp).filter_by(patient_id=patient.id).order_by(CareFollowUp.due_at.desc(), CareFollowUp.id.desc())).scalars().all()
    return [patient_payload(fu) for fu in rows]
