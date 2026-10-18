"""Én samlet beskrivelse af profilfelterne. Bruges til visning, skjulte felter og fuldstændighed."""
from dataclasses import dataclass

from . import choices


@dataclass(frozen=True)
class ProfileField:
    name: str
    label: str
    group: str
    choices: list | None = None
    multiple: bool = False


GROUPS = ["Grundlæggende", "Livsstil", "Relation"]

FIELDS = [
    ProfileField("presentation", "Præsentation", "Grundlæggende"),
    ProfileField("gender", "Køn", "Grundlæggende", choices.GENDERS),
    ProfileField("seeking_genders", "Interesseret i", "Grundlæggende", choices.GENDERS, multiple=True),
    ProfileField("height_cm", "Højde", "Grundlæggende"),
    ProfileField("occupation", "Beskæftigelse", "Grundlæggende"),
    ProfileField("education", "Uddannelse", "Grundlæggende", choices.EDUCATION),
    ProfileField("children", "Børn", "Livsstil", choices.CHILDREN),
    ProfileField("wants_children", "Ønsker børn", "Livsstil", choices.WANTS_CHILDREN),
    ProfileField("exercise", "Motion", "Livsstil", choices.EXERCISE),
    ProfileField("smoking", "Rygning", "Livsstil", choices.SMOKING),
    ProfileField("alcohol", "Alkohol", "Livsstil", choices.ALCOHOL),
    ProfileField("interests", "Interesser og hobbyer", "Livsstil"),
    ProfileField("travel", "Rejser", "Livsstil"),
    ProfileField("relationship_types", "Søger", "Relation", choices.RELATIONSHIP_TYPES, multiple=True),
    ProfileField("age_range", "Aldersinterval", "Relation"),
    ProfileField("preferred_distance_km", "Afstand", "Relation"),
    ProfileField("long_distance_ok", "Åben for langdistance", "Relation"),
    ProfileField("expectations", "Forventninger", "Relation"),
]

FIELD_NAMES = {f.name for f in FIELDS}
# Felter, der altid vises: kønnet bruges til at matche, så det giver ikke mening at skjule det.
NOT_HIDEABLE = {"gender"}


def display_value(profile, field):
    """Teksten, der vises for et felt, eller tom streng hvis feltet ikke er udfyldt."""
    if field.name == "age_range":
        if profile.age_min and profile.age_max:
            return f"{profile.age_min}-{profile.age_max} år"
        if profile.age_min:
            return f"Fra {profile.age_min} år"
        if profile.age_max:
            return f"Op til {profile.age_max} år"
        return ""
    value = getattr(profile, field.name)
    if field.name == "height_cm":
        return f"{value} cm" if value else ""
    if field.name == "preferred_distance_km":
        return f"Op til {value} km" if value else ""
    if field.name == "long_distance_ok":
        return {True: "Ja", False: "Nej"}.get(value, "")
    if field.choices and field.multiple:
        return ", ".join(choices.labels(field.choices, value))
    if field.choices:
        return choices.label(field.choices, value)
    return value or ""


def visible_fields(profile, *, as_owner):
    """Udfyldte felter grupperet til visning. Andre ser ikke felter, ejeren har skjult."""
    groups = {g: [] for g in GROUPS}
    for field in FIELDS:
        if not as_owner and field.name in profile.hidden_fields and field.name not in NOT_HIDEABLE:
            continue
        value = display_value(profile, field)
        if value:
            groups[field.group].append((field, value))
    return [(group, items) for group, items in groups.items() if items]


def completeness(profile, image_count):
    """Diskret indikator for profilens fuldstændighed (specifikation 2.2). Billeder tæller som ét felt."""
    filled = sum(1 for f in FIELDS if display_value(profile, f))
    total = len(FIELDS) + 1
    return round(100 * (filled + (1 if image_count else 0)) / total)
