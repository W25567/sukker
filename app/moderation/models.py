"""Moderationssager og revisionslog (specifikation 9.1 og 9.3)."""
from datetime import date, datetime

from sqlalchemy import DDL, BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Identity, Index, String, Text, event, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db

REASONS = [
    ("chikane", "Chikane eller truende adfærd"),
    ("indhold", "Upassende eller uønsket indhold"),
    ("falsk", "Falsk profil eller identitet"),
    ("svindel", "Spam, svindel eller phishing"),
    ("mindreaarig", "Mistanke om mindreårig bruger"),
    ("andet", "Andet"),
]
REASON_LABELS = dict(REASONS) | {"klage": "Klage over afgørelse", "mistanke": "Moderators egen mistanke"}
HIGH_PRIORITY = {"falsk", "svindel", "mindreaarig"}

STATUSES = [("open", "Åben"), ("in_progress", "Under behandling"), ("closed", "Afsluttet")]
DECISIONS = [
    ("no_action", "Ingen handling"),
    ("warning", "Advarsel"),
    ("restricted", "Kontoen begrænses"),
    ("suspended", "Kontoen suspenderes"),
    ("lifted", "Begrænsning eller suspendering ophæves"),
    ("content_removed", "Indhold fjernet"),
]


class ModerationCase(db.Model):
    __tablename__ = "moderation_cases"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'in_progress', 'closed')", name="status"),
        CheckConstraint("priority IN ('high', 'normal')", name="priority"),
        CheckConstraint("source IN ('report', 'moderator', 'appeal', 'signal')", name="source"),
        Index("ix_moderation_cases_queue", "status", "priority", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    priority: Mapped[str] = mapped_column(String(10), nullable=False, server_default="normal")
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="open")
    subject_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    reporter_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    assigned_to_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    description: Mapped[str | None] = mapped_column(Text)
    # Afgrænset kopi af det anmeldte, så dokumentationen ikke forsvinder, hvis det redigeres eller slettes.
    evidence: Mapped[dict | None] = mapped_column(JSONB)
    decision: Mapped[str | None] = mapped_column(String(20))
    decision_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Beviser slettes efter opbevaringsperioden, regnet fra sagen blev afsluttet. I alvorlige sager kan en
    # moderator forlænge opbevaringen med en begrundelse, højst et år ad gangen (specifikation 13.1).
    evidence_retained_until: Mapped[date | None] = mapped_column(Date)
    evidence_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CaseEvent(db.Model):
    """Sagens revisionshistorik."""

    __tablename__ = "moderation_case_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("moderation_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ModeratorAccessLog(db.Model):
    """Revisionslog over moderatorers adgang til brugerdata (specifikation 9.3).

    Rækkerne kan ikke ændres eller slettes. Det håndhæves af en trigger i databasen. Derfor er der ingen
    fremmednøgler, der kunne udløse ændringer, og navnene gemmes som de var på tidspunktet.
    """

    __tablename__ = "moderator_access_log"
    __table_args__ = (Index("ix_moderator_access_recent", "moderator_id", "target_user_id", "at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    moderator_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    moderator_username: Mapped[str] = mapped_column(String(30), nullable=False)
    target_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_username: Mapped[str] = mapped_column(String(30), nullable=False)
    case_id: Mapped[int | None] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


# Samme SQL står i migrationen for M6. Her bruges den, når tabellerne oprettes direkte (tests).
APPEND_ONLY_SQL = """
CREATE OR REPLACE FUNCTION sukker.moderator_access_log_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'moderator_access_log kan ikke ændres eller slettes';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER moderator_access_log_append_only
    BEFORE UPDATE OR DELETE ON sukker.moderator_access_log
    FOR EACH ROW EXECUTE FUNCTION sukker.moderator_access_log_append_only();
"""

event.listen(ModeratorAccessLog.__table__, "after_create", DDL(APPEND_ONLY_SQL))
