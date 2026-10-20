"""Tests for brands: samme kode som Nogen.nu (app/brands). Bruger samme testdatabase, men en app med BRAND=nogen."""
import re

import pytest
from sqlalchemy import select

from app.extensions import db
from app.subscriptions.models import Subscription

from .conftest import link_from, login, register


@pytest.fixture
def nogen(app, monkeypatch):
    from app import config as config_module
    from app import create_app

    class NogenConfig(config_module.TestingConfig):
        BRAND = "nogen"

    monkeypatch.setitem(config_module.CONFIGS, "testing_nogen", NogenConfig)
    nogen_app = create_app("testing_nogen")
    nogen_app.config["MEDIA_ROOT"] = app.config["MEDIA_ROOT"]
    nogen_app.extensions["mail_outbox"] = []
    with nogen_app.app_context():
        yield nogen_app


def member(app, name="Anna"):
    client = app.test_client()
    email = f"{name.lower()}@example.com"
    register(client, display_name=name, email=email)
    client.get(link_from(app.extensions["mail_outbox"][-1], "/bekraeft-email/"))
    login(client, email=email)
    return client


PUBLIC = ["/", "/hvorfor-sukker", "/saadan-virker-det", "/tryghed", "/ofte-stillede-spoergsmaal", "/om", "/vilkaar",
          "/privatlivspolitik", "/cookies", "/log-ind", "/opret"]


def test_public_pages_carry_the_brand(nogen):
    client = nogen.test_client()
    for path in PUBLIC:
        html = client.get(path).get_data(as_text=True)
        assert "Sukker.nu" not in html, path
        assert "Nogen.nu" in html or "nogen" in html, path
    home = client.get("/").get_data(as_text=True)
    assert "Find nogen at være sammen med" in home and "brands/nogen/full-logo-280.webp" in home
    assert "brands/nogen/brand.css" in home and 'content="#1a8a7f"' in home
    assert re.search(r'og:image" content="http://localhost/static/brands/nogen/og\.jpg', home)
    assert "Premium" not in home
    assert client.get("/favicon.ico").status_code == 200


def test_sukker_is_unchanged(app, client):
    html = client.get("/").get_data(as_text=True)
    assert "Sukker.nu" in html and "brands/sukker/full-logo-280.webp" in html and "brand.css" not in html
    assert client.get("/premium").status_code == 200


def test_no_premium_everything_free(nogen):
    client = nogen.test_client()
    assert client.get("/premium").status_code == 404
    assert "/premium" not in client.get("/sitemap.xml").get_data(as_text=True)
    anna = member(nogen)
    from app.core.features import has_feature
    from .test_zodiac_and_admin import user

    anna_user = user("Anna")
    assert db.session.scalar(select(Subscription).where(Subscription.user_id == anna_user.id)) is None
    assert has_feature(anna_user, "messages.reply") and has_feature(anna_user, "search.filters")
    assert not has_feature(anna_user, "messages.unmatched.send")  # kontakt kræver stadig et ja fra begge
    konto = anna.get("/konto/").get_data(as_text=True)
    assert "Abonnement" not in konto and 'name="mail_premium"' not in konto and 'name="mail_request"' not in konto
    assert "kræver Premium" not in anna.get("/udforsk").get_data(as_text=True)


def test_no_private_album(nogen):
    anna = member(nogen)
    me = anna.get("/profil").get_data(as_text=True)
    assert "Privat album" not in me and "Gør privat" not in me
    assert anna.get("/profil/privat-adgang").status_code == 404
    member(nogen, "Bodil")
    from .conftest import h
    assert anna.post(f"/bruger/{h('Bodil')}/privat/anmod").status_code == 404


def test_mails_and_names_use_the_brand(nogen):
    client = nogen.test_client()
    register(client, display_name="Anna", email="anna@example.com")
    (mail,) = nogen.extensions["mail_outbox"]
    assert mail["subject"] == "Bekræft din e-mail på Nogen.nu"
    assert "Sukker" not in mail["body"] and "Premium" not in mail["body"] and "#1a8a7f" in mail["html"]
    response = register(nogen.test_client(), display_name="Nogen Nu", email="x@example.com")
    assert "ligne et navn fra Nogen.nu" in response.get_data(as_text=True)


def test_sukker_mail_is_unchanged(app, outbox, ctx):
    register(app.test_client(), display_name="Anna", email="anna@example.com")
    (mail,) = outbox
    assert mail["subject"] == "Bekræft din e-mail på Sukker.nu" and "#a8325a" in mail["html"]
