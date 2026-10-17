"""Velkomst for nye medlemmer: vises én gang, før man går i gang.

Siden fortæller ærligt, at Sukker.nu er ny og endnu har få profiler, og at tidlige fugle får Premium for
livstid, mens kampagnen er åben. Den viser aldrig antal brugere (specifikation 12.5 og 16.1).
"""
from flask import g, redirect, request, url_for

# Det, man kan uden at have set velkomsten: læse sider, logge ud, slette profilen og hente billeder.
ALLOWED = {"static", "auth.logout", "account.delete", "core.health", "core.ready", "media.image"}


def needs_welcome(user):
    return user is not None and user.welcomed_at is None


def init_app(app):
    """Registreres efter vilkårs-kontrollen, så nye vilkår altid accepteres først."""

    @app.before_request
    def show_welcome_once():
        if not app.config["SHOW_WELCOME"] or request.method != "GET" or not needs_welcome(g.get("user")):
            return None
        endpoint = request.endpoint or ""
        if endpoint in ALLOWED or endpoint.startswith("pages."):
            return None
        return redirect(url_for("pages.welcome", next=request.full_path.rstrip("?")))
