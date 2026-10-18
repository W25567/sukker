"""Tests for stjernetegn og den udvidede administration (fuld brugervisning, filtre, tilsyn)."""
import io
import re
from datetime import date

import pytest
from PIL import Image
from sqlalchemy import select

from app.admin.models import AdminAuditLog
from app.auth.models import User
from app.extensions import db
from app.profiles.models import Profile
from app.profiles.zodiac import zodiac_for

from .conftest import h, link_from, login, register


def make_user(app, outbox, name, *, birthdate="1990-05-17", role="user", verify=True):
    client = app.test_client()
    email = f"{name.lower()}@example.com"
    register(client, display_name=name, email=email, birthdate=birthdate)
    if verify:
        client.get(link_from(outbox[-1], "/bekraeft-email/"))
    login(client, email=email)
    if role != "user":
        user(name).role = role
        db.session.commit()
    return client


def user(name):
    return db.session.scalar(select(User).where(User.display_name == name))


# --- Stjernetegn ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("day, sign", [
    ("01-01", "Stenbukken"), ("01-19", "Stenbukken"), ("01-20", "Vandmanden"), ("02-29", "Fiskene"),
    ("03-21", "Vædderen"), ("06-21", "Krebsen"), ("07-23", "Løven"), ("10-23", "Skorpionen"),
    ("12-21", "Skytten"), ("12-22", "Stenbukken"), ("12-31", "Stenbukken"),
])
def test_zodiac_boundaries(day, sign):
    month, d = map(int, day.split("-"))
    assert zodiac_for(date(2000, month, d)).name == sign


def test_zodiac_is_shown_by_default_and_can_be_hidden(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna", birthdate="1990-08-01")  # Løven
    bodil = make_user(app, outbox, "Bodil")
    assert "Løven" in bodil.get(f"/bruger/{h('Anna')}").get_data(as_text=True)
    assert "Løven" in anna.get("/profil").get_data(as_text=True)

    anna.post("/profil/rediger", data={"hidden_fields": ["zodiac"]})
    assert "zodiac" in db.session.get(Profile, user("Anna").id).hidden_fields
    assert "Løven" not in bodil.get(f"/bruger/{h('Anna')}").get_data(as_text=True)
    assert "Løven" not in anna.get("/profil/forhaandsvisning").get_data(as_text=True)
    assert "skjult for andre" in anna.get("/profil").get_data(as_text=True)


# --- Administration -------------------------------------------------------------------------------------


def test_admin_sees_everything_and_the_view_is_logged(app, outbox, ctx):
    admin = make_user(app, outbox, "Admin", role="admin")
    anna = make_user(app, outbox, "Anna", birthdate="1990-08-01")
    bodil = make_user(app, outbox, "Bodil")
    anna.post("/profil/rediger", data={"presentation": "Hemmelig tekst", "hidden_fields": ["presentation"]})
    buffer = io.BytesIO()
    Image.new("RGB", (300, 300), "red").save(buffer, "JPEG")
    anna.post("/profil/billeder", data={"image": (io.BytesIO(buffer.getvalue()), "a.jpg")}, content_type="multipart/form-data")
    anna.post(f"/like/{h('Bodil')}")
    bodil.post(f"/like/{h('Anna')}")
    url = anna.get(f"/beskeder/med/{h('Bodil')}").headers["Location"]
    anna.post(url, data={"body": "En privat besked"})

    page = admin.get(f"/admin/bruger/{user('Anna').id}").get_data(as_text=True)
    for expected in ("anna@example.com", "01-08-1990", "Løven", "Hemmelig tekst", "skjult for andre",
                     "En privat besked", "Bodil", "Logins og enheder"):
        assert expected in page, expected
    log = db.session.scalar(select(AdminAuditLog).where(AdminAuditLog.action == "view_user"))
    assert log.admin_username == "Admin" and log.target_username == "Anna"

    image_id = re.search(r"/admin/bruger/\d+/billede/([^/]+)/", page).group(1)
    assert admin.get(f"/admin/bruger/{user('Anna').id}/billede/{image_id}/lille.webp").status_code == 200
    assert admin.get(f"/admin/bruger/{user('Bodil').id}/billede/{image_id}/lille.webp").status_code == 404
    assert anna.get(f"/admin/bruger/{user('Anna').id}").status_code == 404  # kun administratorer


def test_user_list_filters_and_sorting(app, outbox, ctx):
    admin = make_user(app, outbox, "Admin", role="admin")
    make_user(app, outbox, "Ubekraeftet", verify=False)
    anna, bodil, carl = (make_user(app, outbox, n) for n in ("Anna", "Bodil", "Carl"))
    bodil.post(f"/bruger/{h('Anna')}/bloker")
    carl.post(f"/bruger/{h('Anna')}/bloker")

    page = admin.get("/admin/brugere?filter=ubekraeftede").get_data(as_text=True)
    assert "Ubekraeftet" in page and ">Anna<" not in page
    page = admin.get("/admin/brugere?filter=blokeret&sort=blokeringer").get_data(as_text=True)
    assert ">Anna<" in page and ">Bodil<" not in page
    page = admin.get("/admin/brugere?q=bodil@example.com").get_data(as_text=True)
    assert ">Bodil<" in page and ">Carl<" not in page
    assert admin.get("/admin/brugere?filter=findes-ikke&sort=findes-ikke").status_code == 200


def test_dashboard_numbers_link_to_lists_and_oversight_renders(app, outbox, ctx):
    admin = make_user(app, outbox, "Admin", role="admin")
    anna, bodil = make_user(app, outbox, "Anna"), make_user(app, outbox, "Bodil")
    bodil.post(f"/bruger/{h('Anna')}/bloker")
    dashboard = admin.get("/admin/").get_data(as_text=True)
    assert 'href="/admin/brugere?filter=nye7"' in dashboard and 'href="/admin/tilsyn"' in dashboard
    oversight = admin.get("/admin/tilsyn").get_data(as_text=True)
    assert "Blokeret af flest" in oversight and ">Anna<" in oversight
    assert make_user(app, outbox, "Moderator", role="moderator").get("/admin/tilsyn").status_code == 404


# --- Samtaler med Administrator --------------------------------------------------------------------------


def test_admin_message_is_anonymous_logged_and_can_be_answered(app, outbox, ctx):
    admin = make_user(app, outbox, "Hanne", role="admin")
    anna = make_user(app, outbox, "Anna")
    admin.post(f"/admin/bruger/{user('Anna').id}/samtale", data={"body": "Husk venligst at skrive pænt."})

    log = db.session.scalar(select(AdminAuditLog).where(AdminAuditLog.action == "admin_message"))
    assert log.admin_username == "Hanne" and log.target_username == "Anna"

    inbox = anna.get("/beskeder/").get_data(as_text=True)
    assert "Administrator" in inbox and "Husk venligst" in inbox
    assert 'class="unread-count"' in inbox
    page = anna.get("/beskeder/administrator").get_data(as_text=True)
    assert "Husk venligst at skrive pænt." in page and "Hanne" not in page  # anonymt for brugeren

    anna.post("/beskeder/administrator", data={"body": "Undskyld, det skal jeg nok."})
    admin_page = admin.get(f"/admin/bruger/{user('Anna').id}/samtale").get_data(as_text=True)
    assert "Undskyld, det skal jeg nok." in admin_page and "Administrator (Hanne)" in admin_page
    assert "Henvendelser" in admin.get("/admin/samtaler").get_data(as_text=True)


def test_user_can_delete_admin_thread_until_admin_writes_again(app, outbox, ctx):
    admin = make_user(app, outbox, "Hanne", role="admin")
    anna = make_user(app, outbox, "Anna")
    admin.post(f"/admin/bruger/{user('Anna').id}/samtale", data={"body": "Første besked"})
    anna.post("/beskeder/administrator/slet")
    assert 'href="/beskeder/administrator"' not in anna.get("/beskeder/").get_data(as_text=True)
    assert "Første besked" not in anna.get("/beskeder/administrator").get_data(as_text=True)  # skjult for Anna

    admin.post(f"/admin/bruger/{user('Anna').id}/samtale", data={"body": "Anden besked"})
    page = anna.get("/beskeder/administrator").get_data(as_text=True)
    assert "Første besked" in page and "Anden besked" in page  # hele historikken kommer igen


def test_admin_thread_is_admin_only_and_not_for_other_users(app, outbox, ctx):
    anna, bodil = make_user(app, outbox, "Anna"), make_user(app, outbox, "Bodil")
    assert anna.post(f"/admin/bruger/{user('Bodil').id}/samtale", data={"body": "x"}).status_code == 404
    page = bodil.get("/beskeder/administrator").get_data(as_text=True)  # ingen samtale endnu: Bodil kan starte en
    assert "Har du et spørgsmål" in page and "Slet samtalen for mig" not in page


def test_admin_thread_live_updates(app, outbox, ctx):
    admin = make_user(app, outbox, "Hanne", role="admin")
    anna = make_user(app, outbox, "Anna")
    admin.post(f"/admin/bruger/{user('Anna').id}/samtale", data={"body": "Hej"})
    page = anna.get("/beskeder/administrator").get_data(as_text=True)
    version = re.search(r'data-version="([^"]+)"', page).group(1)
    assert anna.get(f"/beskeder/administrator/nye?version={version}").status_code == 204
    admin.post(f"/admin/bruger/{user('Anna').id}/samtale", data={"body": "Er du der?"})
    assert "Er du der?" in anna.get(f"/beskeder/administrator/nye?version={version}").get_json()["html"]


def test_admin_side_of_thread_live_updates(app, outbox, ctx):
    admin = make_user(app, outbox, "Hanne", role="admin")
    anna = make_user(app, outbox, "Anna")
    url = f"/admin/bruger/{user('Anna').id}/samtale"
    admin.post(url, data={"body": "Hej"})
    version = re.search(r'data-version="([^"]+)"', admin.get(url).get_data(as_text=True)).group(1)
    assert admin.get(f"{url}/nye?version={version}").status_code == 204
    anna.post("/beskeder/administrator", data={"body": "Hej igen"})
    html = admin.get(f"{url}/nye?version={version}").get_json()["html"]
    assert "Hej igen" in html and "Administrator (Hanne)" in html
    assert "Henvendelser" in admin.get("/admin/").get_data(as_text=True)
    assert anna.get(f"{url}/nye").status_code == 404  # kun administratorer
