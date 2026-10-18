"""Tests for HTML-udgaven af systemmails: links skal kunne klikkes i alle mailprogrammer."""
import re

from app.core import mail

from .conftest import register


def test_verify_mail_has_clickable_link(app, outbox, ctx):
    register(app.test_client())
    message = outbox[-1]
    url = re.search(r"https?://\S+/bekraeft-email/\S+", message["body"]).group(0)
    assert message["html"] and f'href="{url}"' in message["html"]
    assert "Bekræft din e-mail" in message["html"] and "Hej Anna_K" in message["html"]


def test_reset_mail_has_clickable_link(app, outbox, ctx):
    client = app.test_client()
    register(client)
    client.post("/glemt-password", data={"email": "anna@example.com"})
    message = outbox[-1]
    url = re.search(r"https?://\S+", message["body"].split("Vælg et nyt password her:")[1]).group(0)
    assert f'href="{url}"' in message["html"] and "Vælg et nyt password" in message["html"]


def test_smtp_message_is_multipart_with_text_and_html(app, ctx, monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def starttls(self, context=None):
            pass

        def login(self, user, password):
            pass

        def send_message(self, message):
            sent.append(message)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    # monkeypatch gendanner konfigurationen bagefter, så andre tests ikke forsøger at sende rigtige mails.
    for key, value in {"MAIL_BACKEND": "smtp", "SMTP_HOST": "localhost", "SMTP_PORT": 587,
                       "SMTP_USERNAME": "", "MAIL_FROM": "a@b.dk"}.items():
        monkeypatch.setitem(app.config, key, value)
    with app.test_request_context():
        assert mail.send_mail("x@example.com", "Test", "mail/reset_password.txt", name="X",
                              reset_url="https://sukker.nu/nulstil/abc", minutes=60)
    message = sent[0]
    assert message.get_content_type() == "multipart/alternative"
    parts = {part.get_content_type(): part.get_content() for part in message.iter_parts()}
    assert "https://sukker.nu/nulstil/abc" in parts["text/plain"]
    assert 'href="https://sukker.nu/nulstil/abc"' in parts["text/html"]


def test_text_only_mail_still_works(app, ctx):
    with app.test_request_context():
        assert mail._html_version("mail/alert.txt", {}) is None
