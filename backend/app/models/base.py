"""Shared column types and mixins (database-design.md §2, §4)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from app.extensions import db
from app.models.enums import RecordStatus

# JSON on SQLite (stored as TEXT), JSONB on PostgreSQL.
JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow():
    """Naive UTC datetime at millisecond precision; all DATETIME columns store UTC without tzinfo.

    Milliseconds everywhere (DB values, API ``...sssZ`` strings, the frontend mock), so the same
    instant compares equal in every layer. The columns themselves keep microseconds (SQLite
    text / PostgreSQL TIMESTAMP(6)), so no schema change is needed.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


def generate_uuid():
    return str(uuid.uuid4())


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(db.DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        db.DateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class SoftDeleteMixin:
    """For master / configuration tables. Observation tables use ObservationMixin instead."""

    deleted_at: Mapped[datetime | None] = mapped_column(db.DateTime)

    @property
    def is_deleted(self):
        return self.deleted_at is not None


class ObservationMixin:
    """Append-only observation columns (database-design.md §4 觀察共通欄位).

    Records are never updated or deleted after submission: a correction is a new row
    whose ``amends_id`` points to the original, and the original becomes ``amended``.
    Each concrete model defines its own ``cycle`` relationship.
    """

    cycle_day: Mapped[int | None] = mapped_column(db.SmallInteger)
    source: Mapped[str] = mapped_column(db.String(20), nullable=False)
    record_status: Mapped[str] = mapped_column(
        db.String(20),
        nullable=False,
        default=RecordStatus.FINAL,
        server_default=RecordStatus.FINAL,
    )

    @declared_attr
    def cycle_id(cls) -> Mapped[int | None]:
        return mapped_column(db.ForeignKey("chemotherapy_cycles.id", ondelete="SET NULL"))

    @declared_attr
    def amends_id(cls) -> Mapped[int | None]:
        return mapped_column(db.ForeignKey(f"{cls.__tablename__}.id", ondelete="RESTRICT"))

    @declared_attr
    def amends(cls):
        return relationship(
            cls.__name__,
            remote_side=f"{cls.__name__}.id",
            foreign_keys=f"{cls.__name__}.amends_id",
            back_populates="amendments",
        )

    @declared_attr
    def amendments(cls):
        return relationship(
            cls.__name__,
            foreign_keys=f"{cls.__name__}.amends_id",
            back_populates="amends",
        )

    @property
    def is_final(self):
        return self.record_status == RecordStatus.FINAL
