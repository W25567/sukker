"""Tests for spam- og misbrugsbeskyttelse (specifikation 8.4 og 13.1): ingen ny Premium ved ny konto med samme
e-mail, automatiske signaler og grænser mod scraping."""
import io

from PIL import Image
from sqlalchemy import select

from app.account.services import normalize_email
from app.core.settings import set_setting
from app.extensions import db, limiter
from app.moderation.models import ModerationCase
from app.subscriptions.models import Subscription

from .conftest import h, link_from, login, register
from .test_messaging import matched_pair, open_conversation, send
from .test_zodiac_and_admin import make_user, user


def signals(category):
    return db.session.scalars(select(ModerationCase).where(ModerationCase.source == "signal",
                                                          ModerationCase.category == category)).all()


def bonus_sources(name):
    return sorted(db.session.scalars(select(Subscription.source).where(Subscription.user_id == user(name).id)))


def new_account(app, outbox, name, email):
    client = app.test_client()
    register(client, display_name=name, email=email)
    client.get(link_from(outbox[-1], "/bekraeft-email/"))
    login(client, email=email)
    return client


def test_normalize_email():
    assert normalize_email("Anna.Hansen+dating@Gmail.com") == "annahansen@gmail.com"
    assert normalize_email("anna.hansen@googlemail.com") == "annahansen@gmail.com"
    assert normalize_email("anna+x@example.com") == "anna@example.com"
    assert normalize_email("a.nna@example.com") == "a.nna@example.com"  # punktummer betyder noget uden for Gmail


def test_no_new_signup_premium_after_delete_and_register_again(app, outbox, ctx):
    anna = new_account(app, outbox, "Anna", "anna@example.com")
    assert bonus_sources("Anna") == ["signup_bonus"]
    anna.post("/konto/slet", data={"password": "et-langt-password", "confirm": "y"})

    new_account(app, outbox, "Anna2", "anna+igen@example.com")
    assert bonus_sources("Anna2") == []  # ingen ny Premium-periode
    (case,) = signals("signal_konto")
    assert case.subject_user_id == user("Anna2").id and case.status == "open"

    new_account(app, outbox, "Bodil", "bodil@example.com")  # en helt ny person får stadig Premium
    assert bonus_sources("Bodil") == ["signup_bonus"]


def test_no_new_early_bird_after_delete(app, outbox, ctx):
    set_setting("early_bird_enabled", True)
    db.session.commit()
    buffer = io.BytesIO()
    Image.new("RGB", (300, 300), "red").save(buffer, "JPEG")

    def upload(client):
        client.post("/profil/billeder", data={"image": (io.BytesIO(buffer.getvalue()), "a.jpg")},
                    content_type="multipart/form-data")

    anna = new_account(app, outbox, "Anna", "anna@example.com")
    upload(anna)
    assert "early_bird" in bonus_sources("Anna")
    anna.post("/konto/slet", data={"password": "et-langt-password", "confirm": "y"})
    again = new_account(app, outbox, "Anna2", "anna@example.com")
    upload(again)
    assert "early_bird" not in bonus_sources("Anna2")


def test_same_message_to_many_gives_spam_signal(app, outbox, ctx):
    spammer = make_user(app, outbox, "Spammer")
    text = "Hej! Tjek min fantastiske side for gode tilbud og meget mere"
    for i in range(5):
        other = make_user(app, outbox, f"Modtager{i}")
        spammer.post(f"/like/{h(f'Modtager{i}')}")
        other.post(f"/like/{h('Spammer')}")
        send(spammer, open_conversation(spammer, f"Modtager{i}"), text if i % 2 else text.upper())
    (case,) = signals("signal_spam")
    assert case.subject_user_id == user("Spammer").id and case.evidence["recipients"] == 5
    assert len(case.evidence["sample"]) <= 300

    send(spammer, open_conversation(spammer, "Modtager0"), text)
    assert len(signals("signal_spam")) == 1  # højst ét signal af samme slags pr. døgn


def test_short_greetings_do_not_give_signal(app, outbox, ctx):
    sender = make_user(app, outbox, "Hilser")
    for i in range(5):
        other = make_user(app, outbox, f"Ven{i}")
        sender.post(f"/like/{h(f'Ven{i}')}")
        other.post(f"/like/{h('Hilser')}")
        send(sender, open_conversation(sender, f"Ven{i}"), "Hej, hvordan går det?")
    assert signals("signal_spam") == []


def test_blocked_by_many_gives_signal(app, outbox, ctx):
    make_user(app, outbox, "Plage")
    for i in range(3):
        make_user(app, outbox, f"Blokker{i}").post(f"/bruger/{h('Plage')}/bloker")
    (case,) = signals("signal_blokering")
    assert case.subject_user_id == user("Plage").id


def test_scraping_limit_gives_429_and_signal(app, outbox, ctx, monkeypatch):
    """Grænserne er slået fra i testopsætningen, og Flask-Limiter registrerer så slet ikke sine kontroller.
    Testen laver derfor en app med grænserne slået til (samme database)."""
    from app import create_app
    from app import config as config_module

    class LimitedConfig(config_module.TestingConfig):
        RATELIMIT_ENABLED = True

    monkeypatch.setitem(config_module.CONFIGS, "testing_limited", LimitedConfig)
    limited = create_app("testing_limited")
    limited.config["MEDIA_ROOT"] = app.config["MEDIA_ROOT"]
    try:
        with limited.app_context():
            viewer = make_user(limited, limited.extensions.setdefault("mail_outbox", []), "Samler")
            make_user(limited, limited.extensions["mail_outbox"], "Profil")
            handle = h("Profil")
            limiter.reset()
            statuses = [viewer.get(f"/bruger/{handle}").status_code for _ in range(121)]
            assert statuses[:120] == [200] * 120 and statuses[120] == 429
            (case,) = signals("signal_scraping")
            assert case.subject_user_id == user("Samler").id
            limiter.reset()
    finally:
        limiter.enabled = False  # resten af testene kører uden grænser
