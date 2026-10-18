import secrets
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Identity, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db

HANDLE_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"  # uden tegn, der let forveksles (l, o, 0, 1)


def generate_handle():
    return "".join(secrets.choice(HANDLE_ALPHABET) for _ in range(10))


class User(db.Model):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    # Det navn, andre ser. Behøver ikke være unikt: kun e-mailen er unik. Kolonnen hedder "username" i
    # databasen af hensyn til bagudkompatible migrationer.
    display_name: Mapped[str] = mapped_column("username", String(30), nullable=False,
                                              comment="Vist navn (display_name i koden). Ikke unikt.")
    # Tilfældigt, offentligt id til URL'er (/bruger/<handle>). Afslører hverken navn eller antal brugere.
    handle: Mapped[str] = mapped_column(String(12), nullable=False, default=lambda: generate_handle(),
                                        server_default=text("substr(md5(random()::text || clock_timestamp()::text), 1, 10)"))
    email: Mapped[str] = mapped_column(String(254), nullable=False)  # gemmes altid med små bogstaver
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    birthdate: Mapped[date] = mapped_column(Date, nullable=False)
    postcode: Mapped[str] = mapped_column(String(4), nullable=False)
    # Afstande beregnes ud fra kommunen, ikke postnummeret (specifikation 5). NULL indtil kommunedata er indlæst.
    kommune_code: Mapped[str | None] = mapped_column(ForeignKey("kommuner.code"))
    terms_accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Den version af vilkår og privatlivspolitik, brugeren senest har accepteret (app/pages/legal.py).
    terms_version: Mapped[str] = mapped_column(String(20), nullable=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Hvornår brugeren har set velkomstsiden. NULL = vises ved næste besøg (app/pages/onboarding.py).
    welcomed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Hvornår brugeren sidst åbnede Likes. Nyere matches tælles som nye i menuen (app/notifications.py).
    matches_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # Roller er adskilte: moderator behandler sager, administrator styrer platformen (specifikation 9.3).
    role: Mapped[str] = mapped_column(String(20), nullable=False, server_default="user")
    # active, restricted (kan hverken ses, like eller skrive) eller suspended (kan ikke logge ind).
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Testkonti til at afprøve adgangsregler. De vises aldrig for rigtige brugere og tæller ikke med i målinger.
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # Slettede konti (status "deleted") lukkes og skjules for alle andre, men data bevares til administration og
    # moderation i opbevaringsperioden, så sletning ikke kan bruges til at skjule spor (app/account/services.py).
    # E-mailen flyttes hertil, så adressen bliver fri til en ny konto.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_email: Mapped[str | None] = mapped_column(String(254))
    # Sat, når en administrator har slettet et upassende visningsnavn. Nulstilles, når brugeren vælger et nyt.
    name_removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("role IN ('user', 'moderator', 'admin')", name="role"),
        CheckConstraint("status IN ('active', 'restricted', 'suspended', 'deleted')", name="status"),
    )

    @property
    def is_verified(self):
        return self.email_verified_at is not None

    @property
    def is_moderator(self):
        return self.role == "moderator"

    @property
    def is_active_account(self):
        return self.status == "active"

    @property
    def is_deleted(self):
        return self.status == "deleted"

    @property
    def admin_email(self):
        """E-mailen til administration og moderation, også efter at brugeren har slettet sin profil."""
        return self.deleted_email if self.is_deleted else self.email


Index("uq_users_email", User.email, unique=True)
Index("uq_users_handle", User.handle, unique=True)


class UserSession(db.Model):
    """En login-session pr. enhed. Gør det muligt at logge ud på alle enheder (specifikation 2.3)."""

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmailToken(db.Model):
    """Engangslinks til e-mailverificering og nulstilling af password. Kun en hash af tokenet gemmes."""

    __tablename__ = "email_tokens"
    __table_args__ = (Index("ix_email_tokens_user_purpose", "user_id", "purpose"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
