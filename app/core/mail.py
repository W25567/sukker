"""Afsendelse af systemmails.

Backends:
- console: skriver mailen til loggen og til instance/outbox (lokal udvikling)
- memory:  gemmer mailen i app.extensions["mail_outbox"] (tests)
- smtp:    sender via SMTP med STARTTLS, eller med TLS fra start på port 465

Udviklingsserveren læser .env ved opstart. Genstart den efter ændringer.
"""
import os
import re
import smtplib
import ssl
from email.message import EmailMessage

from flask import current_app, render_template

from .timeutil import now_utc


def send_mail(to, subject, template, **context):
    """Sender en systemmail. Returnerer False, hvis afsendelsen fejlede. Fejlen logges, men vælter ikke
    forespørgslen, så fx en oprettelse ikke går tabt. Brugeren kan bede om mailen igen."""
    body = render_template(template, **context)
    backend = current_app.config["MAIL_BACKEND"]
    if backend == "memory":
        current_app.extensions.setdefault("mail_outbox", []).append({"to": to, "subject": subject, "body": body})
    elif backend == "console":
        _write_to_outbox(to, subject, body)
    elif backend == "smtp":
        try:
            _send_smtp(to, subject, body)
        except (smtplib.SMTPException, OSError):
            current_app.logger.exception("Mail til %s (%s) kunne ikke sendes", to, subject)
            return False
    else:
        raise RuntimeError(f"Ukendt mail-backend: {backend!r}")
    return True


def _write_to_outbox(to, subject, body):
    outbox = os.path.join(current_app.instance_path, "outbox")
    os.makedirs(outbox, exist_ok=True)
    ascii_subject = subject.lower().replace("æ", "ae").replace("ø", "oe").replace("å", "aa")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_subject).strip("-")[:40]
    path = os.path.join(outbox, f"{now_utc():%Y%m%d-%H%M%S-%f}-{slug}.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"Til: {to}\nEmne: {subject}\n\n{body}")
    current_app.logger.info("Mail til %s (%s) gemt i %s", to, subject, path)


def _send_smtp(to, subject, body):
    config = current_app.config
    message = EmailMessage()
    message["From"] = config["MAIL_FROM"]
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    context = ssl.create_default_context()
    if config["SMTP_PORT"] == 465:
        smtp = smtplib.SMTP_SSL(config["SMTP_HOST"], 465, timeout=20, context=context)
    else:
        smtp = smtplib.SMTP(config["SMTP_HOST"], config["SMTP_PORT"], timeout=20)
        smtp.starttls(context=context)
    with smtp:
        if config["SMTP_USERNAME"]:
            smtp.login(config["SMTP_USERNAME"], config["SMTP_PASSWORD"])
        smtp.send_message(message)
