"""護理師追蹤管理 (Phase 1c): care_follow_ups, care_follow_up_events.

A follow-up is a nurse's planned check of one patient's symptom report, created when (or after) the
report is reviewed. It is a work item with a life cycle — open → completed / cancelled — and never
changes the review: the review's signed nursing assessment stays as it is, and the completion is a new
signed nursing assessment (``completion_assessment_id``). "Overdue" is not stored: open and due_at past.

- One open follow-up per symptom record at most (partial unique index), also under concurrency.
- ``version`` is the optimistic lock: every change is a conditional update on (status = open, version).
- Another check after a completion is a new row linked by ``previous_follow_up_id`` (the chain).
- ``care_follow_up_events`` keeps every step (created / rescheduled / completed / cancelled, old and new
  due dates, actor, reason, related assessment); it is append-only like audit_logs.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, Index, event, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin, generate_uuid, utcnow


class CareFollowUp(TimestampMixin, db.Model):
    __tablename__ = "care_follow_ups"
    __table_args__ = (
        # at most one open follow-up per symptom record (PostgreSQL / SQLite partial unique index)
        Index("uq_care_follow_ups_open_record", "symptom_record_id", unique=True,
              sqlite_where=text("status = 'open'"), postgresql_where=text("status = 'open'")),
        Index("ix_care_follow_ups_patient_id_status_due_at", "patient_id", "status", "due_at"),
        Index("ix_care_follow_ups_status_due_at", "status", "due_at"),
        Index("ix_care_follow_ups_symptom_record_id", "symptom_record_id"),
        CheckConstraint("status IN ('open', 'completed', 'cancelled')", name="status"),
        CheckConstraint(
            "(status = 'open' AND completed_at IS NULL AND completed_by IS NULL AND completion_assessment_id IS NULL"
            " AND cancelled_at IS NULL AND cancelled_by IS NULL AND cancellation_reason IS NULL)"
            " OR (status = 'completed' AND completed_at IS NOT NULL AND completed_by IS NOT NULL AND completion_assessment_id IS NOT NULL"
            " AND cancelled_at IS NULL AND cancelled_by IS NULL AND cancellation_reason IS NULL)"
            " OR (status = 'cancelled' AND cancelled_at IS NOT NULL AND cancelled_by IS NOT NULL AND cancellation_reason IS NOT NULL"
            " AND completed_at IS NULL AND completed_by IS NULL AND completion_assessment_id IS NULL)",
            name="status_fields",
        ),
        CheckConstraint("version >= 1", name="version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(db.String(36), unique=True, nullable=False, default=generate_uuid)
    patient_id: Mapped[int] = mapped_column(db.ForeignKey("patient_profiles.id", ondelete="RESTRICT"), nullable=False)
    symptom_record_id: Mapped[int] = mapped_column(db.ForeignKey("symptom_records.id", ondelete="RESTRICT"), nullable=False)
    # the signed nursing assessment that planned it (the review, or the completion of the previous one)
    review_assessment_id: Mapped[int] = mapped_column(db.ForeignKey("nursing_assessments.id", ondelete="RESTRICT"), nullable=False)
    due_at: Mapped[datetime] = mapped_column(db.DateTime, nullable=False)  # UTC
    reason: Mapped[str] = mapped_column(db.Text, nullable=False)
    status: Mapped[str] = mapped_column(db.String(20), nullable=False, default="open")
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    created_by: Mapped[int] = mapped_column(db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    completed_by: Mapped[int | None] = mapped_column(db.ForeignKey("users.id", ondelete="RESTRICT"))
    completed_at: Mapped[datetime | None] = mapped_column(db.DateTime)
    completion_assessment_id: Mapped[int | None] = mapped_column(db.ForeignKey("nursing_assessments.id", ondelete="RESTRICT"))
    cancelled_by: Mapped[int | None] = mapped_column(db.ForeignKey("users.id", ondelete="RESTRICT"))
    cancelled_at: Mapped[datetime | None] = mapped_column(db.DateTime)
    cancellation_reason: Mapped[str | None] = mapped_column(db.Text)
    previous_follow_up_id: Mapped[int | None] = mapped_column(db.ForeignKey("care_follow_ups.id", ondelete="RESTRICT"))

    patient = relationship("PatientProfile")
    record = relationship("SymptomRecord")
    review_assessment = relationship("NursingAssessment", foreign_keys=[review_assessment_id])
    completion_assessment = relationship("NursingAssessment", foreign_keys=[completion_assessment_id])
    creator = relationship("User", foreign_keys=[created_by])
    completer = relationship("User", foreign_keys=[completed_by])
    canceller = relationship("User", foreign_keys=[cancelled_by])
    previous = relationship("CareFollowUp", remote_side=[id], backref="next_follow_ups")

    def is_overdue(self, now=None):
        return self.status == "open" and self.due_at < (now or utcnow())

    def __repr__(self):
        return f"<CareFollowUp {self.public_id} {self.status} due={self.due_at}>"


class CareFollowUpEvent(db.Model):
    __tablename__ = "care_follow_up_events"
    __table_args__ = (
        Index("ix_care_follow_up_events_follow_up_id_id", "follow_up_id", "id"),
        CheckConstraint("event_type IN ('created', 'rescheduled', 'completed', 'cancelled')", name="event_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    follow_up_id: Mapped[int] = mapped_column(db.ForeignKey("care_follow_ups.id", ondelete="RESTRICT"), nullable=False)
    event_type: Mapped[str] = mapped_column(db.String(20), nullable=False)
    old_due_at: Mapped[datetime | None] = mapped_column(db.DateTime)
    new_due_at: Mapped[datetime | None] = mapped_column(db.DateTime)
    actor_id: Mapped[int] = mapped_column(db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(db.DateTime, nullable=False, default=utcnow)
    reason: Mapped[str | None] = mapped_column(db.Text)
    related_assessment_id: Mapped[int | None] = mapped_column(db.ForeignKey("nursing_assessments.id", ondelete="RESTRICT"))
    related_follow_up_id: Mapped[int | None] = mapped_column(db.ForeignKey("care_follow_ups.id", ondelete="RESTRICT"))  # chain link

    follow_up = relationship("CareFollowUp", foreign_keys=[follow_up_id], backref="events")
    actor = relationship("User")


@event.listens_for(CareFollowUpEvent, "before_update")
def _events_are_append_only(mapper, connection, target):
    raise RuntimeError("care_follow_up_events is append-only")


@event.listens_for(CareFollowUpEvent, "before_delete")
def _events_are_never_deleted(mapper, connection, target):
    raise RuntimeError("care_follow_up_events is append-only")
