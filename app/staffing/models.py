from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Identity, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db

STATUS_LABELS = {"pending": "Venter", "approved": "Godkendt", "rejected": "Afvist", "withdrawn": "Trukket tilbage"}


class ModeratorApplication(db.Model):
    """En ansøgning om at blive moderator. Godkendes den, får brugeren rollen moderator (staffing/services.py)."""

    __tablename__ = "moderator_applications"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'approved', 'rejected', 'withdrawn')", name="status"),
        CheckConstraint("length(motivation) BETWEEN 1 AND 2000", name="motivation_length"),
        # Højst én ansøgning, der venter, pr. bruger, også ved samtidige forespørgsler.
        Index("uq_moderator_applications_pending", "user_id", unique=True, postgresql_where=text("status = 'pending'")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    motivation: Mapped[str] = mapped_column(Text, nullable=False)
    experience: Mapped[str | None] = mapped_column(Text)
    availability: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # Begrundelsen ved en afvisning. Brugeren får den som besked fra Administrator.
    decision_note: Mapped[str | None] = mapped_column(Text)
