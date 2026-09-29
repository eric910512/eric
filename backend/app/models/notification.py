"""K. 通知：alert_rules, notifications (database-design.md §6.K)."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Index, false, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import JSONType, TimestampMixin
from app.models.enums import NotificationStatus


class AlertRule(TimestampMixin, db.Model):
    """Risk rule. Exactly one of vital_field / symptom_definition_id / lab_test_type_id
    must be set, according to ``source_type`` (validated in the application layer).
    """

    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(db.String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(db.String(100), nullable=False)
    source_type: Mapped[str] = mapped_column(db.String(20), nullable=False)
    vital_field: Mapped[str | None] = mapped_column(db.String(50))
    symptom_definition_id: Mapped[int | None] = mapped_column(
        db.ForeignKey("symptom_definitions.id", ondelete="RESTRICT")
    )
    lab_test_type_id: Mapped[int | None] = mapped_column(
        db.ForeignKey("lab_test_types.id", ondelete="RESTRICT")
    )
    operator: Mapped[str] = mapped_column(db.String(5), nullable=False)
    threshold_value: Mapped[Decimal] = mapped_column(db.Numeric(10, 3), nullable=False)
    extra_conditions: Mapped[dict | None] = mapped_column(JSONType)
    cancer_type_id: Mapped[int | None] = mapped_column(
        db.ForeignKey("cancer_types.id", ondelete="RESTRICT")
    )
    severity: Mapped[str] = mapped_column(db.String(10), nullable=False)
    message_template: Mapped[str | None] = mapped_column(db.String(500))
    recommended_action: Mapped[str | None] = mapped_column(db.String(500))  # suggested handling for staff
    notify_patient: Mapped[bool | None] = mapped_column(db.Boolean, default=True, server_default=true())
    notify_nurse: Mapped[bool | None] = mapped_column(db.Boolean, default=True, server_default=true())
    cooldown_minutes: Mapped[int | None] = mapped_column(db.Integer, default=0, server_default="0")
    is_active: Mapped[bool | None] = mapped_column(db.Boolean, default=True, server_default=true())

    symptom_definition = relationship("SymptomDefinition", back_populates="alert_rules")
    lab_test_type = relationship("LabTestType", back_populates="alert_rules")
    cancer_type = relationship("CancerType", back_populates="alert_rules")
    notifications = relationship("Notification", back_populates="alert_rule")

    def __repr__(self):
        return (
            f"<AlertRule id={self.id} code={self.code!r} source_type={self.source_type!r} "
            f"severity={self.severity!r}>"
        )


class Notification(TimestampMixin, db.Model):
    """One row per recipient. Rows for the same event share ``event_key``.

    Risk alerts follow ``status``: new → acknowledged → in_progress → resolved. A transition
    is applied to every copy of the event, so the patient's copy shows the same status.
    ``acknowledged_*`` records who took the alert over, ``started_*`` who began handling it
    and ``resolved_*`` who closed it; ``resolution_note`` is an internal staff note.

    Future reminders use ``scheduled_for``; queries only return rows whose
    ``scheduled_for`` is NULL or already past.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_recipient_id_is_read_created_at", "recipient_id", "is_read", "created_at"),
        Index("ix_notifications_patient_id_alert_rule_id_created_at", "patient_id", "alert_rule_id", "created_at"),
        Index("ix_notifications_event_key", "event_key"),
        Index("ix_notifications_patient_id_status_created_at", "patient_id", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    recipient_id: Mapped[int] = mapped_column(
        db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    patient_id: Mapped[int | None] = mapped_column(
        db.ForeignKey("patient_profiles.id", ondelete="SET NULL")
    )
    alert_rule_id: Mapped[int | None] = mapped_column(
        db.ForeignKey("alert_rules.id", ondelete="SET NULL")
    )
    event_key: Mapped[str | None] = mapped_column(db.String(100))
    type: Mapped[str] = mapped_column(db.String(20), nullable=False)
    severity: Mapped[str] = mapped_column(db.String(10), nullable=False)
    title: Mapped[str] = mapped_column(db.String(200), nullable=False)
    message: Mapped[str] = mapped_column(db.Text, nullable=False)
    source_table: Mapped[str | None] = mapped_column(db.String(50))
    source_id: Mapped[int | None] = mapped_column(db.Integer)
    scheduled_for: Mapped[datetime | None] = mapped_column(db.DateTime)
    sent_at: Mapped[datetime | None] = mapped_column(db.DateTime)
    is_read: Mapped[bool | None] = mapped_column(db.Boolean, default=False, server_default=false())
    read_at: Mapped[datetime | None] = mapped_column(db.DateTime)
    acknowledged_by: Mapped[int | None] = mapped_column(
        db.ForeignKey("users.id", ondelete="RESTRICT")
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(db.DateTime)
    status: Mapped[str] = mapped_column(
        db.String(20), nullable=False, default=NotificationStatus.NEW, server_default=NotificationStatus.NEW
    )
    started_by: Mapped[int | None] = mapped_column(db.ForeignKey("users.id", ondelete="RESTRICT"))
    started_at: Mapped[datetime | None] = mapped_column(db.DateTime)
    resolved_by: Mapped[int | None] = mapped_column(db.ForeignKey("users.id", ondelete="RESTRICT"))
    resolved_at: Mapped[datetime | None] = mapped_column(db.DateTime)
    resolution_note: Mapped[str | None] = mapped_column(db.Text)

    recipient = relationship("User", back_populates="notifications", foreign_keys=[recipient_id])
    patient = relationship("PatientProfile", back_populates="notifications")
    alert_rule = relationship("AlertRule", back_populates="notifications")
    acknowledger = relationship("User", foreign_keys=[acknowledged_by])
    starter = relationship("User", foreign_keys=[started_by])
    resolver = relationship("User", foreign_keys=[resolved_by])

    def __repr__(self):
        return (
            f"<Notification id={self.id} recipient_id={self.recipient_id} type={self.type!r} "
            f"severity={self.severity!r} status={self.status!r} is_read={self.is_read}>"
        )
