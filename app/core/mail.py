"""Afsendelse af systemmails.

Backends:
- console: skriver mailen til loggen og til instance/outbox (lokal udvikling)
- memory:  gemmer mailen i app.extensions["mail_outbox"] (tests)
- smtp:    sender via SMTP med STARTTLS
"""
import os
import re
import smtplib
from email.message import EmailMessage

from flask import current_app, render_template

from .timeutil import now_utc


def send_mail(to, subject, template, **context):
    body = render_template(template, **context)
    backend = current_app.config["MAIL_BACKEND"]
    if backend == "memory":
        current_app.extensions.setdefault("mail_outbox", []).append({"to": to, "subject": subject, "body": body})
    elif backend == "console":
        _write_to_outbox(to, subject, body)
    elif backend == "smtp":
        _send_smtp(to, subject, body)
    else:
        raise RuntimeError(f"Ukendt mail-backend: {backend!r}")


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
    with smtplib.SMTP(config["SMTP_HOST"], config["SMTP_PORT"], timeout=20) as smtp:
        smtp.starttls()
        if config["SMTP_USERNAME"]:
            smtp.login(config["SMTP_USERNAME"], config["SMTP_PASSWORD"])
        smtp.send_message(message)
