"""Tests for tidlige fugle, velkomstforløbet, sidefoden og hjælpesiden."""
import json
import re

import pytest
from sqlalchemy import select

from app.admin.services import create_test_accounts
from app.auth.models import User
from app.core.settings import set_setting
from app.extensions import db
from app.subscriptions.models import Subscription
from app.subscriptions.services import early_bird_count, effective_tier

from .conftest import link_from, login, register


def make_user(app, outbox, name, *, verify=True):
    client = app.test_client()
    email = f"{name.lower()}@example.com"
    register(client, display_name=name, email=email)
    if verify:
        client.get(link_from(outbox[-1], "/bekraeft-email/"))
    login(client, email=email)
    return client


def user(name):
    return db.session.scalar(select(User).where(User.display_name == name))


def sources(name):
    return sorted(db.session.scalars(select(Subscription.source).where(Subscription.user_id == user(name).id)))


def setting(key, value):
    set_setting(key, value)
    db.session.commit()


@pytest.fixture
def welcome_on(app):
    app.config["SHOW_WELCOME"] = True
    yield
    app.config["SHOW_WELCOME"] = False


# --- Tidlige fugle ----------------------------------------------------------------------------------------


def test_verified_early_bird_gets_lifetime_premium(app, outbox, ctx):
    make_user(app, outbox, "Anna")
    assert sources("Anna") == ["early_bird"]  # i stedet for den almindelige periode
    subscription = db.session.scalar(select(Subscription).where(Subscription.user_id == user("Anna").id))
    assert subscription.is_lifetime and effective_tier(user("Anna").id) == "premium"


def test_unverified_users_are_not_early_birds_yet(app, outbox, ctx):
    make_user(app, outbox, "Anna", verify=False)
    assert sources("Anna") == []


def test_limit_closes_the_campaign(app, outbox, ctx, client):
    setting("early_bird_limit", 1)
    make_user(app, outbox, "Anna")
    make_user(app, outbox, "Bodil")
    assert sources("Anna") == ["early_bird"] and sources("Bodil") == ["signup_bonus"]
    assert client.get("/tidlige-fugle").status_code == 410
    assert "/tidlige-fugle" not in client.get("/sitemap.xml").get_data(as_text=True)
    assert "Premium for livstid" not in client.get("/").get_data(as_text=True)


def test_test_accounts_never_count_as_early_birds(app, ctx):
    create_test_accounts()
    db.session.commit()
    assert early_bird_count() == 0


def test_account_page_shows_lifetime(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    assert "Premium for livstid" in anna.get("/konto/").get_data(as_text=True)


# --- Offentligt og SEO ----------------------------------------------------------------------------------


def test_campaign_is_public_and_indexable(client):
    page = client.get("/tidlige-fugle")
    assert page.status_code == 200 and "X-Robots-Tag" not in page.headers
    assert "http://localhost/tidlige-fugle" in client.get("/sitemap.xml").get_data(as_text=True)
    home = client.get("/").get_data(as_text=True)
    assert 'href="/tidlige-fugle"' in home
    assert re.search(r'<meta name="description" content="[^"]*Premium for livstid', home)
    faq = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>',
                               client.get("/ofte-stillede-spoergsmaal").get_data(as_text=True), re.S).group(1))
    assert faq["mainEntity"][0]["name"] == "Hvad er en tidlig fugl?"
    assert "tidlig fugl" in client.get("/opret").get_data(as_text=True)


def test_closed_campaign_disappears_everywhere(ctx, client):
    setting("early_bird_enabled", False)
    assert client.get("/tidlige-fugle").status_code == 410
    assert "tidlig" not in client.get("/").get_data(as_text=True).lower()
    assert "Hvad er en tidlig fugl?" not in client.get("/ofte-stillede-spoergsmaal").get_data(as_text=True)


# --- Velkomst --------------------------------------------------------------------------------------------


def test_welcome_is_shown_once(app, outbox, ctx, welcome_on):
    anna = make_user(app, outbox, "Anna")
    response = anna.get("/udforsk")
    assert response.status_code == 302 and "/velkommen?next=/udforsk" in response.headers["Location"]
    page = anna.get("/velkommen?next=/udforsk").get_data(as_text=True)
    assert "ikke så mange profiler" in page and "Premium for livstid" in page
    assert not re.search(r"\d+ (profiler|brugere|medlemmer)", page)  # aldrig tal (specifikation 12.5)

    response = anna.post("/velkommen?next=/udforsk")
    assert response.headers["Location"].endswith("/udforsk")
    assert user("Anna").welcomed_at is not None
    assert anna.get("/udforsk").status_code == 200


def test_existing_members_become_early_birds_on_welcome(app, outbox, ctx, welcome_on):
    setting("early_bird_enabled", False)
    anna = make_user(app, outbox, "Anna")
    assert sources("Anna") == ["signup_bonus"]
    setting("early_bird_enabled", True)
    anna.get("/velkommen")
    assert sources("Anna") == ["early_bird", "signup_bonus"]
    assert effective_tier(user("Anna").id) == "premium"


# --- Sidefod og hjælp ------------------------------------------------------------------------------------


def test_footer_only_for_visitors_and_help_for_members(app, outbox, ctx, client):
    assert 'class="site-footer"' in client.get("/").get_data(as_text=True)
    anna = make_user(app, outbox, "Anna")
    assert 'class="site-footer"' not in anna.get("/udforsk").get_data(as_text=True)
    assert 'class="site-footer"' not in anna.get("/vilkaar").get_data(as_text=True)
    assert 'href="/hjaelp"' in anna.get("/profil").get_data(as_text=True)
    help_page = anna.get("/hjaelp").get_data(as_text=True)
    for path in ("/vilkaar", "/privatlivspolitik", "/cookies", "/ofte-stillede-spoergsmaal", "/tryghed"):
        assert f'href="{path}"' in help_page
    assert client.get("/hjaelp").status_code == 302  # kun for medlemmer
