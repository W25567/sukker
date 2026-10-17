from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, SmallInteger, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db


class Profile(db.Model):
    """Profiloplysninger og synlighedsindstillinger. Alle oplysninger er valgfrie (specifikation 3)."""

    __tablename__ = "profiles"
    __table_args__ = (
        CheckConstraint("height_cm IS NULL OR height_cm BETWEEN 120 AND 230", name="height"),
        CheckConstraint("age_min IS NULL OR age_min BETWEEN 18 AND 99", name="age_min"),
        CheckConstraint("age_max IS NULL OR age_max BETWEEN 18 AND 99", name="age_max"),
        CheckConstraint("age_min IS NULL OR age_max IS NULL OR age_min <= age_max", name="age_range"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)

    # Grundlæggende
    presentation: Mapped[str | None] = mapped_column(Text)
    gender: Mapped[str | None] = mapped_column(String(20))
    seeking_genders: Mapped[list[str]] = mapped_column(ARRAY(String(20)), nullable=False, server_default="{}")
    height_cm: Mapped[int | None] = mapped_column(SmallInteger)
    occupation: Mapped[str | None] = mapped_column(String(80))
    education: Mapped[str | None] = mapped_column(String(30))

    # Livsstil
    children: Mapped[str | None] = mapped_column(String(20))
    wants_children: Mapped[str | None] = mapped_column(String(20))
    exercise: Mapped[str | None] = mapped_column(String(20))
    smoking: Mapped[str | None] = mapped_column(String(20))
    alcohol: Mapped[str | None] = mapped_column(String(20))
    interests: Mapped[str | None] = mapped_column(String(300))
    travel: Mapped[str | None] = mapped_column(String(300))

    # Relation
    relationship_types: Mapped[list[str]] = mapped_column(ARRAY(String(20)), nullable=False, server_default="{}")
    age_min: Mapped[int | None] = mapped_column(SmallInteger)
    age_max: Mapped[int | None] = mapped_column(SmallInteger)
    preferred_distance_km: Mapped[int | None] = mapped_column(SmallInteger)
    long_distance_ok: Mapped[bool | None] = mapped_column(Boolean)
    expectations: Mapped[str | None] = mapped_column(String(500))

    # Synlighed (specifikation 3.2 og 3.3)
    hidden_fields: Mapped[list[str]] = mapped_column(ARRAY(String(40)), nullable=False, server_default="{}")
    hidden_from_search: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    matches_only: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    show_kommune: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    show_activity: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Præferencer for Udforsk
    # Skjul profiler, brugeren har liket, men som ikke har liket tilbage. Kan slås til og fra i Udforsk.
    hide_liked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    @property
    def is_paused(self):
        return self.paused_at is not None
