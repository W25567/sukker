"""Tests for reaktioner på beskeder og permanent lukning af konti uden klageadgang."""
from sqlalchemy import func, select

from app.messaging.models import AdminMessage, AdminMessageReaction, Message, MessageReaction
from app.moderation.models import ModerationCase
from app.subscriptions.services import effective_tier
from app.extensions import db

from .conftest import h, login
from .test_admin import make_free
from .test_safety import latest_case, make_user, matched, user


def conversation_url(client, other):
    return client.get(f"/beskeder/med/{h(other)}").headers["Location"]


def react(client, url, message_id, reaction, **kw):
    return client.post(f"{url}/besked/{message_id}/reaktion", data={"reaction": reaction}, **kw)


def reaction_of(username):
    return db.session.scalar(select(MessageReaction.reaction).where(MessageReaction.user_id == user(username).id))


# --- Reaktioner i samtaler ------------------------------------------------------------------------------------


def test_react_change_and_remove_in_conversation(app, outbox, ctx):
    anna, bodil = make_user(app, outbox, "Anna"), make_user(app, outbox, "Bodil")
    matched(anna, bodil, "Anna", "Bodil")
    url = conversation_url(bodil, "Anna")
    bodil.post(url, data={"body": "Hej Anna"})
    message_id = db.session.scalar(select(Message.id))
    version = anna.get(url + "/nye?version=x").get_json()["version"]

    response = react(anna, url, message_id, "hjerte")
    assert response.status_code == 302 and response.headers["Location"].endswith(f"#b{message_id}")
    assert reaction_of("Anna") == "hjerte"
    assert "❤️" in bodil.get(url).get_data(as_text=True)  # den anden ser reaktionen
    page = anna.get(url).get_data(as_text=True)
    assert 'value="hjerte" title="Hjerte (fjern)"' in page and 'aria-pressed="true"' in page
    # Reaktionen er selv knappen, man retter med, så hjertet ikke står to gange
    assert '<summary class="reaction-pill" title="Ret din reaktion"' in page
    assert 'class="reaction-pill" aria-label' not in page and "has-reactions" in page
    # Den automatiske opdatering opdager reaktionen
    assert anna.get(url + f"/nye?version={version}").status_code == 200

    react(anna, url, message_id, "griner")  # en anden reaktion erstatter
    assert reaction_of("Anna") == "griner"
    react(anna, url, message_id, "griner", headers={"X-Requested-With": "fetch"})  # samme igen fjerner
    assert reaction_of("Anna") is None
    assert react(anna, url, message_id, "hjerte", headers={"X-Requested-With": "fetch"}).status_code == 204


def test_latest_reaction_in_inbox(app, outbox, ctx):
    anna, bodil = make_user(app, outbox, "Anna"), make_user(app, outbox, "Bodil")
    matched(anna, bodil, "Anna", "Bodil")
    url = conversation_url(bodil, "Anna")
    bodil.post(url, data={"body": "Tal ordentligt :)"})
    message_id = db.session.scalar(select(Message.id))
    react(anna, url, message_id, "hjerte")

    assert 'Anna reagerede med ❤️ på "Tal ordentligt :)"' in bodil.get("/beskeder/").get_data(as_text=True)
    assert 'Du reagerede med ❤️ på "Tal ordentligt :)"' in anna.get("/beskeder/").get_data(as_text=True)
    # En ny besked efter reaktionen vises igen som den sidste
    bodil.post(url, data={"body": "Tak"})
    inbox = anna.get("/beskeder/").get_data(as_text=True)
    assert "reagerede med" not in inbox and "Tak" in inbox


def test_reaction_counts_as_unread_until_opened(app, outbox, ctx):
    from .test_notifications import badge

    anna, bodil = make_user(app, outbox, "Anna"), make_user(app, outbox, "Bodil")
    matched(anna, bodil, "Anna", "Bodil")
    url = conversation_url(bodil, "Anna")
    bodil.post(url, data={"body": "Hej Anna"})
    anna.get(url)  # Anna læser beskeden
    react(anna, url, db.session.scalar(select(Message.id)), "hjerte")

    assert badge(bodil.get("/udforsk").get_data(as_text=True), "messages") == 1
    assert bodil.get("/notifikationer").get_json()["messages"] == 1
    assert 'class="unread-count">1<' in bodil.get("/beskeder/").get_data(as_text=True)
    assert badge(anna.get("/udforsk").get_data(as_text=True), "messages") == 0  # ens egen reaktion tæller ikke
    bodil.get(url)  # samtalen åbnes
    assert badge(bodil.get("/udforsk").get_data(as_text=True), "messages") == 0


def test_admin_reaction_counts_as_unread(app, outbox, ctx):
    from .test_notifications import badge

    admin = make_user(app, outbox, "Alma", role="admin")
    anna = make_user(app, outbox, "Anna")
    anna_id = user("Anna").id
    admin.post(f"/admin/bruger/{anna_id}/samtale", data={"body": "Hej"})
    anna.get("/beskeder/administrator")
    anna.post("/beskeder/administrator", data={"body": "Tak"})
    admin.get(f"/admin/bruger/{anna_id}/samtale")
    reply = db.session.scalar(select(AdminMessage.id).where(AdminMessage.from_admin.is_(False)))
    admin.post(f"/admin/bruger/{anna_id}/samtale/besked/{reply}/reaktion", data={"reaction": "hjerte"})
    assert badge(anna.get("/udforsk").get_data(as_text=True), "messages") == 1
    anna.get("/beskeder/administrator")
    assert badge(anna.get("/udforsk").get_data(as_text=True), "messages") == 0


def test_reaction_rules(app, outbox, ctx):
    anna, bodil = make_user(app, outbox, "Anna"), make_user(app, outbox, "Bodil")
    cecilie = make_user(app, outbox, "Cecilie")
    matched(anna, bodil, "Anna", "Bodil")
    url = conversation_url(bodil, "Anna")
    bodil.post(url, data={"body": "Hej Anna"})
    message_id = db.session.scalar(select(Message.id))

    react(bodil, url, message_id, "hjerte")  # egen besked
    react(anna, url, message_id, "kys")  # findes ikke
    assert db.session.scalar(select(func.count()).select_from(MessageReaction)) == 0
    assert 'class="react"' not in bodil.get(url).get_data(as_text=True).split("Hej Anna")[1].split("</li>")[0]
    assert react(cecilie, url, message_id, "hjerte").status_code == 404  # ikke med i samtalen

    # Kan man ikke skrive (Free og Free), kan man heller ikke reagere
    make_free("Anna")
    make_free("Bodil")
    assert react(anna, url, message_id, "hjerte", headers={"X-Requested-With": "fetch"}).status_code == 409
    assert 'class="react"' not in anna.get(url).get_data(as_text=True)


def test_reactions_with_administrator(app, outbox, ctx):
    admin = make_user(app, outbox, "Alma", role="admin")
    anna = make_user(app, outbox, "Anna")
    anna_id = user("Anna").id
    admin.post(f"/admin/bruger/{anna_id}/samtale", data={"body": "Husk vilkårene"})
    from_admin = db.session.scalar(select(AdminMessage.id).where(AdminMessage.from_admin.is_(True)))

    anna.post(f"/beskeder/administrator/besked/{from_admin}/reaktion", data={"reaction": "tommel"})
    assert "👍" in admin.get(f"/admin/bruger/{anna_id}/samtale").get_data(as_text=True)
    assert 'Du reagerede med 👍 på "Husk vilkårene"' in anna.get("/beskeder/").get_data(as_text=True)

    anna.post("/beskeder/administrator", data={"body": "Undskyld"})
    reply = db.session.scalar(select(AdminMessage.id).where(AdminMessage.from_admin.is_(False)))
    admin.post(f"/admin/bruger/{anna_id}/samtale/besked/{reply}/reaktion", data={"reaction": "hjerte"})
    page = anna.get("/beskeder/administrator").get_data(as_text=True)
    assert "❤️" in page and "Alma" not in page  # brugeren ser kun Administrator
    reaction = db.session.get(AdminMessageReaction, (reply, True))
    assert reaction.reaction == "hjerte" and reaction.admin_id == user("Alma").id

    # Ingen reaktion på egne beskeder
    admin.post(f"/admin/bruger/{anna_id}/samtale/besked/{from_admin}/reaktion", data={"reaction": "hjerte"})
    anna.post(f"/beskeder/administrator/besked/{reply}/reaktion", data={"reaction": "hjerte"})
    assert db.session.scalar(select(func.count()).select_from(AdminMessageReaction)) == 2
    export = anna.post("/konto/mine-data").get_data(as_text=True)
    assert "Tommel op" in export


# --- Permanent lukning ------------------------------------------------------------------------------------------


def test_admin_expels_without_appeal_and_ends_subscription(app, outbox, ctx):
    admin = make_user(app, outbox, "Alma", role="admin")
    bodil = make_user(app, outbox, "Bodil")
    bodil_id = user("Bodil").id
    assert effective_tier(bodil_id) != "free"

    admin.post(f"/admin/bruger/{bodil_id}", data={"action": "expel", "note": ""})
    assert user("Bodil").status == "active"  # begrundelse er påkrævet
    admin.post(f"/admin/bruger/{bodil_id}", data={"action": "expel", "note": "Sælger stoffer i beskeder"})
    assert user("Bodil").status == "expelled" and effective_tier(bodil_id) == "free"
    assert bodil.get("/udforsk").status_code == 302  # logget ud med det samme

    response = login(app.test_client(), email="bodil@example.com")
    page = response.get_data(as_text=True)
    assert response.status_code == 403 and "Kontoen er lukket" in page
    assert "Klag over afgørelsen" not in page and app.config["CONTACT_EMAIL"] in page

    cases = db.session.scalar(select(func.count(ModerationCase.id)))
    app.test_client().post("/klage", data={"email": "bodil@example.com", "text": "Det var ikke mig"})
    assert db.session.scalar(select(func.count(ModerationCase.id))) == cases  # ingen klagesag
    assert "lukket permanent" in admin.get(f"/admin/bruger/{bodil_id}").get_data(as_text=True)
    assert "Bodil" in admin.get("/admin/brugere?filter=lukket").get_data(as_text=True)

    # En administrator kan genåbne kontoen, men abonnementet kommer ikke igen
    admin.post(f"/admin/bruger/{bodil_id}", data={"action": "status", "status": "active", "note": "Fejl"})
    assert login(app.test_client(), email="bodil@example.com").status_code == 302
    assert effective_tier(bodil_id) == "free"


def test_moderator_expels_and_cannot_lift(app, outbox, ctx):
    anna, bodil = make_user(app, outbox, "Anna"), make_user(app, outbox, "Bodil")
    mod = make_user(app, outbox, "Mogens", role="moderator")
    anna.post(f"/anmeld/profil/{h('Bodil')}", data={"reason": "svindel"})
    case = latest_case()
    mod.post(f"/moderation/sag/{case.id}", data={"action": "decide", "decision-decision": "expelled",
                                                  "decision-note": "Sælger våben"})
    assert user("Bodil").status == "expelled"
    # En moderator kan ikke ophæve lukningen i en ny sag
    second = ModerationCase(source="moderator", category="andet", subject_user_id=user("Bodil").id)
    db.session.add(second)
    db.session.commit()
    mod.post(f"/moderation/sag/{second.id}", data={"action": "decide", "decision-decision": "lifted",
                                                    "decision-note": "Fortryder"})
    assert user("Bodil").status == "expelled"


def test_terms_describe_permanent_closure(app, client):
    terms = client.get("/vilkaar").get_data(as_text=True)
    assert "narkotika, våben" in terms and "kan ikke påklages her på siden" in terms and "uden refusion" in terms
