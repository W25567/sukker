"""Fejlrapporter fra brugerne med op til tre skærmbilleder."""
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Identity, Integer, SmallInteger, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db

MODULES = [("udforsk", "Udforsk"), ("likes", "Likes"), ("beskeder", "Beskeder"), ("profil", "Min profil"),
           ("konto", "Konto"), ("andet", "Andet")]
MODULE_LABELS = dict(MODULES)


class BugReport(db.Model):
    __tablename__ = "bug_reports"
    __table_args__ = (
        CheckConstraint("module IN ('udforsk', 'likes', 'beskeder', 'profil', 'konto', 'andet')", name="module"),
        CheckConstraint("status IN ('open', 'closed')", name="status"),
        CheckConstraint("length(description) BETWEEN 1 AND 4000", name="description_length"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    # Rapporten bevares, selvom brugeren senere slettes endeligt.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    module: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="open")
    admin_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class BugReportImage(db.Model):
    """Et skærmbillede. Omkodes og renses for metadata som profilbilleder (media/processing.py) og gemmes
    under <MEDIA_ROOT>/bugreports/<public_id>/."""

    __tablename__ = "bug_report_images"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("bug_reports.id", ondelete="CASCADE"), nullable=False, index=True)
    public_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    collection = "bugreports"  # mappen i MEDIA_ROOT (media/storage.py)
