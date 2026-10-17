"""Stjernetegn ud fra fødselsdatoen. Vises på profilen som udgangspunkt, men kan skjules ("zodiac" i hidden_fields)."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Sign:
    name: str
    symbol: str


# (sidste dag i tegnet som (måned, dag), tegn) i kalenderrækkefølge fra årets start.
_SIGNS = [
    ((1, 19), Sign("Stenbukken", "♑")),
    ((2, 18), Sign("Vandmanden", "♒")),
    ((3, 20), Sign("Fiskene", "♓")),
    ((4, 19), Sign("Vædderen", "♈")),
    ((5, 20), Sign("Tyren", "♉")),
    ((6, 20), Sign("Tvillingerne", "♊")),
    ((7, 22), Sign("Krebsen", "♋")),
    ((8, 22), Sign("Løven", "♌")),
    ((9, 22), Sign("Jomfruen", "♍")),
    ((10, 22), Sign("Vægten", "♎")),
    ((11, 21), Sign("Skorpionen", "♏")),
    ((12, 21), Sign("Skytten", "♐")),
    ((12, 31), Sign("Stenbukken", "♑")),
]

FIELD_NAME = "zodiac"
LABEL = "Stjernetegn"


def zodiac_for(birthdate):
    key = (birthdate.month, birthdate.day)
    for last_day, sign in _SIGNS:
        if key <= last_day:
            return sign
    return _SIGNS[-1][1]


def visible_zodiac(user, profile):
    """Stjernetegnet, hvis ejeren ikke har skjult det."""
    if profile is not None and FIELD_NAME in (profile.hidden_fields or []):
        return None
    return zodiac_for(user.birthdate)
