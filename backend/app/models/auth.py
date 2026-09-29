"""A. 帳號與認證：auth_tokens (database-design.md §6.A)."""

from datetime import datetime

from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin, utcnow


class AuthToken(TimestampMixin, db.Model):
    """Refresh / password-reset token. Only the hash is stored.

    Refresh tokens issued within one login session share a ``family_id``; reuse of a
    revoked token revokes the whole family.
    """

    __tablename__ = "auth_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_type: Mapped[str] = mapped_column(db.String(20), nullable=False)
    token_hash: Mapped[str] = mapped_column(db.String(255), unique=True, nullable=False)
    family_id: Mapped[str] = mapped_column(db.CHAR(36), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(db.DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(db.DateTime)
    used_at: Mapped[datetime | None] = mapped_column(db.DateTime)
    ip_address: Mapped[str | None] = mapped_column(db.String(45))
    user_agent: Mapped[str | None] = mapped_column(db.String(255))

    user = relationship("User", back_populates="auth_tokens")

    @property
    def is_usable(self):
        return self.revoked_at is None and self.used_at is None and self.expires_at > utcnow()

    def __repr__(self):
        return (
            f"<AuthToken id={self.id} user_id={self.user_id} type={self.token_type!r} "
            f"revoked={self.revoked_at is not None}>"
        )
