"""Tests for tidlige fugle, velkomstforløbet, sidefoden og hjælpesiden."""
import io
import json
import re

import pytest
from PIL import Image
from sqlalchemy import select

from app.admin.services import create_test_accounts
from app.auth.models import User
from app.core.settings import set_setting
from app.core.timeutil import now_utc
from app.extensions import db
from app.media.models import ProfileImage
from app.subscriptions.models import Subscription
from app.subscriptions.services import early_bird_count, effective_tier

from .conftest import link_from, login, register


def make_user(app, outbox, name, *, verify=True, photo=False):
    client = app.test_client()
    email = f"{name.lower()}@example.com"
    register(client, display_name=name, email=email)
    if verify:
        client.get(link_from(outbox[-1], "/bekraeft-email/"))
    login(client, email=email)
    if photo:
        upload_photo(client)
    return client


def upload_photo(client):
    buffer = io.BytesIO()
    Image.new("RGB", (300, 300), "teal").save(buffer, "JPEG")
    return client.post("/profil/billeder", data={"image": (io.BytesIO(buffer.getvalue()), "a.jpg")},
                       content_type="multipart/form-data")


def user(name):
    return db.session.scalar(select(User).where(User.display_name == name))


def sources(name):
    return sorted(db.session.scalars(select(Subscription.source).where(Subscription.user_id == user(name).id)))


def setting(key, value):
    set_setting(key, value)
    db.session.commit()


def expire_signup_bonus(name):
    for period in db.session.scalars(select(Subscription).where(Subscription.user_id == user(name).id,
                                                                 Subscription.source == "signup_bonus")):
        period.starts_at, period.expires_at = now_utc().replace(year=2020), now_utc()
    db.session.commit()


@pytest.fixture
def welcome_on(app):
    app.config["SHOW_WELCOME"] = True
    yield
    app.config["SHOW_WELCOME"] = False


# --- Tidlige fugle ----------------------------------------------------------------------------------------


def test_early_bird_needs_a_photo(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    assert sources("Anna") == ["signup_bonus"]  # bekræftet, men uden billede: den almindelige periode
    upload_photo(anna)
    with anna.session_transaction() as session:
        flashes = " ".join(message for _, message in session.get("_flashes", []))
    assert "Premium for livstid" in flashes
    assert sources("Anna") == ["early_bird", "signup_bonus"]
    subscription = db.session.scalar(select(Subscription).where(Subscription.user_id == user("Anna").id,
                                                                 Subscription.source == "early_bird"))
    assert subscription.is_lifetime and effective_tier(user("Anna").id) == "premium"


def test_lifetime_pauses_without_photos(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna", photo=True)
    expire_signup_bonus("Anna")
    assert effective_tier(user("Anna").id) == "premium"  # livstid
    image = db.session.scalar(select(ProfileImage).where(ProfileImage.user_id == user("Anna").id))
    anna.post(f"/profil/billeder/{image.public_id}/slet", data={"bekraeft": "1"})
    assert effective_tier(user("Anna").id) == "free"
    assert "sat på pause" in anna.get("/profil").get_data(as_text=True)
    upload_photo(anna)
    assert effective_tier(user("Anna").id) == "premium"
    assert sources("Anna").count("early_bird") == 1  # gives kun én gang


def test_unverified_users_are_not_early_birds_yet(app, outbox, ctx):
    make_user(app, outbox, "Anna", verify=False, photo=True)
    assert sources("Anna") == []


def test_limit_closes_the_campaign(app, outbox, ctx, client):
    setting("early_bird_limit", 1)
    make_user(app, outbox, "Anna", photo=True)
    make_user(app, outbox, "Bodil", photo=True)
    assert "early_bird" in sources("Anna") and "early_bird" not in sources("Bodil")
    assert client.get("/tidlige-fugle").status_code == 410
    assert "/tidlige-fugle" not in client.get("/sitemap.xml").get_data(as_text=True)
    assert "Premium for livstid" not in client.get("/").get_data(as_text=True)


def test_test_accounts_never_count_as_early_birds(app, ctx):
    create_test_accounts()
    db.session.commit()
    assert early_bird_count() == 0


def test_account_page_shows_lifetime(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna", photo=True)
    assert "Premium for livstid" in anna.get("/konto/").get_data(as_text=True)


def test_profile_nudges_early_birds_to_upload(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    assert "Upload et billede, og få Premium for livstid" in anna.get("/profil").get_data(as_text=True)


# --- Offentligt og SEO ----------------------------------------------------------------------------------


def test_campaign_is_public_and_indexable(client):
    page = client.get("/tidlige-fugle")
    assert page.status_code == 200 and "X-Robots-Tag" not in page.headers
    assert "Upload mindst ét billede" in page.get_data(as_text=True)
    assert "http://localhost/tidlige-fugle" in client.get("/sitemap.xml").get_data(as_text=True)
    home = client.get("/").get_data(as_text=True)
    assert 'href="/tidlige-fugle"' in home
    assert re.search(r'<meta name="description" content="[^"]*Premium for livstid', home)
    faq = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>',
                               client.get("/ofte-stillede-spoergsmaal").get_data(as_text=True), re.S).group(1))
    assert faq["mainEntity"][0]["name"] == "Hvad er en tidlig fugl?"
    assert "billede" in faq["mainEntity"][0]["acceptedAnswer"]["text"]
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
    assert "ikke så mange profiler" in page and "mangler, er et billede" in page
    assert not re.search(r"\d+ (profiler|brugere|medlemmer)", page)  # aldrig tal (specifikation 12.5)

    response = anna.post("/velkommen?next=/konto/", data={"goto": "profil"})
    assert response.headers["Location"].endswith("/profil")  # altid profilen, uanset hvor man var på vej hen
    assert user("Anna").welcomed_at is not None
    assert anna.get("/udforsk").status_code == 200


def test_welcome_later_continues_where_you_were_going(app, outbox, ctx, welcome_on):
    anna = make_user(app, outbox, "Anna")
    response = anna.post("/velkommen?next=/konto/", data={"goto": "senere"})
    assert response.headers["Location"].endswith("/konto/")


def test_existing_members_with_photo_become_early_birds_on_welcome(app, outbox, ctx, welcome_on):
    setting("early_bird_enabled", False)
    anna = make_user(app, outbox, "Anna", photo=True)
    assert sources("Anna") == ["signup_bonus"]
    setting("early_bird_enabled", True)
    assert "Premium for livstid" in anna.get("/velkommen").get_data(as_text=True)
    assert sources("Anna") == ["early_bird", "signup_bonus"]


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


# --- Konto ---------------------------------------------------------------------------------------------------


def test_account_shows_email_and_can_change_display_name(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    page = anna.get("/konto/").get_data(as_text=True)
    assert "Logget ind som <strong>anna@example.com</strong>" in page and 'value="Anna"' in page

    response = anna.post("/konto/navn", data={"display_name": "  Anna   Sofie "})
    assert response.status_code == 302 and user("Anna Sofie") is not None

    response = anna.post("/konto/navn", data={"display_name": "!"})
    assert response.status_code == 400 and "2-30 tegn" in response.get_data(as_text=True)
    assert user("Anna Sofie") is not None
