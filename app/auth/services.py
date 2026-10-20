from flask import current_app, url_for
from sqlalchemy import select

from ..brands import current_brand
from ..core.mail import send_mail
from ..core.timeutil import now_utc
from ..extensions import db
from ..pages.legal import TERMS_VERSION
from ..profiles.services import get_or_create_profile
from ..subscriptions.services import grant_early_bird, grant_signup_bonus
from . import tokens
from .models import User
from .passwords import burn_verification_time, hash_password, needs_rehash, verify_password
from .sessions import revoke_sessions


def find_user_by_email(email):
    return db.session.scalar(select(User).where(User.email == email.strip().lower()))


def _absolute_url(endpoint, **values):
    return current_app.config["BASE_URL"].rstrip("/") + url_for(endpoint, **values)


def register_user(form, kommune_code=None):
    """Opretter en ubekræftet konto og sender bekræftelsesmail.

    Findes e-mailen allerede, oprettes intet. Ejeren af adressen får i stedet en mail om forsøget,
    og den, der opretter, ser det samme svar som ved en ny konto, så siden ikke afslører, hvilke
    e-mails der har en profil.
    """
    existing = find_user_by_email(form.email.data)
    if existing is not None:
        send_mail(
            existing.email,
            "Nogen forsøgte at oprette en profil med din e-mail",
            "mail/signup_existing.txt",
            name=existing.display_name,
            login_url=_absolute_url("auth.login"),
            reset_url=_absolute_url("auth.forgot_password"),
        )
        return None

    user = User(
        display_name=form.display_name.data,
        email=form.email.data,
        password_hash=hash_password(form.password.data),
        birthdate=form.birthdate.data,
        postcode=form.postcode.data,
        kommune_code=kommune_code,
        terms_accepted_at=now_utc(),
        terms_version=TERMS_VERSION,
    )
    db.session.add(user)
    db.session.flush()
    profile = get_or_create_profile(user)
    profile.gender = form.gender.data
    profile.seeking_genders = list(form.seeking_genders.data or [])
    send_verification_mail(user)
    return user


def send_verification_mail(user):
    raw = tokens.issue_token(user, tokens.VERIFY_EMAIL, current_app.config["EMAIL_VERIFY_TTL"])
    return send_mail(
        user.email,
        f"Bekræft din e-mail på {current_brand().name}",
        "mail/verify_email.txt",
        name=user.display_name,
        verify_url=_absolute_url("auth.verify_email", token=raw),
        hours=int(current_app.config["EMAIL_VERIFY_TTL"].total_seconds() // 3600),
    )


def resend_verification(email):
    """Sender en ny bekræftelsesmail, hvis der findes en ubekræftet profil med e-mailen. Svaret til brugeren
    er altid det samme, så siden ikke afslører, om e-mailen har en profil."""
    user = find_user_by_email(email)
    if user is not None and not user.is_verified:
        send_verification_mail(user)


def verify_email(raw_token):
    user = tokens.consume_token(raw_token, tokens.VERIFY_EMAIL)
    if user is None:
        return None
    if user.email_verified_at is None:
        user.email_verified_at = now_utc()
        # En ny konto med en e-mail fra en slettet konto giver et signal til moderatorerne (specifikation 8.4).
        from ..account.services import previous_accounts
        from ..moderation.signals import check_new_account

        check_new_account(user, previous_accounts(user.email, exclude_id=user.id))
        # Tidlige fugle får Premium for livstid. Er kampagnen lukket, gælder den almindelige Premium-periode.
        if grant_early_bird(user) is None:
            grant_signup_bonus(user)
    return user


def authenticate(email, password):
    user = find_user_by_email(email)
    if user is None:
        burn_verification_time(password)
        return None
    if not verify_password(user.password_hash, password):
        return None
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    return user


def request_password_reset(email):
    """Sender et nulstillingslink, hvis kontoen findes. Svaret til brugeren er altid det samme."""
    user = find_user_by_email(email)
    if user is None:
        return
    raw = tokens.issue_token(user, tokens.RESET_PASSWORD, current_app.config["PASSWORD_RESET_TTL"])
    send_mail(
        user.email,
        f"Nulstil dit password på {current_brand().name}",
        "mail/reset_password.txt",
        name=user.display_name,
        reset_url=_absolute_url("auth.reset_password", token=raw),
        minutes=int(current_app.config["PASSWORD_RESET_TTL"].total_seconds() // 60),
    )


def reset_password(raw_token, new_password):
    user = tokens.consume_token(raw_token, tokens.RESET_PASSWORD)
    if user is None:
        return None
    set_password(user, new_password)
    revoke_sessions(user.id)
    return user


def set_password(user, new_password):
    user.password_hash = hash_password(new_password)
    user.password_changed_at = now_utc()
