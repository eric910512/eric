"""J. 檢驗：lab_test_types, lab_results (database-design.md §7 J / O).

Implemented ahead of Phase 2 in the Lab Result sprint.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Index, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import ObservationMixin, TimestampMixin


class LabTestType(TimestampMixin, db.Model):
    __tablename__ = "lab_test_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(db.String(20), unique=True, nullable=False)
    loinc_code: Mapped[str | None] = mapped_column(db.String(20))
    name_zh: Mapped[str] = mapped_column(db.String(100), nullable=False)
    unit: Mapped[str] = mapped_column(db.String(20), nullable=False)
    ref_low: Mapped[Decimal | None] = mapped_column(db.Numeric(10, 3))
    ref_high: Mapped[Decimal | None] = mapped_column(db.Numeric(10, 3))
    critical_low: Mapped[Decimal | None] = mapped_column(db.Numeric(10, 3))
    critical_high: Mapped[Decimal | None] = mapped_column(db.Numeric(10, 3))
    display_order: Mapped[int | None] = mapped_column(db.SmallInteger)
    is_active: Mapped[bool | None] = mapped_column(db.Boolean, default=True, server_default=true())

    results = relationship("LabResult", back_populates="test_type")
    alert_rules = relationship("AlertRule", back_populates="lab_test_type")

    def __repr__(self):
        return f"<LabTestType id={self.id} code={self.code!r} unit={self.unit!r}>"


class LabResult(ObservationMixin, TimestampMixin, db.Model):
    """One analyte result (observation, append-only). Unit and reference range are snapshots
    taken when the result is recorded, so later range changes do not rewrite history.
    """

    __tablename__ = "lab_results"
    __table_args__ = (
        Index("ix_lab_results_patient_id_lab_test_type_id_collected_at", "patient_id", "lab_test_type_id", "collected_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(
        db.ForeignKey("patient_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    lab_test_type_id: Mapped[int] = mapped_column(
        db.ForeignKey("lab_test_types.id", ondelete="RESTRICT"), nullable=False
    )
    collected_at: Mapped[datetime] = mapped_column(db.DateTime, nullable=False)
    resulted_at: Mapped[datetime | None] = mapped_column(db.DateTime)
    value_numeric: Mapped[Decimal | None] = mapped_column(db.Numeric(10, 3))
    value_text: Mapped[str | None] = mapped_column(db.String(100))
    unit: Mapped[str | None] = mapped_column(db.String(20))
    ref_low: Mapped[Decimal | None] = mapped_column(db.Numeric(10, 3))
    ref_high: Mapped[Decimal | None] = mapped_column(db.Numeric(10, 3))
    abnormal_flag: Mapped[str | None] = mapped_column(db.String(2))
    recorded_by: Mapped[int | None] = mapped_column(db.ForeignKey("users.id", ondelete="RESTRICT"))

    patient = relationship("PatientProfile", back_populates="lab_results")
    test_type = relationship("LabTestType", back_populates="results")
    cycle = relationship("ChemotherapyCycle", back_populates="lab_results")
    recorder = relationship("User", foreign_keys=[recorded_by])

    def __repr__(self):
        return (
            f"<LabResult id={self.id} patient_id={self.patient_id} type_id={self.lab_test_type_id} "
            f"value={self.value_numeric} flag={self.abnormal_flag!r}>"
        )
