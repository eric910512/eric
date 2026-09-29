"""C. 病人：patient_profiles, patient_care_alerts, nurse_patient_assignments
(database-design.md §6.C, §3 隱私政策).

No national ID column by design; patients are identified by the system-generated
``patient_code``.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Index, UniqueConstraint, false, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import SoftDeleteMixin, TimestampMixin, generate_uuid, utcnow


class PatientProfile(TimestampMixin, SoftDeleteMixin, db.Model):
    __tablename__ = "patient_profiles"
    __table_args__ = (
        CheckConstraint("baseline_ecog BETWEEN 0 AND 5", name="baseline_ecog_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(
        db.CHAR(36), unique=True, nullable=False, default=generate_uuid
    )
    patient_code: Mapped[str] = mapped_column(db.String(20), unique=True, nullable=False)
    user_id: Mapped[int | None] = mapped_column(
        db.ForeignKey("users.id", ondelete="SET NULL"), unique=True
    )
    display_name: Mapped[str] = mapped_column(db.String(100), nullable=False)
    gender: Mapped[str | None] = mapped_column(db.String(10))
    date_of_birth: Mapped[date] = mapped_column(db.Date, nullable=False)
    height_cm: Mapped[Decimal | None] = mapped_column(db.Numeric(5, 1))
    blood_type: Mapped[str | None] = mapped_column(db.String(5))
    allergies: Mapped[str | None] = mapped_column(db.Text)
    baseline_ecog: Mapped[int | None] = mapped_column(db.SmallInteger)
    timezone: Mapped[str] = mapped_column(
        db.String(40), nullable=False, default="Asia/Taipei", server_default="Asia/Taipei"
    )
    is_demo: Mapped[bool] = mapped_column(
        db.Boolean, nullable=False, default=True, server_default=true()
    )
    created_by: Mapped[int | None] = mapped_column(db.ForeignKey("users.id", ondelete="RESTRICT"))

    user = relationship("User", back_populates="patient_profile", foreign_keys=[user_id])
    creator = relationship("User", foreign_keys=[created_by])

    diagnoses = relationship("CancerDiagnosis", back_populates="patient")
    chemotherapy_plans = relationship("ChemotherapyPlan", back_populates="patient")
    chemotherapy_cycles = relationship("ChemotherapyCycle", back_populates="patient")
    appointments = relationship("Appointment", back_populates="patient")
    symptom_records = relationship("SymptomRecord", back_populates="patient")
    vital_signs = relationship("VitalSign", back_populates="patient")
    nursing_assessments = relationship("NursingAssessment", back_populates="patient")
    medication_records = relationship("MedicationRecord", back_populates="patient")
    lab_results = relationship("LabResult", back_populates="patient")
    care_alerts = relationship("PatientCareAlert", back_populates="patient")
    nurse_assignments = relationship("NursePatientAssignment", back_populates="patient")
    notifications = relationship("Notification", back_populates="patient")

    @property
    def active_care_alerts(self):
        return [alert for alert in self.care_alerts if alert.is_active]

    def __repr__(self):
        return f"<PatientProfile id={self.id} code={self.patient_code!r}>"


class PatientCareAlert(TimestampMixin, db.Model):
    """Patient-safety notice (allergy, limb restriction, ...) pinned on the patient banner.
    Deactivate with ``is_active=False`` instead of deleting.
    """

    __tablename__ = "patient_care_alerts"
    __table_args__ = (
        Index("ix_patient_care_alerts_patient_id_is_active", "patient_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(
        db.ForeignKey("patient_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    alert_type: Mapped[str] = mapped_column(db.String(30), nullable=False)
    body_site: Mapped[str | None] = mapped_column(db.String(30))
    description: Mapped[str] = mapped_column(db.String(255), nullable=False)
    severity: Mapped[str] = mapped_column(db.String(10), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        db.Boolean, nullable=False, default=True, server_default=true()
    )
    recorded_by: Mapped[int | None] = mapped_column(db.ForeignKey("users.id", ondelete="RESTRICT"))

    patient = relationship("PatientProfile", back_populates="care_alerts")
    recorder = relationship("User", foreign_keys=[recorded_by])

    def __repr__(self):
        return (
            f"<PatientCareAlert id={self.id} patient_id={self.patient_id} "
            f"type={self.alert_type!r} active={self.is_active}>"
        )


class NursePatientAssignment(TimestampMixin, db.Model):
    """Which nurse currently cares for which patient; the basis of nurse data scope.
    ``ended_at IS NULL`` means the assignment is still active.
    """

    __tablename__ = "nurse_patient_assignments"
    __table_args__ = (
        UniqueConstraint("nurse_id", "patient_id", "assigned_at"),
        Index("ix_nurse_patient_assignments_nurse_id_ended_at", "nurse_id", "ended_at"),
        Index("ix_nurse_patient_assignments_patient_id_ended_at", "patient_id", "ended_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    nurse_id: Mapped[int] = mapped_column(
        db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    patient_id: Mapped[int] = mapped_column(
        db.ForeignKey("patient_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    is_primary: Mapped[bool | None] = mapped_column(db.Boolean, default=False, server_default=false())
    assigned_at: Mapped[datetime] = mapped_column(db.DateTime, nullable=False, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(db.DateTime)
    assigned_by: Mapped[int | None] = mapped_column(db.ForeignKey("users.id", ondelete="RESTRICT"))

    nurse = relationship("User", back_populates="patient_assignments", foreign_keys=[nurse_id])
    patient = relationship("PatientProfile", back_populates="nurse_assignments")
    assigner = relationship("User", foreign_keys=[assigned_by])

    @property
    def is_active(self):
        return self.ended_at is None

    def __repr__(self):
        return (
            f"<NursePatientAssignment id={self.id} nurse_id={self.nurse_id} "
            f"patient_id={self.patient_id} active={self.is_active}>"
        )
