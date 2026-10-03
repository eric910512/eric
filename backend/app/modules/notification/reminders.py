"""Manual and scheduled reminders (api-design.md §10 ``POST /notifications``, Sprint 8).

A nurse (for a currently assigned patient) or an admin writes a reminder to the patient, sent
now or at ``scheduled_for``. It is an ordinary ``notifications`` row (``type = reminder``,
recipient = the patient's account) with its own ``event_key``, so it follows the same
handling lifecycle as risk alerts (new → acknowledged → in_progress → resolved). Until
``scheduled_for`` has passed nobody but staff sees it (``GET /notifications/scheduled``).

``origin`` is derived (services.origin): manual (sent now) or scheduled.
Callers write the audit entry and commit.
"""

import uuid
from datetime import timedelta

from sqlalchemy import select

from app.core.api import APIError
from app.core.timeutil import iso_utc
from app.extensions import db
from app.models import Notification
from app.models.base import utcnow
from app.models.enums import AlertSeverity, NotificationStatus, NotificationType
from app.modules.chemotherapy.appointments import _datetime
from app.modules.chemotherapy.services import Fields
from app.modules.notification.delivery import delivery_payload, latest_email_delivery
from app.modules.notification.services import origin

SEVERITIES = (AlertSeverity.INFO, AlertSeverity.WARNING)  # critical is reserved for risk alerts
MAX_AHEAD = timedelta(days=366)
MIN_AHEAD = timedelta(minutes=1)


def create_reminder(patient, body):
    """``{title, message, severity?: info|warning, scheduled_for?: ISO datetime (future, ≤ 1 year)}``."""
    f = Fields(body, "提醒內容有誤")
    f.text("title", 200, required=True)
    f.text("message", 2000, required=True)
    f.choice("severity", SEVERITIES)
    if "type" in body and body["type"] != NotificationType.REMINDER:
        f.error("type", "must be 'reminder'")
    f.unknown(("patient_id", "type", "title", "message", "severity", "scheduled_for"))
    now = utcnow()
    at = None
    if body.get("scheduled_for") is not None:
        details = []
        at = _datetime(body["scheduled_for"], "scheduled_for", details)
        for d in details:
            f.error(d["field"], d["issue"])
        if at is not None and not now + MIN_AHEAD <= at <= now + MAX_AHEAD:
            f.error("scheduled_for", "must be at least 1 minute and at most 1 year from now (omit it to send now)")
    values = f.done()
    if patient.user_id is None:
        raise APIError(422, "NO_PATIENT_ACCOUNT", "這位病人尚未開通登入帳號，無法收到提醒")
    n = Notification(
        recipient_id=patient.user_id, patient_id=patient.id, event_key=f"reminder:{uuid.uuid4()}",
        type=NotificationType.REMINDER, severity=values.get("severity") or AlertSeverity.INFO,
        title=values["title"], message=values["message"], scheduled_for=at, sent_at=at or now,
        is_read=False, status=NotificationStatus.NEW,
    )
    db.session.add(n)
    db.session.flush()
    return n


def scheduled_reminders(patient):
    """Reminders of one patient that are not due yet (staff only), soonest first."""
    return db.session.execute(
        select(Notification).where(
            Notification.patient_id == patient.id, Notification.type == NotificationType.REMINDER,
            Notification.scheduled_for > utcnow(),
        ).order_by(Notification.scheduled_for, Notification.id)
    ).scalars().all()


def scheduled_payload(n):
    return {
        "id": n.id, "event_key": n.event_key, "type": n.type, "origin": origin(n), "severity": n.severity,
        "title": n.title, "message": n.message, "scheduled_for": iso_utc(n.scheduled_for), "created_at": iso_utc(n.created_at),
        "patient": {"id": n.patient.public_id, "patient_code": n.patient.patient_code, "display_name": n.patient.display_name},
        "email_delivery": delivery_payload(latest_email_delivery(n)),  # scheduled reminders: skipped / scheduled
    }
