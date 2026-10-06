"""Email delivery of nurse-sent notifications (reminders) — a second channel next to the app
notification, never a second state machine.

    POST /notifications
      ├─ transaction: notification row (status new) + plan_email() → delivery row
      │                 (pending, or skipped with a reason) + audit — commit
      └─ after commit: dispatch() → EmailService → delivery sent / failed — own commit

- The app notification is created and committed first; an email failure (provider error,
  timeout, crash before the update) only touches the delivery row, never the notification or its
  lifecycle (``notifications.status``).
- Only reminders written by staff are emailed. Risk alerts are not; scheduled reminders are
  recorded as skipped / ``scheduled`` (no background scheduler).
- What the email may contain follows email_policy.py: the nurse's topic and mode (none / summary /
  full); ``full`` for a sensitive topic is downgraded to ``summary`` here — whatever the client sent.
  The topic, requested and applied mode are kept on the delivery row.
- Nothing is sent unless the contact email is verified and email notifications are on.
"""

from app.core.audit import record_data_event
from app.core.timeutil import iso_utc
from app.extensions import db
from app.models import NotificationDelivery
from app.models.base import utcnow
from app.models.enums import AuditAction, DeliveryChannel, DeliverySkipReason, DeliveryStatus, EmailMode
from app.modules.notification import email_policy
from app.services.email import get_email_service, send_email
from app.services.email.base import NOT_CONFIGURED, SENT
from app.services.email.templates import mask_email, notification_email


def _skip_reason(n, contact, service, now, mode):
    if mode == EmailMode.NONE:
        return DeliverySkipReason.NOT_REQUESTED
    if n.scheduled_for is not None and n.scheduled_for > now:
        return DeliverySkipReason.SCHEDULED
    if not service.available:
        return DeliverySkipReason.NOT_CONFIGURED
    if contact is None or not contact.email:
        return DeliverySkipReason.NO_EMAIL
    if not contact.email_verified:
        return DeliverySkipReason.NOT_VERIFIED
    if not contact.email_notification_enabled:
        return DeliverySkipReason.DISABLED
    return None


def plan_email(n, patient, category=None, requested_mode=None):
    """Decide, inside the notification's transaction, whether and how the email will be sent
    (content policy applied here: a sensitive topic never gets ``full``)."""
    now = utcnow()
    service = get_email_service()
    contact = patient.contact
    category, requested, mode = email_policy.resolve(category, requested_mode)
    reason = _skip_reason(n, contact, service, now, mode)
    delivery = NotificationDelivery(
        notification=n, channel=DeliveryChannel.EMAIL,
        content_category=category, email_mode_requested=requested, email_mode=mode,
        status=DeliveryStatus.SKIPPED if reason else DeliveryStatus.PENDING, skip_reason=reason,
        provider=service.name, recipient_masked=mask_email(contact.email if contact else None),
        completed_at=now if reason else None,
    )
    db.session.add(delivery)
    db.session.flush()
    return delivery


def dispatch(delivery):
    """After the notification is committed: send a pending email and record the outcome. Never
    raises (a failed commit of the outcome leaves the delivery ``pending``). Returns True when an
    attempt was made."""
    if delivery is None or delivery.status != DeliveryStatus.PENDING:
        return False
    n = delivery.notification
    patient = n.patient
    contact = patient.contact if patient else None
    try:
        delivery.attempted_at = utcnow()
        mode = delivery.email_mode or EmailMode.SUMMARY  # NULL: rows from before the policy (summary)
        title = n.title if mode == EmailMode.FULL else email_policy.email_title(delivery.content_category, n.title)
        result = send_email(notification_email(
            contact.email, mode=mode, title=title, message=n.message,
            sent_at=n.sent_at or n.created_at, timezone_name=patient.timezone,
        ))
        delivery.completed_at = utcnow()
        if result.status == SENT:
            delivery.status = DeliveryStatus.SENT
            delivery.provider_message_id = result.provider_message_id
        elif result.status == NOT_CONFIGURED:
            delivery.status, delivery.skip_reason = DeliveryStatus.SKIPPED, DeliverySkipReason.NOT_CONFIGURED
        else:
            delivery.status, delivery.error_code = DeliveryStatus.FAILED, result.error_code
        # outcome only: no address, no message, no provider response
        record_data_event(AuditAction.UPDATE, "notification_deliveries", delivery.id, patient=patient,
                          changes={"notification_id": n.id, "channel": delivery.channel, "status": delivery.status,
                                   "error_code": delivery.error_code})
        db.session.commit()
    except Exception:  # noqa: BLE001 - the notification is already committed; keep the request alive
        db.session.rollback()
    return True


def latest_email_delivery(n):
    rows = [d for d in n.deliveries if d.channel == DeliveryChannel.EMAIL]
    return max(rows, key=lambda d: d.id) if rows else None


def delivery_payload(d):
    if d is None:
        return None
    return {
        "channel": d.channel,
        "status": d.status,
        "skip_reason": d.skip_reason,
        "error_code": d.error_code,
        "recipient_masked": d.recipient_masked,
        "category": d.content_category,
        "mode_requested": d.email_mode_requested,
        "mode": d.email_mode,
        "downgraded": d.email_mode_requested == EmailMode.FULL and d.email_mode == EmailMode.SUMMARY,
        "attempted_at": iso_utc(d.attempted_at),
        "completed_at": iso_utc(d.completed_at),
    }
