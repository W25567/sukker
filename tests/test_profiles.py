import io
import os

from PIL import ExifTags, Image
from sqlalchemy import select

from app.core.settings import set_setting
from app.extensions import db
from app.media import storage
from app.media.models import ProfileImage
from app.profiles.models import Profile

from .conftest import h, link_from, login, register


# --- Hjælpere ----------------------------------------------------------------------------


def jpeg_with_gps(size=(3000, 2000)):
    image = Image.new("RGB", size, (200, 40, 90))
    exif = Image.Exif()
    exif[ExifTags.Base.Make] = "TestCam"
    exif[ExifTags.Base.GPSInfo] = {ExifTags.GPS.GPSLatitudeRef: "N", ExifTags.GPS.GPSLatitude: (55.0, 40.0, 12.0)}
    out = io.BytesIO()
    image.save(out, "JPEG", exif=exif)
    return out.getvalue()


def animated_gif(frames=3):
    images = [Image.new("RGB", (120, 80), color) for color in ((255, 0, 0), (0, 255, 0), (0, 0, 255))[:frames]]
    out = io.BytesIO()
    images[0].save(out, "GIF", save_all=True, append_images=images[1:], duration=100, loop=0)
    return out.getvalue()


def upload(client, data, filename="billede.jpg"):
    return client.post(
        "/profil/billeder", data={"image": (io.BytesIO(data), filename)}, content_type="multipart/form-data"
    )


def flashed(client):
    with client.session_transaction() as session:
        return [message for _, message in session.get("_flashes", [])]


def verify(client, outbox):
    client.get(link_from(outbox[-1], "/bekraeft-email/"))


def setup_two_users(app):
    anna, bo = app.test_client(), app.test_client()
    register(anna)
    register(bo, display_name="Bodil", email="bodil@example.com")
    login(anna)
    login(bo, email="bodil@example.com")
    return anna, bo


def images_of():
    return db.session.scalars(select(ProfileImage).order_by(ProfileImage.position)).all()


# --- Profiloplysninger og synlighed -------------------------------------------------------------


def test_profile_pages_require_login(client):
    assert client.get("/profil").status_code == 302
    assert client.get("/bruger/abcdefghij").status_code == 302


def test_edit_profile_saves_fields(client, ctx):
    register(client)
    login(client)
    response = client.post(
        "/profil/rediger",
        data={
            "presentation": "Glad for gåture",
            "gender": "kvinde",
            "seeking_genders": ["mand", "ikke_binaer"],
            "height_cm": "172",
            "occupation": "Tømrer",
            "relationship_types": ["kaereste"],
            "age_min": "30",
            "age_max": "45",
            "preferred_distance_km": "50",
            "long_distance_ok": "nej",
        },
    )
    assert response.status_code == 302
    profile = db.session.scalar(select(Profile))
    assert profile.presentation == "Glad for gåture"
    assert profile.seeking_genders == ["mand", "ikke_binaer"]
    assert (profile.height_cm, profile.age_min, profile.age_max) == (172, 30, 45)
    assert profile.preferred_distance_km == 50
    assert profile.long_distance_ok is False


def test_age_range_must_be_increasing(client, ctx):
    register(client)
    login(client)
    response = client.post("/profil/rediger", data={"age_min": "45", "age_max": "30"})
    assert response.status_code == 400
    assert db.session.scalar(select(Profile.age_min)) is None


def test_hidden_fields_are_not_shown_to_others(app, ctx):
    anna, bo = setup_two_users(app)
    anna.post("/profil/rediger", data={"occupation": "Tømrer", "interests": "Klatring", "hidden_fields": ["occupation"]})

    seen_by_bo = bo.get(f"/bruger/{h('Anna_K')}").get_data(as_text=True)
    assert "Klatring" in seen_by_bo
    assert "Tømrer" not in seen_by_bo
    preview = anna.get("/profil/forhaandsvisning").get_data(as_text=True)
    assert "Tømrer" not in preview and "Klatring" in preview


def test_paused_or_matches_only_profiles_are_hidden_from_others(app, ctx):
    anna, bo = setup_two_users(app)
    assert bo.get(f"/bruger/{h('Anna_K')}").status_code == 200

    anna.post("/profil/pause")
    assert bo.get(f"/bruger/{h('Anna_K')}").status_code == 404
    anna.post("/profil/pause")
    assert bo.get(f"/bruger/{h('Anna_K')}").status_code == 200

    anna.post("/profil/synlighed", data={"matches_only": "y"})
    assert bo.get(f"/bruger/{h('Anna_K')}").status_code == 404
    assert anna.get("/profil/forhaandsvisning").status_code == 200


def test_completeness_grows_as_profile_is_filled(client, ctx):
    register(client)
    login(client)
    assert "Profilen er 0 % udfyldt" in client.get("/profil").get_data(as_text=True)
    client.post("/profil/rediger", data={"presentation": "Hej", "gender": "kvinde"})
    upload(client, jpeg_with_gps((400, 300)))
    assert "Profilen er 16 % udfyldt" in client.get("/profil").get_data(as_text=True)


# --- Upload og omkodning -----------------------------------------------------------------------------


def test_upload_reencodes_to_webp_strips_metadata_and_limits_size(client, ctx):
    register(client)
    login(client)
    original = jpeg_with_gps()
    assert b"TestCam" in original

    upload(client, original)
    image = images_of()[0]
    with open(storage.file_path(image, "stor"), "rb") as fh:
        stored = fh.read()

    assert stored[:4] == b"RIFF" and stored[8:12] == b"WEBP"
    assert b"TestCam" not in stored
    with Image.open(io.BytesIO(stored)) as reopened:
        assert not reopened.getexif()
        assert max(reopened.size) == 2048
    assert (image.width, image.height) == (2048, 1365)
    assert os.path.exists(storage.file_path(image, "lille"))


def test_upload_rejects_files_that_are_not_images(client, ctx):
    register(client)
    login(client)
    upload(client, b"%PDF-1.7 not an image", "billede.jpg")
    assert "Filtypen understøttes ikke" in flashed(client)[-1]
    upload(client, b"\xff\xd8\xff" + b"garbage" * 50, "billede.jpg")
    assert "kunne ikke læses" in flashed(client)[-1]
    assert images_of() == []


def test_animated_images_require_premium(client, outbox, ctx):
    register(client)
    login(client)
    upload(client, animated_gif(), "anim.gif")
    assert "kræver Premium" in flashed(client)[-1]
    assert images_of() == []

    verify(client, outbox)  # giver 3 måneders Premium
    upload(client, animated_gif(), "anim.gif")
    image = images_of()[0]
    assert image.is_animated
    with Image.open(storage.file_path(image, "stor")) as stored:
        assert stored.format == "WEBP" and stored.n_frames == 3


def test_animated_and_public_image_limits(client, outbox, ctx):
    register(client)
    login(client)
    verify(client, outbox)
    set_setting("max_animated_images", 1)
    set_setting("max_public_images", 2)
    db.session.commit()

    upload(client, animated_gif(), "a.gif")
    upload(client, animated_gif(), "b.gif")
    assert "højst have 1 animerede" in flashed(client)[-1]
    upload(client, jpeg_with_gps((400, 300)))
    upload(client, jpeg_with_gps((400, 300)))
    assert "højst have 2 billeder" in flashed(client)[-1]
    assert len(images_of()) == 2


# --- Beskyttet levering --------------------------------------------------------------------------------


def test_image_access_follows_profile_visibility(app, ctx):
    anna, bo = setup_two_users(app)
    upload(anna, jpeg_with_gps((400, 300)))
    url = f"/billede/{images_of()[0].public_id}/stor.webp"

    owner_response = anna.get(url)
    assert owner_response.status_code == 200
    assert owner_response.mimetype == "image/webp"
    assert owner_response.headers["Cache-Control"] == "private, no-cache"
    assert bo.get(url).status_code == 200
    assert app.test_client().get(url).status_code == 404  # ikke logget ind

    anna.post("/profil/pause")
    assert bo.get(url).status_code == 404
    assert anna.get(url).status_code == 200


def test_unknown_images_and_variants_give_404(client, ctx):
    register(client)
    login(client)
    upload(client, jpeg_with_gps((400, 300)))
    public_id = images_of()[0].public_id
    assert client.get("/billede/findes-ikke/stor.webp").status_code == 404
    assert client.get(f"/billede/{public_id}/original.webp").status_code == 404


def test_x_accel_redirect_in_production_mode(app, client, ctx):
    register(client)
    login(client)
    upload(client, jpeg_with_gps((400, 300)))
    image = images_of()[0]
    app.config["MEDIA_X_ACCEL_PREFIX"] = "/_protected_media/"
    try:
        response = client.get(f"/billede/{image.public_id}/lille.webp")
    finally:
        app.config["MEDIA_X_ACCEL_PREFIX"] = None
    assert response.headers["X-Accel-Redirect"] == f"/_protected_media/public/{image.public_id}/thumb.webp"
    assert response.get_data() == b""


def test_delete_and_make_primary(client, ctx):
    register(client)
    login(client)
    for _ in range(3):
        upload(client, jpeg_with_gps((400, 300)))
    first, second, third = images_of()

    client.post(f"/profil/billeder/{third.public_id}/primaer")
    assert [i.public_id for i in images_of()] == [third.public_id, first.public_id, second.public_id]

    path = storage.file_path(first, "stor")
    assert os.path.exists(path)
    client.post(f"/profil/billeder/{first.public_id}/slet")
    assert os.path.exists(path)  # bevares til sporbarhed i opbevaringsperioden
    assert client.get(f"/billede/{first.public_id}/lille.webp").status_code == 404  # heller ikke for ejeren
    assert [(i.public_id, i.position) for i in images_of() if not i.is_deleted] == [(third.public_id, 0), (second.public_id, 1)]


def test_cannot_delete_someone_elses_image(app, ctx):
    anna, bo = setup_two_users(app)
    upload(anna, jpeg_with_gps((400, 300)))
    image = images_of()[0]
    assert bo.post(f"/profil/billeder/{image.public_id}/slet").status_code == 404
    assert len(images_of()) == 1
