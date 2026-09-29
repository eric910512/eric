"""I. 生命徵象：vital_signs (database-design.md §6.I)."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import ObservationMixin, TimestampMixin


class VitalSign(ObservationMixin, TimestampMixin, db.Model):
    __tablename__ = "vital_signs"
    __table_args__ = (
        Index("ix_vital_signs_patient_id_measured_at", "patient_id", "measured_at"),
        CheckConstraint("pain_score BETWEEN 0 AND 10", name="pain_score_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(
        db.ForeignKey("patient_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    nursing_assessment_id: Mapped[int | None] = mapped_column(
        db.ForeignKey("nursing_assessments.id", ondelete="SET NULL")
    )
    measured_at: Mapped[datetime] = mapped_column(db.DateTime, nullable=False)
    temperature_c: Mapped[Decimal | None] = mapped_column(db.Numeric(4, 1))
    temperature_site: Mapped[str | None] = mapped_column(db.String(10))
    heart_rate_bpm: Mapped[int | None] = mapped_column(db.SmallInteger)
    systolic_bp_mmhg: Mapped[int | None] = mapped_column(db.SmallInteger)
    diastolic_bp_mmhg: Mapped[int | None] = mapped_column(db.SmallInteger)
    bp_measure_site: Mapped[str | None] = mapped_column(db.String(20))
    respiratory_rate: Mapped[int | None] = mapped_column(db.SmallInteger)
    spo2_pct: Mapped[int | None] = mapped_column(db.SmallInteger)
    weight_kg: Mapped[Decimal | None] = mapped_column(db.Numeric(5, 1))
    pain_score: Mapped[int | None] = mapped_column(db.SmallInteger)
    device_id: Mapped[str | None] = mapped_column(db.String(100))
    recorded_by: Mapped[int] = mapped_column(
        db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(db.Text)

    patient = relationship("PatientProfile", back_populates="vital_signs")
    nursing_assessment = relationship("NursingAssessment", back_populates="vital_signs")
    cycle = relationship("ChemotherapyCycle", back_populates="vital_signs")
    recorder = relationship("User", foreign_keys=[recorded_by])

    def __repr__(self):
        return (
            f"<VitalSign id={self.id} patient_id={self.patient_id} "
            f"measured_at={self.measured_at} status={self.record_status!r}>"
        )
