"""H. 症狀紀錄：symptom_records, symptom_record_values, symptom_record_value_options
(database-design.md §6.H).
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import ObservationMixin, TimestampMixin
from app.models.enums import ReviewStatus

# Multi-choice selections: pure association table (composite PK, no extra columns).
symptom_record_value_options = db.Table(
    "symptom_record_value_options",
    db.Column(
        "value_id",
        db.ForeignKey("symptom_record_values.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "option_id",
        db.ForeignKey("symptom_definition_options.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
)


class SymptomRecord(ObservationMixin, TimestampMixin, db.Model):
    __tablename__ = "symptom_records"
    __table_args__ = (
        Index("ix_symptom_records_patient_id_recorded_at", "patient_id", "recorded_at"),
        Index("ix_symptom_records_review_status_recorded_at", "review_status", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(
        db.ForeignKey("patient_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    form_id: Mapped[int | None] = mapped_column(
        db.ForeignKey("symptom_forms.id", ondelete="RESTRICT")
    )
    form_version: Mapped[int | None] = mapped_column(db.SmallInteger)
    nursing_assessment_id: Mapped[int | None] = mapped_column(
        db.ForeignKey("nursing_assessments.id", ondelete="SET NULL")
    )
    recorded_at: Mapped[datetime] = mapped_column(db.DateTime, nullable=False)
    reported_by: Mapped[int] = mapped_column(
        db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    review_status: Mapped[str] = mapped_column(
        db.String(20),
        nullable=False,
        default=ReviewStatus.SUBMITTED,
        server_default=ReviewStatus.SUBMITTED,
    )
    notes: Mapped[str | None] = mapped_column(db.Text)
    reviewed_by: Mapped[int | None] = mapped_column(db.ForeignKey("users.id", ondelete="RESTRICT"))
    reviewed_at: Mapped[datetime | None] = mapped_column(db.DateTime)

    patient = relationship("PatientProfile", back_populates="symptom_records")
    form = relationship("SymptomForm", back_populates="records")
    nursing_assessment = relationship("NursingAssessment", back_populates="symptom_records")
    cycle = relationship("ChemotherapyCycle", back_populates="symptom_records")
    reporter = relationship("User", foreign_keys=[reported_by])
    reviewer = relationship("User", foreign_keys=[reviewed_by])
    values = relationship(
        "SymptomRecordValue",
        back_populates="record",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self):
        return (
            f"<SymptomRecord id={self.id} patient_id={self.patient_id} "
            f"recorded_at={self.recorded_at} status={self.record_status!r}>"
        )


class SymptomRecordValue(TimestampMixin, db.Model):
    """One symptom answer. Only the column matching the definition's value_type is set;
    ``score`` is the normalized value used by trends, alert rules and AI features.
    """

    __tablename__ = "symptom_record_values"
    __table_args__ = (
        UniqueConstraint("symptom_record_id", "definition_id"),
        Index(
            "ix_symptom_record_values_definition_id_symptom_record_id",
            "definition_id",
            "symptom_record_id",
        ),
        CheckConstraint("ctcae_grade BETWEEN 0 AND 5", name="ctcae_grade_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    symptom_record_id: Mapped[int] = mapped_column(
        db.ForeignKey("symptom_records.id", ondelete="CASCADE"), nullable=False
    )
    definition_id: Mapped[int] = mapped_column(
        db.ForeignKey("symptom_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    value_numeric: Mapped[Decimal | None] = mapped_column(db.Numeric(8, 2))
    value_boolean: Mapped[bool | None] = mapped_column(db.Boolean)
    value_text: Mapped[str | None] = mapped_column(db.Text)
    option_id: Mapped[int | None] = mapped_column(
        db.ForeignKey("symptom_definition_options.id", ondelete="RESTRICT")
    )
    score: Mapped[Decimal | None] = mapped_column(db.Numeric(8, 2))
    ctcae_grade: Mapped[int | None] = mapped_column(db.SmallInteger)
    body_location: Mapped[str | None] = mapped_column(db.String(50))

    record = relationship("SymptomRecord", back_populates="values")
    definition = relationship("SymptomDefinition", back_populates="record_values")
    option = relationship("SymptomDefinitionOption", foreign_keys=[option_id])
    selected_options = relationship(
        "SymptomDefinitionOption",
        secondary=symptom_record_value_options,
        passive_deletes=True,
    )

    def __repr__(self):
        return (
            f"<SymptomRecordValue id={self.id} record_id={self.symptom_record_id} "
            f"definition_id={self.definition_id} score={self.score}>"
        )
