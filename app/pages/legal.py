"""Versioner af brugervilkår og privatlivspolitik.

Brugeren accepterer vilkår og privatlivspolitik samlet ved oprettelsen (specifikation 2.1). Når teksterne
ændres væsentligt, hæves TERMS_VERSION, og alle skal acceptere igen, før de kan bruge siden.
Rene sproglige rettelser kræver ikke en ny version.
"""
from datetime import date

from flask import g, redirect, request, url_for

TERMS_VERSION = "2026-10-17"
TERMS_EFFECTIVE = date(2026, 10, 17)

# Det, man kan gøre uden at have accepteret de gældende vilkår: læse dem, logge ud eller slette sin profil.
ALLOWED_WITHOUT_TERMS = {"static", "auth.logout", "account.delete", "core.health", "core.ready"}


def needs_terms(user):
    return user is not None and user.terms_version != TERMS_VERSION


def init_app(app):
    """Skal registreres efter sessions.init_app, så g.user er sat."""

    @app.before_request
    def require_current_terms():
        if not needs_terms(g.get("user")):
            return None
        endpoint = request.endpoint or ""
        if endpoint in ALLOWED_WITHOUT_TERMS or endpoint.startswith("pages."):
            return None
        target = request.full_path if request.method == "GET" else None
        return redirect(url_for("pages.accept_terms", next=target.rstrip("?") if target else None))
