"""Login-sessioner gemt på serveren (specifikation 2.3).

Cookien indeholder kun et tilfældigt token. Serveren slår sessionen op ved hver forespørgsel, så den
kan tilbagekaldes, fx ved "log ud på alle enheder" eller passwordskift.
"""
import secrets
from datetime import timedelta
from functools import wraps
from urllib.parse import urlsplit

from flask import flash, g, redirect, request, session, url_for
from sqlalchemy import select, update

from ..core.timeutil import now_utc
from ..extensions import db
from .models import User, UserSession
from .tokens import hash_token

SESSION_KEY = "sid"
LAST_SEEN_INTERVAL = timedelta(minutes=5)


def start_session(user):
    raw = secrets.token_urlsafe(32)
    db.session.add(
        UserSession(
            user_id=user.id,
            token_hash=hash_token(raw),
            ip_address=request.remote_addr,
            user_agent=(request.user_agent.string or "")[:255],
        )
    )
    session.clear()
    session[SESSION_KEY] = raw
    session.permanent = True


def end_current_session():
    if g.get("user_session") is not None:
        g.user_session.revoked_at = now_utc()
    session.clear()


def revoke_sessions(user_id, keep_session_id=None):
    query = update(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
    if keep_session_id is not None:
        query = query.where(UserSession.id != keep_session_id)
    db.session.execute(query.values(revoked_at=now_utc()))


def active_sessions(user_id):
    return db.session.scalars(
        select(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .order_by(UserSession.last_seen_at.desc())
    ).all()


def _load_current_user():
    g.user = None
    g.user_session = None
    raw = session.get(SESSION_KEY)
    if not raw:
        return
    user_session = db.session.scalar(
        select(UserSession).where(UserSession.token_hash == hash_token(raw), UserSession.revoked_at.is_(None))
    )
    if user_session is None:
        session.clear()
        return
    now = now_utc()
    if now - user_session.last_seen_at > LAST_SEEN_INTERVAL:
        user_session.last_seen_at = now
        db.session.commit()
    g.user_session = user_session
    g.user = db.session.get(User, user_session.user_id)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Log ind for at fortsætte.", "info")
            return redirect(url_for("auth.login", next=request.full_path))
        return view(*args, **kwargs)

    return wrapped


def safe_next_url(target):
    """Tillader kun relative stier på samme site, så login ikke kan bruges til omdirigering ud af siden."""
    if not target:
        return None
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//"):
        return None
    return target


def init_app(app):
    app.before_request(_load_current_user)

    @app.context_processor
    def inject_user():
        return {"current_user": g.get("user")}
