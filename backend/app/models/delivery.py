"""K. 通知：notification_deliveries (database-design.md §6.K).

One row per delivery attempt of a notification over an outside channel (Phase 1: email). The
delivery has its own status and never changes the notification's handling lifecycle
(``notifications.status``): an email that fails leaves the notification exactly as it was.

No copy of the address (``recipient_masked`` only) and no raw provider response is stored;
``error_code`` is one of our own codes.

``content_category`` / ``email_mode_requested`` / ``email_mode``: the email content policy
(email_policy.py). ``email_mode_requested = full`` with ``email_mode = summary`` means the system
downgraded the email because the topic is sensitive.
"""

from datetime import datetime

from sqlalchemy import Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin
from app.models.enums import DeliveryStatus


class NotificationDelivery(TimestampMixin, db.Model):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        Index("ix_notification_deliveries_notification_id", "notification_id"),
        Index("ix_notification_deliveries_status_created_at", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    notification_id: Mapped[int] = mapped_column(
        db.ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(db.String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        db.String(20), nullable=False, default=DeliveryStatus.PENDING, server_default=DeliveryStatus.PENDING
    )
    skip_reason: Mapped[str | None] = mapped_column(db.String(30))
    provider: Mapped[str | None] = mapped_column(db.String(30))
    provider_message_id: Mapped[str | None] = mapped_column(db.String(255))
    error_code: Mapped[str | None] = mapped_column(db.String(50))
    recipient_masked: Mapped[str | None] = mapped_column(db.String(255))
    # email content policy (email_policy.py): the nurse's topic, the mode asked for and the mode applied;
    # NULL on rows created before the policy existed (= summary)
    content_category: Mapped[str | None] = mapped_column(db.String(30))
    email_mode_requested: Mapped[str | None] = mapped_column(db.String(10))
    email_mode: Mapped[str | None] = mapped_column(db.String(10))
    attempted_at: Mapped[datetime | None] = mapped_column(db.DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(db.DateTime)

    notification = relationship("Notification", back_populates="deliveries")

    def __repr__(self):
        return (
            f"<NotificationDelivery id={self.id} notification_id={self.notification_id} "
            f"channel={self.channel!r} status={self.status!r}>"
        )
