"""Tests for skjul, blokér, anmeld, moderationssager og moderatoradgang (M6)."""
import io

import pytest
from PIL import Image
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.auth.models import User
from app.extensions import db
from app.matches.models import Like, Match
from app.messaging.models import Message
from app.moderation.models import CaseEvent, ModerationCase, ModeratorAccessLog

from .conftest import h, link_from, login, register


def make_user(app, outbox, username, role="user"):
    client = app.test_client()
    email = f"{username.lower()}@example.com"
    register(client, display_name=username, email=email)
    client.get(link_from(outbox[-1], "/bekraeft-email/"))
    login(client, email=email)
    if role != "user":
        user(username).role = role
        db.session.commit()
    return client


def user(username):
    return db.session.scalar(select(User).where(User.display_name == username))


def matched(a, b, a_name, b_name):
    a.post(f"/like/{h(b_name)}")
    b.post(f"/like/{h(a_name)}")


def usernames_in_explore(client):
    import re

    return re.findall(r"<strong>([^,<]+), \d+</strong>", client.get("/udforsk").get_data(as_text=True))


def latest_case():
    return db.session.scalar(select(ModerationCase).order_by(ModerationCase.id.desc()))


# --- Skjul --------------------------------------------------------------------------------------------


def test_hide_removes_from_explore_but_keeps_contact(app, outbox, ctx):
    anna, bodil = make_user(app, outbox, "Anna"), make_user(app, outbox, "Bodil")
    matched(anna, bodil, "Anna", "Bodil")
    anna.post(f"/bruger/{h('Bodil')}/skjul")

    assert "Bodil" not in usernames_in_explore(anna)
    assert "Anna" in usernames_in_explore(bodil)  # kun for den, der skjuler
    url = anna.get(f"/beskeder/med/{h('Bodil')}").headers["Location"]
    anna.post(url, data={"body": "Stadig kontakt"})
    assert db.session.scalar(select(func.count(Message.id))) == 1

    anna.post(f"/konto/skjulte/{user('Bodil').id}/vis")
    assert "Bodil" in usernames_in_explore(anna)


# --- Blokér --------------------------------------------------------------------------------------------


def test_block_is_mutual_and_ends_match_and_conversation(app, outbox, ctx):
    anna, bodil = make_user(app, outbox, "Anna"), make_user(app, outbox, "Bodil")
    matched(anna, bodil, "Anna", "Bodil")
    url = anna.get(f"/beskeder/med/{h('Bodil')}").headers["Location"]
    anna.post(url, data={"body": "Hej"})

    assert "Blokér Bodil?" in anna.get(f"/bruger/{h('Bodil')}/bloker").get_data(as_text=True)
    anna.post(f"/bruger/{h('Bodil')}/bloker")

    assert db.session.scalar(select(func.count(Match.id)).where(Match.removed_at.is_(None))) == 0
    assert db.session.scalar(select(func.count(Like.id))) == 0
    for client, other in ((anna, "Bodil"), (bodil, "Anna")):
        assert client.get(f"/bruger/{h(other)}").status_code == 404
        assert client.post(f"/like/{h(other)}").status_code == 404
        assert other not in usernames_in_explore(client)
        assert client.get(url).status_code == 404


def test_unblock_does_not_restore_match(app, outbox, ctx):
    anna, bodil = make_user(app, outbox, "Anna"), make_user(app, outbox, "Bodil")
    matched(anna, bodil, "Anna", "Bodil")
    anna.post(f"/bruger/{h('Bodil')}/bloker")
    anna.post(f"/konto/blokeringer/{user('Bodil').id}/ophaev")
    assert bodil.get(f"/bruger/{h('Anna')}").status_code == 200
    assert db.session.scalar(select(func.count(Match.id)).where(Match.removed_at.is_(None))) == 0


def test_blocked_user_cannot_unblock_themselves(app, outbox, ctx):
    anna, bodil = make_user(app, outbox, "Anna"), make_user(app, outbox, "Bodil")
    anna.post(f"/bruger/{h('Bodil')}/bloker")
    bodil.post(f"/konto/blokeringer/{user('Anna').id}/ophaev")
    assert bodil.get(f"/bruger/{h('Anna')}").status_code == 404


# --- Anmeld -------------------------------------------------------------------------------------------


def test_report_profile_creates_case_with_evidence_copy(app, outbox, ctx):
    anna, bodil = make_user(app, outbox, "Anna"), make_user(app, outbox, "Bodil")
    bodil.post("/profil/rediger", data={"gender": "kvinde", "presentation": "Falsk tekst"})
    buffer = io.BytesIO()
    Image.new("RGB", (300, 300), "blue").save(buffer, "JPEG")
    bodil.post("/profil/billeder", data={"image": (io.BytesIO(buffer.getvalue()), "a.jpg")}, content_type="multipart/form-data")

    page = anna.post(f"/anmeld/profil/{h('Bodil')}", data={"reason": "falsk"}).get_data(as_text=True)
    assert "ikke automatisk til en sanktion" in page

    case = latest_case()
    assert (case.category, case.priority, case.status) == ("falsk", "high", "open")
    assert case.subject_user_id == user("Bodil").id and case.reporter_id == user("Anna").id
    assert case.evidence["fields"]["Præsentation"] == "Falsk tekst"
    assert len(case.evidence["copied_images"]) == 1

    # Kopien bevares, selvom brugeren ændrer profilen bagefter.
    bodil.post("/profil/rediger", data={"gender": "kvinde", "presentation": "Ny tekst"})
    db.session.refresh(case)
    assert case.evidence["fields"]["Præsentation"] == "Falsk tekst"


def test_report_message_and_block_at_the_same_time(app, outbox, ctx):
    anna, bodil = make_user(app, outbox, "Anna"), make_user(app, outbox, "Bodil")
    matched(anna, bodil, "Anna", "Bodil")
    url = bodil.get(f"/beskeder/med/{h('Anna')}").headers["Location"]
    bodil.post(url, data={"body": "Truende besked"})
    message_id = db.session.scalar(select(Message.id))

    anna.post(f"/anmeld/besked/{message_id}", data={"reason": "chikane", "also_block": "y"})
    case = latest_case()
    assert case.evidence["body"] == "Truende besked" and case.evidence["sender_username"] == "Bodil"
    assert anna.get(f"/bruger/{h('Bodil')}").status_code == 404


def test_cannot_report_own_message_or_foreign_conversation(app, outbox, ctx):
    anna, bodil = make_user(app, outbox, "Anna"), make_user(app, outbox, "Bodil")
    carla = make_user(app, outbox, "Carla")
    matched(anna, bodil, "Anna", "Bodil")
    url = anna.get(f"/beskeder/med/{h('Bodil')}").headers["Location"]
    anna.post(url, data={"body": "Min egen"})
    message_id = db.session.scalar(select(Message.id))
    assert anna.get(f"/anmeld/besked/{message_id}").status_code == 404
    assert carla.get(f"/anmeld/besked/{message_id}").status_code == 404
    conversation_id = url.rsplit("/", 1)[1]
    assert carla.get(f"/anmeld/samtale/{conversation_id}").status_code == 404


def test_other_reason_requires_description(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    make_user(app, outbox, "Bodil")
    anna.post(f"/anmeld/profil/{h('Bodil')}", data={"reason": "andet"})
    assert latest_case() is None


# --- Moderation ---------------------------------------------------------------------------------------------


def test_moderation_is_hidden_from_normal_users(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    assert anna.get("/moderation/").status_code == 404
    assert anna.get(f"/moderation/bruger/{user('Anna').id}").status_code == 404


def test_moderator_needs_logged_reason_to_see_user_data(app, outbox, ctx):
    anna, bodil = make_user(app, outbox, "Anna"), make_user(app, outbox, "Bodil")
    mod = make_user(app, outbox, "Mogens", role="moderator")
    matched(anna, bodil, "Anna", "Bodil")
    url = anna.get(f"/beskeder/med/{h('Bodil')}").headers["Location"]
    anna.post(url, data={"body": "Privat besked"})
    bodil_id = user("Bodil").id

    gate = mod.get(f"/moderation/bruger/{bodil_id}").get_data(as_text=True)
    assert "revisionsloggen" in gate and "Privat besked" not in gate

    mod.post(f"/moderation/bruger/{bodil_id}/adgang", data={"reason": "Mistanke om svindel", "case_id": ""})
    data = mod.get(f"/moderation/bruger/{bodil_id}").get_data(as_text=True)
    assert "Privat besked" in data and "bodil@example.com" in data
    entry = db.session.scalar(select(ModeratorAccessLog))
    assert (entry.moderator_username, entry.target_username, entry.reason) == ("Mogens", "Bodil", "Mistanke om svindel")

    # Moderatorens læsning giver ingen signaler: Bodils besked er stadig ulæst.
    assert '<span class="unread-count">1</span>' in bodil.get("/beskeder/").get_data(as_text=True)


def test_access_log_cannot_be_changed(app, outbox, ctx):
    mod = make_user(app, outbox, "Mogens", role="moderator")
    make_user(app, outbox, "Bodil")
    mod.post(f"/moderation/bruger/{user('Bodil').id}/adgang", data={"reason": "Test", "case_id": ""})
    with pytest.raises(DBAPIError):
        db.session.execute(text("UPDATE sukker.moderator_access_log SET reason = 'ændret'"))
    db.session.rollback()
    with pytest.raises(DBAPIError):
        db.session.execute(text("DELETE FROM sukker.moderator_access_log"))
    db.session.rollback()


def test_suspension_logs_out_and_blocks_login_with_appeal(app, outbox, ctx):
    anna, bodil = make_user(app, outbox, "Anna"), make_user(app, outbox, "Bodil")
    mod = make_user(app, outbox, "Mogens", role="moderator")
    anna.post(f"/anmeld/profil/{h('Bodil')}", data={"reason": "svindel"})
    case = latest_case()

    mod.post(f"/moderation/sag/{case.id}", data={"action": "assign"})
    mod.post(f"/moderation/sag/{case.id}", data={"action": "decide", "decision-decision": "suspended",
                                                  "decision-note": "Phishing-links i beskeder"})
    db.session.refresh(case)
    assert case.status == "closed" and case.decision == "suspended"
    assert user("Bodil").status == "suspended"
    assert bodil.get("/konto/").status_code == 302  # logget ud på alle enheder

    response = login(app.test_client(), email="bodil@example.com")
    assert response.status_code == 403 and "Klag over afgørelsen" in response.get_data(as_text=True)

    app.test_client().post("/klage", data={"email": "bodil@example.com", "text": "Det var ikke mig"})
    appeal = latest_case()
    assert (appeal.source, appeal.subject_user_id) == ("appeal", user("Bodil").id)
    kinds = db.session.scalars(select(CaseEvent.kind).where(CaseEvent.case_id == case.id).order_by(CaseEvent.id)).all()
    assert kinds == ["created", "assigned", "decision"]


def test_restricted_account_is_hidden_and_cannot_act(app, outbox, ctx):
    anna, bodil = make_user(app, outbox, "Anna"), make_user(app, outbox, "Bodil")
    matched(anna, bodil, "Anna", "Bodil")
    user("Bodil").status = "restricted"
    db.session.commit()

    assert "Bodil" not in usernames_in_explore(anna)
    assert anna.get(f"/bruger/{h('Bodil')}").status_code == 404
    page = bodil.get("/udforsk").get_data(as_text=True)
    assert "midlertidigt begrænset" in page
    make_user(app, outbox, "Carla")
    bodil.post(f"/like/{h('Carla')}")
    assert db.session.scalar(select(func.count(Like.id)).where(Like.likee_id == user("Carla").id)) == 0


def test_moderator_cannot_decide_own_case(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    mod = make_user(app, outbox, "Mogens", role="moderator")
    anna.post(f"/anmeld/profil/{h('Mogens')}", data={"reason": "chikane"})
    case = latest_case()
    mod.post(f"/moderation/sag/{case.id}", data={"action": "decide", "decision-decision": "no_action",
                                                  "decision-note": "Frikender mig selv"})
    db.session.refresh(case)
    assert case.status == "open"


def test_audit_log_page_is_admin_only(app, outbox, ctx):
    mod = make_user(app, outbox, "Mogens", role="moderator")
    admin = make_user(app, outbox, "Alma", role="admin")
    assert mod.get("/moderation/log").status_code == 404
    assert admin.get("/moderation/log").status_code == 200
    assert admin.get("/moderation/").status_code == 404  # roller er adskilte
