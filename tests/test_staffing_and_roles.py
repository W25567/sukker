"""Tests for ansøgninger om at blive moderator og for, at handlinger altid går ét niveau ned."""
import pytest
from sqlalchemy import select

from app.extensions import db
from app.messaging.models import AdminMessage
from app.moderation.models import ModerationCase
from app.staffing.models import ModeratorApplication

from .conftest import h
from .test_safety import latest_case, make_user, user

APPLICATION = {"motivation": "Jeg vil gerne hjælpe", "experience": "Frivillig i 5 år", "availability": "3 timer",
               "confidentiality": "y"}


def admin_messages_to(username):
    return db.session.scalars(select(AdminMessage.body).where(AdminMessage.user_id == user(username).id,
                                                              AdminMessage.from_admin.is_(True))).all()


# --- Ansøgninger ------------------------------------------------------------------------------------------------


def test_apply_and_get_approved(app, outbox, ctx):
    admin = make_user(app, outbox, "Alma", role="admin")
    anna = make_user(app, outbox, "Anna")
    assert "Bliv moderator" in anna.get("/hjaelp").get_data(as_text=True)
    anna.post("/moderator/ansoeg", data={**APPLICATION, "confidentiality": ""})  # tavshedspligten skal accepteres
    assert db.session.scalar(select(ModeratorApplication.id)) is None
    anna.post("/moderator/ansoeg", data=APPLICATION)
    assert "Din ansøgning venter" in anna.get("/moderator/ansoeg").get_data(as_text=True)
    anna.post("/moderator/ansoeg", data=APPLICATION)  # kun én ad gangen
    assert len(db.session.scalars(select(ModeratorApplication)).all()) == 1

    page = admin.get("/admin/ansoegninger").get_data(as_text=True)
    assert "Jeg vil gerne hjælpe" in page and "Frivillig i 5 år" in page
    assert 'Ansøgninger <span class="unread-count">1</span>' in admin.get("/admin/").get_data(as_text=True)
    application = db.session.scalar(select(ModeratorApplication))
    admin.post("/admin/ansoegninger", data={"action": "approve", "application_id": application.id})
    db.session.refresh(application)
    assert application.status == "approved" and user("Anna").role == "moderator"
    assert any("godkendt" in body for body in admin_messages_to("Anna"))
    assert "Bliv moderator" not in anna.get("/hjaelp").get_data(as_text=True)


def test_reject_withdraw_and_remove_moderator(app, outbox, ctx):
    admin = make_user(app, outbox, "Alma", role="admin")
    anna = make_user(app, outbox, "Anna")
    anna.post("/moderator/ansoeg", data=APPLICATION)
    application = db.session.scalar(select(ModeratorApplication))
    admin.post("/admin/ansoegninger", data={"action": "reject", "application_id": application.id,
                                            "note": "Vi mangler ikke flere lige nu."})
    assert user("Anna").role == "user"
    assert any("Vi mangler ikke flere lige nu." in body for body in admin_messages_to("Anna"))

    anna.post("/moderator/ansoeg", data=APPLICATION)  # må søge igen
    anna.post("/moderator/ansoeg/traek-tilbage")
    assert db.session.scalars(select(ModeratorApplication.status).order_by(ModeratorApplication.id)).all() == [
        "rejected", "withdrawn"]

    mogens = make_user(app, outbox, "Mogens", role="moderator")
    assert "Mogens" in admin.get("/admin/ansoegninger").get_data(as_text=True)
    admin.post("/admin/ansoegninger", data={"action": "remove_moderator", "user_id": user("Mogens").id})
    assert user("Mogens").role == "user"
    assert mogens.get("/moderation/").status_code == 404


# --- Altid ét niveau ned ---------------------------------------------------------------------------------------


def test_moderator_cannot_act_on_admin_or_moderator(app, outbox, ctx):
    make_user(app, outbox, "Alma", role="admin")
    mod = make_user(app, outbox, "Mogens", role="moderator")
    make_user(app, outbox, "Mette", role="moderator")
    anna = make_user(app, outbox, "Anna")

    for name in ("Alma", "Mette"):
        target_id = user(name).id
        response = mod.get(f"/moderation/bruger/{target_id}")
        assert response.status_code == 403 and "lavere niveau" in response.get_data(as_text=True)
        mod.post(f"/moderation/bruger/{target_id}/adgang", data={"reason": "Nysgerrig"})
        assert mod.get(f"/moderation/bruger/{target_id}").status_code == 403
    assert "/moderation/bruger/" not in mod.get(f"/bruger/{h('Alma')}").get_data(as_text=True)

    # En sag om administratoren kan ikke give en kontohandling, men kan lukkes uden handling
    anna.post(f"/anmeld/profil/{h('Alma')}", data={"reason": "svindel"})
    case = latest_case()
    mod.post(f"/moderation/sag/{case.id}", data={"action": "decide", "decision-decision": "suspended",
                                                  "decision-note": "Ud med dig"})
    assert user("Alma").status == "active" and db.session.get(ModerationCase, case.id).status != "closed"
    mod.post(f"/moderation/sag/{case.id}", data={"action": "decide", "decision-decision": "no_action",
                                                  "decision-note": "Sendt videre til en administrator"})
    db.session.refresh(case)
    assert case.status == "closed" and user("Alma").status == "active"

    # En almindelig bruger kan moderatoren stadig håndtere
    assert mod.get(f"/moderation/bruger/{user('Anna').id}").status_code == 200


def test_admin_cannot_act_on_another_admin(app, outbox, ctx):
    alma = make_user(app, outbox, "Alma", role="admin")
    make_user(app, outbox, "Asger", role="admin")
    make_user(app, outbox, "Mogens", role="moderator")
    asger_id = user("Asger").id
    response = alma.post(f"/admin/bruger/{asger_id}", data={"action": "status", "status": "suspended"},
                         follow_redirects=True)
    assert user("Asger").status == "active" and "lavere niveau" in response.get_data(as_text=True)
    alma.post(f"/admin/bruger/{asger_id}", data={"action": "role", "role": "user"})
    alma.post(f"/admin/bruger/{asger_id}", data={"action": "expel", "note": "Test"})
    assert user("Asger").role == "admin" and user("Asger").status == "active"
    # En moderator er et niveau under
    alma.post(f"/admin/bruger/{user('Mogens').id}", data={"action": "status", "status": "suspended", "note": "Misbrug"})
    assert user("Mogens").status == "suspended"


def test_verification_only_one_level_down(app, outbox, ctx):
    from app.verification.services import VerificationRefused, _check_reach

    make_user(app, outbox, "Alma", role="admin")
    make_user(app, outbox, "Mogens", role="moderator")
    make_user(app, outbox, "Anna")
    with pytest.raises(VerificationRefused):
        _check_reach(user("Mogens"), user("Alma"))
    with pytest.raises(VerificationRefused):
        _check_reach(user("Mogens"), user("Mogens"))
    _check_reach(user("Mogens"), user("Anna"))
    _check_reach(user("Alma"), user("Mogens"))
