"""皮膚照片追蹤 (Phase 1b): symptom_photos, patient_consents.

``symptom_photos`` holds metadata only — the image bytes live in a private storage backend
(app/services/photo_storage.py), never in the database and never in a public folder. A photo is
uploaded first (``pending``, owned by the uploader) and attached to a symptom record in the same
transaction that creates the record (``attached``). Pending photos that never get attached are
discarded by the clean-up (``discarded`` + reason); attached photos are part of the care record and
are never deleted here (retention policy pending the hospital).

``patient_consents`` records which version of a consent text a patient accepted (with the hash of
that exact text) and when it was revoked. The texts themselves are versioned in code
(app/modules/photo/consent.py) and are drafts pending the hospital's review.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, Index, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin, generate_uuid


class SymptomPhoto(TimestampMixin, db.Model):
    __tablename__ = "symptom_photos"
    __table_args__ = (
        Index("ix_symptom_photos_patient_id_status_created_at", "patient_id", "status", "created_at"),
        Index("ix_symptom_photos_symptom_record_id", "symptom_record_id"),
        Index("ix_symptom_photos_uploaded_by_created_at", "uploaded_by", "created_at"),  # rate limit
        Index("ix_symptom_photos_status_created_at", "status", "created_at"),  # clean-up of pending photos
        CheckConstraint("status IN ('pending', 'attached', 'discarded')", name="status"),
        CheckConstraint(
            "(status = 'attached' AND symptom_record_id IS NOT NULL AND attached_at IS NOT NULL AND deleted_at IS NULL)"
            " OR (status = 'pending' AND symptom_record_id IS NULL AND deleted_at IS NULL)"
            " OR (status = 'discarded' AND symptom_record_id IS NULL AND deleted_at IS NOT NULL AND delete_reason IS NOT NULL)",
            name="status_fields",
        ),
        CheckConstraint("delete_reason IS NULL OR delete_reason IN ('removed_before_submit', 'expired_unattached')",
                        name="delete_reason"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(db.String(36), unique=True, nullable=False, default=generate_uuid)
    patient_id: Mapped[int] = mapped_column(db.ForeignKey("patient_profiles.id", ondelete="RESTRICT"), nullable=False)
    symptom_record_id: Mapped[int | None] = mapped_column(db.ForeignKey("symptom_records.id", ondelete="RESTRICT"))
    uploaded_by: Mapped[int] = mapped_column(db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(db.String(20), nullable=False, default="pending")
    storage_backend: Mapped[str] = mapped_column(db.String(20), nullable=False)
    storage_key: Mapped[str] = mapped_column(db.String(200), unique=True, nullable=False)  # random, never the file name
    thumb_storage_key: Mapped[str] = mapped_column(db.String(200), unique=True, nullable=False)
    content_type: Mapped[str] = mapped_column(db.String(50), nullable=False)  # of the stored (re-encoded) image
    file_size: Mapped[int] = mapped_column(nullable=False)
    width: Mapped[int] = mapped_column(nullable=False)
    height: Mapped[int] = mapped_column(nullable=False)
    thumb_file_size: Mapped[int] = mapped_column(nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(db.String(64), nullable=False)  # of the stored image
    source_sha256: Mapped[str] = mapped_column(db.String(64), nullable=False)  # of the uploaded bytes: duplicate detection
    body_site: Mapped[str | None] = mapped_column(db.String(50))  # code of PHOTO_BODY_SITES (pending clinical review)
    attached_at: Mapped[datetime | None] = mapped_column(db.DateTime)
    deleted_at: Mapped[datetime | None] = mapped_column(db.DateTime)
    deleted_by: Mapped[int | None] = mapped_column(db.ForeignKey("users.id", ondelete="RESTRICT"))
    delete_reason: Mapped[str | None] = mapped_column(db.String(30))

    patient = relationship("PatientProfile")
    record = relationship("SymptomRecord", backref="photos")
    uploader = relationship("User", foreign_keys=[uploaded_by])

    def __repr__(self):
        return f"<SymptomPhoto {self.public_id} status={self.status!r}>"


class PatientConsent(TimestampMixin, db.Model):
    __tablename__ = "patient_consents"
    __table_args__ = (
        Index("ix_patient_consents_patient_id_consent_type_accepted_at", "patient_id", "consent_type", "accepted_at"),
        # at most one active (not revoked) consent per patient and type
        Index("uq_patient_consents_active", "patient_id", "consent_type", unique=True,
              sqlite_where=text("revoked_at IS NULL"), postgresql_where=text("revoked_at IS NULL")),
        CheckConstraint("consent_type IN ('skin_photo')", name="consent_type"),
        CheckConstraint("(revoked_at IS NULL) = (revoked_by IS NULL)", name="revoked_fields"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(db.ForeignKey("patient_profiles.id", ondelete="RESTRICT"), nullable=False)
    consent_type: Mapped[str] = mapped_column(db.String(30), nullable=False)
    version: Mapped[str] = mapped_column(db.String(40), nullable=False)
    content_sha256: Mapped[str] = mapped_column(db.String(64), nullable=False)  # the exact text that was shown
    accepted_by: Mapped[int] = mapped_column(db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(db.DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(db.DateTime)
    revoked_by: Mapped[int | None] = mapped_column(db.ForeignKey("users.id", ondelete="RESTRICT"))

    def __repr__(self):
        return f"<PatientConsent {self.consent_type} v{self.version} patient={self.patient_id}>"
