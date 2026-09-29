"""D. 診斷：cancer_types, cancer_diagnoses (database-design.md §6.D)."""

from datetime import date

from sqlalchemy import true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import JSONType, SoftDeleteMixin, TimestampMixin


class CancerType(TimestampMixin, db.Model):
    __tablename__ = "cancer_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(db.String(20), unique=True, nullable=False)
    name_zh: Mapped[str] = mapped_column(db.String(100), nullable=False)
    name_en: Mapped[str | None] = mapped_column(db.String(100))
    is_active: Mapped[bool | None] = mapped_column(db.Boolean, default=True, server_default=true())

    diagnoses = relationship("CancerDiagnosis", back_populates="cancer_type")
    alert_rules = relationship("AlertRule", back_populates="cancer_type")

    def __repr__(self):
        return f"<CancerType id={self.id} code={self.code!r} name_zh={self.name_zh!r}>"


class CancerDiagnosis(TimestampMixin, SoftDeleteMixin, db.Model):
    __tablename__ = "cancer_diagnoses"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(
        db.ForeignKey("patient_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    cancer_type_id: Mapped[int] = mapped_column(
        db.ForeignKey("cancer_types.id", ondelete="RESTRICT"), nullable=False
    )
    diagnosis_date: Mapped[date] = mapped_column(db.Date, nullable=False)
    stage: Mapped[str | None] = mapped_column(db.String(10))
    tnm_t: Mapped[str | None] = mapped_column(db.String(10))
    tnm_n: Mapped[str | None] = mapped_column(db.String(10))
    tnm_m: Mapped[str | None] = mapped_column(db.String(10))
    histology: Mapped[str | None] = mapped_column(db.String(100))
    biomarkers: Mapped[dict | None] = mapped_column(JSONType)
    is_primary: Mapped[bool | None] = mapped_column(db.Boolean, default=True, server_default=true())
    status: Mapped[str | None] = mapped_column(db.String(20))
    notes: Mapped[str | None] = mapped_column(db.Text)
    created_by: Mapped[int | None] = mapped_column(db.ForeignKey("users.id", ondelete="RESTRICT"))

    patient = relationship("PatientProfile", back_populates="diagnoses")
    cancer_type = relationship("CancerType", back_populates="diagnoses")
    creator = relationship("User", foreign_keys=[created_by])
    chemotherapy_plans = relationship("ChemotherapyPlan", back_populates="diagnosis")

    def __repr__(self):
        return (
            f"<CancerDiagnosis id={self.id} patient_id={self.patient_id} "
            f"cancer_type_id={self.cancer_type_id} stage={self.stage!r}>"
        )
