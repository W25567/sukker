"""Faste valgmuligheder til profilfelterne (specifikation 3.1). Koderne gemmes, teksterne vises."""

GENDERS = [
    ("kvinde", "Kvinde"),
    ("mand", "Mand"),
    ("ikke_binaer", "Ikke-binær"),
    ("andet", "Andet"),
]

# Flertal til sætninger som "Viser kvinder og mænd".
GENDERS_PLURAL = [
    ("kvinde", "kvinder"),
    ("mand", "mænd"),
    ("ikke_binaer", "ikke-binære"),
    ("andet", "personer med andet køn"),
]

RELATIONSHIP_TYPES = [
    ("kaereste", "Kæreste"),
    ("dating", "Dating"),
    ("venskab", "Venskab"),
    ("uforpligtende", "Noget uforpligtende"),
    ("ved_ikke", "Ved ikke endnu"),
]

EDUCATION = [
    ("grundskole", "Grundskole"),
    ("gymnasial_erhverv", "Gymnasial eller erhvervsuddannelse"),
    ("kort", "Kort videregående"),
    ("mellemlang", "Mellemlang videregående"),
    ("lang", "Lang videregående"),
    ("andet", "Andet"),
]

CHILDREN = [
    ("ingen", "Ingen børn"),
    ("hjemmeboende", "Har hjemmeboende børn"),
    ("udeboende", "Har udeboende børn"),
]

WANTS_CHILDREN = [
    ("ja", "Ja"),
    ("nej", "Nej"),
    ("maaske", "Måske"),
    ("ved_ikke", "Ved ikke"),
]

EXERCISE = [
    ("aldrig", "Aldrig"),
    ("sjaeldent", "Sjældent"),
    ("nogle_gange", "Nogle gange"),
    ("ofte", "Ofte"),
    ("dagligt", "Dagligt"),
]

SMOKING = [
    ("nej", "Ryger ikke"),
    ("fest", "Festryger"),
    ("ja", "Ryger"),
]

ALCOHOL = [
    ("nej", "Drikker ikke"),
    ("sjaeldent", "Sjældent"),
    ("socialt", "Socialt"),
    ("ofte", "Ofte"),
]

# Foretrukken afstand i km. Tom streng betyder hele landet.
DISTANCES = [
    ("", "Hele landet"),
    ("10", "Op til 10 km"),
    ("25", "Op til 25 km"),
    ("50", "Op til 50 km"),
    ("100", "Op til 100 km"),
    ("200", "Op til 200 km"),
]


def label(choices, code):
    return dict(choices).get(code, "")


def labels(choices, codes):
    lookup = dict(choices)
    return [lookup[c] for c in codes or [] if c in lookup]
