from datetime import datetime

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Identity, Index, Integer, SmallInteger,
                        String, UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db


class ProfileImage(db.Model):
    """Et billede i en brugers offentlige samling eller private arkiv (specifikation 4).

    Filerne ligger uden for webserverens offentlige område. `public_id` er et tilfældigt id, så
    billeder ikke kan findes ved at gætte, men adgangen kontrolleres alligevel ved hver forespørgsel.
    """

    __tablename__ = "profile_images"
    __table_args__ = (
        CheckConstraint("collection IN ('public', 'private')", name="collection"),
        Index("ix_profile_images_user_collection", "user_id", "collection", "position"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    public_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    collection: Mapped[str] = mapped_column(String(10), nullable=False, server_default="public")
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    is_animated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # Slettede billeder skjules for alle, også ejeren, men filerne bevares til administration og moderation
    # i opbevaringsperioden (app/maintenance/retention.py). Kun administration og moderation kan se dem.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def is_deleted(self):
        return self.deleted_at is not None


class PrivateAccess(db.Model):
    """Adgang til en brugers private album (specifikation 4.2). Én række pr. par (ejer, den der beder om adgang).

    - pending: anmodningen venter på ejeren. Ejeren kan godkende, afvise eller lade den ligge.
    - approved: adgang til hele albummet (scope "all", også billeder, der lægges i senere) eller kun til de
      billeder, der står i PrivateAccessImage (scope "selected").
    - declined / revoked: afvist eller trukket tilbage. Der kan først anmodes igen efter en karensperiode
      (app/media/access.py), så en afvisning ikke kan omgås ved at anmode igen og igen.
    Adgang kan aldrig opnås via abonnement eller matchstatus, kun via ejerens godkendelse.
    """

    __tablename__ = "private_access"
    __table_args__ = (
        UniqueConstraint("owner_id", "viewer_id", name="uq_private_access_pair"),
        CheckConstraint("owner_id <> viewer_id", name="not_self"),
        CheckConstraint("status IN ('pending', 'approved', 'declined', 'revoked')", name="status"),
        CheckConstraint("scope IN ('all', 'selected')", name="scope"),
        Index("ix_private_access_owner_status", "owner_id", "status"),
        Index("ix_private_access_viewer", "viewer_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    viewer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="pending")
    scope: Mapped[str] = mapped_column(String(10), nullable=False, server_default="all")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PrivateAccessImage(db.Model):
    """De enkelte private billeder, en godkendt adgang med scope "selected" gælder."""

    __tablename__ = "private_access_images"

    access_id: Mapped[int] = mapped_column(ForeignKey("private_access.id", ondelete="CASCADE"), primary_key=True)
    image_id: Mapped[int] = mapped_column(ForeignKey("profile_images.id", ondelete="CASCADE"), primary_key=True)
