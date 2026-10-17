from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Identity, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db


class Subscription(db.Model):
    """En periode med betalt niveau. Free er fraværet af en aktiv periode.

    Perioder lægges i forlængelse af hinanden, så historikken bevares (specifikation 10.1).
    """

    __tablename__ = "subscriptions"
    __table_args__ = (
        CheckConstraint("tier IN ('premium', 'premium_plus')", name="tier"),
        CheckConstraint("source IN ('signup_bonus', 'admin', 'payment', 'early_bird')", name="source"),
        CheckConstraint("expires_at > starts_at", name="period"),
        Index("ix_subscriptions_user_period", "user_id", "starts_at", "expires_at"),
        # Premium ved oprettelse kan kun gives én gang pr. bruger, også ved samtidige forespørgsler.
        Index("uq_subscriptions_signup_bonus", "user_id", unique=True, postgresql_where=text("source = 'signup_bonus'")),
        # Det samme gælder Premium for livstid til tidlige fugle.
        Index("uq_subscriptions_early_bird", "user_id", unique=True, postgresql_where=text("source = 'early_bird'")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    tier: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    granted_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    @property
    def is_lifetime(self):
        return self.expires_at.year >= 9999
