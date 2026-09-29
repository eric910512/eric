"""J. 護理評估：nursing_assessments, nursing_assessment_items (database-design.md §6.J)."""

from datetime import datetime

from sqlalchemy import CheckConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import ObservationMixin, TimestampMixin
from app.models.enums import AssessmentItemStatus, SignStatus


class NursingAssessment(ObservationMixin, TimestampMixin, db.Model):
    """SOAP assessment. Vital signs and symptom records measured during the assessment
    stay in their own time-series tables and link back via ``nursing_assessment_id``.
    """

    __tablename__ = "nursing_assessments"
    __table_args__ = (
        Index("ix_nursing_assessments_patient_id_assessed_at", "patient_id", "assessed_at"),
        Index("ix_nursing_assessments_assessed_by_assessed_at", "assessed_by", "assessed_at"),
        Index("ix_nursing_assessments_sign_status", "sign_status"),
        CheckConstraint("ecog_status BETWEEN 0 AND 5", name="ecog_status_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(
        db.ForeignKey("patient_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    assessed_by: Mapped[int] = mapped_column(
        db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    assessed_at: Mapped[datetime] = mapped_column(db.DateTime, nullable=False)
    assessment_type: Mapped[str] = mapped_column(db.String(20), nullable=False)
    appointment_id: Mapped[int | None] = mapped_column(
        db.ForeignKey("appointments.id", ondelete="SET NULL")
    )
    ecog_status: Mapped[int | None] = mapped_column(db.SmallInteger)
    overall_condition: Mapped[str | None] = mapped_column(db.String(20))
    risk_level: Mapped[str | None] = mapped_column(db.String(10))
    chemo_readiness: Mapped[str | None] = mapped_column(db.String(20))
    subjective: Mapped[str | None] = mapped_column(db.Text)
    objective: Mapped[str | None] = mapped_column(db.Text)
    assessment: Mapped[str | None] = mapped_column(db.Text)
    plan: Mapped[str | None] = mapped_column(db.Text)
    next_follow_up_at: Mapped[datetime | None] = mapped_column(db.DateTime)
    sign_status: Mapped[str] = mapped_column(
        db.String(10), nullable=False, default=SignStatus.DRAFT, server_default=SignStatus.DRAFT
    )
    signed_at: Mapped[datetime | None] = mapped_column(db.DateTime)

    patient = relationship("PatientProfile", back_populates="nursing_assessments")
    assessor = relationship("User", foreign_keys=[assessed_by])
    appointment = relationship("Appointment", back_populates="nursing_assessments")
    cycle = relationship("ChemotherapyCycle", back_populates="nursing_assessments")
    items = relationship(
        "NursingAssessmentItem",
        back_populates="assessment",
        order_by="NursingAssessmentItem.display_order",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    vital_signs = relationship("VitalSign", back_populates="nursing_assessment")
    symptom_records = relationship("SymptomRecord", back_populates="nursing_assessment")

    @property
    def is_signed(self):
        return self.sign_status == SignStatus.SIGNED

    def __repr__(self):
        return (
            f"<NursingAssessment id={self.id} patient_id={self.patient_id} "
            f"type={self.assessment_type!r} sign_status={self.sign_status!r}>"
        )


class NursingAssessmentItem(TimestampMixin, db.Model):
    __tablename__ = "nursing_assessment_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(
        db.ForeignKey("nursing_assessments.id", ondelete="CASCADE"), nullable=False
    )
    item_type: Mapped[str] = mapped_column(db.String(20), nullable=False)
    code: Mapped[str | None] = mapped_column(db.String(50))
    description: Mapped[str] = mapped_column(db.Text, nullable=False)
    priority: Mapped[str | None] = mapped_column(db.String(10))
    item_status: Mapped[str] = mapped_column(
        db.String(20),
        nullable=False,
        default=AssessmentItemStatus.OPEN,
        server_default=AssessmentItemStatus.OPEN,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(db.DateTime)
    display_order: Mapped[int | None] = mapped_column(db.SmallInteger)

    assessment = relationship("NursingAssessment", back_populates="items")

    def __repr__(self):
        return (
            f"<NursingAssessmentItem id={self.id} assessment_id={self.assessment_id} "
            f"type={self.item_type!r} status={self.item_status!r}>"
        )
