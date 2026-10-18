"""Tests for køn ved oprettelse og gensidig præference: man ser kun de køn, man søger, og kun dem, der søger ens
eget køn, kan se og like en."""
import re

from sqlalchemy import select

from app.auth.models import User
from app.extensions import db
from app.profiles.models import Profile

from .conftest import h, register
from .test_zodiac_and_admin import make_user, user


def profile(name):
    return db.session.get(Profile, user(name).id)


def set_profile(name, **values):
    p = profile(name)
    for key, value in values.items():
        setattr(p, key, value)
    db.session.commit()


def names(response):
    return re.findall(r"<strong>([^<,]+), \d+</strong>", response.get_data(as_text=True))


def test_gender_is_required_at_signup(app, ctx):
    client = app.test_client()
    page = register(client, gender="").get_data(as_text=True)
    assert "Vælg dit køn" in page and db.session.scalar(select(User)) is None
    register(client, gender="mand", seeking_genders=["kvinde"])
    p = db.session.scalar(select(Profile))
    assert p.gender == "mand" and p.seeking_genders == ["kvinde"]


def test_existing_profile_without_gender_must_choose(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    set_profile("Anna", gender=None)
    response = anna.get("/udforsk")
    assert response.status_code == 302 and "/profil/koen" in response.headers["Location"]
    assert anna.get("/notifikationer").status_code == 200  # baggrundsopdateringer stopper ikke
    anna.post("/profil/koen?next=/likes", data={"gender": "kvinde"})
    assert profile("Anna").gender == "kvinde"
    assert anna.get("/udforsk").status_code == 200


def test_preference_works_both_ways(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")  # kvinde, ingen præference
    bo = make_user(app, outbox, "Bo")
    set_profile("Bo", gender="mand", seeking_genders=["mand"])  # søger kun mænd
    carl = make_user(app, outbox, "Carl")
    set_profile("Carl", gender="mand", seeking_genders=["kvinde"])

    assert "Bo" not in names(anna.get("/udforsk"))  # Bo søger ikke kvinder
    assert "Carl" in names(anna.get("/udforsk"))
    assert "Anna" not in names(bo.get("/udforsk"))  # Bo ser kun mænd
    assert "Carl" not in names(bo.get("/udforsk"))  # Carl søger ikke mænd

    page = anna.get(f"/bruger/{h('Bo')}").get_data(as_text=True)
    assert "Profilen søger ikke kontakt med dit køn" in page and "like-button" not in page
    response = anna.post(f"/like/{h('Bo')}", follow_redirects=True)
    assert "Profilen søger ikke kontakt med dit køn" in response.get_data(as_text=True)
    assert anna.post(f"/like/{h('Carl')}").status_code == 302


def test_likes_from_other_genders_are_not_shown(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    bo = make_user(app, outbox, "Bo")
    set_profile("Bo", gender="mand")
    bo.post(f"/like/{h('Anna')}")  # før Anna valgte "Interesseret i"
    assert "Bo" in anna.get("/likes").get_data(as_text=True)
    set_profile("Anna", seeking_genders=["kvinde"])
    likes = anna.get("/likes").get_data(as_text=True)
    assert "Bo, " not in likes and "Ingen nye likes lige nu" in likes


def test_gender_can_not_be_hidden(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    edit = anna.get("/profil/rediger").get_data(as_text=True)
    assert 'value="gender"' not in edit  # intet "Skjul for andre" ved køn
    set_profile("Anna", hidden_fields=["gender"])
    bo = make_user(app, outbox, "Bo")
    assert "Kvinde" in bo.get(f"/bruger/{h('Anna')}").get_data(as_text=True)
    assert anna.post("/profil/rediger", data={"gender": ""}).status_code == 400
