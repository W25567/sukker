"""Omtrentlig afstand (specifikation 5.1).

Afstanden vises aldrig præcist: korte afstande bliver til "I nærheden", og resten afrundes. Den samme
grove værdi ("spand") bruges til sortering og filtrering, så rækkefølgen ikke afslører mere end det viste.
"""
import math

from sqlalchemy import case, func

from ..core.settings import get_setting


def distance_visible():
    """Administrator bestemmer, om brugerne kan se afstanden til hinanden (indstillingen show_distance)."""
    return bool(get_setting("show_distance"))


def kommune_visible():
    """Administrator bestemmer, om brugerne kan vise deres kommune (indstillingen show_kommune). Er den slået fra,
    vises ingen kommuner, uanset brugerens eget valg, som bevares til, hvis den slås til igen."""
    return bool(get_setting("show_kommune"))


def _limits():
    return float(get_setting("distance_nearby_km")), float(get_setting("distance_rounding_km"))


def distance_bucket(km):
    """Den afrundede afstand i km. 0 betyder "I nærheden". None hvis afstanden er ukendt."""
    if km is None:
        return None
    nearby, rounding = _limits()
    km = float(km)
    if km < nearby:
        return 0
    # Runder ,5 op ligesom PostgreSQL's round(), så visning og sortering altid giver samme tal.
    return int(max(rounding, math.floor(km / rounding + 0.5) * rounding))


def label_for_bucket(bucket):
    if bucket is None:
        return None
    return "I nærheden" if bucket == 0 else f"ca. {int(bucket)} km"


def distance_label(km):
    return label_for_bucket(distance_bucket(km))


def bucket_expression(km_column):
    """Samme afrunding som distance_bucket, men i SQL, så der kan sorteres og filtreres i databasen."""
    nearby, rounding = _limits()
    return case(
        (km_column.is_(None), None),
        (km_column < nearby, 0),
        else_=func.greatest(rounding, func.round(km_column / rounding) * rounding),
    )
