"""Samtaler og beskeder mellem matches (specifikation 8 og 7.2)."""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Identity, Index, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db


class Conversation(db.Model):
    """Én samtale pr. par. Samtalen genbruges, hvis de to matcher igen, så historikken kommer tilbage.

    Brugerne gemmes som et ordnet par. Sletter en af dem kontoen, bliver feltet NULL, og den anden
    ser afsenderen som "Slettet bruger".
    """

    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("user_low_id", "user_high_id", name="uq_conversations_pair"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_low_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    user_high_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Sat, når matchet fjernes. Så er samtalen skjult for begge, indtil de matcher igen.
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def other_id(self, user_id):
        return self.user_high_id if user_id == self.user_low_id else self.user_low_id


class ConversationMember(db.Model):
    """Hver deltagers egen tilstand: læst, arkiveret og "slettet for mig"."""

    __tablename__ = "conversation_members"

    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # Beskeder fra før dette tidspunkt er slettet for denne deltager og kommer aldrig igen.
    cleared_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(db.Model):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("length(body) BETWEEN 1 AND 2000", name="body_length"),
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    sender_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class MessageDeletion(db.Model):
    """En besked, som én deltager har slettet for sig selv. Den anden part beholder den (specifikation 7.2)."""

    __tablename__ = "message_deletions"

    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
