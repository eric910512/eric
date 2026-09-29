"""Notification listing, detail and the clinical handling lifecycle (api-design.md §10).

Risk alerts and reminders (manual / scheduled, Sprint 8) move through
``new → acknowledged → in_progress → resolved``. Every copy of one
event (patient + each nurse, sharing ``event_key``) carries the same status, so a transition
is applied to all copies at once.

Visibility:
- patient: only their own copies; no staff names or internal notes
- nurse:   events of currently assigned patients (one row per event)
- admin:   every patient event
"""

from datetime import timedelta

from sqlalchemy import String, case, cast, func, literal, select

from app.core.api import APIError
from app.core.timeutil import iso_utc, patient_zone, to_local, to_ms
from app.extensions import db
from app.models import LabResult, Notification, NursePatientAssignment, PatientProfile, SymptomRecord, VitalSign
from app.models.base import utcnow
from app.models.enums import NotificationStatus as S
from app.models.enums import NotificationType, RoleName
from app.services.alert_engine import describe_trigger
from app.services.treatment import active_plan_and_cycle, cycle_nadir

STAFF_STATUS_TEXT = {S.NEW: "待處理", S.ACKNOWLEDGED: "已接手", S.IN_PROGRESS: "處理中", S.RESOLVED: "已完成"}
PATIENT_STATUS_TEXT = {
    S.NEW: "護理團隊已收到通知",
    S.ACKNOWLEDGED: "護理師已接手",
    S.IN_PROGRESS: "護理師正在處理",
    S.RESOLVED: "已處理完成",
}

# action → (allowed current statuses, new status)
TRANSITIONS = {
    "acknowledge": ((S.NEW,), S.ACKNOWLEDGED),
    "start": ((S.ACKNOWLEDGED,), S.IN_PROGRESS),
    "resolve": ((S.IN_PROGRESS,), S.RESOLVED),
}
ACTION_TEXT = {"acknowledge": "接手", "start": "開始處理", "resolve": "完成處理"}

# status filter values accepted by GET /notifications: a single status, "pending" (new or
# acknowledged: not yet being worked on), "open" (not resolved; "unresolved" kept for older clients)
PENDING = (S.NEW, S.ACKNOWLEDGED)
STATUS_FILTERS = ("all", "open", "unresolved", "pending", *S.ALL)


# notification types that follow the handling lifecycle (staff side)
LIFECYCLE_TYPES = (NotificationType.RISK_ALERT, NotificationType.REMINDER)


def origin(n):
    """Where a notification came from (derived; no column): ``alert_rule`` (a risk rule),
    ``scheduled`` (sent at a set time, or generated from a record such as an appointment) or
    ``manual`` (written by staff and sent right away)."""
    if n.alert_rule_id is not None or n.type == NotificationType.RISK_ALERT:
        return "alert_rule"
    if n.scheduled_for is not None or n.source_table is not None:
        return "scheduled"
    return "manual"


def _has_lifecycle(n, viewer):
    """Staff see the handling of alerts and reminders; patients only of their alerts."""
    return n.type == NotificationType.RISK_ALERT or (n.type in LIFECYCLE_TYPES and is_staff(viewer))


def is_staff(user):
    return user.role_name in (RoleName.NURSE, RoleName.ADMIN)


def _visible(now):
    """Reminders scheduled for the future stay hidden until their time."""
    return (Notification.scheduled_for.is_(None)) | (Notification.scheduled_for <= now)


def _person(user):
    return {"id": user.public_id, "display_name": user.display_name} if user else None


# ------------------------------------------------------------------ payloads


def _handling(n, *, staff):
    """Who did what, and when. Patients only see the times, never names or notes."""
    steps = {
        "acknowledged": (n.acknowledged_at, n.acknowledger),
        "started": (n.started_at, n.starter),
        "resolved": (n.resolved_at, n.resolver),
    }
    data = {
        key: ({"at": iso_utc(at), "by": _person(by)} if staff else {"at": iso_utc(at)}) if at else None
        for key, (at, by) in steps.items()
    }
    if staff:
        data["resolution_note"] = n.resolution_note
    return data


def notification_payload(n, viewer):
    staff = is_staff(viewer)
    status_text = (STAFF_STATUS_TEXT if staff else PATIENT_STATUS_TEXT).get(n.status)
    lifecycle = _has_lifecycle(n, viewer)
    data = {
        "id": n.id,
        "event_key": n.event_key,
        "type": n.type,
        "origin": origin(n),
        "severity": n.severity,
        "title": n.title,
        "message": n.message,
        "patient": (
            {"id": n.patient.public_id, "patient_code": n.patient.patient_code, "display_name": n.patient.display_name}
            if n.patient else None
        ),
        "alert_rule": {"id": n.alert_rule.id, "code": n.alert_rule.code} if n.alert_rule else None,
        "source": {"table": n.source_table, "id": n.source_id} if n.source_table else None,
        "status": n.status if lifecycle else None,
        "status_text": status_text if lifecycle else None,
        "handling": _handling(n, staff=staff) if lifecycle else None,
        "is_mine": n.recipient_id == viewer.id,
        "is_read": bool(n.is_read) if n.recipient_id == viewer.id else None,
        "read_at": iso_utc(n.read_at) if n.recipient_id == viewer.id else None,
        "scheduled_for": iso_utc(n.scheduled_for),
        "created_at": iso_utc(n.created_at),
    }
    if staff:
        # Deprecated (pre-lifecycle clients): the resolution, formerly called "acknowledged".
        data["acknowledged"] = (
            {"by": _person(n.resolver), "at": iso_utc(n.resolved_at), "resolution_note": n.resolution_note}
            if n.status == S.RESOLVED else None
        )
    return data


# ------------------------------------------------------------------ listing


def _staff_scope(user):
    """Row filter for the patients a staff member may see."""
    if user.role_name == RoleName.ADMIN:
        return Notification.patient_id.is_not(None)
    assigned = select(NursePatientAssignment.patient_id).where(
        NursePatientAssignment.nurse_id == user.id, NursePatientAssignment.ended_at.is_(None)
    )
    return Notification.patient_id.in_(assigned)


def _status_filter(status, type_=None):
    """Status filters apply to lifecycle notifications of ``type_`` (default: risk alerts)."""
    kind = Notification.type == (type_ if type_ in LIFECYCLE_TYPES else NotificationType.RISK_ALERT)
    if status in ("open", "unresolved"):
        return [kind, Notification.status.in_(S.OPEN)]
    if status == "pending":
        return [kind, Notification.status.in_(PENDING)]
    if status in S.ALL:
        return [kind, Notification.status == status]
    return []


def _event_representatives(user, filters):
    """One row per event for staff: the viewer's own copy, else a staff copy, else the patient's."""
    group = func.coalesce(Notification.event_key, literal("id:") + cast(Notification.id, String))
    preference = case(
        (Notification.recipient_id == user.id, 0),
        (Notification.recipient_id == PatientProfile.user_id, 2),
        else_=1,
    )
    ranked = (
        select(
            Notification.id.label("id"),
            func.row_number().over(partition_by=group, order_by=(preference, Notification.id)).label("rn"),
        )
        .join(PatientProfile, PatientProfile.id == Notification.patient_id)
        .where(*filters)
        .subquery()
    )
    return select(ranked.c.id).where(ranked.c.rn == 1)


def list_notifications(user, *, status, type_, severity, patient_public_id, page, per_page, is_read=None):
    now = utcnow()
    staff = is_staff(user)
    if staff:
        base = [_staff_scope(user), _visible(now), Notification.type == (type_ or NotificationType.RISK_ALERT)]
    else:
        base = [Notification.recipient_id == user.id, _visible(now)]
        if type_:
            base.append(Notification.type == type_)
    scoped = list(base)
    if patient_public_id:
        scoped.append(Notification.patient.has(PatientProfile.public_id == patient_public_id))
    filters = scoped + _status_filter(status, type_)
    if severity:
        filters.append(Notification.severity == severity)
    if is_read is not None:
        filters += [Notification.recipient_id == user.id,
                    Notification.is_read.is_(True) if is_read else Notification.is_read.is_not(True)]

    def ids(conds):
        return _event_representatives(user, conds) if staff else select(Notification.id).where(*conds)

    def count(conds):
        return db.session.execute(select(func.count()).select_from(ids(conds).subquery())).scalar()

    order = (
        (Notification.resolved_at.desc(), Notification.created_at.desc())
        if status == S.RESOLVED else (Notification.created_at.desc(),)
    )
    items = db.session.execute(
        select(Notification).where(Notification.id.in_(ids(filters)))
        .order_by(*order, Notification.id.desc())
        .offset((page - 1) * per_page).limit(per_page)
    ).scalars().all()

    # counts per status within the same scope / patient / severity filters (for tabs and badges)
    tab_filters = scoped + ([Notification.severity == severity] if severity else [])
    counts = {s: count(tab_filters + _status_filter(s, type_)) for s in S.ALL}
    counts["open"] = sum(counts[s] for s in S.OPEN)
    counts["pending"] = sum(counts[s] for s in PENDING)
    meta = {
        "page": page,
        "per_page": per_page,
        "total": count(filters),
        "counts": counts,
        "unresolved": counts["open"],
        "unread": db.session.execute(
            select(func.count()).select_from(Notification)
            .where(Notification.recipient_id == user.id, _visible(now), Notification.is_read.is_not(True))
        ).scalar(),
    }
    return items, meta


def _own_unread(user, now):
    return [Notification.recipient_id == user.id, _visible(now), Notification.is_read.is_not(True)]


def unread_count(user):
    """The signed-in user's own unread notifications that are visible now (scheduled ones excluded)."""
    return db.session.execute(select(func.count()).select_from(Notification).where(*_own_unread(user, utcnow()))).scalar()


def read_all(user):
    """Mark every own visible notification read. Returns the ids that changed."""
    now = utcnow()
    rows = db.session.execute(select(Notification).where(*_own_unread(user, now))).scalars().all()
    for n in rows:
        n.is_read = True
        n.read_at = now
    return [n.id for n in rows]


# ------------------------------------------------------------------ access


def get_own_notification(user, notification_id):
    """The signed-in user's own copy; anyone else's is reported as not found."""
    n = db.session.get(Notification, notification_id)
    if n is None or n.recipient_id != user.id or (n.scheduled_for and n.scheduled_for > utcnow()):
        raise APIError(404, "NOT_FOUND", "Notification not found")
    return n


def get_visible_notification(user, notification_id):
    """Patients: own copies. Nurses: any copy for a currently assigned patient. Admin: any
    patient notification. Out of scope → 404."""
    if not is_staff(user):
        return get_own_notification(user, notification_id)
    n = db.session.get(Notification, notification_id)
    in_scope = (
        n is not None
        and n.patient_id is not None
        and not (n.scheduled_for and n.scheduled_for > utcnow())
        and db.session.execute(select(Notification.id).where(Notification.id == n.id, _staff_scope(user))).first()
    )
    if not in_scope:
        raise APIError(404, "NOT_FOUND", "Notification not found")
    return n


def mark_read(n):
    """Returns True when the state changed."""
    if n.is_read:
        return False
    n.is_read = True
    n.read_at = utcnow()
    return True


# ------------------------------------------------------------------ lifecycle


def _event_rows(n):
    if not n.event_key:
        return [n]
    return db.session.execute(select(Notification).where(Notification.event_key == n.event_key)).scalars().all()


def _require_lifecycle(n):
    if n.type not in LIFECYCLE_TYPES:
        raise APIError(422, "INVALID_STATE", "只有風險警示與提醒需要處理")


def _require_alert(n):
    """Quick resolve (``PATCH /resolve`` and the record panels) stays for risk alerts only."""
    if n.type != NotificationType.RISK_ALERT:
        raise APIError(422, "INVALID_STATE", "只有風險警示需要處理")


def transition(n, user, action, note=None):
    """Apply one lifecycle step to every copy of the event (risk alert or reminder). Caller
    writes audit and commits.

    Returns (old_status, rows).
    """
    _require_lifecycle(n)
    allowed, target = TRANSITIONS[action]
    old = n.status
    if old not in allowed:
        raise APIError(
            409, "INVALID_TRANSITION",
            f"這則警示目前是「{STAFF_STATUS_TEXT.get(old, old)}」，不能{ACTION_TEXT[action]}",
            [{"field": "status", "issue": f"current status is '{old}'; '{action}' requires {' or '.join(allowed)}"}],
        )
    now = _after_previous_steps(n, utcnow())
    rows = _event_rows(n)
    for row in rows:
        row.status = target
        if action == "acknowledge":
            row.acknowledged_by, row.acknowledged_at = user.id, now
        elif action == "start":
            row.started_by, row.started_at = user.id, now
        else:
            row.resolved_by, row.resolved_at = user.id, now
            row.resolution_note = note
    if n.recipient_id == user.id:
        mark_read(n)
    return old, rows


STEP_GAP = timedelta(milliseconds=1)


def _after_previous_steps(n, now):
    """Time for a new handling step: strictly later (≥ 1 ms, the stored / serialized precision)
    than any step already taken, even when the system clock has not advanced (Windows clock
    resolution, several steps within one second). Steps sharing one timestamp therefore always
    mean "done in one operation" (quick resolve), which the timeline shows as one step.
    """
    taken = [to_ms(t) for t in (n.acknowledged_at, n.started_at) if t is not None]
    latest = max(taken, default=None)
    return latest + STEP_GAP if latest is not None and now <= latest else now


def _fast_forward(row, user, note, now):
    """Close an open alert in one step (existing quick flows): fill any step not yet taken."""
    now = _after_previous_steps(row, now)
    if row.acknowledged_at is None:
        row.acknowledged_by, row.acknowledged_at = user.id, now
    if row.started_at is None:
        row.started_by, row.started_at = user.id, now
    row.resolved_by, row.resolved_at = user.id, now
    row.resolution_note = note
    row.status = S.RESOLVED


def resolve_event(n, nurse, note):
    """Quick resolve from any open status (PATCH /resolve, vital / lab panels): every open copy
    of the event is closed with the same note. Returns the rows updated. Caller commits.
    """
    _require_alert(n)
    if n.status == S.RESOLVED:
        by = n.resolver.display_name if n.resolver else "其他人員"
        raise APIError(422, "INVALID_STATE", f"這則警示已由{by}處理")
    now = utcnow()
    related = [row for row in _event_rows(n) if row.status != S.RESOLVED]
    for row in related:
        _fast_forward(row, nurse, note, now)
    if n.recipient_id == nurse.id:
        mark_read(n)
    return related


def resolve_source_alerts(source_table, source_id, nurse, note):
    """Close all open risk alerts raised by one source record (symptom review)."""
    rows = db.session.execute(
        select(Notification).where(
            Notification.source_table == source_table,
            Notification.source_id == source_id,
            Notification.type == NotificationType.RISK_ALERT,
            Notification.status.in_(S.OPEN),
        )
    ).scalars().all()
    now = utcnow()
    for row in rows:
        _fast_forward(row, nurse, note, now)
        if row.recipient_id == nurse.id:
            mark_read(row)
    return rows


# ------------------------------------------------------------------ detail


def _source_record(n):
    model = {"symptom_records": SymptomRecord, "vital_signs": VitalSign, "lab_results": LabResult}.get(n.source_table)
    return db.session.get(model, n.source_id) if model and n.source_id else None


def _source_payload(n, record):
    # local imports: these modules import this one (symptom review) or the alert engine
    if n.source_table == "symptom_records":
        from app.modules.symptom.services import serialize_value

        return {
            "table": n.source_table, "id": record.id, "recorded_at": iso_utc(record.recorded_at),
            "cycle_day": record.cycle_day, "source": record.source, "review_status": record.review_status,
            "notes": record.notes, "values": [serialize_value(v) for v in record.values],
        }
    if n.source_table == "vital_signs":
        from app.modules.vital_signs.services import _values

        return {
            "table": n.source_table, "id": record.id, "measured_at": iso_utc(record.measured_at),
            "cycle_day": record.cycle_day, "source": record.source, "values": _values(record),
        }
    if n.source_table == "lab_results":
        from app.modules.labs.services import result_payload

        return {"table": n.source_table, **result_payload(record, include_alerts=False)}
    return None


def _patient_context(patient):
    if patient is None:
        return None
    _, cycle = active_plan_and_cycle(patient)
    today = to_local(utcnow(), patient_zone(patient.timezone)).date()
    return {
        "id": patient.public_id,
        "patient_code": patient.patient_code,
        "display_name": patient.display_name,
        "diagnoses": [d.cancer_type.name_zh for d in patient.diagnoses if d.deleted_at is None and d.cancer_type],
        "care_alerts": [
            {"alert_type": a.alert_type, "description": a.description}
            for a in patient.active_care_alerts
        ],
        "current_cycle": (
            {"cycle_number": cycle.cycle_number, "cycle_day": cycle.cycle_day_on(today), "in_nadir": cycle_nadir(cycle, today)[0]}
            if cycle else None
        ),
    }


def notification_detail(n, viewer):
    """Full detail for staff; for the patient only the reminder and its handling status."""
    data = notification_payload(n, viewer)
    if not is_staff(viewer):
        return data
    record = _source_record(n)
    rule = n.alert_rule
    data.update({
        "patient": _patient_context(n.patient),
        "trigger": describe_trigger(rule, record) if rule else None,
        "source_record": _source_payload(n, record) if record else None,
        "recommended_action": rule.recommended_action if rule else None,
        "recipients": len(_event_rows(n)),
        "allowed_actions": [a for a, (allowed, _) in TRANSITIONS.items() if n.status in allowed]
        if n.type in LIFECYCLE_TYPES else [],
    })
    return data
