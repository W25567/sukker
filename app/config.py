"""Konfiguration pr. miljø. Værdier læses fra miljøvariabler (lokalt fra .env)."""
import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()


def _env(name, default=None):
    return os.environ.get(name, default)


class BaseConfig:
    SECRET_KEY = _env("SUKKER_SECRET_KEY")
    BASE_URL = _env("SUKKER_BASE_URL", "http://localhost:5000")
    TIMEZONE = "Europe/Copenhagen"

    # Applikationen kører som sukker_app med en kontrolleret search_path (specifikation 14.4).
    SQLALCHEMY_DATABASE_URI = _env("SUKKER_DATABASE_URL")
    SQLALCHEMY_ENGINE_OPTIONS = {
        "connect_args": {"options": "-c search_path=sukker -c timezone=UTC"},
        "pool_pre_ping": True,
    }
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)

    WTF_CSRF_TIME_LIMIT = None  # CSRF-tokenet følger sessionen

    RATELIMIT_ENABLED = True
    RATELIMIT_STORAGE_URI = _env("SUKKER_RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_HEADERS_ENABLED = False

    MAIL_BACKEND = _env("SUKKER_MAIL_BACKEND", "console")
    MAIL_FROM = _env("SUKKER_MAIL_FROM", "Sukker.nu <noreply@sukker.nu>")
    SMTP_HOST = _env("SUKKER_SMTP_HOST")
    SMTP_PORT = int(_env("SUKKER_SMTP_PORT", "587"))
    SMTP_USERNAME = _env("SUKKER_SMTP_USERNAME")
    SMTP_PASSWORD = _env("SUKKER_SMTP_PASSWORD")

    # Levetid for engangslinks (specifikation 2.3)
    EMAIL_VERIFY_TTL = timedelta(hours=48)
    PASSWORD_RESET_TTL = timedelta(minutes=60)

    # Billeder ligger uden for webserverens offentlige område (specifikation 4.3 og 14.3).
    # Tom MEDIA_ROOT betyder instance/media. Med MEDIA_X_ACCEL_PREFIX leverer Nginx filerne via X-Accel-Redirect.
    MEDIA_ROOT = _env("SUKKER_MEDIA_ROOT")
    MEDIA_X_ACCEL_PREFIX = _env("SUKKER_MEDIA_X_ACCEL_PREFIX")
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # lidt over den største tilladte upload på 15 MB

    # Bag Nginx i produktion
    PROXY_FIX = False

    # Offentlige sider og juridiske tekster (specifikation 12.5 og 13.1)
    CONTACT_EMAIL = _env("SUKKER_CONTACT_EMAIL", "kontakt@sukker.nu")
    COMPANY_NAME = _env("SUKKER_COMPANY_NAME")
    COMPANY_CVR = _env("SUKKER_COMPANY_CVR")
    COMPANY_ADDRESS = _env("SUKKER_COMPANY_ADDRESS")
    # Officiel lancering. Indtil da er siden i pre-release: en landingsside tæller ned, og man skal acceptere, at
    # platformen endnu ikke er i luften, før man går videre (app/pages/prerelease.py). Tom = ingen pre-release.
    LAUNCH_AT = _env("SUKKER_LAUNCH_AT", "2026-11-01T00:00:00+01:00")
    # Staging: alt sendes med noindex, og robots.txt udelukker hele siden (specifikation 14.7).
    NOINDEX_ALL = _env("SUKKER_NOINDEX_ALL", "0") == "1"

    # Velkomstsiden vises én gang for hvert medlem (app/pages/onboarding.py). Slået fra i de fleste tests.
    SHOW_WELCOME = True

    # Drift (specifikation 14.6–14.9). Sættes i /etc/sukker/<miljø>.env af deploy.py.
    ENV_NAME = "development"
    MIGRATOR_DATABASE_URL = _env("SUKKER_MIGRATOR_DATABASE_URL")
    BACKUP_DIR = _env("SUKKER_BACKUP_DIR")  # tom = instance/backups
    BACKUP_KEEP = int(_env("SUKKER_BACKUP_KEEP", "7"))
    # Offentlige age-nøgler (age1...), adskilt af mellemrum. Den private nøgle opbevares uden for serveren.
    BACKUP_AGE_RECIPIENTS = _env("SUKKER_BACKUP_AGE_RECIPIENTS", "").split()
    BACKUP_REQUIRE_ENCRYPTION = False
    BACKUP_REMOTE = _env("SUKKER_BACKUP_REMOTE")  # rsync-mål uden for serveren, fx backup@host:sukker/
    BACKUP_REMOTE_PORT = int(_env("SUKKER_BACKUP_REMOTE_PORT", "22") or 22)  # fx 23 for en Hetzner Storage Box
    PG_BIN_DIR = _env("SUKKER_PG_BIN_DIR")  # mappe med pg_dump, hvis den ikke er i PATH
    ALERT_EMAIL = _env("SUKKER_ALERT_EMAIL")  # den navngivne ansvarlige for alarmer (specifikation 14.8)
    ACCESS_LOG = _env("SUKKER_ACCESS_LOG")  # Nginx' JSON-adgangslog til fejlraten
    SITE_DOMAIN = _env("SUKKER_SITE_DOMAIN")  # domænet, hvis certifikat overvåges
    SERVICE_NAME = _env("SUKKER_SERVICE_NAME", "sukker")  # præfiks for systemd-enheder, der overvåges


class DevelopmentConfig(BaseConfig):
    DEBUG = True


class TestingConfig(BaseConfig):
    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = _env("SUKKER_TEST_DATABASE_URL")
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False
    MAIL_BACKEND = "memory"
    BASE_URL = "http://localhost"
    ENV_NAME = "testing"
    SHOW_WELCOME = False
    LAUNCH_AT = ""  # ingen pre-release i testene, undtagen hvor den slås til (tests/test_prerelease.py)


class ProductionConfig(BaseConfig):
    ENV_NAME = "production"
    SESSION_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"
    PROXY_FIX = True
    MEDIA_ROOT = _env("SUKKER_MEDIA_ROOT", "/var/lib/sukker")
    MEDIA_X_ACCEL_PREFIX = _env("SUKKER_MEDIA_X_ACCEL_PREFIX", "/_protected_media/")
    BACKUP_DIR = _env("SUKKER_BACKUP_DIR", "/var/backups/sukker")
    BACKUP_REQUIRE_ENCRYPTION = True


class StagingConfig(ProductionConfig):
    """Fuldt adskilt fra produktion: eget domæne, egen database, egne filer (specifikation 14.7)."""

    ENV_NAME = "staging"
    NOINDEX_ALL = True
    MEDIA_ROOT = _env("SUKKER_MEDIA_ROOT", "/var/lib/sukker-staging")
    BACKUP_DIR = _env("SUKKER_BACKUP_DIR", "/var/backups/sukker-staging")
    BACKUP_REQUIRE_ENCRYPTION = False  # staging indeholder aldrig rigtige brugerdata


CONFIGS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "staging": StagingConfig,
    "production": ProductionConfig,
}


def get_config(name=None):
    name = name or _env("SUKKER_ENV", "development")
    if name not in CONFIGS:
        raise RuntimeError(f"Ukendt SUKKER_ENV: {name!r}. Brug development, testing, staging eller production.")
    config = CONFIGS[name]
    if not config.SECRET_KEY:
        raise RuntimeError("SUKKER_SECRET_KEY mangler. Se .env.example.")
    if not config.SQLALCHEMY_DATABASE_URI:
        variable = "SUKKER_TEST_DATABASE_URL" if name == "testing" else "SUKKER_DATABASE_URL"
        raise RuntimeError(f"{variable} mangler. Se .env.example.")
    return config
