"""Tests for inaktive profiler, Premium-sortering, usædvanlig interesse (afstandsmaske), verificering med selfie og
afmelding af notifikationsmails med ét klik."""
import io
import os
from datetime import timedelta

from PIL import Image
from sqlalchemy import select

from app.core.settings import set_setting
from app.core.timeutil import now_utc
from app.extensions import db
from app.moderation.models import ModerationCase
from app.notify import mail
from app.notify.prefs import wants
from app.safety.models import DistanceMask, ProfileVisit
from app.verification.models import PhotoVerification

from .conftest import h, login
from .test_messaging import make_free, matched_pair, open_conversation, send
from .test_zodiac_and_admin import make_user, user


def jpeg(color="red"):
    buffer = io.BytesIO()
    Image.new("RGB", (300, 300), color).save(buffer, "JPEG")
    return buffer.getvalue()


def upload(client, color="red"):
    client.post("/profil/billeder", data={"image": (io.BytesIO(jpeg(color)), f"{color}.jpg")},
                content_type="multipart/form-data")


def explore(client, **args):
    return client.get("/udforsk", query_string=args).get_data(as_text=True)


# --- Inaktive profiler -----------------------------------------------------------------------------------------


def test_inactive_profile_is_hidden_until_login(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    make_user(app, outbox, "Bodil")
    assert f"/bruger/{h('Bodil')}" in explore(anna)
    user("Bodil").last_active_at = now_utc() - timedelta(days=95)
    db.session.commit()
    assert f"/bruger/{h('Bodil')}" not in explore(anna)

    login(app.test_client(), email="bodil@example.com")  # Bodil logger ind igen
    assert f"/bruger/{h('Bodil')}" in explore(anna)

    set_setting("inactive_hide_months", 0)  # slået fra
    user("Bodil").last_active_at = now_utc() - timedelta(days=400)
    db.session.commit()
    assert f"/bruger/{h('Bodil')}" in explore(anna)


def test_inactive_warning_mail_once(app, outbox, ctx):
    make_user(app, outbox, "Anna")
    user("Anna").last_active_at = now_utc() - timedelta(days=85)
    db.session.commit()
    assert mail.run()["inaktiv"] == 1
    assert mail.run()["inaktiv"] == 0
    (sent,) = [m for m in outbox if m["subject"] == "Din profil på Sukker.nu bliver snart skjult"]
    assert "bliver din profil derfor skjult fra Udforsk" in sent["body"] and "/afmeld/" in sent["body"]
    assert sent["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_no_inactive_warning_when_paused(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    anna.post("/profil/pause")
    user("Anna").last_active_at = now_utc() - timedelta(days=85)
    db.session.commit()
    assert mail.run()["inaktiv"] == 0


# --- Premium: nyeste først -------------------------------------------------------------------------------------


def test_newest_first_is_premium(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    make_user(app, outbox, "Bodil")
    make_user(app, outbox, "Cecilie")
    page = explore(anna)
    assert 'name="sortering"' in page and "Rækkefølge: efter afstand og derefter nyeste profil." in page
    page = explore(anna, sortering="nyeste")
    assert "Rækkefølge: nyeste profiler først." in page
    assert page.index(f"/bruger/{h('Cecilie')}") < page.index(f"/bruger/{h('Bodil')}")

    make_free("Anna")
    page = explore(anna, sortering="nyeste")
    assert 'name="sortering"' not in page and "Rækkefølge: efter afstand og derefter nyeste profil." in page
    assert "at se de nyeste profiler først kræver Premium" in page


def test_stored_feature_mapping_gets_new_feature(app, outbox, ctx):
    from app.core import features

    set_setting("tier_features", {"free": ["messages.read"], "premium": ["messages.read"], "premium_plus": []})
    db.session.commit()
    assert "search.newest_first" in features.tier_features()["premium"]  # ny funktion får standarden
    set_setting("tier_features", features.stored_mapping({"free": [], "premium": [], "premium_plus": []}))
    db.session.commit()
    assert "search.newest_first" not in features.tier_features()["premium"]  # gemt efter, at den fandtes


# --- Usædvanlig interesse --------------------------------------------------------------------------------------


def visit(client, name, times):
    for _ in range(times):
        assert client.get(f"/bruger/{h(name)}").status_code == 200
        for row in db.session.scalars(select(ProfileVisit)):
            row.last_view_at -= timedelta(minutes=10)  # så hvert besøg tæller
        db.session.commit()


def test_interest_signal_masks_distance(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    make_user(app, outbox, "Bodil")
    visit(anna, "Bodil", 14)
    assert db.session.scalar(select(DistanceMask)) is None
    visit(anna, "Bodil", 1)
    mask = db.session.scalar(select(DistanceMask))
    assert (mask.viewer_id, mask.target_id) == (user("Anna").id, user("Bodil").id)
    case = db.session.scalar(select(ModerationCase).where(ModerationCase.category == "signal_interesse"))
    assert case.subject_user_id == user("Anna").id and case.evidence["target_user_id"] == user("Bodil").id
    assert mask.case_id == case.id
    assert "I nærheden" in anna.get(f"/bruger/{h('Bodil')}").get_data(as_text=True)
    assert "I nærheden" in explore(anna)


def test_quick_revisits_count_once(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    make_user(app, outbox, "Bodil")
    for _ in range(20):
        anna.get(f"/bruger/{h('Bodil')}")
    assert db.session.scalar(select(ProfileVisit.views)) == 1
    assert db.session.scalar(select(DistanceMask)) is None


# --- Verificering med selfie -----------------------------------------------------------------------------------


def send_selfie(client):
    client.get("/profil/verificering")
    return client.post("/profil/verificering", data={"selfie": (io.BytesIO(jpeg("blue")), "selfie.jpg")},
                       content_type="multipart/form-data")


def test_selfie_requires_public_image(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    assert "mindst ét offentligt profilbillede" in anna.get("/profil/verificering").get_data(as_text=True)
    response = send_selfie(anna)
    assert response.status_code == 400 and db.session.scalar(select(PhotoVerification)) is None


def test_verification_approved(app, outbox, ctx):
    anna, bodil = make_user(app, outbox, "Anna"), make_user(app, outbox, "Bodil")
    upload(anna)
    assert "Verificér din profil" in anna.get("/profil").get_data(as_text=True)
    page = anna.get("/profil/verificering").get_data(as_text=True)
    assert 'class="gesture"' in page
    send_selfie(anna)
    row = db.session.scalar(select(PhotoVerification))
    assert row.status == "pending" and row.public_id
    selfie_dir = os.path.join(app.config["MEDIA_ROOT"], "verification", row.public_id)
    assert os.path.isdir(selfie_dir)
    assert "venter på at blive vurderet" in anna.get("/profil").get_data(as_text=True)
    assert send_selfie(anna).status_code == 400  # kun én ad gangen

    assert bodil.get("/moderation/verificeringer").status_code == 404  # kun moderatorer og administratorer
    mod = make_user(app, outbox, "Mods", role="moderator")
    assert "Anna" in mod.get("/moderation/verificeringer").get_data(as_text=True)
    assert mod.get(f"/moderation/verificering/{row.id}/selfie.webp").status_code == 200
    mod.post(f"/moderation/verificering/{row.id}", data={"action": "approve"})
    db.session.refresh(row)
    assert row.status == "approved" and row.public_id is None and not os.path.exists(selfie_dir)
    assert user("Anna").photo_verified_at is not None
    assert "Verificeret" in bodil.get(f"/bruger/{h('Anna')}").get_data(as_text=True)
    assert "verified-badge" in explore(bodil)
    from app.messaging.models import AdminMessage
    assert "verificeret" in db.session.scalars(select(AdminMessage.body)).first()


def test_verification_rejected_needs_reason_and_revoke(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    upload(anna)
    send_selfie(anna)
    row = db.session.scalar(select(PhotoVerification))
    admin = make_user(app, outbox, "Hanne", role="admin")
    admin.post(f"/moderation/verificering/{row.id}", data={"action": "reject", "note": ""})
    db.session.refresh(row)
    assert row.status == "pending"
    admin.post(f"/moderation/verificering/{row.id}", data={"action": "reject", "note": "Ansigtet kan ikke ses."})
    db.session.refresh(row)
    assert row.status == "rejected" and row.public_id is None
    assert "Ansigtet kan ikke ses." in anna.get("/profil/verificering").get_data(as_text=True)

    send_selfie(anna)  # nyt forsøg
    second = db.session.scalar(select(PhotoVerification).where(PhotoVerification.status == "pending"))
    admin.post(f"/moderation/verificering/{second.id}", data={"action": "approve"})
    admin.post(f"/moderation/verificering/{second.id}", data={"action": "revoke", "note": "Nye billeder af en anden."})
    db.session.refresh(second)
    assert second.status == "revoked" and user("Anna").photo_verified_at is None


def test_deleting_account_removes_pending_selfie(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    upload(anna)
    send_selfie(anna)
    row = db.session.scalar(select(PhotoVerification))
    selfie_dir = os.path.join(app.config["MEDIA_ROOT"], "verification", row.public_id)
    anna.post("/konto/slet", data={"password": "et-langt-password", "confirm": "y"})
    db.session.refresh(row)
    assert row.status == "rejected" and row.public_id is None and not os.path.exists(selfie_dir)


# --- Afmelding med ét klik -------------------------------------------------------------------------------------


def test_one_click_unsubscribe(app, outbox, ctx):
    anna, bodil = matched_pair(app, outbox)
    send(bodil, open_conversation(bodil, "Anna"), "Hej")
    mail.run(now_utc() + timedelta(minutes=20))
    (sent,) = [m for m in outbox if m["to"] == "anna@example.com" and m["subject"].startswith("Der er nyt")]
    url = sent["headers"]["List-Unsubscribe"].strip("<>")
    path = url[url.index("/afmeld/"):]
    assert path in sent["body"]

    stranger = app.test_client()  # uden login, som et mailprogram
    assert "Vil du afmelde e-mails om nyt" in stranger.get(path).get_data(as_text=True)
    assert wants(user("Anna"), "message", "mail")  # GET ændrer intet
    response = stranger.post(path, data={"List-Unsubscribe": "One-Click"})
    assert response.status_code == 200 and "Du er afmeldt" in response.get_data(as_text=True)
    db.session.refresh(user("Anna"))
    assert not wants(user("Anna"), "message", "mail") and not wants(user("Anna"), "match", "mail")
    assert wants(user("Anna"), "premium", "mail") and wants(user("Anna"), "message", "app")

    assert stranger.post("/afmeld/forkert-token").status_code == 404
