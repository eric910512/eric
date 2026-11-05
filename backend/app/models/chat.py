"""病人與護理師聊天室：chat_messages, chat_read_states (Phase 1 純文字聊天室).

One conversation per patient, shared by the patient and the nurses currently assigned to them
(access = ``ensure_can_view_patient``; admin has no chat access in this version). Messages are
append-only text. ``auto_reply_date`` is set only on the system auto reply and is unique per
patient, so at most one auto reply exists per patient per Asia/Taipei day — the database enforces
it, also when two messages are sent at the same time.
"""

from datetime import date, datetime

from sqlalchemy import CheckConstraint, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import utcnow


class ChatMessage(db.Model):
    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint("patient_id", "auto_reply_date", name="uq_chat_messages_patient_id_auto_reply_date"),
        Index("ix_chat_messages_patient_id_id", "patient_id", "id"),
        Index("ix_chat_messages_sender_id_created_at", "sender_id", "created_at"),  # flood guard
        CheckConstraint("sender_role IN ('patient', 'nurse', 'system')", name="sender_role"),
        CheckConstraint("kind IN ('text', 'auto_reply')", name="kind"),
        CheckConstraint(
            "(kind = 'auto_reply' AND sender_role = 'system' AND sender_id IS NULL AND auto_reply_date IS NOT NULL)"
            " OR (kind = 'text' AND sender_role <> 'system' AND sender_id IS NOT NULL AND auto_reply_date IS NULL)",
            name="sender_kind",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(
        db.ForeignKey("patient_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    sender_id: Mapped[int | None] = mapped_column(db.ForeignKey("users.id", ondelete="RESTRICT"))
    sender_role: Mapped[str] = mapped_column(db.String(10), nullable=False)
    kind: Mapped[str] = mapped_column(db.String(20), nullable=False)
    body: Mapped[str] = mapped_column(db.Text, nullable=False)
    auto_reply_date: Mapped[date | None] = mapped_column(db.Date)  # Asia/Taipei day; auto replies only
    created_at: Mapped[datetime] = mapped_column(db.DateTime, nullable=False, default=utcnow)

    patient = relationship("PatientProfile")
    sender = relationship("User")

    def __repr__(self):
        return f"<ChatMessage id={self.id} patient_id={self.patient_id} role={self.sender_role!r} kind={self.kind!r}>"


class ChatReadState(db.Model):
    """How far one user has read one patient's conversation (unread = later messages from others)."""

    __tablename__ = "chat_read_states"
    __table_args__ = (UniqueConstraint("patient_id", "user_id", name="uq_chat_read_states_patient_id_user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(
        db.ForeignKey("patient_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    last_read_message_id: Mapped[int] = mapped_column(db.Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    def __repr__(self):
        return f"<ChatReadState patient_id={self.patient_id} user_id={self.user_id} last={self.last_read_message_id}>"
