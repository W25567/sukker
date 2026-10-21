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


PUBLIC = ["/", "/hvorfor", "/saadan-virker-det", "/tryghed", "/ofte-stillede-spoergsmaal", "/om", "/vilkaar",
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


# --- Venskab i stedet for dating ---------------------------------------------------------------------------


def connected_pair(nogen):
    """Anna og Bodil, der har hilst på hinanden (et match i koden)."""
    from .conftest import h

    anna, bodil = member(nogen), member(nogen, "Bodil")
    anna.post(f"/like/{h('Bodil')}")
    bodil.post(f"/like/{h('Anna')}")
    return anna, bodil


def test_all_brands_have_the_same_words():
    from app.brands import BRANDS, DATING_WORDS

    for brand in BRANDS.values():
        assert set(brand.words) == set(DATING_WORDS), brand.key
    assert BRANDS["sukker"].words == DATING_WORDS


def test_hello_and_connections_instead_of_likes_and_matches(nogen):
    from .conftest import h
    from .test_matches import flashed

    anna, bodil = member(nogen), member(nogen, "Bodil")
    explore = anna.get("/udforsk").get_data(as_text=True)
    assert ">Hils på</button>" in explore and ">Like</button>" not in explore
    assert "Forbindelser<span" in explore and "Likes<span" not in explore and 'data-label="nye forbindelser"' in explore
    assert 'data-match-text="Ny forbindelse med"' in explore
    assert "Skjul profiler, jeg har hilst på" in explore and 'valgt under "Jeg vil helst møde"' not in explore

    anna.post(f"/like/{h('Bodil')}")
    assert ">Hilst på</button>" in anna.get("/udforsk").get_data(as_text=True)
    assert "Du har hilst på · fortryd" in anna.get(f"/bruger/{h('Bodil')}").get_data(as_text=True)
    page = bodil.get("/likes").get_data(as_text=True)
    assert "<h1>Forbindelser</h1>" in page and "Har hilst på dig" in page and "Hilsner og forbindelser" in page
    bodil.post(f"/like/{h('Anna')}")
    assert "I er forbundet! Du og Anna har hilst på hinanden." in flashed(bodil)

    page = bodil.get("/likes").get_data(as_text=True)
    assert "Dine forbindelser" in page and "Fjern forbindelse" in page and '<span class="new-badge">Ny</span>' in page
    assert "match" not in re.sub(r'(href|action|data-badge|data-match-text)="[^"]*"', "", page).lower()
    profile = anna.get(f"/bruger/{h('Bodil')}").get_data(as_text=True)
    assert ">Forbundet</span>" in profile and "Fjern forbindelse" in profile
    confirm = anna.get(f"/match/{h('Bodil')}/fjern").get_data(as_text=True)
    assert "Fjern forbindelse med Bodil?" in confirm and "Bliver I forbundet igen" in confirm
    inbox = anna.get("/beskeder/").get_data(as_text=True)
    assert "Ny forbindelse. Sig hej!" in inbox
    konto = anna.get("/konto/").get_data(as_text=True)
    assert "Nye forbindelser" in konto and "Når en, du har hilst på, hilser igen." in konto

    conversation = anna.get(f"/beskeder/med/{h('Bodil')}").headers["Location"]
    anna.post(conversation, data={"body": "Hej Bodil"})
    anna.post(f"/match/{h('Bodil')}/fjern")
    assert "Forbindelsen med Bodil er fjernet." in flashed(anna)
    inbox = anna.get("/beskeder/").get_data(as_text=True)
    assert "Forbindelse fjernet" in inbox and "Forbindelsen blev fjernet" in inbox


def test_refusals_use_the_brand_words(nogen):
    from .conftest import h
    from .test_matches import flashed
    from .test_zodiac_and_admin import user

    anna = member(nogen)
    anna.post(f"/like/{h('Anna')}")
    assert "Du kan ikke hilse på dig selv." in flashed(anna)
    member(nogen, "Bodil")
    from app.profiles.models import Profile

    db.session.get(Profile, user("Bodil").id).seeking_genders = ["mand"]
    db.session.commit()
    assert "Profilen vil helst møde andre køn end dit" in anna.get(f"/bruger/{h('Bodil')}").get_data(as_text=True)


def test_news_mail_says_connection(nogen):
    from datetime import timedelta

    from app.core.timeutil import now_utc
    from app.notify import mail

    connected_pair(nogen)
    nogen.extensions["mail_outbox"].clear()
    mail.run(now_utc() + timedelta(minutes=20))
    (sent,) = [m for m in nogen.extensions["mail_outbox"] if m["to"] == "anna@example.com"]
    assert "Du har en ny forbindelse." in sent["body"] and "match" not in sent["body"].lower()
    assert sent["subject"] == "Der er nyt til dig på Nogen.nu"


def test_dating_fields_are_hidden(nogen):
    from app.profiles.models import Profile
    from .conftest import h
    from .test_zodiac_and_admin import user

    anna, bodil = member(nogen), member(nogen, "Bodil")
    edit = anna.get("/profil/rediger").get_data(as_text=True)
    for name in ("wants_children", "age_min", "age_max", "preferred_distance_km", "long_distance_ok", "expectations"):
        assert f'name="{name}"' not in edit, name
    assert "Kæreste" not in edit and "Noget uforpligtende" not in edit and "Ønsker børn" not in edit
    assert "<legend>Jeg vil helst møde</legend>" in edit and "Lader du feltet stå tomt, møder du alle." in edit
    assert "<legend>Hvad har du lyst til</legend>" in edit and "En kop kaffe" in edit and "<h2>Samvær</h2>" in edit
    assert 'value="age_range"' not in edit and 'value="relationship_types"' in edit  # "Skjul for andre"

    # Felterne kan heller ikke sættes uden om formularen, og "Hvad har du lyst til" gemmes uden migration.
    response = anna.post("/profil/rediger", data={"gender": "kvinde", "relationship_types": ["kaffe", "gaatur"],
                                                  "expectations": "Kæreste", "age_min": "30", "wants_children": "ja"})
    assert response.status_code == 302
    profile = db.session.get(Profile, user("Anna").id)
    assert profile.relationship_types == ["kaffe", "gaatur"]
    assert profile.expectations is None and profile.age_min is None and profile.wants_children is None
    assert anna.post("/profil/rediger", data={"gender": "kvinde", "relationship_types": ["kaereste"]}).status_code == 400

    seen = bodil.get(f"/bruger/{h('Anna')}").get_data(as_text=True)
    assert "Hvad har du lyst til" in seen and "En kop kaffe" in seen and "Gåture" in seen and "Søger" not in seen
    assert "Samvær" in seen and "Relation" not in seen

    explore = bodil.get("/udforsk").get_data(as_text=True)
    assert "<legend>Har lyst til</legend>" in explore and "Kæreste" not in explore
    assert "Anna" in bodil.get("/udforsk?soeger=kaffe").get_data(as_text=True)
    assert "Anna" not in bodil.get("/udforsk?soeger=online").get_data(as_text=True)

    register_page = nogen.test_client().get("/opret").get_data(as_text=True)
    assert "Jeg vil helst møde" in register_page and "Interesseret i" not in register_page


def test_friendship_interest_catalog(nogen):
    from app.profiles import interests

    assert interests.catalog()[0][0] == "Mødes over"
    assert interests.parse_text(interests.to_text(interests.FRIENDSHIP_CATALOG))  # gyldigt som administratorens tekst
    assert "Mødes over" in member(nogen).get("/profil/rediger").get_data(as_text=True)


def test_own_texts_for_friendship(nogen):
    client = nogen.test_client()
    for path in ("/saadan-virker-det", "/tryghed", "/om", "/vilkaar", "/privatlivspolitik", "/ofte-stillede-spoergsmaal"):
        html = client.get(path).get_data(as_text=True)
        text = re.sub(r"<[^>]+>", " ", html)
        assert "dating" not in text.lower().replace("ikke en datingside", "").replace("en datingside?", ""), path
        assert "Premium" not in text and "Privat album" not in text and "match" not in text.lower(), path
    assert "Er Nogen.nu en datingside?" in client.get("/ofte-stillede-spoergsmaal").get_data(as_text=True)
    assert "Hils på" in client.get("/saadan-virker-det").get_data(as_text=True)
    assert "Kom godt i gang" in member(nogen).get("/velkommen").get_data(as_text=True)


MEMBER_SKIP = ("/admin", "/moderation", "/static", "/pre-release", "/sitemap.xml", "/robots.txt", "/favicon.ico")
OFF_BRAND = {"privat album/billeder": r"\bprivate? (?:billede|billeder|album)\w*", "Premium": r"\bpremium\b",
             "like": r"\blike[st]?\b|\bliket\b|\bliker\b", "match": r"\bmatch(?:es|et)?\b"}


def test_member_pages_never_mention_dating_or_paid_features(nogen):
    """Alle sider, et medlem kan åbne, taler Nogen.nu's sprog: ingen private billeder, Premium, likes eller matches."""
    anna = member(nogen)
    offenders = []
    for rule in nogen.url_map.iter_rules():
        if "GET" not in rule.methods or rule.arguments or rule.rule.startswith(MEMBER_SKIP):
            continue
        response = anna.get(rule.rule)
        if response.status_code != 200 or "html" not in response.mimetype:
            continue
        html = re.sub(r"<script.*?</script>|<style.*?</style>", " ", response.get_data(as_text=True), flags=re.S)
        text = re.sub(r"<[^>]+>", " ", html).lower()
        offenders += [f"{rule.rule}: {name}" for name, pattern in OFF_BRAND.items() if re.search(pattern, text)]
    assert offenders == []


def test_tells_why_the_site_is_empty(nogen, app, outbox, ctx):
    """Ingen nedtælling på Nogen.nu, men forsiden, velkomsten og en tom Udforsk fortæller, hvorfor der er få profiler."""
    assert "Vi er lige begyndt" in nogen.test_client().get("/").get_data(as_text=True)
    assert nogen.test_client().get("/pre-release", follow_redirects=False).status_code == 302
    anna = member(nogen)
    assert "falske profiler" in anna.get("/velkommen").get_data(as_text=True)
    explore = anna.get("/udforsk").get_data(as_text=True)
    assert "Der er ingen profiler at vise endnu." in explore and "falske profiler" in explore
    # Sukker.nu viser den korte tekst som før
    with app.test_request_context():
        from flask import render_template

        assert render_template("explore/_empty.html").strip() == "<p>Der er ingen profiler at vise endnu.</p>"


def test_purpose_page_has_its_own_address(nogen):
    client = nogen.test_client()
    assert client.get("/hvorfor").status_code == 200
    old = client.get("/hvorfor-sukker")
    assert old.status_code == 301 and old.headers["Location"].endswith("/hvorfor")
    home = client.get("/").get_data(as_text=True)
    assert 'href="/hvorfor"' in home and "hvorfor-sukker" not in home
    sitemap = client.get("/sitemap.xml").get_data(as_text=True)
    assert "/hvorfor</loc>" in sitemap and "hvorfor-sukker" not in sitemap


def test_sukker_words_and_fields_are_unchanged(app, outbox, ctx):
    from .conftest import h
    from .test_matches import flashed, make_user

    anna, bodil = make_user(app, outbox, "Anna"), make_user(app, outbox, "Bodil")
    explore = anna.get("/udforsk").get_data(as_text=True)
    assert ">Like</button>" in explore and "Likes<span" in explore and 'data-match-text="Nyt match med"' in explore
    anna.post(f"/like/{h('Bodil')}")
    bodil.post(f"/like/{h('Anna')}")
    assert "Det er et match! Du og Anna kan lide hinanden." in flashed(bodil)
    page = bodil.get("/likes").get_data(as_text=True)
    assert "<h1>Likes</h1>" in page and "Dine matches" in page and "Fjern match" in page
    assert '<span class="new-badge">Nyt</span>' in page
    assert ">Match</span>" in anna.get(f"/bruger/{h('Bodil')}").get_data(as_text=True)
    edit = anna.get("/profil/rediger").get_data(as_text=True)
    for name in ("wants_children", "age_min", "age_max", "preferred_distance_km", "long_distance_ok", "expectations"):
        assert f'name="{name}"' in edit, name
    assert "<legend>Interesseret i</legend>" in edit and "<legend>Søger</legend>" in edit and "Kæreste" in edit
    assert "<h2>Relation</h2>" in edit and "Lader du feltet stå tomt" not in edit
    assert "<legend>Søger</legend>" in anna.get("/udforsk").get_data(as_text=True)
    konto = anna.get("/konto/").get_data(as_text=True)
    assert "Nye matches" in konto and "Når en, du har liket, liker dig tilbage." in konto
    from app.profiles import interests

    assert interests.catalog()[0][0] == "Natur og bevægelse"
    visitor = app.test_client()
    assert visitor.get("/hvorfor-sukker").status_code == 200 and visitor.get("/hvorfor").status_code == 404
