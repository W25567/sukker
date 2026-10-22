"""Interesser: et fast katalog, brugerne vælger fra, så profiler kan sammenlignes og filtreres
("I har 3 interesser til fælles").

Kataloget kan redigeres af administrator under Administration → Lister (indstillingen interest_catalog) uden
at ændre i koden. Uden en gemt udgave bruges brandets standard (DEFAULT_CATALOGS). Profilen gemmer en kode pr. interesse (slug af
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

# Til venskab (Nogen.nu): det, man kan mødes om. Samme regler som ovenfor – heller ikke tro, politik eller helbred.
FRIENDSHIP_CATALOG = [
    ("Mødes over", ["Kaffe", "Gåture", "Madlavning", "Brætspil", "Quiz", "Hygge derhjemme", "Fællesspisning"]),
    ("Ud i naturen", ["Vandring", "Cykling", "Havet", "Svampe og bær", "Fuglekiggeri", "Camping",
                      "Natur og friluftsliv"]),
    ("Bevægelse", ["Løb", "Svømning", "Vinterbadning", "Yoga", "Dans", "Fitness", "Petanque", "Padel"]),
    ("Kultur", ["Musik", "Koncerter", "Teater", "Film og serier", "Museer", "Bøger", "Læseklub", "Historie",
                "Fotografi", "Kunst"]),
    ("Hobby", ["Have", "Håndarbejde", "Kreative hobbyer", "Gør det selv", "Hunde", "Katte", "Gaming",
               "Teknologi"]),
    ("Ud at opleve", ["Rejser", "Udflugter", "Loppemarkeder", "Festivaler", "Restauranter", "Sommerhus"]),
    ("Fællesskab", ["Frivilligt arbejde", "Foreningsliv", "Sprog", "Ny i byen", "Nye mennesker", "Online snak"]),
]

# Standardkataloget pr. brand (app/brands, Brand.interest_catalog).
DEFAULT_CATALOGS = {"dating": DEFAULT_CATALOG, "venskab": FRIENDSHIP_CATALOG}


def default_catalog():
    """Det kørende brands standardkatalog, som det bruges, når administrator ikke har gemt sit eget."""
    from ..brands import current_brand

    return DEFAULT_CATALOGS[current_brand().interest_catalog]

_FOLD = str.maketrans({"æ": "ae", "ø": "oe", "å": "aa", "é": "e", "ü": "u", "ö": "o", "ä": "a"})


def key_for(label):
    """Koden, der gemmes på profilen: "Film og serier" → "film_og_serier"."""
    return re.sub(r"[^a-z0-9]+", "_", label.lower().translate(_FOLD)).strip("_")[:40]


def catalog():
    """[(gruppe, [(kode, navn), ...]), ...] fra administratorens udgave eller standarden."""
    from ..core.settings import get_setting

    stored = get_setting("interest_catalog")
    groups = stored if stored else default_catalog()
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


def current_groups():
    """Kataloget, som det gælder nu, som [[gruppe, [navn, ...]], ...]."""
    return [[group, [label for _, label in items]] for group, items in catalog()]


def add(group, label):
    """Tilføjer én interesse til en eksisterende eller ny gruppe og returnerer det nye katalog (kontrolleret som
    tekst). Fx "Knallerter" under "Teknik og spil"."""
    group, label = (group or "").strip(), (label or "").strip()
    if not group or not label:
        raise CatalogError("Vælg en gruppe og skriv interessen.")
    if ":" in group or "," in label or ":" in label:
        raise CatalogError("Gruppe og interesse må ikke indeholde kolon eller komma.")
    groups = current_groups()
    for existing in groups:
        if existing[0].lower() == group.lower():
            existing[1].append(label)
            break
    else:
        groups.append([group, [label]])
    return parse_text(to_text(groups))


def for_storage(groups):
    """None, hvis kataloget er brandets standard, så fremtidige ændringer af standarden slår igennem."""
    return None if groups == [[group, list(labels_)] for group, labels_ in default_catalog()] else groups


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
