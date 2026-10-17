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
