"""病人與護理師聊天室 API (Phase 1, text only).

Access: the patient (``me`` or their own id) and the nurses currently assigned to the patient —
``resolve_patient`` + ``ensure_can_view_patient``, out of scope → 404. Admin is not a chat role in
this version (403). Message bodies never go to the audit log or the application log.
"""

from flask import g, request

from app.core.api import APIError, ok, query_int
from app.core.audit import record_data_event, record_patient_view
from app.core.auth import ensure_can_view_patient, require_auth, resolve_patient
from app.core.idempotency import run_idempotent
from app.extensions import db
from app.models import ChatMessage
from app.models.enums import AuditAction, RoleName
from app.modules.chat import chat_bp, services

MAX_ID = 2**31 - 1


def _conversation(patient_id):
    patient = resolve_patient(patient_id)
    ensure_can_view_patient(patient)
    return patient


def _json_body():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise APIError(400, "VALIDATION_ERROR", "Request body must be a JSON object")
    return body


def _message_text(body):
    value = body.get("body")
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > services.MAX_BODY_LENGTH:
        raise APIError(400, "VALIDATION_ERROR", "訊息內容有誤",
                       [{"field": "body", "issue": f"is required (1–{services.MAX_BODY_LENGTH} characters)"}])
    return value.strip()


@chat_bp.get("/patients/<patient_id>/messages")
@require_auth(RoleName.PATIENT, RoleName.NURSE)
def list_messages(patient_id):
    """Oldest-first messages. Query: ``limit`` (1–100, default 30) and either ``before_id`` (older
    page; ``meta.has_more`` = even older exist) or ``after_id`` (newer than the last one shown —
    polling; ``meta.has_more`` = more new ones than ``limit``). ``meta`` also holds the service
    information, the viewer's read pointer and unread count. A nurse opening the conversation
    (no ``after_id``) is recorded as a VIEW."""
    patient = _conversation(patient_id)
    args = request.args
    before_id = query_int(args, "before_id", None, 1, MAX_ID)
    after_id = query_int(args, "after_id", None, 0, MAX_ID)
    if before_id is not None and after_id is not None:
        raise APIError(400, "VALIDATION_ERROR", "Invalid query parameter",
                       [{"field": "after_id", "issue": "cannot be combined with before_id"}])
    limit = query_int(args, "limit", services.DEFAULT_PAGE, 1, services.MAX_PAGE)
    user = g.current_user
    items, has_more = services.list_messages(patient, before_id=before_id, after_id=after_id, limit=limit)
    if user.role_name == RoleName.NURSE and after_id is None:
        record_patient_view(patient, "chat_messages")
        db.session.commit()
    return ok(
        [services.message_payload(m, user) for m in items],
        meta={
            "has_more": has_more,
            "service": services.service_info(),
            "last_read_message_id": services.read_pointer(patient.id, user.id),
            "unread_count": services.unread_count(patient, user),
        },
    )


@chat_bp.post("/patients/<patient_id>/messages")
@require_auth(RoleName.PATIENT, RoleName.NURSE)
def send_message(patient_id):
    """Send a text message ``{body}`` (1–1000 characters). ``Idempotency-Key`` is required: a retry
    with the same key returns the stored message instead of a second one. The patient's first
    message of the Asia/Taipei day is followed by the system auto reply (``data.auto_reply``)."""
    patient = _conversation(patient_id)
    body = _json_body()
    text = _message_text(body)
    user = g.current_user

    def create():
        message, auto_reply = services.send_message(patient, user, text)
        record_data_event(AuditAction.CREATE, "chat_messages", message.id, patient,
                          {"sender_role": message.sender_role, "length": len(text), "auto_reply": auto_reply is not None})
        return message.id, {"data": {
            "message": services.message_payload(message, user),
            "auto_reply": services.message_payload(auto_reply, user) if auto_reply else None,
        }}, 201

    def replay(resource_id):
        message = db.session.get(ChatMessage, int(resource_id))
        return {"data": {"message": services.message_payload(message, user), "auto_reply": None}}

    return run_idempotent(resource_type="chat_messages", body=body, create=create, replay=replay, required=True)


@chat_bp.post("/patients/<patient_id>/messages/read")
@require_auth(RoleName.PATIENT, RoleName.NURSE)
def mark_read(patient_id):
    """``{last_message_id}``: the viewer has read up to this message (the pointer never moves back)."""
    patient = _conversation(patient_id)
    raw = _json_body().get("last_message_id")
    if not isinstance(raw, int) or isinstance(raw, bool) or not 0 <= raw <= MAX_ID:
        raise APIError(400, "VALIDATION_ERROR", "輸入資料格式錯誤",
                       [{"field": "last_message_id", "issue": "must be a non-negative integer"}])
    user = g.current_user
    pointer = services.mark_read(patient, user, raw)
    db.session.commit()
    return ok({"last_read_message_id": pointer, "unread_count": services.unread_count(patient, user)})


@chat_bp.get("/chat/unread-count")
@require_auth(RoleName.PATIENT, RoleName.NURSE)
def unread_count():
    """Patient: unread nurse messages. Nurse: unread patient messages over every assigned patient."""
    return ok(services.total_unread(g.current_user))


@chat_bp.get("/chat/inbox")
@require_auth(RoleName.NURSE)
def inbox():
    """病人訊息: the nurse's currently assigned patients with last message and unread count."""
    rows = services.nurse_inbox(g.current_user)
    return ok(rows, meta={"total": len(rows), "unread_count": sum(r["unread_count"] for r in rows)})
