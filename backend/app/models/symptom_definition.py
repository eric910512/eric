"""G. 症狀定義：symptom_categories, symptom_definitions, symptom_definition_options,
symptom_forms, symptom_form_items (database-design.md §6.G).
"""

from decimal import Decimal

from sqlalchemy import UniqueConstraint, false, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import JSONType, TimestampMixin
from app.models.enums import FormAvailability


class SymptomCategory(TimestampMixin, db.Model):
    __tablename__ = "symptom_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(db.String(30), unique=True, nullable=False)
    name_zh: Mapped[str] = mapped_column(db.String(50), nullable=False)
    display_order: Mapped[int | None] = mapped_column(db.SmallInteger)

    definitions = relationship("SymptomDefinition", back_populates="category")

    def __repr__(self):
        return f"<SymptomCategory id={self.id} code={self.code!r}>"


class SymptomDefinition(TimestampMixin, db.Model):
    """A symptom item definition.

    Once referenced by any SymptomRecordValue, semantic fields (value_type, min/max,
    option scores) must not change; create a new definition with ``supersedes_id``
    instead (enforced in the service layer).
    """

    __tablename__ = "symptom_definitions"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(db.String(50), unique=True, nullable=False)
    category_id: Mapped[int | None] = mapped_column(
        db.ForeignKey("symptom_categories.id", ondelete="RESTRICT")
    )
    name_zh: Mapped[str] = mapped_column(db.String(100), nullable=False)
    name_en: Mapped[str | None] = mapped_column(db.String(100))
    question_text: Mapped[str | None] = mapped_column(db.String(255))
    help_text: Mapped[str | None] = mapped_column(db.Text)
    value_type: Mapped[str] = mapped_column(db.String(20), nullable=False)
    min_value: Mapped[Decimal | None] = mapped_column(db.Numeric(8, 2))
    max_value: Mapped[Decimal | None] = mapped_column(db.Numeric(8, 2))
    step: Mapped[Decimal | None] = mapped_column(db.Numeric(8, 2))
    unit: Mapped[str | None] = mapped_column(db.String(20))
    min_label: Mapped[str | None] = mapped_column(db.String(50))
    max_label: Mapped[str | None] = mapped_column(db.String(50))
    higher_is_worse: Mapped[bool] = mapped_column(
        db.Boolean, nullable=False, default=True, server_default=true()
    )
    ctcae_term: Mapped[str | None] = mapped_column(db.String(100))
    is_system: Mapped[bool | None] = mapped_column(db.Boolean, default=False, server_default=false())
    is_active: Mapped[bool | None] = mapped_column(db.Boolean, default=True, server_default=true())
    supersedes_id: Mapped[int | None] = mapped_column(
        db.ForeignKey("symptom_definitions.id", ondelete="RESTRICT")
    )
    created_by: Mapped[int | None] = mapped_column(db.ForeignKey("users.id", ondelete="RESTRICT"))

    category = relationship("SymptomCategory", back_populates="definitions")
    options = relationship(
        "SymptomDefinitionOption",
        back_populates="definition",
        order_by="SymptomDefinitionOption.display_order",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    supersedes = relationship(
        "SymptomDefinition", remote_side=[id], back_populates="superseded_by"
    )
    superseded_by = relationship("SymptomDefinition", back_populates="supersedes")
    creator = relationship("User", foreign_keys=[created_by])
    form_items = relationship("SymptomFormItem", back_populates="definition")
    record_values = relationship("SymptomRecordValue", back_populates="definition")
    alert_rules = relationship("AlertRule", back_populates="symptom_definition")

    def __repr__(self):
        return f"<SymptomDefinition id={self.id} code={self.code!r} value_type={self.value_type!r}>"


class SymptomDefinitionOption(TimestampMixin, db.Model):
    __tablename__ = "symptom_definition_options"
    __table_args__ = (UniqueConstraint("definition_id", "value_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    definition_id: Mapped[int] = mapped_column(
        db.ForeignKey("symptom_definitions.id", ondelete="CASCADE"), nullable=False
    )
    value_code: Mapped[str] = mapped_column(db.String(50), nullable=False)
    label_zh: Mapped[str] = mapped_column(db.String(100), nullable=False)
    score: Mapped[Decimal | None] = mapped_column(db.Numeric(6, 2))
    display_order: Mapped[int | None] = mapped_column(db.SmallInteger)
    is_active: Mapped[bool | None] = mapped_column(db.Boolean, default=True, server_default=true())

    definition = relationship("SymptomDefinition", back_populates="options")

    def __repr__(self):
        return (
            f"<SymptomDefinitionOption id={self.id} definition_id={self.definition_id} "
            f"value_code={self.value_code!r}>"
        )


class SymptomForm(TimestampMixin, db.Model):
    __tablename__ = "symptom_forms"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(db.String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(db.String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(db.Text)
    intended_for: Mapped[str] = mapped_column(db.String(20), nullable=False)
    recall_period_hours: Mapped[int | None] = mapped_column(db.SmallInteger)
    availability: Mapped[str] = mapped_column(
        db.String(20),
        nullable=False,
        default=FormAvailability.ALWAYS,
        server_default=FormAvailability.ALWAYS,
    )
    version: Mapped[int] = mapped_column(
        db.SmallInteger, nullable=False, default=1, server_default="1"
    )
    is_active: Mapped[bool | None] = mapped_column(db.Boolean, default=True, server_default=true())
    created_by: Mapped[int | None] = mapped_column(db.ForeignKey("users.id", ondelete="RESTRICT"))

    items = relationship(
        "SymptomFormItem",
        back_populates="form",
        order_by="SymptomFormItem.display_order",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    creator = relationship("User", foreign_keys=[created_by])
    records = relationship("SymptomRecord", back_populates="form")

    def __repr__(self):
        return f"<SymptomForm id={self.id} code={self.code!r} version={self.version}>"


class SymptomFormItem(TimestampMixin, db.Model):
    __tablename__ = "symptom_form_items"
    __table_args__ = (UniqueConstraint("form_id", "definition_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    form_id: Mapped[int] = mapped_column(
        db.ForeignKey("symptom_forms.id", ondelete="CASCADE"), nullable=False
    )
    definition_id: Mapped[int] = mapped_column(
        db.ForeignKey("symptom_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    display_order: Mapped[int] = mapped_column(db.SmallInteger, nullable=False)
    is_required: Mapped[bool | None] = mapped_column(db.Boolean, default=False, server_default=false())
    display_condition: Mapped[dict | None] = mapped_column(JSONType)

    form = relationship("SymptomForm", back_populates="items")
    definition = relationship("SymptomDefinition", back_populates="form_items")

    def __repr__(self):
        return (
            f"<SymptomFormItem id={self.id} form_id={self.form_id} "
            f"definition_id={self.definition_id} order={self.display_order}>"
        )
