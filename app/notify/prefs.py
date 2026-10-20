"""Hvilke notifikationer brugeren vil have.

To kanaler pr. slags:
- "app":  midlertidige beskeder nederst på siden, mens brugeren er logget ind (static/js/notifications.js)
- "mail": en kort, diskret e-mail, når brugeren ikke er på siden (app/notify/mail.py)

Tallene i menuen vises altid: de er brugerens eget overblik og kan ikke slås fra. Valgene gemmes i
users.notification_prefs som afvigelser fra standarden, så nye slags får deres standard uden en migration.
"""
from dataclasses import dataclass

from ..extensions import db


@dataclass(frozen=True)
class Kind:
    key: str
    label: str
    hint: str
    app: bool | None  # standard på siden. None = findes ikke som besked på siden
    mail: bool | None  # standard pr. e-mail. None = sendes ikke pr. e-mail
    admin_only: bool = False


KINDS = [
    Kind("message", "Nye beskeder", "Fra dine matches.", True, True),
    Kind("match", "Nye matches", "Når en, du har liket, liker dig tilbage.", True, True),
    Kind("request", "Anmodninger om private billeder", "Når nogen beder om at se dit private album.", True, True),
    Kind("grant", "Adgang til private billeder", "Når nogen giver dig adgang til deres private billeder.", True, True),
    Kind("admin", "Beskeder fra Administrator", "Fx svar på en henvendelse eller besked om en afgørelse.", True, True),
    Kind("premium", "Premium udløber snart", "En påmindelse 14 og 3 dage før din Premium-periode udløber.", None, True),
    Kind("inactive", "Din profil skjules snart", "En uge før din profil skjules fra Udforsk, fordi du ikke har været "
                                                 "logget ind i længere tid.", None, True),
    Kind("staff", "Svar til Administrator", "Når en bruger svarer i en samtale med Administrator.", True, True,
         admin_only=True),
]
KIND_BY_KEY = {kind.key: kind for kind in KINDS}
CHANNELS = ("app", "mail")


def kinds_for(user):
    from ..brands import current_brand

    brand = current_brand()
    return [kind for kind in KINDS if (not kind.admin_only or user.role == "admin")
            and (kind.key != "premium" or brand.premium) and (kind.key not in PRIVATE_KINDS or brand.private_album)]


# Notifikationer, der kun findes, når brandet har private album (app/brands).
PRIVATE_KINDS = {"request", "grant"}


def wants(user, key, channel):
    """Om brugeren vil have notifikationer af slagsen `key` via `channel` ("app" eller "mail")."""
    kind = KIND_BY_KEY[key]
    default = getattr(kind, channel)
    if default is None or kind not in kinds_for(user):
        return False
    stored = (user.notification_prefs or {}).get(key, {})
    return bool(stored.get(channel, default))


def current(user):
    """{key: {"app": bool | None, "mail": bool | None}} til indstillingssiden. None = findes ikke."""
    return {kind.key: {channel: (wants(user, kind.key, channel) if getattr(kind, channel) is not None else None)
                       for channel in CHANNELS}
            for kind in kinds_for(user)}


def update_from_form(user, form):
    """Gemmer valgene fra formularen (afkrydsningsfelter med navnene "<kanal>_<slags>"). Kun afvigelser fra
    standarden gemmes."""
    stored = {}
    for kind in kinds_for(user):
        for channel in CHANNELS:
            default = getattr(kind, channel)
            if default is None:
                continue
            chosen = form.get(f"{channel}_{kind.key}") == "1"
            if chosen != default:
                stored.setdefault(kind.key, {})[channel] = chosen
    # Valg for slags, brugeren ikke kan se (fx administratorers egne), bevares uændret.
    for key, values in (user.notification_prefs or {}).items():
        if key in KIND_BY_KEY and KIND_BY_KEY[key] not in kinds_for(user):
            stored[key] = values
    user.notification_prefs = stored or None
    db.session.flush()
