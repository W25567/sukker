from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Identity, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db


class Like(db.Model):
    """Et like er privat, indtil der opstår et match (specifikation 7.1)."""

    __tablename__ = "likes"
    __table_args__ = (
        UniqueConstraint("liker_id", "likee_id", name="uq_likes_pair"),
        CheckConstraint("liker_id <> likee_id", name="not_self"),
        Index("ix_likes_likee", "likee_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    liker_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    likee_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Match(db.Model):
    """Ét match pr. par. Et fjernet match genaktiveres, hvis de to matcher igen (specifikation 7.2)."""

    __tablename__ = "matches"
    __table_args__ = (
        UniqueConstraint("user_low_id", "user_high_id", name="uq_matches_pair"),
        CheckConstraint("user_low_id < user_high_id", name="ordered_pair"),
        Index("ix_matches_high", "user_high_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_low_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    user_high_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    matched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    @property
    def is_active(self):
        return self.removed_at is None

    def other_id(self, user_id):
        return self.user_high_id if user_id == self.user_low_id else self.user_low_id


class MatchEvent(db.Model):
    """Historik over matches og fjernelser. Bruges til markeringen "Match fjernet" i samtaler (M5)."""

    __tablename__ = "match_events"
    __table_args__ = (CheckConstraint("kind IN ('matched', 'removed')", name="kind"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
