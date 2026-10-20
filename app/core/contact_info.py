"""Finder kontaktoplysninger i tekster, brugerne skriver på deres profil.

E-mail, links og telefonnumre er ikke tilladt i profiltekster (brugervilkårene afsnit 4). Det beskytter mod
spam, markedsføring og svindel, hvor andre lokkes væk fra siden. Kontrollen fanger også de mest almindelige
omskrivninger, fx "navn (at) mail punktum dk" og "12 34 56 78". Den er ikke vandtæt: resten håndteres ved
anmeldelser og moderation.
"""
import re

_AT = r"(?:@|\(\s*at\s*\)|\[\s*at\s*\]|\s+at\s+|\s*snabel-?a\s*)"
_DOT = r"(?:\.|\(\s*dot\s*\)|\[\s*dot\s*\]|\s+dot\s+|\s+punktum\s+)"
EMAIL = re.compile(rf"[\w.+-]+\s*{_AT}\s*[\w-]+(?:\s*{_DOT}\s*[\w-]+)*\s*{_DOT}\s*[a-z]{{2,}}\b", re.IGNORECASE)

_TLDS = ("dk|com|net|org|io|me|se|no|de|eu|info|biz|co|app|link|ly|uk|xyz|online|site|shop|tv|cc|gg|to|fi|nl|be|at|ch|"
         "pl|ru|es|it|fr|us|ca|tk|club|live|chat|vip|top|store|page|dating|nu")
URL = re.compile(rf"(?:\b(?:https?://|www\.)\S+|\b[\w-]+(?:\.[\w-]+)*\.(?:{_TLDS})\b(?:/\S*)?)", re.IGNORECASE)
# Siden selv er ikke et link væk fra siden.
ALLOWED_DOMAINS = {"sukker.nu", "www.sukker.nu"}


def _allowed_domains():
    from ..brands import current_brand

    domain = current_brand().domain
    return ALLOWED_DOMAINS | {domain, f"www.{domain}"}

# Danske numre (8 cifre, evt. i par eller fire og fire) og internationale numre med landekode.
PHONE = re.compile(
    r"(?<![\w+])(?:(?:\+|00)\s?\d{1,3}[\s.\-]?)?(?:\d{8}|\d{2}(?:[\s.\-]\d{2}){3}|\d{4}[\s.\-]\d{4}|\d{3}[\s.\-]\d{2}[\s.\-]\d{3})(?!\d)"
    r"|(?:\+|00)\d{1,3}[\s.\-]?\d(?:[\s.\-]?\d){6,12}(?!\d)"
)
YEAR_RANGE = re.compile(r"^(19|20)\d{2}\s*[.\-/ ]\s*(19|20)\d{2}$")


def _phone(text):
    for match in PHONE.finditer(text):
        value = match.group(0).strip()
        if YEAR_RANGE.match(value):
            continue  # fx "1998-2005" er årstal, ikke et telefonnummer
        return value
    return None


def find(text):
    """Returnerer en beskrivelse af den første kontaktoplysning i teksten, eller None."""
    if not text:
        return None
    if EMAIL.search(text):
        return "e-mail"
    for match in URL.finditer(text):
        if match.group(0).lower().rstrip("/.") not in _allowed_domains():
            return "link"
    if _phone(text):
        return "telefonnummer"
    return None


MESSAGE = ("Skriv ikke {what} i din profil. E-mail, links og telefonnumre er ikke tilladt på profilen "
           "(se brugervilkårene).")


def validator(form, field):
    """WTForms-validator til profilens fritekstfelter."""
    from wtforms.validators import ValidationError

    what = find(field.data if isinstance(field.data, str) else None)
    if what:
        raise ValidationError(MESSAGE.format(what={"e-mail": "en e-mail", "link": "et link",
                                                    "telefonnummer": "et telefonnummer"}[what]))
