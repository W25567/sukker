"""Interesser: et fast katalog, brugerne vælger fra, så profiler kan sammenlignes og filtreres
("I har 3 interesser til fælles").

Kataloget kan redigeres af administrator under Administration → Indstillinger (indstillingen interest_catalog) uden
at ændre i koden. Uden en gemt udgave bruges DEFAULT_CATALOG. Profilen gemmer en kode pr. interesse (slug af
navnet). Fjernes en interesse fra kataloget, vises den ikke længere, men den bliver liggende på profilerne og kommer
frem igen, hvis den tilføjes igen med samme navn. Omdøbes en interesse, er det en ny interesse.

Kataloget indeholder bevidst ikke tro, politik, helbred eller seksualitet: valg af den slags interesser ville være
følsomme personoplysninger (databeskyttelsesforordningens artikel 9).
"""
import re

MAX_PER_PROFILE = 12
MAX_LABEL = 30
MAX_TOTAL = 120

DEFAULT_CATALOG = [
    ("Natur og bevægelse", ["Gåture", "Vandring", "Løb", "Cykling", "Fitness", "Yoga", "Svømning", "Dans",
                            "Fodbold", "Golf", "Vandsport", "Fiskeri"]),
    ("Kultur", ["Musik", "Koncerter", "Teater", "Film og serier", "Kunst", "Museer", "Bøger", "Historie",
                "Fotografi"]),
    ("Mad og hygge", ["Madlavning", "Bagning", "Restauranter", "Kaffe", "Vin", "Øl", "Brætspil", "Quiz"]),
    ("Hjem og hobby", ["Have", "Kreative hobbyer", "Håndarbejde", "Gør det selv", "Hunde", "Katte", "Heste"]),
    ("Rejser og oplevelser", ["Rejser", "Storbyferie", "Camping", "Sommerhus", "Festivaler", "Natur og friluftsliv"]),
    ("Teknik og spil", ["Gaming", "Teknologi", "Biler", "Motorcykler"]),
    ("Fællesskab", ["Frivilligt arbejde", "Foreningsliv", "Sprog", "Nye mennesker"]),
]

_FOLD = str.maketrans({"æ": "ae", "ø": "oe", "å": "aa", "é": "e", "ü": "u", "ö": "o", "ä": "a"})


def key_for(label):
    """Koden, der gemmes på profilen: "Film og serier" → "film_og_serier"."""
    return re.sub(r"[^a-z0-9]+", "_", label.lower().translate(_FOLD)).strip("_")[:40]


def catalog():
    """[(gruppe, [(kode, navn), ...]), ...] fra administratorens udgave eller standarden."""
    from ..core.settings import get_setting

    stored = get_setting("interest_catalog")
    groups = stored if stored else DEFAULT_CATALOG
    return [(group, [(key_for(label), label) for label in labels]) for group, labels in groups]


def choices():
    """Alle interesser som (kode, navn) til formularer."""
    return [item for _, items in catalog() for item in items]


def labels(keys):
    """Navnene på de interesser, der stadig findes i kataloget, i katalogets rækkefølge."""
    wanted = set(keys or [])
    return [label for key, label in choices() if key in wanted]


def shared(keys_a, keys_b):
    """Navnene på de interesser, to profiler har til fælles."""
    return labels(set(keys_a or []) & set(keys_b or []))


# --- Redigering i administrationen ---------------------------------------------------------------------------


def to_text(groups=None):
    """Kataloget som tekst til administrationen: én gruppe pr. linje, "Gruppe: interesse, interesse"."""
    groups = groups if groups is not None else [(g, [label for _, label in items]) for g, items in catalog()]
    return "\n".join(f"{group}: {', '.join(labels_)}" for group, labels_ in groups)


class CatalogError(ValueError):
    pass


def parse_text(raw):
    """Fortolker administratorens tekst. Tom tekst betyder standardkataloget (None)."""
    groups, seen = [], set()
    for number, line in enumerate((raw or "").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        if ":" not in line:
            raise CatalogError(f"Linje {number} mangler et kolon: \"Gruppe: interesse, interesse\".")
        group, rest = (part.strip() for part in line.split(":", 1))
        if not group:
            raise CatalogError(f"Linje {number} mangler et gruppenavn.")
        items = []
        for label in (part.strip() for part in rest.split(",")):
            if not label:
                continue
            if len(label) > MAX_LABEL:
                raise CatalogError(f"\"{label}\" er for langt. Højst {MAX_LABEL} tegn.")
            key = key_for(label)
            if not key:
                raise CatalogError(f"\"{label}\" skal indeholde bogstaver eller tal.")
            if key in seen:
                raise CatalogError(f"\"{label}\" står flere gange.")
            seen.add(key)
            items.append(label)
        if items:
            groups.append([group, items])
    if len(seen) > MAX_TOTAL:
        raise CatalogError(f"Højst {MAX_TOTAL} interesser i alt.")
    return groups or None
