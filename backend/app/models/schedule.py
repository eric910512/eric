"""F. 治療行程：appointments, appointment_instructions (database-design.md §6.F)."""

from datetime import datetime

from sqlalchemy import Index, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import SoftDeleteMixin, TimestampMixin
from app.models.enums import AppointmentStatus


class Appointment(TimestampMixin, SoftDeleteMixin, db.Model):
    __tablename__ = "appointments"
    __table_args__ = (
        Index("ix_appointments_patient_id_scheduled_at", "patient_id", "scheduled_at"),
        Index("ix_appointments_scheduled_at_status", "scheduled_at", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(
        db.ForeignKey("patient_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    cycle_id: Mapped[int | None] = mapped_column(
        db.ForeignKey("chemotherapy_cycles.id", ondelete="SET NULL")
    )
    appointment_type: Mapped[str] = mapped_column(db.String(30), nullable=False)
    title: Mapped[str] = mapped_column(db.String(100), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(db.DateTime, nullable=False)
    duration_min: Mapped[int | None] = mapped_column(db.SmallInteger)
    location: Mapped[str | None] = mapped_column(db.String(100))
    status: Mapped[str] = mapped_column(
        db.String(20),
        nullable=False,
        default=AppointmentStatus.SCHEDULED,
        server_default=AppointmentStatus.SCHEDULED,
    )
    rescheduled_from_id: Mapped[int | None] = mapped_column(
        db.ForeignKey("appointments.id", ondelete="SET NULL")
    )
    notes: Mapped[str | None] = mapped_column(db.Text)
    created_by: Mapped[int | None] = mapped_column(db.ForeignKey("users.id", ondelete="RESTRICT"))

    patient = relationship("PatientProfile", back_populates="appointments")
    cycle = relationship("ChemotherapyCycle", back_populates="appointments")
    creator = relationship("User", foreign_keys=[created_by])
    rescheduled_from = relationship(
        "Appointment", remote_side=[id], back_populates="rescheduled_to"
    )
    rescheduled_to = relationship("Appointment", back_populates="rescheduled_from")
    nursing_assessments = relationship("NursingAssessment", back_populates="appointment")
    instructions = relationship(
        "AppointmentInstruction",
        back_populates="appointment",
        order_by="AppointmentInstruction.display_order",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self):
        return (
            f"<Appointment id={self.id} patient_id={self.patient_id} "
            f"type={self.appointment_type!r} scheduled_at={self.scheduled_at}>"
        )


class AppointmentInstruction(TimestampMixin, db.Model):
    """Preparation step for an appointment (fasting, check-in time, ...)."""

    __tablename__ = "appointment_instructions"

    id: Mapped[int] = mapped_column(primary_key=True)
    appointment_id: Mapped[int] = mapped_column(
        db.ForeignKey("appointments.id", ondelete="CASCADE"), nullable=False
    )
    instruction_type: Mapped[str] = mapped_column(db.String(20), nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(db.DateTime)
    text: Mapped[str] = mapped_column(db.String(255), nullable=False)
    is_highlighted: Mapped[bool | None] = mapped_column(db.Boolean, default=True, server_default=true())
    display_order: Mapped[int | None] = mapped_column(db.SmallInteger)

    appointment = relationship("Appointment", back_populates="instructions")

    def __repr__(self):
        return (
            f"<AppointmentInstruction id={self.id} appointment_id={self.appointment_id} "
            f"type={self.instruction_type!r}>"
        )
