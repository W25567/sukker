"""Tests for offentlige sider, SEO, vilkårsversioner og dataudtræk (M8)."""
import json
import re

import pytest
from sqlalchemy import select

from app.auth.models import User
from app.core.settings import set_setting
from app.extensions import db
from app.pages.content import PUBLIC_PAGES
from app.pages.legal import TERMS_VERSION

from .conftest import link_from, login, register

PUBLIC_PATHS = ["/", "/saadan-virker-det", "/tryghed", "/premium", "/ofte-stillede-spoergsmaal", "/om",
                "/vilkaar", "/privatlivspolitik", "/cookies"]


def make_user(app, outbox, username):
    client = app.test_client()
    email = f"{username.lower()}@example.com"
    register(client, username=username, email=email)
    client.get(link_from(outbox[-1], "/bekraeft-email/"))
    login(client, email=email)
    return client


def user(username):
    return db.session.scalar(select(User).where(User.username == username))


def meta(html, name):
    match = re.search(r'<meta (?:name|property)="' + re.escape(name) + r'" content="([^"]*)"', html)
    return match.group(1) if match else None


def json_ld(html):
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    return json.loads(match.group(1)) if match else None


# --- Offentlige sider og metadata --------------------------------------------------------------------------


def test_registry_matches_routes(app):
    with app.test_request_context():
        from flask import url_for

        assert [url_for(page.endpoint) for page in PUBLIC_PAGES] == PUBLIC_PATHS


@pytest.mark.parametrize("path", PUBLIC_PATHS)
def test_public_page_is_indexable_with_metadata(client, path):
    response = client.get(path)
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "X-Robots-Tag" not in response.headers
    assert '<html lang="da">' in html
    assert len(re.findall(r"<h1[ >]", html)) == 1
    assert meta(html, "description")
    assert f'<link rel="canonical" href="http://localhost{path}">' in html
    assert meta(html, "og:image") == "http://localhost/static/img/og.jpg"
    assert "/billede/" not in html  # aldrig brugerbilleder


def test_titles_and_descriptions_are_unique(client):
    titles, descriptions = set(), set()
    for path in PUBLIC_PATHS:
        html = client.get(path).get_data(as_text=True)
        titles.add(re.search(r"<title>(.*?)</title>", html).group(1))
        descriptions.add(meta(html, "description"))
    assert len(titles) == len(descriptions) == len(PUBLIC_PATHS)


@pytest.mark.parametrize("path", ["/log-ind", "/opret", "/glemt-password", "/health", "/udforsk", "/findes-ikke"])
def test_non_public_responses_are_noindex(client, path):
    assert client.get(path).headers["X-Robots-Tag"] == "noindex, nofollow"


def test_logged_in_pages_are_noindex_and_have_no_seo_metadata(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    response = anna.get("/udforsk")
    html = response.get_data(as_text=True)
    assert response.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert 'rel="canonical"' not in html and "og:title" not in html


def test_public_pages_never_show_user_data(app, outbox, ctx, client):
    anna = make_user(app, outbox, "Annabella")
    anna.post("/profil/rediger", data={"presentation": "Hemmelig præsentation"})
    for path in PUBLIC_PATHS:
        html = client.get(path).get_data(as_text=True)
        assert "Annabella" not in html and "Hemmelig" not in html


def test_real_404(client):
    response = client.get("/findes-ikke")
    assert response.status_code == 404 and "Siden findes ikke" in response.get_data(as_text=True)


def test_home_redirects_logged_in_users_but_other_pages_do_not(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    assert anna.get("/").status_code == 302
    assert anna.get("/vilkaar").status_code == 200


# --- Strukturerede data ------------------------------------------------------------------------------------


def test_home_has_organization_and_website(client):
    data = json_ld(client.get("/").get_data(as_text=True))
    assert {node["@type"] for node in data["@graph"]} == {"Organization", "WebSite"}


def test_faq_structured_data_matches_page(client):
    html = client.get("/ofte-stillede-spoergsmaal").get_data(as_text=True)
    data = json_ld(html)
    assert data["@type"] == "FAQPage"
    questions = [q["name"] for q in data["mainEntity"]]
    assert len(questions) == html.count("<details") and all(q in html for q in questions)


def test_premium_page_follows_settings(app, ctx, client):
    assert "Filtrere på alder" in client.get("/premium").get_data(as_text=True)
    set_setting("disabled_features", ["search.filters"])
    set_setting("signup_premium_months", 6)
    db.session.commit()
    html = client.get("/premium").get_data(as_text=True)
    assert "Filtrere på alder" not in html and "6 måneders Premium" in html


def test_legal_pages_show_draft_note_until_reviewed(app, client):
    assert "Udkast" in client.get("/vilkaar").get_data(as_text=True)
    app.config["LEGAL_REVIEWED"] = True
    try:
        assert "Udkast" not in client.get("/vilkaar").get_data(as_text=True)
    finally:
        app.config["LEGAL_REVIEWED"] = False


# --- sitemap.xml og robots.txt -----------------------------------------------------------------------------


def test_sitemap_contains_only_public_pages(client):
    response = client.get("/sitemap.xml")
    locs = re.findall(r"<loc>(.*?)</loc>", response.get_data(as_text=True))
    assert response.mimetype == "application/xml"
    assert locs == ["http://localhost" + path for path in PUBLIC_PATHS]


def test_robots_excludes_everything_behind_login(client):
    body = client.get("/robots.txt").get_data(as_text=True)
    disallowed = re.findall(r"^Disallow: (\S+)$", body, re.M)
    for prefix in ["/konto", "/udforsk", "/beskeder", "/admin", "/moderation", "/billede", "/log-ind", "/opret",
                   "/vilkaar/accepter"]:
        assert prefix in disallowed
    for path in PUBLIC_PATHS:  # robots.txt matcher på præfiks, så ingen offentlig side må starte med en udelukket sti
        assert not any(path.startswith(prefix) for prefix in disallowed), path
    assert "/static" not in disallowed  # CSS skal kunne hentes, så siden kan gengives
    assert "/favicon.ico" not in disallowed
    assert "Sitemap: http://localhost/sitemap.xml" in body


def test_staging_is_never_indexed(app, client):
    app.config["NOINDEX_ALL"] = True
    try:
        assert client.get("/").headers["X-Robots-Tag"] == "noindex, nofollow"
        assert client.get("/robots.txt").get_data(as_text=True) == "User-agent: *\nDisallow: /\n"
        assert client.get("/sitemap.xml").status_code == 404
    finally:
        app.config["NOINDEX_ALL"] = False


def test_favicon(client):
    response = client.get("/favicon.ico")
    assert response.status_code == 200 and response.data[:4] == b"\x00\x00\x01\x00"


# --- Vilkårsversioner --------------------------------------------------------------------------------------


def test_registration_records_terms_version(app, outbox, ctx):
    make_user(app, outbox, "Anna")
    assert user("Anna").terms_version == TERMS_VERSION


def test_registration_links_to_terms(client):
    html = client.get("/opret").get_data(as_text=True)
    assert 'href="/vilkaar"' in html and 'href="/privatlivspolitik"' in html


def test_outdated_terms_must_be_accepted(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    user("Anna").terms_version = "0"
    db.session.commit()

    response = anna.get("/udforsk")
    assert response.status_code == 302 and "/vilkaar/accepter?next=/udforsk" in response.headers["Location"]
    assert anna.post("/like/Bodil").status_code == 302  # handlinger er også spærret
    assert anna.get("/vilkaar").status_code == 200  # vilkårene kan læses
    assert anna.get("/konto/slet").status_code == 200  # og profilen kan slettes

    anna.post("/vilkaar/accepter?next=/udforsk", data={})
    assert user("Anna").terms_version == "0"
    response = anna.post("/vilkaar/accepter?next=/udforsk", data={"accept_terms": "y"})
    assert response.headers["Location"].endswith("/udforsk")
    assert user("Anna").terms_version == TERMS_VERSION
    assert anna.get("/udforsk").status_code == 200


def test_accept_terms_ignores_external_next(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    user("Anna").terms_version = "0"
    db.session.commit()
    response = anna.post("/vilkaar/accepter?next=https://example.org/", data={"accept_terms": "y"})
    assert response.headers["Location"].endswith("/udforsk")


# --- Hent mine data ----------------------------------------------------------------------------------------


def test_export_contains_own_data_only(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    bodil = make_user(app, outbox, "Bodil")
    carl = make_user(app, outbox, "Carl")
    dorte = make_user(app, outbox, "Dorte")
    anna.post("/like/Bodil")
    dorte.post("/like/Anna")
    carl.post("/bruger/Anna/bloker")

    response = anna.post("/konto/mine-data")
    assert response.status_code == 200
    assert response.headers["Content-Disposition"].startswith('attachment; filename="sukker-data-Anna-')
    assert response.headers["Cache-Control"] == "no-store"
    data = json.loads(response.get_data(as_text=True))
    assert data["konto"]["email"] == "anna@example.com"
    assert [like["til"] for like in data["likes_givet"]] == ["Bodil"]
    assert data["likes_modtaget_antal"] == 1
    text = json.dumps(data)
    assert "Carl" not in text and "Dorte" not in text  # hvem der har blokeret eller liket Anna, afsløres ikke
    assert "password" not in text.lower()
    assert bodil  # bruges kun til at have en modtager


def test_export_requires_login(client):
    response = client.post("/konto/mine-data")
    assert response.status_code == 302 and "/log-ind" in response.headers["Location"]
