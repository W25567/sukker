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


class ProductionConfig(BaseConfig):
    SESSION_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"
    PROXY_FIX = True
    MEDIA_ROOT = _env("SUKKER_MEDIA_ROOT", "/var/lib/sukker")
    MEDIA_X_ACCEL_PREFIX = _env("SUKKER_MEDIA_X_ACCEL_PREFIX", "/_protected_media/")


CONFIGS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config(name=None):
    name = name or _env("SUKKER_ENV", "development")
    if name not in CONFIGS:
        raise RuntimeError(f"Ukendt SUKKER_ENV: {name!r}. Brug development, testing eller production.")
    config = CONFIGS[name]
    if not config.SECRET_KEY:
        raise RuntimeError("SUKKER_SECRET_KEY mangler. Se .env.example.")
    if not config.SQLALCHEMY_DATABASE_URI:
        variable = "SUKKER_TEST_DATABASE_URL" if name == "testing" else "SUKKER_DATABASE_URL"
        raise RuntimeError(f"{variable} mangler. Se .env.example.")
    return config
