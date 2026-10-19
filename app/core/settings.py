"""Centrale indstillinger, som administrator kan ændre uden kodeændringer (specifikation 10.4).

Værdier, der ikke er gemt i databasen, falder tilbage til DEFAULTS. None betyder ubegrænset.
"""
from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db

DEFAULTS = {
    "signup_premium_months": 3,
    # Tidlige fugle: Premium for livstid, mens der er få brugere. None som grænse = ingen grænse.
    "early_bird_enabled": True,
    "early_bird_limit": None,
    "free_match_limit_enabled": False,
    "free_match_limit": 30,
    "premium_match_limit": None,
    "premium_plus_match_limit": None,
    "premium_plus_daily_contacts": 20,
    # Om brugerne kan se den omtrentlige afstand til hinanden, og om de kan vise deres kommune. Uden afstand
    # sorteres Udforsk efter nyeste profil, og der kan ikke filtreres på afstand.
    "show_distance": True,
    "show_kommune": True,
    "distance_nearby_km": 10,
    "distance_rounding_km": 10,
    "data_retention_months": 12,
    # Profiler skjules fra Udforsk, når brugeren ikke har været logget ind i så mange måneder. 0 = aldrig.
    "inactive_hide_months": 3,
    # Så mange visninger af den samme profil på 7 dage giver et signal, og afstanden vises derefter altid som
    # "I nærheden" for den, der kigger (moderation/signals.py). 0 = slået fra.
    "interest_signal_views": 15,
    "max_public_images": 20,
    "max_private_images": 10,
    "max_animated_images": 3,
    "max_upload_mb_still": 15,
    "max_upload_mb_animated": 8,
    # Rettighedsmodellen (app/core/features.py). None betyder standardfordelingen i koden.
    "tier_features": None,
    "disabled_features": [],
    # Administratorens tilføjelser til reglerne for viste navne (app/core/name_rules.py). Lægges oven i listerne i koden.
    "name_reserved": [],
    "name_blocked_anywhere": [],
    "name_blocked_exact": [],
    "name_blocked_prefix": [],
    "name_allowed": [],
    # Kataloget over interesser (app/profiles/interests.py) som [[gruppe, [interesse, ...]], ...]. None = standarden.
    "interest_catalog": None,
}

# Ordlister, der redigeres i administrationen som tekst: ét ord pr. linje eller adskilt med komma.
WORDLIST_META = [
    ("name_reserved", "Navne, der kan forveksles med administrationen",
     "Afvises overalt i et ord, fx \"admin\" i \"SuperAdmin\"."),
    ("name_blocked_anywhere", "Ord, der ikke må stå nogen steder i et navn",
     "Også inde i andre ord. Brug lange, entydige ord, fx \"fisse\"."),
    ("name_blocked_exact", "Ord, der kun afvises som et helt ord",
     "Til korte ord, der ellers ville ramme almindelige navne, fx \"pik\" (Pikachu er tilladt)."),
    ("name_blocked_prefix", "Ord, der afvises i starten af et ord",
     "Fx \"sexy\" afviser Sexy_Sofie, men ikke ord, hvor det står inde i ordet."),
    ("name_allowed", "Navne, der altid er tilladt",
     "Hele ord, der ellers ville blive afvist ved en fejl, fx \"Fisseha\"."),
]

# Beskrivelse af de indstillinger, der redigeres i administrationen. Typen styrer formularfeltet:
# "bool", "int" (heltal) eller "optional_int" (heltal eller tomt for ubegrænset).
SETTING_META = [
    ("signup_premium_months", "Premium-måneder ved oprettelse", "int"),
    ("early_bird_enabled", "Tidlige fugle får Premium for livstid", "bool"),
    ("early_bird_limit", "Højst antal tidlige fugle (tom = ingen grænse)", "optional_int"),
    ("free_match_limit_enabled", "Match-loft for Free er aktivt", "bool"),
    ("free_match_limit", "Match-loft for Free", "int"),
    ("premium_match_limit", "Match-loft for Premium (tom = ubegrænset)", "optional_int"),
    ("premium_plus_match_limit", "Match-loft for Premium+ (tom = ubegrænset)", "optional_int"),
    ("premium_plus_daily_contacts", "Nye modtagere uden match pr. dag (Premium+)", "int"),
    ("show_distance", "Brugerne kan se den omtrentlige afstand til hinanden (ellers sorteres efter nyeste)", "bool"),
    ("show_kommune", "Brugerne kan vise deres kommune på profilen", "bool"),
    ("distance_nearby_km", "Afstand, der vises som \"I nærheden\" (km)", "int"),
    ("distance_rounding_km", "Afrunding af viste afstande (km)", "int"),
    ("data_retention_months", "Opbevaring af skjulte og slettede data (måneder)", "int"),
    ("inactive_hide_months", "Skjul profiler fra Udforsk efter inaktivitet (måneder, 0 = aldrig)", "int"),
    ("interest_signal_views", "Visninger af samme profil på 7 dage, der giver et signal (0 = slået fra)", "int"),
    ("max_public_images", "Offentlige billeder pr. bruger", "int"),
    ("max_private_images", "Billeder i privat arkiv pr. bruger", "int"),
    ("max_animated_images", "Animerede billeder pr. bruger", "int"),
    ("max_upload_mb_still", "Maks. filstørrelse for billeder (MB)", "int"),
    ("max_upload_mb_animated", "Maks. filstørrelse for animationer (MB)", "int"),
]


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
