"""Regler for viste navne: ingen navne, der ligner administrationen, og ingen grove eller seksuelle ord.

Kontrollen skal fange det åbenlyse uden at ramme almindelige navne (fx Michelle, der indeholder "hell", eller
Nazir, der begynder med "nazi"). Derfor tre slags regler:

- ANYWHERE: lange, entydige ord, der ikke må stå nogen steder i et ord, heller ikke inde i et andet ord eller
  stavet med tegn imellem ("Fisse-Fanny", "f.i.s.s.e", "Pornostjerne").
- EXACT: korte ord, der kun afvises som et helt ord ("Pik", "Røv 69"), fordi de ellers ville ramme navne som
  Pikachu eller Røvig.
- PREFIX: ord, der afvises i starten af et ord ("Sexy_Sofie", "Hornyboy"), men ikke inde i andre ord (Essex, Thorny).

Før kontrollen gøres navnet ensartet: små bogstaver, tal og tegn, der ligner bogstaver (1 → i, 0 → o, @ → a),
og et bogstav gentaget tre eller flere gange tælles som ét ("Fiiisse"). Ord deles ved mellemrum, tegn og store
bogstaver midt i et ord ("SuperPik" → super, pik).

Listen er ikke vandtæt. Det, der slipper igennem, kan administrator slette (moderation/content.py), og navne,
der bliver afvist ved en fejl, tilføjes i ALLOWED_WORDS.
"""
import re

# Navne, der kan forveksles med Sukker.nu selv eller administrationen. Må ikke stå nogen steder i navnet.
RESERVED = ("admin", "moderator", "support", "kundeservice", "systembesked", "system")
SITE_NAME = "sukkernu"

ANYWHERE = (
    "fisse", "kusse", "tissemand", "pikhoved", "pikfjaes", "pikfjæs", "pikslikker", "rovhul", "røvhul", "rovbanan",
    "røvbanan", "rovslikker", "røvslikker", "lortefjaes", "lortefjæs", "luder", "kaelling", "kælling", "horeunge",
    "porno", "porn", "playboy", "playgirl", "onlyfans", "pornhub", "dildo", "orgasm", "escort", "fuck", "bitch",
    "whore", "neger", "perker", "hitler", "paedo", "pædo", "pedofil", "voldtaeg", "voldtæg", "kneppe", "knepper",
)
EXACT = ("pik", "rov", "røv", "lort", "hore", "nazi", "nazist", "shit", "cunt", "cock", "tits", "pis", "fanden",
         "satan", "sex")
PREFIX = ("sexy", "sexet", "sexgal", "horny", "milf", "slut", "nazist")

# Almindelige navne, der ellers ville blive afvist. Sammenlignes med hele ord.
ALLOWED_WORDS = {"fisseha"}

_LOOKALIKES = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s",
                             "€": "e", "!": "i"})
_REPEATS = re.compile(r"(.)\1{2,}")
_CAMEL = re.compile(r"(?<=[a-zæøåäöüéè])(?=[A-ZÆØÅÄÖÜÉÈ])")
_NOT_LETTER = re.compile(r"[^a-zæøåäöüéè]+")


def _words(name, camel=True):
    folded = name.translate(_LOOKALIKES)
    if camel:
        folded = _CAMEL.sub(" ", folded)
    return [_REPEATS.sub(r"\1", word) for word in _NOT_LETTER.split(folded.lower()) if word]


def _spelled(words):
    """Ord, der er stavet med tegn eller mellemrum mellem bogstaverne ("f.i.s.s.e" → "fisse")."""
    runs, current = [], ""
    for word in words + [""]:
        if len(word) == 1:
            current += word
        else:
            if len(current) > 1:
                runs.append(current)
            current = ""
    return runs


def problem(name):
    """"reserved", "offensive" eller None."""
    if not name:
        return None
    # Ord delt ved tegn og mellemrum ("PlayBoy" er ét ord) og desuden ved store bogstaver ("SuperPik" → super, pik).
    plain = [word for word in _words(name, camel=False) if word not in ALLOWED_WORDS]
    words = [word for word in _words(name) if word not in ALLOWED_WORDS]
    # Søges inde i hvert ord, ikke på tværs af ord, så fx "Lu Derek" ikke bliver til "luder" og "Ad Mina" ikke til
    # "admin". Kun sidens eget navn søges på tværs ("Sukker.nu", "Sukker Nu").
    if any(term in word for word in plain + _spelled(plain) for term in RESERVED) or SITE_NAME in "".join(plain):
        return "reserved"
    if any(term in word for word in plain + _spelled(plain) for term in ANYWHERE):
        return "offensive"
    if any(word in EXACT for word in words) or any(word.startswith(PREFIX) for word in words):
        return "offensive"
    return None


MESSAGES = {
    "reserved": "Navnet må ikke ligne et navn fra Sukker.nu eller administrationen. Vælg et andet navn.",
    "offensive": "Navnet må ikke indeholde grove, krænkende eller seksuelle ord. Vælg et andet navn.",
}


def validator(form, field):
    """WTForms-validator til det viste navn."""
    from wtforms.validators import ValidationError

    found = problem(field.data if isinstance(field.data, str) else None)
    if found:
        raise ValidationError(MESSAGES[found])
