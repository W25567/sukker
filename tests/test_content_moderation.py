"""Tests for moderatorers fjernelse af upassende billeder (også uden anmeldelse) og administratorers sletning af
upassende visningsnavne."""
import io
import os

from PIL import Image
from sqlalchemy import select

from app.admin.models import AdminAuditLog
from app.extensions import db
from app.media.models import ProfileImage
from app.messaging.models import AdminMessage
from app.moderation.content import REMOVED_NAME
from app.moderation.models import ModerationCase
from app.moderation.services import evidence_image_path

from .conftest import h
from .test_zodiac_and_admin import make_user, user


def upload(client, color):
    buffer = io.BytesIO()
    Image.new("RGB", (300, 300), color).save(buffer, "JPEG")
    client.post("/profil/billeder", data={"image": (io.BytesIO(buffer.getvalue()), f"{color}.jpg")},
                content_type="multipart/form-data")


def images(name):
    return db.session.scalars(select(ProfileImage).where(ProfileImage.user_id == user(name).id)
                              .order_by(ProfileImage.position, ProfileImage.id)).all()


def moderator_with_access(app, outbox, target):
    mod = make_user(app, outbox, "Mods", role="moderator")
    mod.post(f"/moderation/bruger/{user(target).id}/adgang", data={"reason": "Rutinetjek af billeder"})
    return mod


def test_moderator_removes_image_without_report(app, outbox, ctx):
    anna, bodil = make_user(app, outbox, "Anna"), make_user(app, outbox, "Bodil")
    upload(anna, "red")
    upload(anna, "blue")
    first, second = images("Anna")
    mod = moderator_with_access(app, outbox, "Anna")
    assert "Fjern som upassende" in mod.get(f"/moderation/bruger/{user('Anna').id}").get_data(as_text=True)

    mod.post(f"/moderation/bruger/{user('Anna').id}/billede/{second.public_id}/fjern", data={"reason": "seksuelt"})
    db.session.refresh(second)
    assert second.is_removed and not second.is_deleted
    case = db.session.scalar(select(ModerationCase))
    assert case.source == "moderator" and case.status == "closed" and case.decision == "content_removed"
    assert evidence_image_path(case, second.public_id) is not None  # kopi som bevis

    # Ingen kan se billedet, heller ikke ejeren
    assert anna.get(f"/billede/{second.public_id}/lille.webp").status_code == 404
    assert bodil.get(f"/billede/{second.public_id}/lille.webp").status_code == 404
    assert second.public_id not in bodil.get(f"/bruger/{h('Anna')}").get_data(as_text=True)
    me = anna.get("/profil").get_data(as_text=True)
    assert "Fjernet af moderator" in me and f"/billede/{second.public_id}/" not in me
    assert f"/profil/billeder/{second.public_id}/slet" in me  # markeringen kan slettes
    assert "et af dine billeder er fjernet" in db.session.scalar(select(AdminMessage.body)).lower()

    # Ejeren kan kun slette markeringen, ikke gøre billedet offentligt eller dreje det
    assert anna.post(f"/profil/billeder/{second.public_id}/offentlig").status_code == 404
    anna.post(f"/profil/billeder/{second.public_id}/slet", data={"bekraeft": "1"})
    db.session.refresh(second)
    assert second.is_deleted and "Fjernet af moderator" not in anna.get("/profil").get_data(as_text=True)


def test_removed_last_public_image_and_private(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    upload(anna, "red")
    upload(anna, "blue")
    first, second = images("Anna")
    anna.post(f"/profil/billeder/{second.public_id}/privat")
    mod = moderator_with_access(app, outbox, "Anna")
    mod.post(f"/moderation/bruger/{user('Anna').id}/billede/{first.public_id}/fjern", data={"reason": "upassende"})
    db.session.refresh(first)
    assert first.is_removed
    anna.post(f"/profil/billeder/{first.public_id}/slet", data={"bekraeft": "1"})
    db.session.refresh(first)
    assert first.is_deleted  # en fjernet markering kan altid slettes


def test_moderator_needs_access_and_reason(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    upload(anna, "red")
    (image,) = images("Anna")
    mod = make_user(app, outbox, "Mods", role="moderator")
    url = f"/moderation/bruger/{user('Anna').id}/billede/{image.public_id}/fjern"
    assert mod.post(url, data={"reason": "seksuelt"}).status_code == 404  # ingen registreret adgang
    assert anna.post(url, data={"reason": "seksuelt"}).status_code == 404  # ikke moderator
    mod.post(f"/moderation/bruger/{user('Anna').id}/adgang", data={"reason": "Tjek"})
    mod.post(url, data={"reason": ""})
    db.session.refresh(image)
    assert not image.is_removed


def test_admin_removes_image_and_it_is_logged(app, outbox, ctx):
    admin = make_user(app, outbox, "Hanne", role="admin")
    anna = make_user(app, outbox, "Anna")
    upload(anna, "red")
    (image,) = images("Anna")
    assert "Fjern som upassende" in admin.get(f"/admin/bruger/{user('Anna').id}").get_data(as_text=True)
    admin.post(f"/admin/bruger/{user('Anna').id}/billede/{image.public_id}/fjern", data={"reason": "kontakt"})
    db.session.refresh(image)
    assert image.is_removed
    log = db.session.scalar(select(AdminAuditLog).where(AdminAuditLog.action == "image_removed"))
    assert log.target_username == "Anna" and log.details["image"] == image.public_id


def test_admin_deletes_display_name(app, outbox, ctx):
    admin = make_user(app, outbox, "Hanne", role="admin")
    anna = make_user(app, outbox, "Grimtnavn")
    target_id = user("Grimtnavn").id
    admin.post(f"/admin/bruger/{target_id}", data={"action": "remove_name", "note": "Stødende"})
    from app.auth.models import User
    account = db.session.get(User, target_id)
    assert account.display_name == REMOVED_NAME and account.name_removed_at is not None
    log = db.session.scalar(select(AdminAuditLog).where(AdminAuditLog.action == "name_removed"))
    assert log.details["original"] == "Grimtnavn" and log.target_username == "Grimtnavn"

    page = anna.get("/konto/").get_data(as_text=True)
    assert "Dit visningsnavn er slettet af en moderator" in page
    assert anna.post("/konto/navn", data={"display_name": REMOVED_NAME}).status_code == 400
    anna.post("/konto/navn", data={"display_name": "Pænt navn"})
    db.session.refresh(account)
    assert account.display_name == "Pænt navn" and account.name_removed_at is None
    assert make_user(app, outbox, "Bodil").post(f"/admin/bruger/{target_id}", data={"action": "remove_name"}).status_code == 404
