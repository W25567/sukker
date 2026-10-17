import os

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
    from .cli import register_cli
    from .core import errors, security
    from .core.views import bp as core_bp
    from .explore.routes import bp as explore_bp
    from .matches.routes import bp as matches_bp
    from .media.routes import bp as media_bp
    from .profiles.routes import bp as profiles_bp

    security.init_app(app)
    sessions.init_app(app)
    errors.init_app(app)
    register_cli(app)

    app.register_blueprint(core_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(profiles_bp)
    app.register_blueprint(media_bp)
    app.register_blueprint(explore_bp)
    app.register_blueprint(matches_bp)

    return app
