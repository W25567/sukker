"""Tests for brugerens valg af notifikationer på siden og pr. e-mail, e-mailjobbet og påmindelser om Premium."""
import re
from datetime import timedelta

from sqlalchemy import select

from app.core.timeutil import now_utc
from app.extensions import db
from app.notify import mail
from app.notify.prefs import wants
from app.subscriptions.models import Subscription

from .conftest import h
from .test_messaging import matched_pair, open_conversation, send
from .test_zodiac_and_admin import make_user, user

LATER = timedelta(minutes=20)  # brugerne har ikke været på siden i et stykke tid


def news_mails(outbox, to):
    return [m for m in outbox if m["to"] == to and m["subject"] == "Der er nyt til dig på Sukker.nu"]


def save(client, **checked):
    data = {name: "1" for name, on in checked.items() if on}
    return client.post("/konto/notifikationer", data=data)


def test_settings_card_and_defaults(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    page = anna.get("/konto/").get_data(as_text=True)
    assert 'id="notifikationer"' in page and 'name="mail_message"' in page and 'name="app_match"' in page
    assert 'name="app_premium"' not in page and 'name="mail_premium"' in page  # Premium findes kun pr. e-mail
    assert 'name="mail_staff"' not in page  # kun for administratorer
    assert wants(user("Anna"), "message", "app") and wants(user("Anna"), "message", "mail")
    assert user("Anna").notification_prefs is None


def test_saving_choices_stores_only_differences(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    everything = {f"{c}_{k}": True for k in ("message", "match", "request", "grant", "admin") for c in ("app", "mail")}
    save(anna, **{**everything, "mail_premium": True, "app_match": False, "mail_message": False})
    db.session.refresh(user("Anna"))
    assert user("Anna").notification_prefs == {"match": {"app": False}, "message": {"mail": False}}
    assert not wants(user("Anna"), "match", "app") and wants(user("Anna"), "match", "mail")
    page = anna.get("/konto/").get_data(as_text=True)
    assert re.search(r'name="app_match" value="1"\s+aria-label', page)  # ikke afkrydset
    assert re.search(r'name="mail_match" value="1" checked', page)


def test_in_app_toast_can_be_turned_off_but_badge_stays(app, outbox, ctx):
    anna, bodil = matched_pair(app, outbox)
    send(bodil, open_conversation(bodil, "Anna"), "Hej Anna")
    data = anna.get("/notifikationer").get_json()
    assert data["messages"] == 1 and data["latest_message"]["name"] == "Bodil"
    save(anna, mail_message=True)  # alt på siden slået fra
    data = anna.get("/notifikationer").get_json()
    assert data["messages"] == 1 and data["latest_message"] is None and data["latest_match"] is None


def test_mail_when_away_once_until_back(app, outbox, ctx):
    anna, bodil = matched_pair(app, outbox)
    send(bodil, open_conversation(bodil, "Anna"), "Hej Anna, en hemmelig besked")
    assert mail.run(now_utc()) == {"nyt": 0, "premium": 0}  # Anna er lige logget ind og er på siden

    counts = mail.run(now_utc() + LATER)
    (sent,) = news_mails(outbox, "anna@example.com")
    assert counts["nyt"] >= 1
    assert "Du har nye beskeder." in sent["body"] and "Du har et nyt match." in sent["body"]
    assert "Bodil" not in sent["body"] and "hemmelig" not in sent["body"]  # diskret
    assert "/konto/#notifikationer" in sent["body"] and sent["html"]

    send(bodil, open_conversation(bodil, "Anna"), "Er du der?")
    mail.run(now_utc() + LATER * 4)
    assert len(news_mails(outbox, "anna@example.com")) == 1  # ikke igen, før Anna har været inde


def test_mail_again_after_user_has_been_back(app, outbox, ctx):
    from app.auth.models import UserSession

    anna, bodil = matched_pair(app, outbox)
    send(bodil, open_conversation(bodil, "Anna"), "Hej")
    first = now_utc() + LATER
    mail.run(first)
    # Anna er inde igen to timer senere og går så igen
    for session in db.session.scalars(select(UserSession).where(UserSession.user_id == user("Anna").id)):
        session.last_seen_at = first + timedelta(hours=2)
    db.session.commit()
    message_time = first + timedelta(hours=3)
    from app.messaging.models import Message
    send(bodil, open_conversation(bodil, "Anna"), "Hej igen")
    newest = db.session.scalars(select(Message).order_by(Message.id.desc())).first()
    newest.created_at = message_time
    db.session.commit()
    mail.run(message_time + LATER)
    assert len(news_mails(outbox, "anna@example.com")) == 2


def test_no_mail_when_turned_off_or_unverified(app, outbox, ctx):
    anna, bodil = matched_pair(app, outbox)
    save(anna, app_message=True)  # alle e-mails slået fra
    make_user(app, outbox, "Cecilie", verify=False)
    send(bodil, open_conversation(bodil, "Anna"), "Hej")
    mail.run(now_utc() + LATER)
    assert news_mails(outbox, "anna@example.com") == []
    assert news_mails(outbox, "cecilie@example.com") == []


def test_private_access_request_mail(app, outbox, ctx):
    from app.media.models import PrivateAccess

    make_user(app, outbox, "Anna")
    make_user(app, outbox, "Bodil")
    db.session.add(PrivateAccess(owner_id=user("Anna").id, viewer_id=user("Bodil").id))
    db.session.commit()
    mail.run(now_utc() + LATER)
    (sent,) = news_mails(outbox, "anna@example.com")
    assert "private billeder" in sent["body"]


def test_admin_gets_mail_about_replies(app, outbox, ctx):
    admin = make_user(app, outbox, "Hanne", role="admin")
    anna = make_user(app, outbox, "Anna")
    admin.post(f"/admin/bruger/{user('Anna').id}/samtale", data={"body": "Hej Anna"})
    anna.post("/beskeder/administrator", data={"body": "Hej igen"})
    assert 'name="mail_staff"' in admin.get("/konto/").get_data(as_text=True)
    mail.run(now_utc() + LATER)
    (to_admin,) = news_mails(outbox, "hanne@example.com")
    assert "svaret Administrator" in to_admin["body"]


def set_expiry(name, delta):
    for subscription in db.session.scalars(select(Subscription).where(Subscription.user_id == user(name).id)):
        subscription.expires_at = now_utc() + delta
    db.session.commit()


def premium_mails(outbox, to):
    return [m for m in outbox if m["to"] == to and "Premium udløber" in m["subject"]]


def test_premium_expiry_reminders(app, outbox, ctx):
    make_user(app, outbox, "Anna")
    assert db.session.scalar(select(Subscription.source).where(Subscription.user_id == user("Anna").id)) == "signup_bonus"
    set_expiry("Anna", timedelta(days=30))
    mail.run()
    assert premium_mails(outbox, "anna@example.com") == []

    set_expiry("Anna", timedelta(days=10))
    mail.run()
    mail.run()
    (first,) = premium_mails(outbox, "anna@example.com")
    assert first["subject"] == "Din Premium udløber om 10 dage" and "fortsætter din konto som Free" in first["body"]

    set_expiry("Anna", timedelta(days=2, hours=1))
    mail.run()
    mail.run()
    assert [m["subject"] for m in premium_mails(outbox, "anna@example.com")] == [
        "Din Premium udløber om 10 dage", "Din Premium udløber om 2 dage"]


def test_no_premium_reminder_when_continued_lifetime_or_off(app, outbox, ctx):
    from app.subscriptions.services import LIFETIME_END

    anna = make_user(app, outbox, "Anna")
    make_user(app, outbox, "Bodil")
    make_user(app, outbox, "Cecilie")
    set_expiry("Anna", timedelta(days=5))
    set_expiry("Bodil", timedelta(days=5))
    set_expiry("Cecilie", timedelta(days=5))
    # Bodil har en ny periode i forlængelse af den gamle
    end = db.session.scalar(select(Subscription.expires_at).where(Subscription.user_id == user("Bodil").id))
    db.session.add(Subscription(user_id=user("Bodil").id, tier="premium", source="admin", starts_at=end,
                                expires_at=end + timedelta(days=90)))
    # Cecilie har Premium for livstid
    db.session.add(Subscription(user_id=user("Cecilie").id, tier="premium", source="admin", starts_at=now_utc(),
                                expires_at=LIFETIME_END))
    db.session.commit()
    save(anna, mail_message=True)  # Premium-påmindelsen slået fra
    mail.run()
    assert premium_mails(outbox, "anna@example.com") == []
    assert premium_mails(outbox, "bodil@example.com") == []
    assert premium_mails(outbox, "cecilie@example.com") == []


def test_cli_command(app, outbox, ctx):
    result = app.test_cli_runner().invoke(args=["sukker", "notify"])
    assert result.exit_code == 0 and "Notifikationer:" in result.output
