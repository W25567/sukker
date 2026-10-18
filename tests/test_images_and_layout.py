"""Tests for billeder fra iPhone (MPO), rotation og små layoutrettelser."""
import io
import re

from PIL import Image
from sqlalchemy import select

from app.extensions import db
from app.media import storage
from app.media.models import ProfileImage
from app.media.processing import process_upload

from .test_zodiac_and_admin import make_user


def iphone_jpeg(orientation=6):
    """Et billede som fra en iPhone: JPEG med et ekstra indlejret billede (MPO) og EXIF-orientering."""
    exif = Image.Exif()
    exif[0x0112] = orientation
    buffer = io.BytesIO()
    Image.new("RGB", (400, 300), "red").save(buffer, "MPO", save_all=True,
                                             append_images=[Image.new("RGB", (200, 150), "gray")], exif=exif)
    return buffer.getvalue()


def test_iphone_mpo_is_a_still_and_follows_exif_orientation():
    result = process_upload(iphone_jpeg(), max_still_bytes=15 * 2**20, max_animated_bytes=8 * 2**20,
                            animated_refusal="Animerede billeder kræver Premium")
    assert result.animated is False and (result.width, result.height) == (300, 400)


def test_iphone_upload_for_free_user_is_accepted(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    response = anna.post("/profil/billeder", data={"image": (io.BytesIO(iphone_jpeg()), "IMG_0001.JPG")},
                         content_type="multipart/form-data", follow_redirects=True)
    assert "Billedet er uploadet" in response.get_data(as_text=True)
    image = db.session.scalar(select(ProfileImage))
    assert image.is_animated is False


def test_rotate_image(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    buffer = io.BytesIO()
    Image.new("RGB", (400, 300), "blue").save(buffer, "JPEG")
    anna.post("/profil/billeder", data={"image": (io.BytesIO(buffer.getvalue()), "a.jpg")},
              content_type="multipart/form-data")
    image = db.session.scalar(select(ProfileImage))
    assert (image.width, image.height) == (400, 300)
    page = anna.get("/profil").get_data(as_text=True)
    assert f"/profil/billeder/{image.public_id}/drej/hoejre" in page

    anna.post(f"/profil/billeder/{image.public_id}/drej/hoejre")
    db.session.refresh(image)
    assert (image.width, image.height) == (300, 400)
    with Image.open(storage.file_path(image, "stor")) as stored:
        assert stored.size == (300, 400)
    anna.post(f"/profil/billeder/{image.public_id}/drej/venstre")
    db.session.refresh(image)
    assert (image.width, image.height) == (400, 300)

    bodil = make_user(app, outbox, "Bodil")
    assert bodil.post(f"/profil/billeder/{image.public_id}/drej/hoejre").status_code == 404


def test_profile_pages_layout(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    me = anna.get("/profil").get_data(as_text=True)
    assert "Dit stjernetegn" not in me
    assert re.search(r'<button type="submit" class="button secondary upload-submit">', me)  # skjules af upload.js
    edit = anna.get("/profil/rediger").get_data(as_text=True)
    assert 'form="profile-form"' in edit and 'id="profile-form"' in edit and "desktop-only" in edit
    response = anna.post("/profil/rediger", data={"presentation": "Hej"})
    assert response.status_code == 302
