"""Centrale indstillinger, som administrator kan ændre uden kodeændringer (specifikation 10.4).

Værdier, der ikke er gemt i databasen, falder tilbage til DEFAULTS. None betyder ubegrænset.
"""
from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db

DEFAULTS = {
    "signup_premium_months": 3,
    "free_match_limit_enabled": False,
    "free_match_limit": 30,
    "premium_match_limit": None,
    "premium_plus_match_limit": None,
    "premium_plus_daily_contacts": 20,
    "distance_nearby_km": 10,
    "distance_rounding_km": 10,
    "data_retention_months": 12,
    "max_public_images": 20,
    "max_private_images": 10,
    "max_animated_images": 3,
    "max_upload_mb_still": 15,
    "max_upload_mb_animated": 8,
}


class Setting(db.Model):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value = mapped_column(JSONB, nullable=True)
    updated_at = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


def get_setting(key):
    if key not in DEFAULTS:
        raise KeyError(f"Ukendt indstilling: {key}")
    row = db.session.get(Setting, key)
    return DEFAULTS[key] if row is None else row.value


def set_setting(key, value):
    if key not in DEFAULTS:
        raise KeyError(f"Ukendt indstilling: {key}")
    row = db.session.get(Setting, key)
    if row is None:
        db.session.add(Setting(key=key, value=value))
    else:
        row.value = value
