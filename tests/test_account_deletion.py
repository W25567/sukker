"""Tests for blød sletning: en slettet konto er lukket og skjult for andre, men alle data bevares til
administration og moderation i opbevaringsperioden og slettes først derefter (sporbarhed)."""
import io
import os

from PIL import Image
from sqlalchemy import select

from app.admin.models import AdminAuditLog
from app.auth.models import User
from app.core.timeutil import add_months, now_utc, today_local
from app.extensions import db
from app.maintenance import retention
from app.media import storage
from app.media.models import ProfileImage
from app.messaging.models import AdminThread
from app.moderation.models import CaseEvent, ModerationCase

from .conftest import h, link_from, login, register
from .test_messaging import matched_pair, open_conversation, send
from .test_zodiac_and_admin import make_user

PASSWORD = "et-langt-password"
LONG_AGO = add_months(now_utc(), -13)


def user(name):
    return db.session.scalar(select(User).where(User.display_name == name))


def upload(client, color="red"):
    buffer = io.BytesIO()
    Image.new("RGB", (300, 300), color).save(buffer, "JPEG")
    client.post("/profil/billeder", data={"image": (io.BytesIO(buffer.getvalue()), "a.jpg")},
                content_type="multipart/form-data")


def delete(client):
    return client.post("/konto/slet", data={"password": PASSWORD, "confirm": "y"})


def test_deleted_account_is_closed_hidden_and_email_is_free(app, outbox, ctx):
    anna, bodil = matched_pair(app, outbox)
    upload(anna)
    handle = h("Anna")
    assert delete(anna).status_code == 302

    anna_user = user("Anna")
    assert anna_user.status == "deleted" and anna_user.deleted_email == "anna@example.com"
    assert bodil.get(f"/bruger/{handle}").status_code == 404
    assert "Anna, " not in bodil.get("/udforsk").get_data(as_text=True)
    assert "Anna" not in bodil.get("/likes").get_data(as_text=True)
    assert anna.get("/profil").status_code == 302  # logget ud
    stranger = app.test_client()
    login(stranger)  # den gamle e-mail og password virker ikke længere
    assert stranger.get("/profil").status_code == 302

    # Samme e-mail kan bruges til en ny konto
    again = app.test_client()
    register(again, display_name="Anna2", email="anna@example.com")
    assert db.session.scalar(select(User).where(User.email == "anna@example.com")).display_name == "Anna2"
    log = db.session.scalar(select(AdminAuditLog).where(AdminAuditLog.action == "account_deleted"))
    assert log.target_user_id == anna_user.id and "anna@example.com" not in str(log.details)


def test_admin_sees_deleted_user_with_name_email_images_and_threads(app, outbox, ctx):
    admin = make_user(app, outbox, "Hanne", role="admin")
    anna = make_user(app, outbox, "Anna")
    upload(anna)
    upload(anna, "blue")
    first, second = db.session.scalars(select(ProfileImage).where(ProfileImage.user_id == user("Anna").id)
                                       .order_by(ProfileImage.position)).all()
    anna.post(f"/profil/billeder/{second.public_id}/slet")  # billedet slettes af brugeren selv
    admin.post(f"/admin/bruger/{user('Anna').id}/samtale", data={"body": "Husk reglerne"})
    delete(anna)
    anna_id = user("Anna").id

    page = admin.get(f"/admin/bruger/{anna_id}").get_data(as_text=True)
    assert "Slettet bruger" in page and "anna@example.com" in page and "slettede sin profil" in page
    assert second.public_id in page and "Slettet af brugeren" in page  # også det slettede billede
    assert admin.get(f"/admin/bruger/{anna_id}/billede/{second.public_id}/lille.webp").status_code == 200
    assert db.session.get(AdminThread, anna_id) is not None
    threads = admin.get("/admin/samtaler").get_data(as_text=True)
    assert "Anna · slettet bruger (anna@example.com)" in threads
    listing = admin.get("/admin/brugere?filter=slettede").get_data(as_text=True)
    assert ">Anna<" in listing and "anna@example.com" in listing
    assert ">Anna<" in admin.get("/admin/brugere?q=anna@example.com").get_data(as_text=True)

    # En slettet konto kan ikke genåbnes eller få beskeder
    admin.post(f"/admin/bruger/{anna_id}", data={"action": "status", "status": "active"})
    assert user("Anna").status == "deleted"
    response = admin.post(f"/admin/bruger/{anna_id}/samtale", data={"body": "Hallo?"}, follow_redirects=True)
    assert "slettet sin profil" in response.get_data(as_text=True)


def test_case_survives_deletion_and_blocks_purge(app, outbox, ctx):
    anna, bodil = matched_pair(app, outbox)
    url = open_conversation(anna, "Bodil")
    send(anna, url, "Truende besked")
    bodil.post(f"/anmeld/samtale/{url.rsplit('/', 1)[1]}", data={"reason": "chikane", "description": "Truer mig"})
    case = db.session.scalar(select(ModerationCase))
    delete(anna)

    db.session.refresh(case)
    anna_id = user("Anna").id
    assert case.subject_user_id == anna_id  # sagen peger stadig på kontoen
    assert any("slettede sin profil" in (e.text or "") for e in db.session.scalars(
        select(CaseEvent).where(CaseEvent.case_id == case.id)))

    user("Anna").deleted_at = LONG_AGO
    db.session.commit()
    assert retention.run()["slettede_konti"] == 0  # åben sag: data bevares
    assert user("Anna") is not None

    case.status, case.closed_at = "closed", now_utc()
    case.evidence_retained_until = today_local().replace(year=today_local().year + 1)  # forlænget, fx politiet
    db.session.commit()
    assert retention.run()["slettede_konti"] == 0

    case.evidence_retained_until = None
    db.session.commit()
    assert retention.run()["slettede_konti"] == 1
    assert user("Anna") is None
    assert db.session.get(ModerationCase, case.id) is not None  # sager forsvinder aldrig
    assert db.session.scalar(select(AdminAuditLog).where(AdminAuditLog.action == "account_purged")) is not None


def test_report_conversation_with_deleted_user_attaches_case_to_account(app, outbox, ctx):
    anna, bodil = matched_pair(app, outbox)
    url = open_conversation(anna, "Bodil")
    send(anna, url, "Grim besked")
    delete(anna)
    page = bodil.get(f"/anmeld/samtale/{url.rsplit('/', 1)[1]}").get_data(as_text=True)
    assert "Personen får ikke at vide" in page  # anonym for den, der anmelder
    bodil.post(f"/anmeld/samtale/{url.rsplit('/', 1)[1]}", data={"reason": "chikane"})
    assert db.session.scalar(select(ModerationCase)).subject_user_id == user("Anna").id


def test_deleted_image_is_kept_until_retention_then_purged(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    upload(anna)
    upload(anna, "blue")
    image = db.session.scalars(select(ProfileImage).order_by(ProfileImage.position)).all()[1]
    path = storage.file_path(image, "lille")
    anna.post(f"/profil/billeder/{image.public_id}/slet")
    db.session.refresh(image)
    assert image.is_deleted and os.path.exists(path)
    assert image.public_id not in anna.get("/profil").get_data(as_text=True)

    retention.run()
    assert os.path.exists(path)
    image.deleted_at = LONG_AGO
    db.session.commit()
    assert retention.run()["slettede_billeder"] == 1
    assert not os.path.exists(path) and db.session.scalar(select(ProfileImage).where(ProfileImage.id == image.id)) is None
