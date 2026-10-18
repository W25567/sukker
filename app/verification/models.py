from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Identity, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db

STATUS_LABELS = {"pending": "Venter", "approved": "Godkendt", "rejected": "Afvist", "revoked": "Fjernet"}


class PhotoVerification(db.Model):
    """En anmodning om verificering. Selfien ligger under <MEDIA_ROOT>/verification/<public_id>/ og slettes, når
    anmodningen er afgjort (`public_id` sættes da til NULL)."""

    __tablename__ = "photo_verifications"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'approved', 'rejected', 'revoked')", name="status"),
        Index("ix_photo_verifications_status", "status", "created_at"),
        # Højst én anmodning, der venter, pr. bruger
        Index("uq_photo_verifications_pending", "user_id", unique=True, postgresql_where=text("status = 'pending'")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="pending")
    gesture: Mapped[str] = mapped_column(String(20), nullable=False)
    public_id: Mapped[str | None] = mapped_column(String(32), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    note: Mapped[str | None] = mapped_column(Text)

    collection = "verification"  # mappen i MEDIA_ROOT (media/storage.py)
