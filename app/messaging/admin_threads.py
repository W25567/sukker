"""Samtaler mellem en bruger og "Administrator" (fx henstillinger).

- Brugeren ser kun afsenderen "Administrator", aldrig hvilken administrator der skrev.
- Hver besked fra en administrator registreres i administrationsloggen.
- Brugeren kan svare og kan slette samtalen for sig selv. Skriver en administrator igen, vises hele
  historikken igen.
- Samtalen er ikke en match-samtale: den virker uanset abonnement, match og begrænsning.
"""
from dataclasses import dataclass

from sqlalchemy import func, select

from ..core.timeutil import now_utc
from ..extensions import db
from .models import AdminMessage, AdminThread

MAX_LENGTH = 2000
SENDER_NAME = "Administrator"


class AdminMessageRefused(ValueError):
    pass


def thread_for(user_id):
    return db.session.get(AdminThread, user_id)


def messages(user_id):
    return db.session.scalars(
        select(AdminMessage).where(AdminMessage.user_id == user_id).order_by(AdminMessage.created_at, AdminMessage.id)
    ).all()


def _clean(body):
    body = (body or "").strip()
    if not body:
        raise AdminMessageRefused("Skriv en besked.")
    if len(body) > MAX_LENGTH:
        raise AdminMessageRefused(f"En besked må højst være {MAX_LENGTH} tegn.")
    return body


def send_from_admin(admin, user, body):
    """Administratoren skriver til brugeren. Registreres i administrationsloggen af kalderen (admin.services.audit)."""
    if admin.id == user.id:
        raise AdminMessageRefused("Du kan ikke skrive til dig selv.")
    body = _clean(body)
    now = now_utc()
    thread = thread_for(user.id)
    if thread is None:
        thread = AdminThread(user_id=user.id, created_at=now)
        db.session.add(thread)
        db.session.flush()
    message = AdminMessage(user_id=user.id, from_admin=True, admin_id=admin.id, body=body, created_at=now)
    db.session.add(message)
    thread.last_message_at = now
    thread.admin_last_read_at = now
    db.session.flush()
    return message


def visible_to_user(user):
    """Samtalen er synlig for brugeren, hvis den findes og ikke er slettet – eller hvis en administrator har
    skrevet efter, at brugeren slettede den."""
    thread = thread_for(user.id)
    if thread is None or thread.last_message_at is None:
        return None
    if thread.user_hidden_at is None:
        return thread
    newer = db.session.scalar(
        select(AdminMessage.id).where(AdminMessage.user_id == user.id, AdminMessage.from_admin.is_(True),
                                      AdminMessage.created_at > thread.user_hidden_at).limit(1)
    )
    return thread if newer else None


def reply_from_user(user, body):
    thread = visible_to_user(user)
    if thread is None:
        raise AdminMessageRefused("Samtalen findes ikke.")
    body = _clean(body)
    now = now_utc()
    message = AdminMessage(user_id=user.id, from_admin=False, body=body, created_at=now)
    db.session.add(message)
    thread.last_message_at = now
    thread.user_last_read_at = now
    return message


def hide_for_user(user):
    thread = thread_for(user.id)
    if thread is not None:
        thread.user_hidden_at = now_utc()


def mark_read_by_user(user):
    thread = thread_for(user.id)
    if thread is not None:
        thread.user_last_read_at = now_utc()


def mark_read_by_admin(user_id):
    thread = thread_for(user_id)
    if thread is not None:
        thread.admin_last_read_at = now_utc()


def unread_for_user(user):
    thread = visible_to_user(user)
    if thread is None:
        return 0
    query = select(func.count(AdminMessage.id)).where(AdminMessage.user_id == user.id, AdminMessage.from_admin.is_(True))
    if thread.user_last_read_at is not None:
        query = query.where(AdminMessage.created_at > thread.user_last_read_at)
    return db.session.scalar(query)


def unread_for_admin(user_id):
    thread = thread_for(user_id)
    if thread is None:
        return 0
    query = select(func.count(AdminMessage.id)).where(AdminMessage.user_id == user_id, AdminMessage.from_admin.is_(False))
    if thread.admin_last_read_at is not None:
        query = query.where(AdminMessage.created_at > thread.admin_last_read_at)
    return db.session.scalar(query)


def version(user_id):
    count, last_id = db.session.execute(
        select(func.count(AdminMessage.id), func.max(AdminMessage.id)).where(AdminMessage.user_id == user_id)
    ).one()
    return f"{count}-{last_id or 0}"


@dataclass
class ThreadSummary:
    user_id: int
    display_name: str
    last_message: AdminMessage | None
    unread: int
    last_message_at: object


def admin_overview():
    """Alle administrator-samtaler til administrationen, nyeste aktivitet først, ulæste svar øverst."""
    from ..auth.models import User

    rows = db.session.execute(
        select(AdminThread, User.display_name).join(User, User.id == AdminThread.user_id)
        .order_by(AdminThread.last_message_at.desc().nulls_last())
    ).all()
    summaries = []
    for thread, name in rows:
        last = db.session.scalar(select(AdminMessage).where(AdminMessage.user_id == thread.user_id)
                                 .order_by(AdminMessage.created_at.desc(), AdminMessage.id.desc()).limit(1))
        summaries.append(ThreadSummary(thread.user_id, name, last, unread_for_admin(thread.user_id), thread.last_message_at))
    return sorted(summaries, key=lambda s: (s.unread == 0,), reverse=False)


def total_unread_for_admins():
    threads = db.session.scalars(select(AdminThread.user_id)).all()
    return sum(1 for user_id in threads if unread_for_admin(user_id))
