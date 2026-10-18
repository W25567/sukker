"""Velkomst for nye medlemmer: vises én gang, før man går i gang.

Siden fortæller ærligt, at Sukker.nu er ny og endnu har få profiler, og at tidlige fugle får Premium for
livstid, mens kampagnen er åben. Den viser aldrig antal brugere (specifikation 12.5 og 16.1).
"""
from flask import g, redirect, request, url_for

# Links fra mails (bekræft e-mail, nulstil password) skal altid virke. Ellers kan fx velkomstsiden fange
# bekræftelseslinket, så e-mailen aldrig bliver bekræftet. Bruges også af vilkårs-kontrollen (pages/legal.py).
MAIL_LINKS = {"auth.verify_email", "auth.reset_password", "notify.unsubscribe"}
# Det, man kan uden at have set velkomsten: læse sider, logge ud, slette profilen og hente billeder.
ALLOWED = {"static", "auth.logout", "account.delete", "core.health", "core.ready", "media.image"} | MAIL_LINKS
# Det, man kan uden at have valgt køn. Administration og moderation virker altid.
GENDER_ALLOWED = {"profiles.choose_gender", "core.notifications", "account.overview", "auth.logout",
                  "verification.queue", "verification.review", "verification.selfie", "verification.profile_image"}


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

    @app.before_request
    def require_gender():
        """Profiler fra før køn blev påkrævet skal vælge køn, før de bruger siden. Kønnet bruges til at
        matche i begge retninger (explore.services og matches.services)."""
        user = g.get("user")
        if user is None or request.method != "GET":
            return None
        endpoint = request.endpoint or ""
        if endpoint in ALLOWED | GENDER_ALLOWED or endpoint.startswith(("pages.", "admin.", "moderation.")):
            return None
        from ..profiles.services import needs_gender

        if needs_gender(user):
            return redirect(url_for("profiles.choose_gender", next=request.full_path.rstrip("?")))
        return None
