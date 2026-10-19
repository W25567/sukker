"""Pre-release indtil den officielle lancering (LAUNCH_AT, 1. november 2026).

Indtil lanceringen ser alle først en landingsside med nedtælling, hvor de skal acceptere, at Sukker.nu endnu ikke
er officielt i luften. Accepten gemmes i sessionen og, for brugere med en konto, i users.prerelease_accepted_at,
så den følger med på andre enheder og kan dokumenteres. Efter lanceringstidspunktet forsvinder det hele af sig
selv: landingssiden sender videre til forsiden, og banneret og kontrollen holder op.

De offentlige informationssider (vilkår, privatlivspolitik, formål osv.) kan læses uden accept, og links fra mails
virker altid.
"""
from datetime import datetime

from flask import current_app, g, redirect, request, session, url_for

from ..core.timeutil import DANISH_TZ, now_utc
from ..extensions import db
from .onboarding import MAIL_LINKS

SESSION_KEY = "prerelease_ok"
# Login er tilladt, så en bruger, der har accepteret på en anden enhed, ikke skal acceptere igen: efter login
# gælder accepten på kontoen.
ALLOWED = {"static", "core.health", "core.ready", "auth.login", "auth.logout", "auth.forgot_password"} | MAIL_LINKS
# Siderne under pages.* er offentlig information, bortset fra dem, der kræver en konto.
MEMBER_PAGES = {"pages.welcome", "pages.accept_terms"}


def launch_at():
    """Lanceringstidspunktet, eller None, hvis der ikke er en pre-release."""
    value = current_app.config.get("LAUNCH_AT")
    if not value:
        return None
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=DANISH_TZ)


MONTHS = ["januar", "februar", "marts", "april", "maj", "juni", "juli", "august", "september", "oktober",
          "november", "december"]


def launch_label():
    """Lanceringsdatoen på dansk, fx "1. november 2026"."""
    local = launch_at().astimezone(DANISH_TZ)
    return f"{local.day}. {MONTHS[local.month - 1]} {local.year}"


def is_prerelease(now=None):
    moment = launch_at()
    return moment is not None and (now or now_utc()) < moment


def has_accepted(user=None):
    user = user if user is not None else g.get("user")
    return bool(session.get(SESSION_KEY)) or (user is not None and user.prerelease_accepted_at is not None)


def accept(user=None):
    """Registrerer accepten i sessionen og på kontoen. Kalderen committer."""
    session[SESSION_KEY] = True
    if user is not None and user.prerelease_accepted_at is None:
        user.prerelease_accepted_at = now_utc()


def carry_over(user):
    """Ved login og oprettelse: en accept, der er givet i browseren før login, gemmes på kontoen (auth/sessions.py)."""
    if session.get(SESSION_KEY) and user.prerelease_accepted_at is None:
        user.prerelease_accepted_at = now_utc()


def init_app(app):
    """Registreres lige efter sessions.init_app, så g.user er sat, og før vilkår og velkomst."""

    @app.before_request
    def require_prerelease_acceptance():
        if not is_prerelease():
            return None
        endpoint = request.endpoint or ""
        if endpoint in ALLOWED or (endpoint.startswith("pages.") and endpoint not in MEMBER_PAGES):
            return None
        user = g.get("user")
        if has_accepted(user):
            if user is not None and user.prerelease_accepted_at is None:
                carry_over(user)
                db.session.commit()
            return None
        target = request.full_path.rstrip("?") if request.method == "GET" else None
        return redirect(url_for("pages.prerelease_landing", next=target))

    @app.context_processor
    def inject_prerelease():
        return {"prerelease_label": launch_label() if is_prerelease() else None}
