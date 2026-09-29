"""E. 化療：drugs, chemo_regimens, regimen_drugs, chemotherapy_plans, chemotherapy_cycles,
medication_records (database-design.md §6.E).
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Index, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import ObservationMixin, SoftDeleteMixin, TimestampMixin
from app.models.enums import CycleStatus


class Drug(TimestampMixin, db.Model):
    """Drug master data (table ``drugs``)."""

    __tablename__ = "drugs"

    id: Mapped[int] = mapped_column(primary_key=True)
    generic_name: Mapped[str] = mapped_column(db.String(100), unique=True, nullable=False)
    brand_name: Mapped[str | None] = mapped_column(db.String(100))
    drug_class: Mapped[str | None] = mapped_column(db.String(50))
    default_route: Mapped[str | None] = mapped_column(db.String(20))
    is_active: Mapped[bool | None] = mapped_column(db.Boolean, default=True, server_default=true())

    regimen_drugs = relationship("RegimenDrug", back_populates="drug")
    medication_records = relationship("MedicationRecord", back_populates="drug")

    def __repr__(self):
        return f"<Drug id={self.id} generic_name={self.generic_name!r}>"


class ChemoRegimen(TimestampMixin, db.Model):
    __tablename__ = "chemo_regimens"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(db.String(50), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(db.Text)
    cycle_length_days: Mapped[int | None] = mapped_column(db.SmallInteger)
    default_total_cycles: Mapped[int | None] = mapped_column(db.SmallInteger)
    emetogenic_risk: Mapped[str | None] = mapped_column(db.String(10))
    is_active: Mapped[bool | None] = mapped_column(db.Boolean, default=True, server_default=true())

    plans = relationship("ChemotherapyPlan", back_populates="regimen")
    regimen_drugs = relationship(
        "RegimenDrug",
        back_populates="regimen",
        order_by="RegimenDrug.sequence",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self):
        return f"<ChemoRegimen id={self.id} name={self.name!r}>"


class RegimenDrug(TimestampMixin, db.Model):
    """A drug within a regimen template, with its standard dose and cycle day(s)."""

    __tablename__ = "regimen_drugs"
    __table_args__ = (UniqueConstraint("regimen_id", "drug_id", "day_of_cycle"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    regimen_id: Mapped[int] = mapped_column(
        db.ForeignKey("chemo_regimens.id", ondelete="CASCADE"), nullable=False
    )
    drug_id: Mapped[int] = mapped_column(
        db.ForeignKey("drugs.id", ondelete="RESTRICT"), nullable=False
    )
    dose_value: Mapped[Decimal | None] = mapped_column(db.Numeric(10, 2))
    dose_unit: Mapped[str | None] = mapped_column(db.String(20))
    route: Mapped[str | None] = mapped_column(db.String(20))
    day_of_cycle: Mapped[str | None] = mapped_column(db.String(20))
    sequence: Mapped[int | None] = mapped_column(db.SmallInteger)

    regimen = relationship("ChemoRegimen", back_populates="regimen_drugs")
    drug = relationship("Drug", back_populates="regimen_drugs")

    def __repr__(self):
        return (
            f"<RegimenDrug id={self.id} regimen_id={self.regimen_id} drug_id={self.drug_id} "
            f"day_of_cycle={self.day_of_cycle!r}>"
        )


class ChemotherapyPlan(TimestampMixin, SoftDeleteMixin, db.Model):
    __tablename__ = "chemotherapy_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(
        db.ForeignKey("patient_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    diagnosis_id: Mapped[int] = mapped_column(
        db.ForeignKey("cancer_diagnoses.id", ondelete="RESTRICT"), nullable=False
    )
    regimen_id: Mapped[int | None] = mapped_column(
        db.ForeignKey("chemo_regimens.id", ondelete="RESTRICT")
    )
    plan_name: Mapped[str | None] = mapped_column(db.String(100))
    intent: Mapped[str | None] = mapped_column(db.String(20))
    line_of_therapy: Mapped[int | None] = mapped_column(db.SmallInteger)
    total_cycles: Mapped[int | None] = mapped_column(db.SmallInteger)
    start_date: Mapped[date] = mapped_column(db.Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(db.Date)
    status: Mapped[str] = mapped_column(db.String(20), nullable=False)
    discontinue_reason: Mapped[str | None] = mapped_column(db.Text)
    attending_physician_name: Mapped[str | None] = mapped_column(db.String(100))
    created_by: Mapped[int | None] = mapped_column(db.ForeignKey("users.id", ondelete="RESTRICT"))

    patient = relationship("PatientProfile", back_populates="chemotherapy_plans")
    diagnosis = relationship("CancerDiagnosis", back_populates="chemotherapy_plans")
    regimen = relationship("ChemoRegimen", back_populates="plans")
    creator = relationship("User", foreign_keys=[created_by])
    cycles = relationship(
        "ChemotherapyCycle",
        back_populates="plan",
        order_by="ChemotherapyCycle.cycle_number",
    )

    def __repr__(self):
        return (
            f"<ChemotherapyPlan id={self.id} patient_id={self.patient_id} "
            f"status={self.status!r} total_cycles={self.total_cycles}>"
        )


class ChemotherapyCycle(TimestampMixin, SoftDeleteMixin, db.Model):
    __tablename__ = "chemotherapy_cycles"
    __table_args__ = (
        UniqueConstraint("plan_id", "cycle_number"),
        Index("ix_chemotherapy_cycles_patient_id_scheduled_date", "patient_id", "scheduled_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        db.ForeignKey("chemotherapy_plans.id", ondelete="RESTRICT"), nullable=False
    )
    patient_id: Mapped[int] = mapped_column(
        db.ForeignKey("patient_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    cycle_number: Mapped[int] = mapped_column(db.SmallInteger, nullable=False)
    scheduled_date: Mapped[date] = mapped_column(db.Date, nullable=False)
    actual_start_date: Mapped[date | None] = mapped_column(db.Date)
    actual_end_date: Mapped[date | None] = mapped_column(db.Date)
    weight_kg: Mapped[Decimal | None] = mapped_column(db.Numeric(5, 1))
    bsa_m2: Mapped[Decimal | None] = mapped_column(db.Numeric(4, 2))
    dose_modification_pct: Mapped[int | None] = mapped_column(
        db.SmallInteger, default=100, server_default="100"
    )
    status: Mapped[str] = mapped_column(db.String(20), nullable=False)
    delay_days: Mapped[int | None] = mapped_column(db.SmallInteger)
    delay_reason: Mapped[str | None] = mapped_column(db.Text)
    nadir_start_day: Mapped[int | None] = mapped_column(db.SmallInteger)
    nadir_end_day: Mapped[int | None] = mapped_column(db.SmallInteger)
    notes: Mapped[str | None] = mapped_column(db.Text)

    plan = relationship("ChemotherapyPlan", back_populates="cycles")
    patient = relationship("PatientProfile", back_populates="chemotherapy_cycles")
    appointments = relationship("Appointment", back_populates="cycle")
    symptom_records = relationship("SymptomRecord", back_populates="cycle")
    vital_signs = relationship("VitalSign", back_populates="cycle")
    nursing_assessments = relationship("NursingAssessment", back_populates="cycle")
    medication_records = relationship("MedicationRecord", back_populates="cycle")
    lab_results = relationship("LabResult", back_populates="cycle")

    def cycle_day_on(self, day):
        """Day number within this cycle (Day 1 = actual_start_date), or None if not started."""
        if self.actual_start_date is None or day < self.actual_start_date:
            return None
        return (day - self.actual_start_date).days + 1

    @property
    def is_completed(self):
        return self.status == CycleStatus.COMPLETED

    def __repr__(self):
        return (
            f"<ChemotherapyCycle id={self.id} plan_id={self.plan_id} "
            f"cycle_number={self.cycle_number} status={self.status!r}>"
        )


class MedicationRecord(ObservationMixin, TimestampMixin, db.Model):
    """Actual drug administration (observation, append-only).
    Unlike other observations, ``cycle_id`` is required and RESTRICT on delete.
    """

    __tablename__ = "medication_records"
    __table_args__ = (
        Index("ix_medication_records_patient_id_administered_at", "patient_id", "administered_at"),
        Index("ix_medication_records_cycle_id", "cycle_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(
        db.ForeignKey("patient_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    # Overrides ObservationMixin.cycle_id (nullable / SET NULL) per the table spec.
    cycle_id: Mapped[int] = mapped_column(
        db.ForeignKey("chemotherapy_cycles.id", ondelete="RESTRICT"), nullable=False
    )
    drug_id: Mapped[int] = mapped_column(
        db.ForeignKey("drugs.id", ondelete="RESTRICT"), nullable=False
    )
    medication_type: Mapped[str] = mapped_column(db.String(20), nullable=False)
    dose_value: Mapped[Decimal] = mapped_column(db.Numeric(10, 2), nullable=False)
    dose_unit: Mapped[str] = mapped_column(db.String(20), nullable=False)
    route: Mapped[str | None] = mapped_column(db.String(20))
    administered_at: Mapped[datetime] = mapped_column(db.DateTime, nullable=False)
    infusion_duration_min: Mapped[int | None] = mapped_column(db.Integer)
    administration_status: Mapped[str] = mapped_column(db.String(20), nullable=False)
    reaction_notes: Mapped[str | None] = mapped_column(db.Text)
    administered_by: Mapped[int | None] = mapped_column(
        db.ForeignKey("users.id", ondelete="RESTRICT")
    )

    patient = relationship("PatientProfile", back_populates="medication_records")
    cycle = relationship("ChemotherapyCycle", back_populates="medication_records")
    drug = relationship("Drug", back_populates="medication_records")
    administrator = relationship("User", foreign_keys=[administered_by])

    def __repr__(self):
        return (
            f"<MedicationRecord id={self.id} patient_id={self.patient_id} drug_id={self.drug_id} "
            f"dose={self.dose_value} {self.dose_unit} status={self.record_status!r}>"
        )
