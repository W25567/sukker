import os
from datetime import datetime

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import get_config
from .extensions import csrf, db, limiter, migrate


def create_app(config_name=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(get_config(config_name))
    os.makedirs(app.instance_path, exist_ok=True)
    if not app.config["MEDIA_ROOT"]:
        app.config["MEDIA_ROOT"] = os.path.join(app.instance_path, "media")

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
    from .safety.routes import bp as safety_bp
    from .media.routes import bp as media_bp
    from .profiles.routes import bp as profiles_bp
    from .pages import legal
    from .pages.routes import bp as pages_bp

    security.init_app(app)
    sessions.init_app(app)
    legal.init_app(app)  # efter sessions, så g.user er sat
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

    from .core.timeutil import DANISH_TZ
    from .messaging.services import total_unread

    @app.template_filter("local_datetime")
    def local_datetime(moment):
        if isinstance(moment, str):  # ISO-tidspunkter fra gemte kopier i moderationssager
            moment = datetime.fromisoformat(moment)
        return moment.astimezone(DANISH_TZ).strftime("%d-%m-%Y %H:%M") if moment else ""

    @app.context_processor
    def inject_unread():
        from flask import g

        user = g.get("user")
        return {"unread_messages": total_unread(user) if user is not None else 0}

    from .pages.content import PUBLIC_PAGES

    @app.context_processor
    def inject_footer():
        return {"footer_pages": PUBLIC_PAGES[1:], "current_year": datetime.now(DANISH_TZ).year}

    return app
