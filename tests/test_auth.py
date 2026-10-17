from datetime import timedelta

from sqlalchemy import func, select

from app.auth.models import EmailToken, User, UserSession
from app.core.timeutil import add_months, now_utc, today_local
from app.extensions import db
from app.subscriptions.models import Subscription
from app.subscriptions.services import effective_tier, grant_period

from .conftest import link_from, login, register, registration_data


def disable_early_bird():
    """Tester den almindelige Premium-periode ved oprettelse, som gælder, når kampagnen er lukket."""
    from app.core.settings import set_setting

    set_setting("early_bird_enabled", False)
    db.session.commit()


def _user(email="anna@example.com"):
    return db.session.scalar(select(User).where(User.email == email))


# --- Oprettelse -----------------------------------------------------------------


def test_register_creates_unverified_user_and_sends_verification_mail(client, outbox, ctx):
    response = register(client)
    assert response.status_code == 200
    assert "Tjek din e-mail" in response.get_data(as_text=True)

    user = _user()
    assert user.display_name == "Anna_K"
    assert user.email_verified_at is None
    assert user.password_hash != "et-langt-password"
    assert len(outbox) == 1 and outbox[0]["to"] == "anna@example.com"
    assert "/bekraeft-email/" in outbox[0]["body"]


def test_register_normalises_email_to_lowercase(client, ctx):
    register(client, email="Anna@example.com")
    assert _user().email == "anna@example.com"


def test_register_rejects_under_18(client, ctx):
    today = today_local()
    seventeen = today.replace(year=today.year - 17).isoformat()
    response = register(client, birthdate=seventeen)
    assert "mindst 18 år" in response.get_data(as_text=True)
    assert _user() is None


def test_register_requires_accepting_terms(client, ctx):
    data = registration_data()
    del data["accept_terms"]
    response = client.post("/opret", data=data)
    assert "acceptere vilkårene" in response.get_data(as_text=True)
    assert _user() is None


def test_display_name_does_not_have_to_be_unique(client, ctx):
    register(client, display_name="Anna")
    register(client, display_name="Anna", email="other@example.com")
    first, second = _user(), _user("other@example.com")
    assert first.display_name == second.display_name == "Anna"
    assert first.handle != second.handle and len(first.handle) == 10


def test_display_name_is_normalised_and_validated(client, ctx):
    register(client, display_name="  Anna   Sofie ")
    assert _user().display_name == "Anna Sofie"
    response = register(client, display_name="-", email="other@example.com")
    assert "2-30 tegn" in response.get_data(as_text=True) and _user("other@example.com") is None
    register(client, display_name="Søren Ågård", email="soren@example.com")
    assert _user("soren@example.com").display_name == "Søren Ågård"


def test_register_with_existing_email_reveals_nothing(client, outbox, ctx):
    register(client)
    outbox.clear()

    response = register(client, display_name="SomeoneElse")
    assert response.status_code == 200
    assert "Tjek din e-mail" in response.get_data(as_text=True)
    assert db.session.scalar(select(func.count(User.id))) == 1
    assert len(outbox) == 1
    assert "Nogen forsøgte" in outbox[0]["subject"]


# --- E-mailverificering og Premium ved oprettelse ----------------------------------


def test_verifying_email_grants_three_months_premium(client, outbox, ctx):
    disable_early_bird()
    register(client)
    assert effective_tier(_user().id) == "free"

    response = client.get(link_from(outbox[0], "/bekraeft-email/"))
    assert response.status_code == 302

    user = _user()
    assert user.email_verified_at is not None
    assert effective_tier(user.id) == "premium"
    bonus = db.session.scalar(select(Subscription).where(Subscription.user_id == user.id))
    assert bonus.source == "signup_bonus"
    assert bonus.expires_at == add_months(bonus.starts_at, 3)


def test_verification_link_leads_to_login_with_email_filled_in(client, outbox, ctx):
    register(client)
    response = client.get(link_from(outbox[0], "/bekraeft-email/"))
    assert response.headers["Location"] == "/log-ind"

    page = client.get("/log-ind").get_data(as_text=True)
    assert "Din e-mail er bekræftet" in page
    assert 'value="anna@example.com"' in page
    assert login(client).status_code == 302  # kan logge ind med det samme
    assert 'value="anna@example.com"' not in client.get("/log-ind").get_data(as_text=True)


def test_verification_link_when_logged_in_goes_to_explore(client, outbox, ctx):
    register(client)
    login(client)
    response = client.get(link_from(outbox[0], "/bekraeft-email/"))
    assert response.headers["Location"] == "/udforsk"


def test_verification_link_works_only_once(client, outbox, ctx):
    register(client)
    link = link_from(outbox[0], "/bekraeft-email/")
    assert client.get(link).status_code == 302
    assert client.get(link).status_code == 400


def test_expired_verification_link_is_rejected(client, outbox, ctx):
    register(client)
    db.session.execute(EmailToken.__table__.update().values(expires_at=now_utc() - timedelta(minutes=1)))
    db.session.commit()
    assert client.get(link_from(outbox[0], "/bekraeft-email/")).status_code == 400
    assert _user().email_verified_at is None


def test_new_verification_mail_invalidates_the_old_link(client, outbox, ctx):
    register(client)
    login(client)
    old_link = link_from(outbox[0], "/bekraeft-email/")
    client.post("/bekraeft-email/send-igen")
    new_link = link_from(outbox[-1], "/bekraeft-email/")

    assert client.get(old_link).status_code == 400
    assert client.get(new_link).status_code == 302


def test_signup_bonus_is_only_granted_once(client, outbox, ctx):
    disable_early_bird()
    register(client)
    client.get(link_from(outbox[0], "/bekraeft-email/"))
    user = _user()
    user.email_verified_at = None  # fx hvis verificering nogensinde nulstilles
    db.session.commit()

    login(client)
    client.post("/bekraeft-email/send-igen")
    client.get(link_from(outbox[-1], "/bekraeft-email/"))
    assert db.session.scalar(select(func.count(Subscription.id))) == 1


def test_admin_grant_extends_from_current_expiry(client, outbox, ctx):
    disable_early_bird()
    register(client)
    client.get(link_from(outbox[0], "/bekraeft-email/"))
    user = _user()
    bonus = db.session.scalar(select(Subscription).where(Subscription.user_id == user.id))

    extra = grant_period(user, 2, source="admin", note="Testbruger")
    db.session.commit()
    assert extra.starts_at == bonus.expires_at
    assert extra.expires_at == add_months(bonus.expires_at, 2)


# --- Login og sessioner ------------------------------------------------------------


def test_login_with_wrong_password_or_unknown_email_gives_same_message(client, ctx):
    register(client)
    wrong_password = login(client, password="forkert-password").get_data(as_text=True)
    unknown_email = login(client, email="ukendt@example.com").get_data(as_text=True)
    assert "Forkert e-mail eller password." in wrong_password
    assert "Forkert e-mail eller password." in unknown_email


def test_unverified_user_can_log_in(client, ctx):
    register(client)
    response = login(client)
    assert response.status_code == 302
    assert client.get("/").headers["Location"] == "/udforsk"
    assert client.get("/udforsk").status_code == 200


def test_login_redirect_only_to_local_paths(client, ctx):
    register(client)
    response = client.post("/log-ind?next=https://evil.example/", data={"email": "anna@example.com", "password": "et-langt-password"})
    assert response.headers["Location"] == "/"


def test_logout_revokes_the_session(client, ctx):
    register(client)
    login(client)
    client.post("/log-ud")
    assert db.session.scalar(select(UserSession.revoked_at)) is not None
    assert client.get("/konto/").status_code == 302


def test_logout_everywhere_revokes_all_sessions(app, client, ctx):
    register(client)
    other_device = app.test_client()
    login(client)
    login(other_device)

    client.post("/konto/log-ud-overalt")
    assert other_device.get("/konto/").status_code == 302
    assert client.get("/konto/").status_code == 302


def test_logout_other_devices_keeps_current(app, client, ctx):
    register(client)
    other_device = app.test_client()
    login(client)
    login(other_device)

    client.post("/konto/log-ud-andre")
    assert client.get("/konto/").status_code == 200
    assert other_device.get("/konto/").status_code == 302


def test_change_password_logs_out_other_devices(app, client, ctx):
    register(client)
    other_device = app.test_client()
    login(client)
    login(other_device)

    response = client.post(
        "/konto/password",
        data={"current_password": "et-langt-password", "password": "nyt-langt-password", "password_confirm": "nyt-langt-password"},
    )
    assert response.status_code == 302
    assert client.get("/konto/").status_code == 200
    assert other_device.get("/konto/").status_code == 302
    assert login(app.test_client(), password="nyt-langt-password").status_code == 302


def test_change_password_requires_current_password(app, client, ctx):
    register(client)
    login(client)
    response = client.post(
        "/konto/password",
        data={"current_password": "forkert-password", "password": "nyt-langt-password", "password_confirm": "nyt-langt-password"},
    )
    assert response.status_code == 400
    # Passwordet er uændret: det nye virker ikke, det gamle gør.
    assert login(app.test_client(), password="nyt-langt-password").status_code == 200
    assert login(app.test_client()).status_code == 302


# --- Glemt password ------------------------------------------------------------------


def test_forgot_password_gives_same_answer_for_unknown_email(client, outbox, ctx):
    register(client)
    outbox.clear()
    known = client.post("/glemt-password", data={"email": "anna@example.com"}).get_data(as_text=True)
    unknown = client.post("/glemt-password", data={"email": "ukendt@example.com"}).get_data(as_text=True)
    assert known == unknown
    assert len(outbox) == 1 and outbox[0]["to"] == "anna@example.com"


def test_reset_password_logs_out_everywhere_and_link_is_single_use(app, client, outbox, ctx):
    register(client)
    other_device = app.test_client()
    login(other_device)
    client.post("/glemt-password", data={"email": "anna@example.com"})
    link = link_from(outbox[-1], "/nulstil-password/")

    response = client.post(link, data={"password": "helt-nyt-password", "password_confirm": "helt-nyt-password"})
    assert response.status_code == 302
    assert other_device.get("/konto/").status_code == 302
    assert login(app.test_client(), password="helt-nyt-password").status_code == 302
    assert client.get(link).status_code == 400


def test_new_reset_link_invalidates_the_old_one(client, outbox, ctx):
    register(client)
    client.post("/glemt-password", data={"email": "anna@example.com"})
    old_link = link_from(outbox[-1], "/nulstil-password/")
    client.post("/glemt-password", data={"email": "anna@example.com"})
    new_link = link_from(outbox[-1], "/nulstil-password/")

    assert client.get(old_link).status_code == 400
    assert client.get(new_link).status_code == 200


def test_expired_reset_link_is_rejected(client, outbox, ctx):
    register(client)
    client.post("/glemt-password", data={"email": "anna@example.com"})
    db.session.execute(EmailToken.__table__.update().values(expires_at=now_utc() - timedelta(minutes=1)))
    db.session.commit()
    assert client.get(link_from(outbox[-1], "/nulstil-password/")).status_code == 400


def test_resend_verification_without_login(app, client, outbox, ctx):
    register(client)
    old_link = link_from(outbox[-1], "/bekraeft-email/")
    outbox.clear()

    answer = client.post("/bekraeft-email/ny-mail", data={"email": "anna@example.com"}).get_data(as_text=True)
    assert len(outbox) == 1
    assert client.get(old_link).status_code == 400  # det nye link erstatter det gamle
    assert client.get(link_from(outbox[-1], "/bekraeft-email/")).status_code == 302

    # Ukendte og allerede bekræftede e-mails får samme svar, men ingen mail.
    outbox.clear()
    unknown = app.test_client().post("/bekraeft-email/ny-mail", data={"email": "ukendt@example.com"}).get_data(as_text=True)
    verified = app.test_client().post("/bekraeft-email/ny-mail", data={"email": "anna@example.com"}).get_data(as_text=True)
    assert answer == unknown == verified
    assert outbox == []


def test_check_email_page_offers_resend(client, ctx):
    html = register(client).get_data(as_text=True)
    assert 'action="/bekraeft-email/ny-mail"' in html
    assert "Send mailen igen" in html


# --- Mailfejl ----------------------------------------------------------------------------


def test_smtp_failure_does_not_lose_the_registration(app, client, ctx, monkeypatch):
    import smtplib

    from app.core import mail

    def broken(*args, **kwargs):
        raise smtplib.SMTPConnectError(421, "nede")

    monkeypatch.setattr(mail, "_send_smtp", broken)
    app.config["MAIL_BACKEND"] = "smtp"
    try:
        response = register(client)
        assert response.status_code == 200
        assert _user() is not None
        login(client)
        client.post("/bekraeft-email/send-igen")
        with client.session_transaction() as session:
            assert "kunne ikke sendes" in session["_flashes"][-1][1]
    finally:
        app.config["MAIL_BACKEND"] = "memory"


# --- CSRF ------------------------------------------------------------------------------


def test_csrf_is_enforced_when_enabled(app):
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        response = app.test_client().post("/log-ind", data={"email": "a@example.com", "password": "x"})
        assert response.status_code == 400
    finally:
        app.config["WTF_CSRF_ENABLED"] = False
