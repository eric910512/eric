"""B. 稽核與請求控制：audit_logs, idempotency_records (database-design.md §6.B)."""

from datetime import datetime, timedelta

from sqlalchemy import Index, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import JSONType, TimestampMixin, utcnow
from app.models.enums import IdempotencyStatus

IDEMPOTENCY_TTL = timedelta(hours=24)


class AuditLog(db.Model):
    """Append-only audit trail. No ``updated_at``; rows must never be updated or deleted.

    ``changes`` holds only the changed fields, with sensitive values masked by the writer.
    Relationships are one-directional on purpose: this table grows large, so User /
    PatientProfile do not expose an eager collection of audit rows.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_patient_id_occurred_at", "patient_id", "occurred_at"),
        Index("ix_audit_logs_actor_user_id_occurred_at", "actor_user_id", "occurred_at"),
        Index("ix_audit_logs_resource_type_resource_id", "resource_type", "resource_id"),
        Index("ix_audit_logs_action_occurred_at", "action", "occurred_at"),
    )

    # BIGINT on PostgreSQL; SQLite requires INTEGER PRIMARY KEY for autoincrement.
    id: Mapped[int] = mapped_column(
        db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True
    )
    occurred_at: Mapped[datetime] = mapped_column(db.DateTime, nullable=False, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(db.DateTime, nullable=False, default=utcnow)
    actor_user_id: Mapped[int | None] = mapped_column(
        db.ForeignKey("users.id", ondelete="RESTRICT")
    )
    actor_role: Mapped[str | None] = mapped_column(db.String(20))
    actor_identifier: Mapped[str | None] = mapped_column(db.String(255))
    category: Mapped[str] = mapped_column(db.String(20), nullable=False)
    action: Mapped[str] = mapped_column(db.String(30), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(db.String(50))
    resource_id: Mapped[str | None] = mapped_column(db.String(50))
    patient_id: Mapped[int | None] = mapped_column(
        db.ForeignKey("patient_profiles.id", ondelete="RESTRICT")
    )
    changes: Mapped[dict | None] = mapped_column(JSONType)
    outcome: Mapped[str] = mapped_column(db.String(10), nullable=False)
    reason: Mapped[str | None] = mapped_column(db.String(255))
    request_id: Mapped[str | None] = mapped_column(db.CHAR(36))
    http_method: Mapped[str | None] = mapped_column(db.String(10))
    endpoint: Mapped[str | None] = mapped_column(db.String(255))
    ip_address: Mapped[str | None] = mapped_column(db.String(45))
    user_agent: Mapped[str | None] = mapped_column(db.String(255))
    prev_hash: Mapped[str | None] = mapped_column(db.CHAR(64))
    row_hash: Mapped[str | None] = mapped_column(db.CHAR(64))

    actor = relationship("User", foreign_keys=[actor_user_id])
    patient = relationship("PatientProfile", foreign_keys=[patient_id])

    def __repr__(self):
        return (
            f"<AuditLog id={self.id} action={self.action!r} "
            f"resource={self.resource_type}:{self.resource_id} outcome={self.outcome!r}>"
        )


@event.listens_for(AuditLog, "before_update")
def _reject_audit_update(mapper, connection, target):
    raise RuntimeError("audit_logs is append-only; updates are not allowed")


@event.listens_for(AuditLog, "before_delete")
def _reject_audit_delete(mapper, connection, target):
    raise RuntimeError("audit_logs is append-only; deletes are not allowed")


def _default_idempotency_expiry():
    return utcnow() + IDEMPOTENCY_TTL


class IdempotencyRecord(TimestampMixin, db.Model):
    """Guards write endpoints against duplicate submission (``Idempotency-Key`` header).

    Stores only a pointer to the created resource, never the response body, so this
    table holds no copy of patient data.
    """

    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
        Index("ix_idempotency_records_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(db.String(64), nullable=False)
    http_method: Mapped[str] = mapped_column(db.String(10), nullable=False)
    endpoint: Mapped[str] = mapped_column(db.String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(db.CHAR(64), nullable=False)
    status: Mapped[str] = mapped_column(
        db.String(20),
        nullable=False,
        default=IdempotencyStatus.PROCESSING,
        server_default=IdempotencyStatus.PROCESSING,
    )
    response_status: Mapped[int | None] = mapped_column(db.SmallInteger)
    resource_type: Mapped[str | None] = mapped_column(db.String(50))
    resource_id: Mapped[str | None] = mapped_column(db.String(50))
    error_code: Mapped[str | None] = mapped_column(db.String(50))
    completed_at: Mapped[datetime | None] = mapped_column(db.DateTime)
    expires_at: Mapped[datetime] = mapped_column(
        db.DateTime, nullable=False, default=_default_idempotency_expiry
    )

    user = relationship("User", foreign_keys=[user_id])

    @property
    def is_expired(self):
        return self.expires_at <= utcnow()

    def __repr__(self):
        return (
            f"<IdempotencyRecord id={self.id} user_id={self.user_id} "
            f"key={self.idempotency_key!r} status={self.status!r}>"
        )
