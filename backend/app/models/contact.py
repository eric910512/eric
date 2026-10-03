"""O. 病人聯絡資料：patient_contacts (database-design.md §6.C1, Phase 2 table brought forward).

Direct identifiers live apart from ``patient_profiles`` (§3 隱私政策). This sprint only creates the
notification email columns; phone / address / medical record number stay Phase 2.

The contact email is **not** the login account (``users.email``): the patient maintains it, it must
be verified before anything is sent to it, and changing it clears the verification.
``email_verified`` is derived from ``email_verified_at`` (no separate boolean).
"""

from datetime import datetime

from sqlalchemy import false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin


class PatientContact(TimestampMixin, db.Model):
    __tablename__ = "patient_contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(
        db.ForeignKey("patient_profiles.id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    email: Mapped[str | None] = mapped_column(db.String(255))
    email_verified_at: Mapped[datetime | None] = mapped_column(db.DateTime)
    email_notification_enabled: Mapped[bool] = mapped_column(
        db.Boolean, nullable=False, default=False, server_default=false()
    )

    patient = relationship("PatientProfile", back_populates="contact")

    @property
    def email_verified(self):
        return self.email is not None and self.email_verified_at is not None

    @property
    def can_receive_email(self):
        """Email notifications go out only to a verified address with notifications switched on."""
        return self.email_verified and bool(self.email_notification_enabled)

    def __repr__(self):
        return f"<PatientContact id={self.id} patient_id={self.patient_id} verified={self.email_verified}>"
