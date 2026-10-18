"""Tests for private album: anmod om adgang, godkend hele albummet eller udvalgte billeder, afvis, tilbagekald,
karensperiode, blokering og den nye profilside med mosaik."""
import io
import re
from datetime import timedelta

from PIL import Image
from sqlalchemy import select

from app.core.timeutil import now_utc
from app.extensions import db
from app.media.models import PrivateAccess, ProfileImage

from .conftest import h
from .test_zodiac_and_admin import make_user, user


def upload(client, color):
    buffer = io.BytesIO()
    Image.new("RGB", (300, 300), color).save(buffer, "JPEG")
    client.post("/profil/billeder", data={"image": (io.BytesIO(buffer.getvalue()), f"{color}.jpg")},
                content_type="multipart/form-data")


def owner_with_private(app, outbox, private=2):
    """Anna med ét offentligt billede og `private` private billeder. Returnerer (klient, offentlig, private)."""
    anna = make_user(app, outbox, "Anna")
    colors = ["red", "green", "blue", "yellow", "purple"][: private + 1]
    for color in colors:
        upload(anna, color)
    images = db.session.scalars(select(ProfileImage).order_by(ProfileImage.position, ProfileImage.id)).all()
    for image in images[1:]:
        anna.post(f"/profil/billeder/{image.public_id}/privat")
    db.session.expire_all()
    return anna, images[0], images[1:]


def access():
    return db.session.scalar(select(PrivateAccess))


def status(client, image):
    return client.get(f"/billede/{image.public_id}/lille.webp").status_code


def test_request_and_approve_whole_album(app, outbox, ctx):
    anna, public, private = owner_with_private(app, outbox)
    bodil = make_user(app, outbox, "Bodil")
    page = bodil.get(f"/bruger/{h('Anna')}").get_data(as_text=True)
    assert "2 private" in page and "Anmod om adgang" in page
    assert all(image.public_id not in page for image in private)  # intet afsløres om de private billeder
    assert all(status(bodil, image) == 404 for image in private)

    bodil.post(f"/bruger/{h('Anna')}/privat/anmod")
    assert access().status == "pending"
    assert "Anmodning sendt" in bodil.get(f"/bruger/{h('Anna')}").get_data(as_text=True)
    me = anna.get("/profil").get_data(as_text=True)
    assert re.search(r'data-badge="requests"[^>]*>1<', me)
    assert anna.get("/notifikationer").get_json()["latest_request"]["name"] == "Bodil"
    assert "Bodil" in anna.get("/profil/privat-adgang").get_data(as_text=True)

    anna.post(f"/profil/privat-adgang/{access().id}/alle")
    assert access().status == "approved" and access().scope == "all"
    assert all(status(bodil, image) == 200 for image in private)
    page = bodil.get(f"/bruger/{h('Anna')}").get_data(as_text=True)
    assert all(image.public_id in page for image in private) and 'class="pv-badge"' in page  # låsemærke
    assert bodil.get("/notifikationer").get_json()["latest_grant"]["name"] == "Anna"

    upload(anna, "black")  # nye billeder i albummet er også omfattet af "hele albummet"
    newest = db.session.scalars(select(ProfileImage).order_by(ProfileImage.id.desc())).first()
    upload(anna, "white")  # endnu et offentligt, så det første kan gøres privat
    anna.post(f"/profil/billeder/{newest.public_id}/privat")
    assert status(bodil, newest) == 200


def test_selected_images_only(app, outbox, ctx):
    anna, public, private = owner_with_private(app, outbox)
    bodil = make_user(app, outbox, "Bodil")
    bodil.post(f"/bruger/{h('Anna')}/privat/anmod")
    edit = anna.get(f"/profil/privat-adgang/{access().id}").get_data(as_text=True)
    assert private[0].public_id in edit and private[1].public_id in edit
    response = anna.post(f"/profil/privat-adgang/{access().id}", data={"scope": "selected"}, follow_redirects=True)
    assert "Vælg mindst ét billede" in response.get_data(as_text=True)
    anna.post(f"/profil/privat-adgang/{access().id}", data={"scope": "selected", "image": [str(private[0].id)]})
    assert access().scope == "selected"
    assert status(bodil, private[0]) == 200 and status(bodil, private[1]) == 404
    assert "Du har adgang til 1 af 2 private billeder" in bodil.get(f"/bruger/{h('Anna')}").get_data(as_text=True)

    # Et billede, der bliver offentligt, forsvinder fra udvalget; gøres det privat igen, er det ikke med
    anna.post(f"/profil/billeder/{private[0].public_id}/offentlig")
    anna.post(f"/profil/billeder/{private[0].public_id}/privat")
    assert status(bodil, private[0]) == 404


def test_revoke_and_cooldown(app, outbox, ctx):
    anna, public, private = owner_with_private(app, outbox, private=1)
    bodil = make_user(app, outbox, "Bodil")
    bodil.post(f"/bruger/{h('Anna')}/privat/anmod")
    anna.post(f"/profil/privat-adgang/{access().id}/alle")
    anna.post(f"/profil/privat-adgang/{access().id}/tilbagekald")
    assert access().status == "revoked" and status(bodil, private[0]) == 404

    page = bodil.get(f"/bruger/{h('Anna')}").get_data(as_text=True)
    assert "Du kan anmode om adgang igen efter" in page and "Anmod om adgang</button>" not in page
    response = bodil.post(f"/bruger/{h('Anna')}/privat/anmod", follow_redirects=True)
    assert "Du kan anmode igen efter" in response.get_data(as_text=True)
    assert access().status == "revoked"

    access().decided_at = now_utc() - timedelta(days=31)
    db.session.commit()
    bodil.post(f"/bruger/{h('Anna')}/privat/anmod")
    assert access().status == "pending"


def test_decline_is_not_revealed_and_cancel(app, outbox, ctx):
    anna, public, private = owner_with_private(app, outbox, private=1)
    bodil = make_user(app, outbox, "Bodil")
    bodil.post(f"/bruger/{h('Anna')}/privat/anmod")
    anna.post(f"/profil/privat-adgang/{access().id}/afvis")
    assert access().status == "declined"
    page = bodil.get(f"/bruger/{h('Anna')}").get_data(as_text=True)
    assert "Anmodning sendt" in page and "afvist" not in page.lower()
    assert 'class="access-list"' not in anna.get("/profil/privat-adgang").get_data(as_text=True)  # ingen på listerne

    carl = make_user(app, outbox, "Carl")
    carl.post(f"/bruger/{h('Anna')}/privat/anmod")
    carl.post(f"/bruger/{h('Anna')}/privat/fortryd")
    assert db.session.scalar(select(PrivateAccess).where(PrivateAccess.viewer_id == user("Carl").id)) is None


def test_block_removes_access_and_others_cannot_manage(app, outbox, ctx):
    anna, public, private = owner_with_private(app, outbox, private=1)
    bodil, carl = make_user(app, outbox, "Bodil"), make_user(app, outbox, "Carl")
    bodil.post(f"/bruger/{h('Anna')}/privat/anmod")
    access_id = access().id
    assert carl.post(f"/profil/privat-adgang/{access_id}/alle").status_code == 404
    assert carl.get(f"/profil/privat-adgang/{access_id}").status_code == 404
    anna.post(f"/profil/privat-adgang/{access_id}/alle")
    bodil.post(f"/bruger/{h('Anna')}/bloker")
    assert access() is None and status(bodil, private[0]) == 404


def test_no_album_no_button_and_preview(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    upload(anna, "red")
    bodil = make_user(app, outbox, "Bodil")
    page = bodil.get(f"/bruger/{h('Anna')}").get_data(as_text=True)
    assert "Anmod om adgang" not in page and "pv-locked" not in page
    response = bodil.post(f"/bruger/{h('Anna')}/privat/anmod", follow_redirects=True)
    assert "ingen private billeder" in response.get_data(as_text=True)


def test_gallery_layout(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    bodil = make_user(app, outbox, "Bodil")
    upload(anna, "red")
    page = bodil.get(f"/bruger/{h('Anna')}").get_data(as_text=True)
    assert 'class="pv-hero"' in page and 'class="pv-thumbs"' not in page  # ét billede: kun hovedbilledet
    for color in ("green", "blue", "yellow"):
        upload(anna, color)
    page = bodil.get(f"/bruger/{h('Anna')}").get_data(as_text=True)
    assert page.count('class="pv-thumb"') == 3 and "1 / 4" in page
    assert page.count("<li data-src=") == 4 and 'class="lightbox"' in page
