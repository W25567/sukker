import hashlib
import logging
import os
from datetime import datetime
from functools import lru_cache

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import get_config
from .extensions import csrf, db, limiter, migrate


def _static_version(path):
    try:
        return _hash_file(path, os.path.getmtime(path))
    except OSError:
        return None


@lru_cache(maxsize=512)
def _hash_file(path, mtime):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()[:10]


def create_app(config_name=None):
    # På serveren er versionsmappen skrivebeskyttet, så instance-mappen ligger under de vedvarende data.
    app = Flask(__name__, instance_relative_config=True, instance_path=os.environ.get("SUKKER_INSTANCE_PATH"))
    app.config.from_object(get_config(config_name))
    os.makedirs(app.instance_path, exist_ok=True)
    if not app.config["MEDIA_ROOT"]:
        app.config["MEDIA_ROOT"] = os.path.join(app.instance_path, "media")
    try:
        os.makedirs(app.config["MEDIA_ROOT"], exist_ok=True)
    except OSError:
        pass  # på serveren oprettes mappen af deploy.py med de rigtige rettigheder; /health/ready melder fejlen

    if app.config["PROXY_FIX"]:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    limiter.init_app(app)

    from . import models  # noqa: F401  (registrerer alle modeller hos SQLAlchemy og Alembic)
    from .auth import sessions
    from .auth.routes import bp as auth_bp
    from .account.routes import bp as account_bp
    from .admin.routes import bp as admin_bp
    from .cli import register_cli
    from .core import errors, security
    from .core.views import bp as core_bp
    from .explore.routes import bp as explore_bp
    from .matches.routes import bp as matches_bp
    from .messaging.routes import bp as messaging_bp
    from .moderation.routes import bp as moderation_bp
    from .notify.routes import bp as notify_bp
    from .verification.routes import bp as verification_bp
    from .safety.routes import bp as safety_bp
    from .media.routes import bp as media_bp
    from .profiles.routes import bp as profiles_bp
    from .pages import legal, onboarding
    from .pages.routes import bp as pages_bp
    from .support.routes import bp as support_bp

    security.init_app(app)
    sessions.init_app(app)
    legal.init_app(app)  # efter sessions, så g.user er sat
    onboarding.init_app(app)  # efter vilkårene
    errors.init_app(app)
    register_cli(app)

    app.register_blueprint(core_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(profiles_bp)
    app.register_blueprint(media_bp)
    app.register_blueprint(explore_bp)
    app.register_blueprint(matches_bp)
    app.register_blueprint(messaging_bp)
    app.register_blueprint(safety_bp)
    app.register_blueprint(moderation_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(support_bp)
    app.register_blueprint(notify_bp)
    app.register_blueprint(verification_bp)

    from .core.timeutil import DANISH_TZ
    from .messaging.services import total_unread

    from .moderation.userdata import staff_label

    # Navn til administration og moderation: slettede brugere markeres og vises med e-mail i parentes.
    app.template_filter("staff_name")(staff_label)

    @app.template_filter("local_datetime")
    def local_datetime(moment):
        if isinstance(moment, str):  # ISO-tidspunkter fra gemte kopier i moderationssager
            moment = datetime.fromisoformat(moment)
        if moment and moment.year >= 9999:  # Premium for livstid
            return "livstid"
        return moment.astimezone(DANISH_TZ).strftime("%d-%m-%Y %H:%M") if moment else ""

    @app.context_processor
    def inject_unread():
        from flask import g

        from .notifications import new_match_count

        from .media.access import pending_count

        user = g.get("user")
        if user is None:
            return {"unread_messages": 0, "new_matches": 0, "access_requests": 0}
        return {"unread_messages": total_unread(user), "new_matches": new_match_count(user),
                "access_requests": pending_count(user)}

    @app.url_defaults
    def static_version(endpoint, values):
        # ?v=<indholdshash> på statiske filer, så Nginx kan cache dem længe, og en ny version slår igennem med det samme.
        if endpoint == "static" and "filename" in values and "v" not in values:
            version = _static_version(os.path.join(app.static_folder, values["filename"]))
            if version:
                values["v"] = version

    if not app.debug and not app.testing:
        app.logger.setLevel(logging.INFO)  # til journalen via Gunicorns errorlog

    from .pages.content import visible_pages

    @app.context_processor
    def inject_footer():
        # En funktion, så databasen kun spørges, når sidefoden faktisk vises (kun for besøgende).
        from .messaging.admin_threads import total_unread_for_admins
        from .support.services import open_count as open_bug_reports
        from .subscriptions.services import early_bird_open
        from .location.distance import distance_visible, kommune_visible

        return {"footer_pages": lambda: visible_pages()[1:], "current_year": datetime.now(DANISH_TZ).year,
                "early_bird_open": early_bird_open, "admin_unread_threads": total_unread_for_admins,
                "admin_open_bug_reports": open_bug_reports,
                # Administrators valg af, om afstand og kommune vises (core/settings.py)
                "distance_visible": distance_visible, "kommune_visible": kommune_visible}

    return app
