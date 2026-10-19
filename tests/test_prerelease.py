"""Tests for pre-release indtil lanceringen: landingsside med nedtælling, accept og at det hele forsvinder ved
lanceringen."""
from datetime import timedelta

import pytest

from app.auth.models import User
from app.core.timeutil import now_utc
from app.extensions import db

from .conftest import link_from, login, register


@pytest.fixture
def prerelease(app):
    app.config["LAUNCH_AT"] = (now_utc() + timedelta(days=12, hours=3)).isoformat()
    yield
    app.config["LAUNCH_AT"] = ""


def accept(client, next_url=None):
    return client.post("/pre-release" + (f"?next={next_url}" if next_url else ""), data={"understood": "y"})


def test_visitors_see_landing_page_first(app, client, prerelease):
    response = client.get("/")
    assert response.status_code == 302 and "/pre-release" in response.headers["Location"]
    page = client.get("/pre-release").get_data(as_text=True)
    assert "Noget nyt er på vej" in page and 'class="countdown"' in page and 'data-unit="days">12<' in page
    assert "countdown.js" in page
    # Informationssider og mail-links kan læses uden accept
    assert client.get("/vilkaar").status_code == 200 and client.get("/privatlivspolitik").status_code == 200
    assert client.get("/health").status_code == 200


def test_acceptance_is_required(app, client, prerelease):
    response = client.post("/pre-release", data={})
    assert response.status_code == 400 and "Sæt kryds" in response.get_data(as_text=True)
    assert client.get("/opret").status_code == 302

    response = accept(client, "/opret")
    assert response.headers["Location"].endswith("/opret")
    page = client.get("/opret").get_data(as_text=True)
    assert page and "prerelease-bar" in page and "Pre-release" in page  # banneret på alle sider


def test_acceptance_follows_the_account(app, outbox, ctx, prerelease):
    client = app.test_client()
    accept(client)
    register(client, display_name="Anna", email="anna@example.com")
    client.get(link_from(outbox[-1], "/bekraeft-email/"))
    login(client)
    user = db.session.query(User).filter_by(display_name="Anna").one()
    assert user.prerelease_accepted_at is not None

    other_device = app.test_client()  # ny browser uden accept i sessionen
    login(other_device)
    response = other_device.get("/udforsk")
    assert response.status_code == 200, response.headers.get("Location")


def test_logged_in_user_without_acceptance_is_asked(app, outbox, ctx):
    client = app.test_client()
    register(client, display_name="Bodil", email="bodil@example.com")
    client.get(link_from(outbox[-1], "/bekraeft-email/"))
    app.config["LAUNCH_AT"] = (now_utc() + timedelta(days=3)).isoformat()
    try:
        fresh = app.test_client()
        login(fresh, email="bodil@example.com")
        assert "/pre-release" in fresh.get("/udforsk").headers["Location"]
        accept(fresh)
        assert fresh.get("/udforsk").status_code == 200
        assert db.session.query(User).filter_by(display_name="Bodil").one().prerelease_accepted_at is not None
    finally:
        app.config["LAUNCH_AT"] = ""


def test_cookie_policy_mentions_prerelease_until_launch(app, client, prerelease):
    page = client.get("/cookies").get_data(as_text=True)
    assert "accepteret, at Sukker.nu er i pre-release" in page and "indtil du lukker browseren" in page
    app.config["LAUNCH_AT"] = (now_utc() - timedelta(minutes=1)).isoformat()
    assert "pre-release" not in client.get("/cookies").get_data(as_text=True)


def test_everything_disappears_after_launch(app, client):
    app.config["LAUNCH_AT"] = (now_utc() - timedelta(minutes=1)).isoformat()
    try:
        assert client.get("/").status_code == 200
        assert "prerelease-bar" not in client.get("/").get_data(as_text=True)
        assert client.get("/pre-release").status_code == 302
        assert "Pre-release:" not in client.get("/vilkaar").get_data(as_text=True)
    finally:
        app.config["LAUNCH_AT"] = ""


def test_launch_label_is_danish(app, ctx):
    from app.pages.prerelease import launch_label

    app.config["LAUNCH_AT"] = "2026-11-01T00:00:00+01:00"
    try:
        assert launch_label() == "1. november 2026"
    finally:
        app.config["LAUNCH_AT"] = ""
