"""Engangslinks sendt pr. e-mail (specifikation 2.3).

Tokenet valideres på serveren, bliver ugyldigt efter brug, og et nyt link af samme type gør de
tidligere ubrugte links ugyldige.
"""
import hashlib
import secrets

from sqlalchemy import select, update

from ..core.timeutil import now_utc
from ..extensions import db
from .models import EmailToken, User

VERIFY_EMAIL = "verify_email"
RESET_PASSWORD = "reset_password"


def hash_token(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_token(user, purpose, lifetime):
    now = now_utc()
    db.session.execute(
        update(EmailToken)
        .where(EmailToken.user_id == user.id, EmailToken.purpose == purpose, EmailToken.used_at.is_(None))
        .values(used_at=now)
    )
    raw = secrets.token_urlsafe(32)
    db.session.add(EmailToken(user_id=user.id, purpose=purpose, token_hash=hash_token(raw), expires_at=now + lifetime))
    return raw


def _valid_token(raw, purpose, lock=False):
    query = select(EmailToken).where(
        EmailToken.token_hash == hash_token(raw),
        EmailToken.purpose == purpose,
        EmailToken.used_at.is_(None),
        EmailToken.expires_at > now_utc(),
    )
    if lock:
        query = query.with_for_update()
    return db.session.scalar(query)


def peek_token(raw, purpose):
    """Returnerer brugeren, hvis tokenet er gyldigt, uden at bruge det."""
    token = _valid_token(raw, purpose)
    return None if token is None else db.session.get(User, token.user_id)


def consume_token(raw, purpose):
    """Bruger tokenet og returnerer brugeren, eller None hvis tokenet er ugyldigt, udløbet eller brugt."""
    token = _valid_token(raw, purpose, lock=True)
    if token is None:
        return None
    token.used_at = now_utc()
    return db.session.get(User, token.user_id)
