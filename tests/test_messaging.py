"""Tests for beskeder, låsning, skjult historik og slettede brugere (M5)."""
import re

from sqlalchemy import func, select

from app.auth.models import User
from app.core.timeutil import now_utc
from app.extensions import db
from app.messaging.models import Conversation, Message
from app.profiles.models import Profile
from app.subscriptions.models import Subscription

from .conftest import h, link_from, login, register


def make_user(app, outbox, username, *, verify=True):
    client = app.test_client()
    email = f"{username.lower()}@example.com"
    register(client, display_name=username, email=email)
    if verify:
        client.get(link_from(outbox[-1], "/bekraeft-email/"))
    login(client, email=email)
    return client


def user(username):
    return db.session.scalar(select(User).where(User.display_name == username))


def make_free(username):
    for subscription in db.session.scalars(select(Subscription).where(Subscription.user_id == user(username).id)):
        subscription.starts_at = now_utc().replace(year=2020)
        subscription.expires_at = now_utc()
    db.session.commit()


def matched_pair(app, outbox):
    anna = make_user(app, outbox, "Anna")
    bodil = make_user(app, outbox, "Bodil")
    anna.post(f"/like/{h('Bodil')}")
    bodil.post(f"/like/{h('Anna')}")
    return anna, bodil


def open_conversation(client, username):
    response = client.get(f"/beskeder/med/{h(username)}")
    assert response.status_code == 302, response.status_code
    return response.headers["Location"]


def send(client, url, body):
    return client.post(url, data={"body": body})


def bodies(html):
    return re.findall(r"<p>([^<]+)</p>", html)


def flashed(client):
    with client.session_transaction() as session:
        return [message for _, message in session.get("_flashes", [])]


# --- Grundlæggende ------------------------------------------------------------------------------


def test_matches_can_write_and_read(app, outbox, ctx):
    anna, bodil = matched_pair(app, outbox)
    url = open_conversation(anna, "Bodil")
    send(anna, url, "Hej Bodil")
    send(bodil, open_conversation(bodil, "Anna"), "Hej Anna")

    html = anna.get(url).get_data(as_text=True)
    assert bodies(html)[-2:] == ["Hej Bodil", "Hej Anna"]


def test_cannot_write_without_a_match(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    make_user(app, outbox, "Bodil")
    assert anna.get(f"/beskeder/med/{h('Bodil')}").status_code == 404


def test_outsiders_cannot_open_a_conversation(app, outbox, ctx):
    anna, _ = matched_pair(app, outbox)
    url = open_conversation(anna, "Bodil")
    carla = make_user(app, outbox, "Carla")
    assert carla.get(url).status_code == 404
    assert send(carla, url, "Hej").status_code == 404


def test_unread_count_and_mark_read(app, outbox, ctx):
    anna, bodil = matched_pair(app, outbox)
    send(anna, open_conversation(anna, "Bodil"), "Hej")
    send(anna, open_conversation(anna, "Bodil"), "Er du der?")

    inbox = bodil.get("/beskeder/").get_data(as_text=True)
    assert '<span class="unread-count">2</span>' in inbox
    bodil.get(open_conversation(bodil, "Anna"))
    assert 'class="unread-count"' not in bodil.get("/beskeder/").get_data(as_text=True)


def test_message_length_is_limited(app, outbox, ctx):
    anna, _ = matched_pair(app, outbox)
    url = open_conversation(anna, "Bodil")
    send(anna, url, "x" * 2001)
    assert db.session.scalar(select(func.count(Message.id))) == 0


# --- Låsning ------------------------------------------------------------------------------------------


def test_free_free_conversation_is_locked_but_readable(app, outbox, ctx):
    anna, bodil = matched_pair(app, outbox)
    url = open_conversation(anna, "Bodil")
    send(anna, url, "Hej før låsning")
    make_free("Anna")
    make_free("Bodil")

    send(anna, url, "Hej efter låsning")
    assert "begge Free" in flashed(anna)[-1]
    html = anna.get(url).get_data(as_text=True)
    assert "Hej før låsning" in html and "Hej efter låsning" not in html
    assert "begge Free" in html


def test_one_paying_party_unlocks_for_both(app, outbox, ctx):
    anna, bodil = matched_pair(app, outbox)
    make_free("Anna")  # Bodil har stadig Premium
    send(anna, open_conversation(anna, "Bodil"), "Jeg har Free, men kan skrive")
    assert db.session.scalar(select(func.count(Message.id))) == 1


def test_paused_profile_cannot_write(app, outbox, ctx):
    anna, _ = matched_pair(app, outbox)
    url = open_conversation(anna, "Bodil")
    db.session.get(Profile, user("Anna").id).paused_at = now_utc()
    db.session.commit()
    send(anna, url, "Hej")
    assert "på pause" in flashed(anna)[-1]
    assert db.session.scalar(select(func.count(Message.id))) == 0


# --- Fjernet match og match igen ------------------------------------------------------------------------


def test_removed_match_hides_conversation_and_rematch_restores_it(app, outbox, ctx):
    anna, bodil = matched_pair(app, outbox)
    url = open_conversation(anna, "Bodil")
    send(anna, url, "Første besked")
    anna.post(f"/match/{h('Bodil')}/fjern")

    assert anna.get(url).status_code == 404
    assert bodil.get(url).status_code == 404
    assert "Første besked" not in bodil.get("/beskeder/").get_data(as_text=True)

    anna.post(f"/like/{h('Bodil')}")
    bodil.post(f"/like/{h('Anna')}")
    html = bodil.get(url).get_data(as_text=True)
    assert "Første besked" in html
    assert "Match fjernet" in html


# --- Slet for mig ----------------------------------------------------------------------------------------


def test_delete_message_only_for_me_and_it_never_returns(app, outbox, ctx):
    anna, bodil = matched_pair(app, outbox)
    url = open_conversation(anna, "Bodil")
    send(anna, url, "Hemmelig")
    message_id = db.session.scalar(select(Message.id))

    anna.post(f"{url}/besked/{message_id}/slet")
    assert "Hemmelig" not in anna.get(url).get_data(as_text=True)
    assert "Hemmelig" in bodil.get(url).get_data(as_text=True)

    anna.post(f"/match/{h('Bodil')}/fjern")
    anna.post(f"/like/{h('Bodil')}")
    bodil.post(f"/like/{h('Anna')}")
    assert "Hemmelig" not in anna.get(url).get_data(as_text=True)


def test_clear_conversation_for_me(app, outbox, ctx):
    anna, bodil = matched_pair(app, outbox)
    url = open_conversation(anna, "Bodil")
    send(anna, url, "Gammel besked")
    anna.post(f"{url}/slet")
    assert "Gammel besked" not in anna.get(url).get_data(as_text=True)
    assert "Gammel besked" in bodil.get(url).get_data(as_text=True)
    send(bodil, url, "Ny besked")
    assert "Ny besked" in anna.get(url).get_data(as_text=True)


def test_archive_and_new_message_unarchives(app, outbox, ctx):
    anna, bodil = matched_pair(app, outbox)
    url = open_conversation(anna, "Bodil")
    send(bodil, url, "Hej")
    anna.post(f"{url}/arkiv")
    assert "Bodil" not in anna.get("/beskeder/").get_data(as_text=True).split("<main")[1]
    assert "Bodil" in anna.get("/beskeder/?arkiv=1").get_data(as_text=True)
    send(bodil, url, "Hallo?")
    assert "Hallo?" in anna.get("/beskeder/").get_data(as_text=True)


# --- Slet konto -------------------------------------------------------------------------------------------


def test_deleted_account_shows_as_deleted_user(app, outbox, ctx):
    anna, bodil = matched_pair(app, outbox)
    url = open_conversation(anna, "Bodil")
    send(anna, url, "Hej fra Anna")
    send(bodil, url, "Hej tilbage")

    response = anna.post("/konto/slet", data={"password": "et-langt-password", "confirm": "y"})
    assert response.status_code == 302
    assert user("Anna") is None
    assert db.session.scalar(select(func.count(Message.id))) == 2

    html = bodil.get(url).get_data(as_text=True)
    thread = html.split('<ol class="thread" id="thread"')[1].split("</ol>")[0]
    assert "Slettet bruger" in thread and "Hej fra Anna" in thread
    assert "Anna" not in thread.replace("Hej fra Anna", "")  # intet navn eller link til den slettede profil
    assert "har slettet sin profil" in html
    assert send(bodil, url, "Er du der?").status_code == 302
    assert db.session.scalar(select(func.count(Message.id))) == 2
    assert "Slettet bruger" in bodil.get("/beskeder/").get_data(as_text=True)


def test_delete_account_requires_password(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    response = anna.post("/konto/slet", data={"password": "forkert", "confirm": "y"})
    assert response.status_code == 400
    assert user("Anna") is not None


def test_conversation_hidden_by_removed_match_stays_hidden_after_account_deletion(app, outbox, ctx):
    anna, bodil = matched_pair(app, outbox)
    url = open_conversation(anna, "Bodil")
    send(anna, url, "Skjult historik")
    anna.post(f"/match/{h('Bodil')}/fjern")
    anna.post("/konto/slet", data={"password": "et-langt-password", "confirm": "y"})
    assert bodil.get(url).status_code == 404
    assert db.session.scalar(select(Conversation.hidden_at)) is not None


# --- Automatisk opdatering --------------------------------------------------------------------------------


def test_open_conversation_receives_new_messages_without_reload(app, outbox, ctx):
    anna, bodil = matched_pair(app, outbox)
    url = open_conversation(anna, "Bodil")
    page = anna.get(url).get_data(as_text=True)
    version = re.search(r'data-version="([^"]+)"', page).group(1)
    poll_url = re.search(r'data-poll-url="([^"]+)"', page).group(1)
    assert 'src="/static/js/conversation.js' in page

    unchanged = anna.get(f"{poll_url}?version={version}")
    assert unchanged.status_code == 204 and unchanged.headers["Cache-Control"] == "no-store"

    send(bodil, open_conversation(bodil, "Anna"), "Er du der?")
    changed = anna.get(f"{poll_url}?version={version}")
    data = changed.get_json()
    assert changed.status_code == 200 and "Er du der?" in data["html"] and data["version"] != version
    assert anna.get(f"{poll_url}?version={data['version']}").status_code == 204
    assert "unread-count" not in anna.get("/udforsk").get_data(as_text=True)  # markeret som læst


def test_live_updates_respect_access(app, outbox, ctx):
    anna, bodil = matched_pair(app, outbox)
    url = open_conversation(anna, "Bodil")
    poll_url = url.rstrip("/") + "/nye"
    carla = make_user(app, outbox, "Carla")
    assert carla.get(poll_url).status_code == 404
    bodil.post(f"/bruger/{h('Anna')}/bloker")
    assert anna.get(poll_url).status_code == 404  # blokering slår igennem med det samme


# --- Fjernet match i indbakken -------------------------------------------------------------------------------


def test_removed_match_is_shown_without_content_for_both(app, outbox, ctx):
    anna, bodil = matched_pair(app, outbox)
    send(anna, open_conversation(anna, "Bodil"), "Hemmelig samtale")
    anna.post(f"/match/{h('Bodil')}/fjern")
    for client, other in ((anna, "Bodil"), (bodil, "Anna")):
        inbox = client.get("/beskeder/").get_data(as_text=True)
        assert other in inbox and "Match fjernet" in inbox and "kan ikke længere læses" in inbox
        assert "Hemmelig samtale" not in inbox
    conversation_id = db.session.scalar(select(Conversation.id))
    assert bodil.get(f"/beskeder/{conversation_id}").status_code == 404  # indholdet kan ikke læses

    bodil.post(f"/beskeder/{conversation_id}/fjern-markering")
    assert "Match fjernet" not in bodil.get("/beskeder/").get_data(as_text=True)
    assert "Match fjernet" in anna.get("/beskeder/").get_data(as_text=True)  # kun fjernet for Bodil


def test_blocked_conversation_disappears_completely(app, outbox, ctx):
    anna, bodil = matched_pair(app, outbox)
    send(anna, open_conversation(anna, "Bodil"), "Hej")
    bodil.post(f"/bruger/{h('Anna')}/bloker")
    for client in (anna, bodil):
        inbox = client.get("/beskeder/").get_data(as_text=True)
        assert '<ul class="inbox">' not in inbox and "Du har ingen samtaler endnu" in inbox  # skjult uden videre
    conversation_id = db.session.scalar(select(Conversation.id))
    assert anna.post(f"/beskeder/{conversation_id}/fjern-markering").status_code == 404
