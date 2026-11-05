"""病人與護理師聊天室 — conversation rules (Phase 1, text only).

- One conversation per patient. The patient and the nurses currently assigned to them read and write
  it; the route layer checks access with ``resolve_patient`` + ``ensure_can_view_patient`` (admin has
  no chat role in this version).
- Daily auto reply: the first message a patient sends on an Asia/Taipei day is followed by one system
  auto reply (``kind=auto_reply``, ``sender_role=system``). ``chat_messages.auto_reply_date`` is
  unique per patient, so a second auto reply for the same day cannot be stored even when two
  messages race; the losing insert is rolled back to its savepoint and the message itself is kept.
- Unread: each user keeps one read pointer per conversation (``chat_read_states``). A patient's
  unread = nurse messages after it; a nurse's unread = patient messages after it. Sending a message
  moves the sender's own pointer to it (you have read what you answered).
"""

from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

from flask import current_app
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError

from app.core.api import APIError
from app.core.timeutil import iso_utc
from app.extensions import db
from app.models import ChatMessage, ChatReadState, NursePatientAssignment, PatientProfile
from app.models.base import utcnow
from app.models.enums import ChatMessageKind, ChatSenderRole, RoleName

MAX_BODY_LENGTH = 1000
DEFAULT_PAGE = 30
MAX_PAGE = 100
PREVIEW_LENGTH = 60
# Flood guard per sender: at most this many messages in the window (429 RATE_LIMITED).
RATE_LIMIT_COUNT = 20
RATE_LIMIT_WINDOW = timedelta(seconds=60)
SYSTEM_LABEL = "系統自動回覆"


def _config():
    return current_app.config["CHAT_SERVICE"]


def _zone():
    return ZoneInfo(_config()["timezone"])


def local_now(now):
    """Naive UTC → aware Asia/Taipei."""
    return now.replace(tzinfo=timezone.utc).astimezone(_zone())


def in_service_hours(now):
    local = local_now(now)
    if local.weekday() >= 5:
        return False
    hm = local.strftime("%H:%M")
    return any(start <= hm < end for start, end in _config()["hours"])


def service_info(now=None):
    """What the chat header shows (fixed 義大醫院放射腫瘤科 information)."""
    cfg = _config()
    return {
        "name": cfg["name"],
        "hours_text": cfg["hours_text"],
        "phone": cfg["phone"],
        "extension": cfg["extension"],
        "phone_text": cfg["phone_text"],
        "in_service_hours": in_service_hours(now or utcnow()),
    }


# ------------------------------------------------------------------ payloads
def sender_label(message, viewer):
    if message.sender_role == ChatSenderRole.SYSTEM:
        return SYSTEM_LABEL
    if message.sender_role == ChatSenderRole.NURSE:
        # patients always see the team label, never an individual nurse's name
        if viewer.role_name == RoleName.PATIENT:
            return _config()["nurse_label"]
        return message.sender.display_name if message.sender else _config()["nurse_label"]
    return message.patient.display_name


def message_payload(message, viewer):
    return {
        "id": message.id,
        "sender_role": message.sender_role,
        "kind": message.kind,
        "body": message.body,
        "created_at": iso_utc(message.created_at),
        "is_mine": message.sender_id is not None and message.sender_id == viewer.id,
        "sender_label": sender_label(message, viewer),
    }


# ------------------------------------------------------------------ reading
def list_messages(patient, before_id=None, after_id=None, limit=DEFAULT_PAGE):
    """Oldest-first page. ``after_id``: messages newer than it (polling). Otherwise the newest
    ``limit`` messages (or those older than ``before_id``) and whether older ones exist."""
    query = select(ChatMessage).where(ChatMessage.patient_id == patient.id)
    if after_id is not None:
        rows = db.session.execute(
            query.where(ChatMessage.id > after_id).order_by(ChatMessage.id).limit(limit + 1)
        ).scalars().all()
        return rows[:limit], len(rows) > limit
    if before_id is not None:
        query = query.where(ChatMessage.id < before_id)
    rows = db.session.execute(query.order_by(ChatMessage.id.desc()).limit(limit + 1)).scalars().all()
    return list(reversed(rows[:limit])), len(rows) > limit


def read_pointer(patient_id, user_id):
    return db.session.execute(
        select(ChatReadState.last_read_message_id).filter_by(patient_id=patient_id, user_id=user_id)
    ).scalar_one_or_none() or 0


def _counterpart_role(viewer):
    """Whose messages count as unread for this viewer."""
    return ChatSenderRole.NURSE if viewer.role_name == RoleName.PATIENT else ChatSenderRole.PATIENT


def unread_count(patient, viewer):
    return db.session.execute(
        select(func.count(ChatMessage.id)).where(
            ChatMessage.patient_id == patient.id,
            ChatMessage.sender_role == _counterpart_role(viewer),
            ChatMessage.id > read_pointer(patient.id, viewer.id),
        )
    ).scalar_one()


def _latest_id(patient_id):
    return db.session.execute(
        select(func.max(ChatMessage.id)).where(ChatMessage.patient_id == patient_id)
    ).scalar_one() or 0


def _set_pointer(patient_id, user_id, message_id):
    """Move the read pointer forward (never back). Commits with the caller."""
    state = db.session.execute(
        select(ChatReadState).filter_by(patient_id=patient_id, user_id=user_id)
    ).scalar_one_or_none()
    if state is None:
        try:
            with db.session.begin_nested():
                db.session.add(ChatReadState(patient_id=patient_id, user_id=user_id, last_read_message_id=message_id))
            return message_id
        except IntegrityError:  # created by a concurrent request: fall through and update it
            state = db.session.execute(
                select(ChatReadState).filter_by(patient_id=patient_id, user_id=user_id)
            ).scalar_one()
    if message_id > state.last_read_message_id:
        state.last_read_message_id = message_id
    return state.last_read_message_id


def mark_read(patient, viewer, last_message_id):
    """Read up to ``last_message_id`` (capped at the newest message of this conversation)."""
    return _set_pointer(patient.id, viewer.id, min(last_message_id, _latest_id(patient.id)))


# ------------------------------------------------------------------ sending
def _check_rate(sender, now):
    recent = db.session.execute(
        select(func.count(ChatMessage.id)).where(
            ChatMessage.sender_id == sender.id, ChatMessage.created_at > now - RATE_LIMIT_WINDOW
        )
    ).scalar_one()
    if recent >= RATE_LIMIT_COUNT:
        raise APIError(429, "RATE_LIMITED", "訊息傳送太頻繁，請稍後再試")


def _auto_reply_exists(patient_id, day):
    return db.session.execute(
        select(ChatMessage.id).filter_by(patient_id=patient_id, auto_reply_date=day)
    ).first() is not None


def _add_daily_auto_reply(patient, now):
    """The day's auto reply, or None when it already exists (also when a concurrent request won)."""
    day = local_now(now).date()
    if _auto_reply_exists(patient.id, day):
        return None
    reply = ChatMessage(
        patient_id=patient.id,
        sender_id=None,
        sender_role=ChatSenderRole.SYSTEM,
        kind=ChatMessageKind.AUTO_REPLY,
        body=_config()["auto_reply_text"],
        auto_reply_date=day,
        created_at=now,
    )
    try:
        with db.session.begin_nested():
            db.session.add(reply)
        return reply
    except IntegrityError:  # uq_chat_messages_patient_id_auto_reply_date: another message got it first
        return None


def send_message(patient, sender, body):
    """Store one text message (and, for the patient's first message of the day, the auto reply).
    Adds to the session; the caller commits."""
    now = utcnow()
    _check_rate(sender, now)
    role = ChatSenderRole.PATIENT if sender.role_name == RoleName.PATIENT else ChatSenderRole.NURSE
    message = ChatMessage(
        patient_id=patient.id, sender_id=sender.id, sender_role=role, kind=ChatMessageKind.TEXT, body=body, created_at=now
    )
    db.session.add(message)
    db.session.flush()
    auto_reply = _add_daily_auto_reply(patient, now) if role == ChatSenderRole.PATIENT else None
    _set_pointer(patient.id, sender.id, (auto_reply or message).id)
    return message, auto_reply


# ------------------------------------------------------------------ nurse inbox / unread
def _assigned_patients(nurse):
    return db.session.execute(
        select(PatientProfile)
        .join(NursePatientAssignment, NursePatientAssignment.patient_id == PatientProfile.id)
        .where(
            NursePatientAssignment.nurse_id == nurse.id,
            NursePatientAssignment.ended_at.is_(None),
            PatientProfile.deleted_at.is_(None),
        )
        .distinct()
    ).scalars().all()


def _unread_by_patient(viewer, patient_ids):
    if not patient_ids:
        return {}
    rows = db.session.execute(
        select(ChatMessage.patient_id, func.count(ChatMessage.id))
        .outerjoin(
            ChatReadState,
            and_(ChatReadState.patient_id == ChatMessage.patient_id, ChatReadState.user_id == viewer.id),
        )
        .where(
            ChatMessage.patient_id.in_(patient_ids),
            ChatMessage.sender_role == _counterpart_role(viewer),
            ChatMessage.id > func.coalesce(ChatReadState.last_read_message_id, 0),
        )
        .group_by(ChatMessage.patient_id)
    ).all()
    return dict(rows)


def nurse_inbox(nurse):
    """Every currently assigned patient: last message, unread count. Unread first, then newest."""
    patients = _assigned_patients(nurse)
    ids = [p.id for p in patients]
    last_ids = dict(
        db.session.execute(
            select(ChatMessage.patient_id, func.max(ChatMessage.id))
            .where(ChatMessage.patient_id.in_(ids))
            .group_by(ChatMessage.patient_id)
        ).all()
    ) if ids else {}
    last = {
        m.patient_id: m
        for m in db.session.execute(select(ChatMessage).where(ChatMessage.id.in_(list(last_ids.values())))).scalars()
    } if last_ids else {}
    unread = _unread_by_patient(nurse, ids)

    def row(p):
        m = last.get(p.id)
        preview = None
        if m is not None:
            text = " ".join(m.body.split())
            preview = {
                "id": m.id,
                "sender_role": m.sender_role,
                "kind": m.kind,
                "body": text[:PREVIEW_LENGTH] + ("…" if len(text) > PREVIEW_LENGTH else ""),
                "created_at": iso_utc(m.created_at),
            }
        return {
            "patient": {"id": p.public_id, "display_name": p.display_name, "patient_code": p.patient_code},
            "last_message": preview,
            "unread_count": unread.get(p.id, 0),
        }

    rows = [row(p) for p in patients]
    rows.sort(key=lambda r: r["patient"]["patient_code"])
    rows.sort(key=lambda r: r["last_message"]["id"] if r["last_message"] else 0, reverse=True)
    rows.sort(key=lambda r: r["unread_count"] == 0)
    return rows


def total_unread(viewer):
    if viewer.role_name == RoleName.PATIENT:
        patient = viewer.patient_profile
        return {"unread_count": unread_count(patient, viewer) if patient else 0}
    counts = _unread_by_patient(viewer, [p.id for p in _assigned_patients(viewer)])
    return {"unread_count": sum(counts.values()), "patients_with_unread": sum(1 for n in counts.values() if n)}
