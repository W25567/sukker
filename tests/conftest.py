import os
import re

import pytest
from sqlalchemy import select, text

from app import create_app
from app.extensions import SCHEMA, db

# app.config indlæser .env, så variablen er sat her, hvis den findes.
if not os.environ.get("SUKKER_TEST_DATABASE_URL"):
    pytest.exit("SUKKER_TEST_DATABASE_URL mangler. Se .env.example og scripts/setup_local_db.ps1.", returncode=2)


@pytest.fixture(scope="session")
def app(tmp_path_factory):
    app = create_app("testing")
    app.config["MEDIA_ROOT"] = str(tmp_path_factory.mktemp("media"))
    with app.app_context():
        db.drop_all()
        db.create_all()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture(autouse=True)
def clean_db(app):
    """Tømmer alle tabeller og udbakken før hver test."""
    with app.app_context():
        tables = ", ".join(f'{SCHEMA}."{t.name}"' for t in db.metadata.sorted_tables)
        db.session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        db.session.commit()
    app.extensions["mail_outbox"] = []
    yield
    with app.app_context():
        db.session.remove()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def outbox(app):
    return app.extensions["mail_outbox"]


@pytest.fixture
def ctx(app):
    with app.app_context():
        yield


def h(display_name):
    """Handle (til URL'er) for brugeren med det viste navn. Testene bruger forskellige navne pr. bruger."""
    from app.auth.models import User

    return db.session.scalar(select(User.handle).where(User.display_name == display_name))


def registration_data(**overrides):
    data = {
        "display_name": "Anna_K",
        "email": "anna@example.com",
        "password": "et-langt-password",
        "password_confirm": "et-langt-password",
        "birthdate": "1990-05-17",
        "postcode": "8000",
        "accept_terms": "y",
    }
    data.update(overrides)
    return data


def register(client, **overrides):
    return client.post("/opret", data=registration_data(**overrides))


def login(client, email="anna@example.com", password="et-langt-password"):
    return client.post("/log-ind", data={"email": email, "password": password})


def link_from(mail, path_prefix):
    match = re.search(r"https?://[^\s]+(" + re.escape(path_prefix) + r"[^\s]+)", mail["body"])
    assert match, f"Intet link med {path_prefix} i mailen:\n{mail['body']}"
    return match.group(1)
