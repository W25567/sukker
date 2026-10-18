"""Skjul og blokér (specifikation 9.2)."""
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db


class UserBlock(db.Model):
    """Kontakt- og adgangsforbud i begge retninger, uanset hvem der blokerede."""

    __tablename__ = "user_blocks"
    __table_args__ = (CheckConstraint("blocker_id <> blocked_id", name="not_self"),)

    blocker_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    blocked_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ProfileVisit(db.Model):
    """Hvor mange gange en bruger har åbnet en bestemt profil pr. dag. Kun et antal, ingen tidspunkter for de
    enkelte visninger. Bruges til at opdage usædvanlig interesse for én profil (moderation/signals.py) og slettes
    efter VISIT_KEEP_DAYS (maintenance/retention.py). Ses aldrig af den, hvis profil er besøgt."""

    __tablename__ = "profile_visits"
    __table_args__ = (CheckConstraint("viewer_id <> target_id", name="not_self"),)

    viewer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    views: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    last_view_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DistanceMask(db.Model):
    """Afstanden mellem `viewer` og `target` vises altid som "I nærheden" for `viewer`, uanset hvor de bor.
    Oprettes, når `viewer` har vist usædvanlig interesse for profilen, så afstanden ikke kan bruges til at finde
    personen, fx ved at oprette konti flere steder i landet (moderation/signals.py)."""

    __tablename__ = "distance_masks"
    __table_args__ = (CheckConstraint("viewer_id <> target_id", name="not_self"),)

    viewer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("moderation_cases.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class UserHide(db.Model):
    """En visningsindstilling for én bruger. Påvirker ikke kontakt."""

    __tablename__ = "user_hides"
    __table_args__ = (CheckConstraint("hider_id <> hidden_id", name="not_self"),)

    hider_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    hidden_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
