"""Treatment schedule (api-design.md §5, database-design.md §6.F): appointments and their
preparation steps.

- Access control: an appointment belongs to a patient (existing rule, 404 otherwise); the
  assigned nurse writes.
- Status: scheduled → checked_in → completed; scheduled / checked_in → cancelled.
  Rescheduling never edits the time of an appointment: it creates a new one
  (``rescheduled_from_id`` → original) and the original becomes ``rescheduled``, so the
  history of every move is kept. Callers commit.
"""

from datetime import datetime, time, timedelta, timezone

from sqlalchemy import select

from app.core.api import APIError
from app.core.timeutil import iso_utc, local_day_bounds_utc, patient_zone, to_local
from app.extensions import db
from app.models import Appointment, AppointmentInstruction, ChemotherapyCycle, NursePatientAssignment, PatientProfile
from app.models.base import utcnow
from app.models.enums import AppointmentStatus as S, AppointmentType, InstructionType
from app.modules.chemotherapy.services import Fields, _invalid, _person, is_staff, local_today

MAX_AHEAD = timedelta(days=366)
MAX_BACKDATE = timedelta(days=30)
OPEN = (S.SCHEDULED, S.CHECKED_IN)
HIDDEN_TODAY = (S.CANCELLED, S.RESCHEDULED)  # not shown in 今日行程
FIELDS = ("appointment_type", "title", "duration_min", "location", "notes", "cycle_id", "instructions")


def _conflict(message):
    raise APIError(409, "INVALID_STATE", message)


# ------------------------------------------------------------------ serialize


def instruction_payload(i):
    return {"id": i.id, "instruction_type": i.instruction_type, "due_at": iso_utc(i.due_at), "text": i.text,
            "is_highlighted": bool(i.is_highlighted)}


def appointment_payload(a, viewer):
    data = {
        "id": a.id, "patient_id": a.patient.public_id, "cycle_id": a.cycle_id,
        "cycle_number": a.cycle.cycle_number if a.cycle else None,
        "appointment_type": a.appointment_type, "title": a.title, "scheduled_at": iso_utc(a.scheduled_at),
        "duration_min": a.duration_min, "location": a.location, "status": a.status,
        "rescheduled_from_id": a.rescheduled_from_id,
        "rescheduled_to_id": a.rescheduled_to[0].id if a.rescheduled_to else None,
        "instructions": [instruction_payload(i) for i in a.instructions],
    }
    if is_staff(viewer):
        data.update({"notes": a.notes, "created_by": _person(a.creator), "created_at": iso_utc(a.created_at)})
    return data


def get_appointment(appointment_id):
    a = db.session.get(Appointment, appointment_id)
    if a is None or a.deleted_at is not None:
        raise APIError(404, "NOT_FOUND", "Appointment not found")
    return a


# ------------------------------------------------------------------ query


def _local_date(raw, field, details):
    from datetime import date
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        details.append({"field": field, "issue": "must be a date (YYYY-MM-DD)"})
        return None


def list_appointments(patient, args):
    """``date`` (one local day) or ``from`` / ``to`` (local dates, inclusive); ``status``;
    default: every appointment, soonest first."""
    details = []
    zone = patient_zone(patient.timezone)
    day = _local_date(args.get("date"), "date", details)
    start = _local_date(args.get("from"), "from", details)
    end = _local_date(args.get("to"), "to", details)
    status = args.get("status")
    if status and status not in S.ALL:
        details.append({"field": "status", "issue": f"must be one of: {', '.join(S.ALL)}"})
    if start and end and start > end:
        details.append({"field": "to", "issue": "must not be earlier than from"})
    if details:
        _invalid(details, "查詢條件有誤")
    conds = [Appointment.patient_id == patient.id, Appointment.deleted_at.is_(None)]
    if day:
        start = end = day
    if start:
        conds.append(Appointment.scheduled_at >= local_day_bounds_utc(start, zone)[0])
    if end:
        conds.append(Appointment.scheduled_at < local_day_bounds_utc(end, zone)[1])
    if status:
        conds.append(Appointment.status == status)
    return db.session.execute(select(Appointment).where(*conds).order_by(Appointment.scheduled_at, Appointment.id)).scalars().all()


def nurse_today(nurse):
    """today-appointments widget: today's appointments of the nurse's current patients
    (each patient's own local day), cancelled / rescheduled excluded."""
    patients = db.session.execute(
        select(PatientProfile).join(NursePatientAssignment, NursePatientAssignment.patient_id == PatientProfile.id)
        .where(NursePatientAssignment.nurse_id == nurse.id, NursePatientAssignment.ended_at.is_(None),
               PatientProfile.deleted_at.is_(None))
    ).scalars().all()
    items = []
    for p in patients:
        start, end = local_day_bounds_utc(local_today(p), patient_zone(p.timezone))
        for a in db.session.execute(
            select(Appointment).where(Appointment.patient_id == p.id, Appointment.deleted_at.is_(None),
                                      Appointment.scheduled_at >= start, Appointment.scheduled_at < end,
                                      Appointment.status.notin_(HIDDEN_TODAY))
        ).scalars():
            items.append({"id": a.id, "patient_id": p.public_id, "patient_code": p.patient_code, "display_name": p.display_name,
                          "appointment_type": a.appointment_type, "title": a.title, "scheduled_at": iso_utc(a.scheduled_at),
                          "location": a.location, "status": a.status})
    items.sort(key=lambda i: (i["scheduled_at"], i["id"]))
    return items


# ------------------------------------------------------------------ parse


def _datetime(raw, field, details, *, required=True):
    if raw is None:
        if required:
            details.append({"field": field, "issue": "is required (ISO 8601 datetime with timezone)"})
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00")) if isinstance(raw, str) else None
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        details.append({"field": field, "issue": "must be an ISO 8601 datetime with timezone"})
        return None
    at = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return at.replace(microsecond=at.microsecond // 1000 * 1000)


def _scheduled_at(raw, details, field="scheduled_at"):
    at = _datetime(raw, field, details)
    now = utcnow()
    if at is not None and not now - MAX_BACKDATE <= at <= now + MAX_AHEAD:
        details.append({"field": field, "issue": "must be within 30 days ago and one year ahead"})
    return at


def _instructions(raw, details):
    if not isinstance(raw, list) or len(raw) > 10:
        details.append({"field": "instructions", "issue": "must be a list of at most 10 items"})
        return []
    rows = []
    for n, item in enumerate(raw):
        at = f"instructions[{n}]"
        if not isinstance(item, dict):
            details.append({"field": at, "issue": "must be an object"})
            continue
        if item.get("instruction_type") not in InstructionType.ALL:
            details.append({"field": f"{at}.instruction_type", "issue": f"must be one of: {', '.join(InstructionType.ALL)}"})
        text = item.get("text")
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > 255:
            details.append({"field": f"{at}.text", "issue": "is required (at most 255 characters)"})
        due = _datetime(item.get("due_at"), f"{at}.due_at", details, required=False)
        highlighted = item.get("is_highlighted", True)
        if not isinstance(highlighted, bool):
            details.append({"field": f"{at}.is_highlighted", "issue": "must be true or false"})
        unknown = set(item) - {"instruction_type", "text", "due_at", "is_highlighted"}
        details += [{"field": f"{at}.{k}", "issue": "is not a supported field"} for k in sorted(unknown)]
        rows.append({"instruction_type": item.get("instruction_type"), "text": (text or "").strip(), "due_at": due,
                     "is_highlighted": highlighted is True, "display_order": n + 1})
    return rows


def _values(patient, body, partial):
    f = Fields(body, "行程資料有誤")
    if not partial or f.has("appointment_type"):
        f.choice("appointment_type", AppointmentType.ALL, required=True)
    if not partial or f.has("title"):
        f.text("title", 100, required=True)
    f.integer("duration_min", 1, 1440)
    if f.has("location"):
        f.text("location", 100)
    if f.has("notes"):
        f.text("notes", 2000)
    instructions = _instructions(body["instructions"], f.details) if "instructions" in body else None
    if "cycle_id" in body and body["cycle_id"] is not None:
        c = db.session.get(ChemotherapyCycle, body["cycle_id"]) if isinstance(body["cycle_id"], int) else None
        if c is None or c.patient_id != patient.id or c.deleted_at is not None:
            f.error("cycle_id", "must be one of this patient's cycles")
        else:
            f.values["cycle_id"] = c.id
    elif "cycle_id" in body:
        f.values["cycle_id"] = None
    return f, instructions


def _set_instructions(appointment, rows):
    appointment.instructions = [AppointmentInstruction(**r) for r in rows]


# ------------------------------------------------------------------ write


def create_appointment(patient, user, body):
    f, instructions = _values(patient, body, partial=False)
    at = _scheduled_at(body.get("scheduled_at"), f.details)
    f.unknown(("patient_id", "scheduled_at", *FIELDS))
    values = f.done()
    a = Appointment(patient=patient, scheduled_at=at, status=S.SCHEDULED, created_by=user.id, **values)
    _set_instructions(a, instructions or [])
    db.session.add(a)
    db.session.flush()
    return a


def update_appointment(a, body):
    if a.status not in OPEN:
        _conflict("已結束、取消或改期的行程不能修改")
    f, instructions = _values(a.patient, body, partial=True)
    if "scheduled_at" in body:
        f.error("scheduled_at", "use reschedule (the original time is kept in the history)")
    f.unknown(FIELDS + ("scheduled_at",))
    values = f.done()
    changed = [k for k, v in values.items() if getattr(a, k) != v]
    for k in changed:
        setattr(a, k, values[k])
    if instructions is not None:
        _set_instructions(a, instructions)
        changed.append("instructions")
    return changed


def _local_day_of(a):
    return to_local(a.scheduled_at, patient_zone(a.patient.timezone)).date()


def check_in(a):
    if a.status != S.SCHEDULED:
        _conflict("只有尚未報到的行程可以報到")
    if _local_day_of(a) > local_today(a.patient):
        _conflict("行程還沒到，不能報到")
    a.status = S.CHECKED_IN


def complete(a):
    if a.status not in OPEN:
        _conflict("這個行程已結束、取消或改期")
    if _local_day_of(a) > local_today(a.patient):
        _conflict("行程還沒到，不能完成")
    a.status = S.COMPLETED


def cancel(a, body):
    if a.status not in OPEN:
        _conflict("這個行程已結束、取消或改期")
    f = Fields(body, "取消資料有誤")
    f.text("reason", 500, required=True)
    f.unknown(("reason",))
    reason = f.done()["reason"]
    a.status = S.CANCELLED
    return reason


def reschedule(a, user, body):
    """New appointment at ``scheduled_at`` with the same details; preparation steps move by the
    same amount of time. The original becomes ``rescheduled`` and points to the new one."""
    if a.status != S.SCHEDULED:
        _conflict("只有尚未報到的行程可以改期")
    f = Fields(body, "改期資料有誤")
    at = _scheduled_at(body.get("scheduled_at"), f.details)
    f.text("reason", 500, required=True)
    f.unknown(("scheduled_at", "reason"))
    values = f.done()
    if at == a.scheduled_at:
        _invalid([{"field": "scheduled_at", "issue": "must differ from the current time"}], "改期資料有誤")
    return _move(a, user, at), values["reason"]


def _move(a, user, at):
    delta = at - a.scheduled_at
    steps = [AppointmentInstruction(instruction_type=i.instruction_type, text=i.text, is_highlighted=i.is_highlighted,
                                    display_order=i.display_order, due_at=i.due_at + delta if i.due_at else None)
             for i in a.instructions]
    with db.session.no_autoflush:
        # relationships (not only ids) so the loaded collections (cycle.appointments, a.rescheduled_to) stay current
        new = Appointment(patient=a.patient, cycle=a.cycle, appointment_type=a.appointment_type, title=a.title,
                          scheduled_at=at, duration_min=a.duration_min, location=a.location, status=S.SCHEDULED,
                          notes=a.notes, created_by=user.id, instructions=steps)
        db.session.add(new)
        new.rescheduled_from = a
        a.status = S.RESCHEDULED
    db.session.flush()
    return new


# ------------------------------------------------------------------ chemotherapy integration


def infusion_options(raw):
    """``generate_infusion_appointments: {enabled, time: "HH:MM", location?}`` → (time, location) or None."""
    if raw is None or raw is False:
        return None
    details = []
    if not isinstance(raw, dict):
        _invalid([{"field": "generate_infusion_appointments", "issue": "must be an object {enabled, time, location}"}], "療程資料有誤")
    if raw.get("enabled", True) is not True:
        return None
    try:
        t = time.fromisoformat(raw.get("time")) if isinstance(raw.get("time"), str) and len(raw["time"]) == 5 else None
    except ValueError:
        t = None
    if t is None:
        details.append({"field": "generate_infusion_appointments.time", "issue": "must be HH:MM (patient's local time)"})
    location = raw.get("location")
    if location is not None and (not isinstance(location, str) or len(location) > 100):
        details.append({"field": "generate_infusion_appointments.location", "issue": "must be text of at most 100 characters"})
    unknown = set(raw) - {"enabled", "time", "location"}
    details += [{"field": f"generate_infusion_appointments.{k}", "issue": "is not a supported field"} for k in sorted(unknown)]
    if details:
        _invalid(details, "療程資料有誤")
    return t, (location or "").strip() or None


def create_infusions(plan, user, options):
    """One ``chemo_infusion`` appointment per cycle at the cycle's scheduled date + local time."""
    t, location = options
    zone = patient_zone(plan.patient.timezone)
    rows = []
    for c in plan.cycles:
        local = datetime.combine(c.scheduled_date, t, tzinfo=zone)
        a = Appointment(patient=plan.patient, cycle=c, appointment_type=AppointmentType.CHEMO_INFUSION,
                        title=f"第 {c.cycle_number} 次化療注射", scheduled_at=local.astimezone(timezone.utc).replace(tzinfo=None),
                        location=location, status=S.SCHEDULED, created_by=user.id)
        db.session.add(a)
        rows.append(a)
    db.session.flush()
    return rows


def move_cycle_appointments(cycle, user, days):
    """Cycle delayed by ``days``: every not-yet-attended appointment of the cycle is rescheduled
    by the same number of days (new rows; originals ``rescheduled``). Returns [(old, new)]."""
    moved = []
    for a in list(cycle.appointments):
        if a.deleted_at is None and a.status == S.SCHEDULED:
            moved.append((a, _move(a, user, a.scheduled_at + timedelta(days=days))))
    return moved


def cycle_appointment_id(cycle):
    """The cycle's current infusion appointment (for the cycle payload), if any."""
    rows = [a for a in cycle.appointments if a.deleted_at is None and a.appointment_type == AppointmentType.CHEMO_INFUSION
            and a.status not in HIDDEN_TODAY]
    return min(rows, key=lambda a: a.scheduled_at).id if rows else None
