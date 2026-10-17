"""Aggregerede tal til administrationens dashboard (specifikation 11.1).

Kun optællinger: ingen beskedindhold og ingen placering. Testkonti tæller ikke med.
"""
from datetime import timedelta

from sqlalchemy import distinct, func, select, text

from ..auth.models import User, UserSession
from ..core.features import has_feature
from ..core.timeutil import now_utc
from ..extensions import db
from ..location.models import Kommune, Postcode
from ..matches.models import Like, Match, MatchEvent
from ..messaging.models import Conversation, Message
from ..moderation.models import ModerationCase
from ..profiles.models import Profile
from ..subscriptions.models import Subscription
from ..subscriptions.services import early_bird_count

REAL = User.is_test.is_(False)


def _count(query):
    return db.session.scalar(select(func.count()).select_from(query.subquery()))


def collect():
    now = now_utc()
    week, month = now - timedelta(days=7), now - timedelta(days=30)
    real_ids = select(User.id).where(REAL)

    users = {
        "total": _count(select(User.id).where(REAL)),
        "verified": _count(select(User.id).where(REAL, User.email_verified_at.isnot(None))),
        "new_7": _count(select(User.id).where(REAL, User.created_at > week)),
        "new_30": _count(select(User.id).where(REAL, User.created_at > month)),
        "active_7": _count(select(UserSession.user_id).distinct().where(UserSession.last_seen_at > week,
                                                                          UserSession.user_id.in_(real_ids))),
        "paused": _count(select(Profile.user_id).where(Profile.paused_at.isnot(None), Profile.user_id.in_(real_ids))),
        "restricted": _count(select(User.id).where(REAL, User.status == "restricted")),
        "suspended": _count(select(User.id).where(REAL, User.status == "suspended")),
    }

    paying = {
        tier: _count(select(Subscription.user_id).distinct().where(
            Subscription.tier == tier, Subscription.starts_at <= now, Subscription.expires_at > now,
            Subscription.user_id.in_(real_ids)))
        for tier in ("premium", "premium_plus")
    }
    paying["early_birds"] = early_bird_count()

    activity = {
        "likes_7": _count(select(Like.id).where(Like.created_at > week, Like.liker_id.in_(real_ids))),
        "matches_7": _count(select(MatchEvent.id).where(MatchEvent.kind == "matched", MatchEvent.at > week,
                                                        MatchEvent.actor_id.in_(real_ids))),
        "active_matches": _count(select(Match.id).where(Match.removed_at.is_(None), Match.user_low_id.in_(real_ids))),
        "messages_7": _count(select(Message.id).where(Message.created_at > week, Message.sender_id.in_(real_ids))),
        "conversations_started_30": _count(
            select(Message.conversation_id).where(Message.sender_id.in_(real_ids)).group_by(Message.conversation_id).having(func.min(Message.created_at) > month)),
    }
    senders = select(Message.conversation_id, func.count(distinct(Message.sender_id)).label("n")).where(Message.sender_id.in_(real_ids)).group_by(Message.conversation_id).subquery()
    with_messages = _count(select(senders.c.conversation_id))
    answered = _count(select(senders.c.conversation_id).where(senders.c.n >= 2))
    activity["reply_rate"] = round(100 * answered / with_messages) if with_messages else None
    activity["locked_free_free"] = _locked_free_free()

    safety = {
        "reports_30": _count(select(ModerationCase.id).where(ModerationCase.source == "report", ModerationCase.created_at > month)),
        "open_cases": _count(select(ModerationCase.id).where(ModerationCase.status != "closed")),
        "high_open": _count(select(ModerationCase.id).where(ModerationCase.status != "closed", ModerationCase.priority == "high")),
    }
    return {"users": users, "paying": paying, "activity": activity, "safety": safety}


def _locked_free_free():
    """Aktive matches, hvor ingen af de to kan skrive. Beregnes i Python, fordi det afhænger af rettighedsmodellen."""
    locked = 0
    for match in db.session.scalars(select(Match).where(Match.removed_at.is_(None))):
        low, high = db.session.get(User, match.user_low_id), db.session.get(User, match.user_high_id)
        if low.is_test or high.is_test:
            continue
        if not (has_feature(low, "messages.reply") or has_feature(high, "messages.reply")):
            locked += 1
    return locked


def system_status(app):
    try:
        db.session.execute(text("SELECT 1"))
        database = "ok"
        revision = db.session.scalar(text("SELECT version_num FROM sukker.alembic_version"))
    except Exception:
        db.session.rollback()
        database, revision = "fejl", None
    return {
        "database": database,
        "migration": revision,
        "kommuner": db.session.scalar(select(func.count(Kommune.code))),
        "postnumre": db.session.scalar(select(func.count(Postcode.postcode))),
        "mail_backend": app.config["MAIL_BACKEND"],
        "media_root": app.config["MEDIA_ROOT"],
        "conversations": db.session.scalar(select(func.count(Conversation.id))),
    }
