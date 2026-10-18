"""Tests for notifikationer (røde bobler og /notifikationer), små profilbilleder og "skjul profiler, jeg har liket"."""
import io
import re

from PIL import Image
from sqlalchemy import select

from app.auth.models import User
from app.extensions import db
from app.profiles.models import Profile

from .conftest import h
from .test_zodiac_and_admin import make_user, user


def badge(page, name):
    """Tallet i menuens boble, eller 0 hvis boblen er skjult."""
    tag = re.search(rf'<span class="nav-badge" data-badge="{name}"[^>]*>(\d+)</span>', page)
    assert tag, name
    return 0 if " hidden" in tag.group(0) else int(tag.group(1))


def upload(client):
    buffer = io.BytesIO()
    Image.new("RGB", (300, 300), "blue").save(buffer, "JPEG")
    client.post("/profil/billeder", data={"image": (io.BytesIO(buffer.getvalue()), "a.jpg")},
                content_type="multipart/form-data")


def test_new_match_badge_until_likes_is_opened(app, outbox, ctx):
    anna, bodil = make_user(app, outbox, "Anna"), make_user(app, outbox, "Bodil")
    anna.post(f"/like/{h('Bodil')}")
    bodil.post(f"/like/{h('Anna')}")  # Bodil udløser matchet og får besked med det samme

    assert badge(anna.get("/udforsk").get_data(as_text=True), "matches") == 1
    assert badge(bodil.get("/udforsk").get_data(as_text=True), "matches") == 0
    likes = anna.get("/likes").get_data(as_text=True)
    assert 'class="new-badge"' in likes
    assert badge(anna.get("/udforsk").get_data(as_text=True), "matches") == 0


def test_notifications_endpoint(app, outbox, ctx):
    anna, bodil = make_user(app, outbox, "Anna"), make_user(app, outbox, "Bodil")
    anna.post(f"/like/{h('Bodil')}")
    bodil.post(f"/like/{h('Anna')}")
    url = bodil.get(f"/beskeder/med/{h('Anna')}").headers["Location"]
    bodil.post(url, data={"body": "Hej Anna"})

    data = anna.get("/notifikationer").get_json()
    assert data["messages"] == 1 and data["matches"] == 1
    assert data["latest_message"]["name"] == "Bodil" and data["latest_message"]["url"] == url
    assert data["latest_match"]["name"] == "Bodil"
    assert "Hej Anna" not in str(data)  # beskedteksten sendes ikke med
    assert badge(anna.get("/udforsk").get_data(as_text=True), "messages") == 1

    anna.get(url)  # læst
    data = anna.get("/notifikationer").get_json()
    assert data["messages"] == 0 and data["latest_message"] is None
    assert app.test_client().get("/notifikationer").status_code == 401


def test_admin_message_shows_as_notification(app, outbox, ctx):
    admin = make_user(app, outbox, "Hanne", role="admin")
    anna = make_user(app, outbox, "Anna")
    admin.post(f"/admin/bruger/{user('Anna').id}/samtale", data={"body": "Hej"})
    data = anna.get("/notifikationer").get_json()
    assert data["latest_admin"]["name"] == "Administrator" and data["messages"] == 1


def test_avatars_in_inbox_and_likes(app, outbox, ctx):
    anna, bodil = make_user(app, outbox, "Anna"), make_user(app, outbox, "Bodil")
    upload(bodil)
    anna.post(f"/like/{h('Bodil')}")
    bodil.post(f"/like/{h('Anna')}")
    for page in (anna.get("/beskeder/").get_data(as_text=True), anna.get("/likes").get_data(as_text=True)):
        assert re.search(r'<img class="avatar" src="/billede/[^/]+/lille\.webp"', page)
    # Anna har intet billede: Bodil ser forbogstavet
    assert 'class="avatar avatar-empty"' in bodil.get("/beskeder/").get_data(as_text=True)


def test_hide_liked_filter_and_preference(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    bodil, carl = make_user(app, outbox, "Bodil"), make_user(app, outbox, "Carl")
    anna.post(f"/like/{h('Carl')}")  # ikke liket tilbage
    anna.post(f"/like/{h('Bodil')}")
    bodil.post(f"/like/{h('Anna')}")  # match vises altid

    page = anna.get("/udforsk").get_data(as_text=True)
    assert "Carl, " in page and "Bodil, " in page
    page = anna.get("/udforsk?likede=skjul").get_data(as_text=True)
    assert "Carl, " not in page and "Bodil, " in page

    anna.post("/profil/udforsk", data={"hide_liked": "y"})
    assert db.session.get(Profile, user("Anna").id).hide_liked is True
    assert "Carl, " not in anna.get("/udforsk").get_data(as_text=True)
    assert "Carl, " in anna.get("/udforsk?likede=vis").get_data(as_text=True)
    assert 'value="skjul" checked' in anna.get("/udforsk").get_data(as_text=True)
    # Formularen sender både "vis" og (når feltet er krydset af) "skjul". Det sidste vinder.
    assert "Carl, " in anna.get("/udforsk?likede=vis").get_data(as_text=True)
    assert "Carl, " not in anna.get("/udforsk?likede=vis&likede=skjul").get_data(as_text=True)
