"""Afmelding af e-mailnotifikationer med ét klik, uden at logge ind (RFC 8058).

Hver notifikationsmail har et personligt link og headerne List-Unsubscribe og List-Unsubscribe-Post, så
mailprogrammer som Gmail kan vise en "Afmeld"-knap. Linket slår kun e-mails af den slags fra, mailen handler om.
Det er signeret med appens hemmelige nøgle og kan ikke gættes eller ændres til en anden bruger.
"""
from flask import current_app, url_for
from itsdangerous import BadSignature, URLSafeSerializer

from ..auth.models import User
from ..extensions import db

GROUPS = {
    "news": ("message", "match", "request", "grant", "admin", "staff"),
    "premium": ("premium",),
    "inactive": ("inactive",),
}
GROUP_LABELS = {"news": "e-mails om nyt på siden", "premium": "påmindelser om Premium",
                "inactive": "varsler om, at din profil bliver skjult"}
SALT = "sukker-afmeld-notifikationer"


def _serializer():
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt=SALT)


def token_for(user, group):
    return _serializer().dumps([user.id, group])


def unsubscribe_url(user, group):
    return current_app.config["BASE_URL"].rstrip("/") + url_for("notify.unsubscribe", token=token_for(user, group))


def mail_headers(user, group):
    return {"List-Unsubscribe": f"<{unsubscribe_url(user, group)}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}


def read_token(token):
    """(bruger, gruppe) eller None, hvis linket er ugyldigt."""
    try:
        user_id, group = _serializer().loads(token)
    except (BadSignature, ValueError, TypeError):
        return None
    user = db.session.get(User, user_id)
    if user is None or user.is_deleted or group not in GROUPS:
        return None
    return user, group


def unsubscribe(user, group):
    """Slår e-mails fra for alle slags i gruppen. Valgene på siden ændres ikke."""
    stored = {key: dict(values) for key, values in (user.notification_prefs or {}).items()}
    for kind in GROUPS[group]:
        stored.setdefault(kind, {})["mail"] = False
    user.notification_prefs = stored
