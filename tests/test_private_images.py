"""Tests for at gøre billeder private og offentlige. Der skal altid være mindst ét offentligt billede,
når der er private billeder."""
import io
import os
import re

from PIL import Image
from sqlalchemy import select

from app.extensions import db
from app.media import storage
from app.media.models import ProfileImage

from .conftest import h
from .test_zodiac_and_admin import make_user, user


def upload(client, color="red"):
    buffer = io.BytesIO()
    Image.new("RGB", (300, 300), color).save(buffer, "JPEG")
    client.post("/profil/billeder", data={"image": (io.BytesIO(buffer.getvalue()), "a.jpg")},
                content_type="multipart/form-data")


def images(name):
    return db.session.scalars(select(ProfileImage).where(ProfileImage.user_id == user(name).id)
                              .order_by(ProfileImage.collection, ProfileImage.position)).all()


def test_make_private_and_public_moves_files_and_hides_image(app, outbox, ctx):
    anna, bodil = make_user(app, outbox, "Anna"), make_user(app, outbox, "Bodil")
    upload(anna, "red")
    upload(anna, "blue")
    first, second = images("Anna")

    anna.post(f"/profil/billeder/{second.public_id}/privat")
    db.session.refresh(second)
    assert second.collection == "private"
    assert os.path.exists(storage.file_path(second, "lille"))
    assert not os.path.exists(os.path.join(app.config["MEDIA_ROOT"], "public", second.public_id))
    assert anna.get(f"/billede/{second.public_id}/lille.webp").status_code == 200  # ejeren ser det
    assert bodil.get(f"/billede/{second.public_id}/lille.webp").status_code == 404
    assert second.public_id not in bodil.get(f"/bruger/{h('Anna')}").get_data(as_text=True)
    assert "Privat album" in anna.get("/profil").get_data(as_text=True)

    anna.post(f"/profil/billeder/{second.public_id}/offentlig")
    db.session.refresh(second)
    assert second.collection == "public" and second.position == 1
    assert bodil.get(f"/billede/{second.public_id}/lille.webp").status_code == 200


def test_last_public_image_cannot_be_made_private_or_deleted_while_private_exist(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    upload(anna)
    (only,) = images("Anna")
    anna.post(f"/profil/billeder/{only.public_id}/privat")
    db.session.refresh(only)
    assert only.collection == "public"  # det eneste offentlige billede bliver offentligt

    upload(anna, "blue")
    first, second = images("Anna")
    anna.post(f"/profil/billeder/{second.public_id}/privat")
    response = anna.post(f"/profil/billeder/{first.public_id}/slet", data={"bekraeft": "1"}, follow_redirects=True)
    assert "mindst ét offentligt billede" in response.get_data(as_text=True)
    assert len(images("Anna")) == 2
    page = anna.get("/profil").get_data(as_text=True)
    assert not re.search(rf"/profil/billeder/{first.public_id}/(slet|privat)", page)

    anna.post(f"/profil/billeder/{second.public_id}/slet", data={"bekraeft": "1"})  # private kan altid slettes
    anna.post(f"/profil/billeder/{first.public_id}/slet", data={"bekraeft": "1"})  # nu er der ingen private
    assert all(image.is_deleted for image in images("Anna"))


def test_other_users_cannot_move_images(app, outbox, ctx):
    anna, bodil = make_user(app, outbox, "Anna"), make_user(app, outbox, "Bodil")
    upload(anna)
    upload(anna, "blue")
    _, second = images("Anna")
    assert bodil.post(f"/profil/billeder/{second.public_id}/privat").status_code == 404
    assert anna.post(f"/profil/billeder/{second.public_id}/hemmelig").status_code == 404
