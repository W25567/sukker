from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import MetaData

# Alle tabeller ligger i Sukker.nu's eget skema (specifikation 14.4).
SCHEMA = "sukker"

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

db = SQLAlchemy(metadata=MetaData(schema=SCHEMA, naming_convention=NAMING_CONVENTION))
migrate = Migrate(render_as_batch=False)  # batch-tilstand er kun nødvendig for SQLite
csrf = CSRFProtect()
limiter = Limiter(key_func=get_remote_address)


def user_or_ip():
    """Nøgle til hastighedsgrænser: den indloggede bruger, ellers IP-adressen. Så rammer en grænse den, der henter
    meget, og ikke alle bag samme netværk."""
    from flask import g

    user = g.get("user")
    return f"user:{user.id}" if user is not None else get_remote_address()


# Beskyttelse mod scraping og masseindsamling af profiler (specifikation 13.1). Grænserne gælder pr. Gunicorn-
# proces, fordi tællerne ligger i hukommelsen (RATELIMIT_STORAGE_URI); med flere processer er den reelle grænse
# derfor nogle gange højere. Rammer en bruger grænsen, oprettes et signal til moderatorerne (moderation/signals.py).
PROFILE_VIEW_LIMIT = "120 per hour;500 per day"
EXPLORE_LIMIT = "240 per hour"
IMAGE_LIMIT = "2000 per hour"
