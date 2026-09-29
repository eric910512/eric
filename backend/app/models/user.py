"""A. 帳號與認證：roles, users (database-design.md §6.A)."""

from datetime import datetime

from sqlalchemy import true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin, generate_uuid


class Role(TimestampMixin, db.Model):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(db.String(20), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(db.String(255))

    users = relationship("User", back_populates="role")

    def __repr__(self):
        return f"<Role id={self.id} name={self.name!r}>"


class User(TimestampMixin, db.Model):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(
        db.CHAR(36), unique=True, nullable=False, default=generate_uuid
    )
    role_id: Mapped[int] = mapped_column(
        db.ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False
    )
    email: Mapped[str] = mapped_column(db.String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(db.String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(db.String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        db.Boolean, nullable=False, default=True, server_default=true()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(db.DateTime)
    failed_login_count: Mapped[int | None] = mapped_column(db.Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(db.DateTime)
    password_changed_at: Mapped[datetime | None] = mapped_column(db.DateTime)

    role = relationship("Role", back_populates="users")
    patient_profile = relationship(
        "PatientProfile",
        back_populates="user",
        uselist=False,
        foreign_keys="PatientProfile.user_id",
    )
    nurse_profile = relationship(
        "NurseProfile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    auth_tokens = relationship(
        "AuthToken",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    patient_assignments = relationship(
        "NursePatientAssignment",
        back_populates="nurse",
        foreign_keys="NursePatientAssignment.nurse_id",
    )
    notifications = relationship(
        "Notification",
        back_populates="recipient",
        foreign_keys="Notification.recipient_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def role_name(self):
        return self.role.name if self.role else None

    def __repr__(self):
        return f"<User id={self.id} email={self.email!r} role={self.role_name!r}>"


class NurseProfile(TimestampMixin, db.Model):
    __tablename__ = "nurse_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        db.ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    staff_code: Mapped[str | None] = mapped_column(db.String(30), unique=True)
    department: Mapped[str | None] = mapped_column(db.String(100))
    title: Mapped[str | None] = mapped_column(db.String(50))

    user = relationship("User", back_populates="nurse_profile")

    def __repr__(self):
        return f"<NurseProfile id={self.id} user_id={self.user_id} staff_code={self.staff_code!r}>"
