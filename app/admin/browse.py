"""Brugerlister med filtre, sortering og signaler til administrationen.

Signalerne er optællinger pr. bruger, der kan pege på brud på reglerne: anmeldelser, blokeringer,
skjulninger og usædvanligt mange likes eller beskeder. De er ikke en afgørelse – kun et sted at kigge.
"""
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import exists, func, or_, select

from ..auth.models import User, UserSession
from ..core.timeutil import now_utc
from ..extensions import db
from ..matches.models import Like
from ..media.models import ProfileImage
from ..messaging.models import Message
from ..moderation.models import ModerationCase
from ..profiles.models import Profile
from ..safety.models import UserBlock, UserHide
from ..subscriptions.models import Subscription

PAGE_SIZE = 50


def _since(days):
    return now_utc() - timedelta(days=days)


def _count(model_column, *conditions):
    return select(func.count(model_column)).where(*conditions).correlate(User).scalar_subquery()


def signal_columns():
    """Korrelerede optællinger pr. bruger (bruges både i lister og på brugersiden)."""
    week, month = _since(7), _since(30)
    return {
        "reports": _count(ModerationCase.id, ModerationCase.subject_user_id == User.id),
        "reports_30": _count(ModerationCase.id, ModerationCase.subject_user_id == User.id,
                             ModerationCase.created_at > month),
        "blocked_by": _count(UserBlock.blocker_id, UserBlock.blocked_id == User.id),
        "hidden_by": _count(UserHide.hider_id, UserHide.hidden_id == User.id),
        "likes_7": _count(Like.id, Like.liker_id == User.id, Like.created_at > week),
        "messages_7": _count(Message.id, Message.sender_id == User.id, Message.created_at > week),
        "images": _count(ProfileImage.id, ProfileImage.user_id == User.id, ProfileImage.collection == "public", ProfileImage.deleted_at.is_(None)),
        "last_seen": select(func.max(UserSession.last_seen_at)).where(UserSession.user_id == User.id)
        .correlate(User).scalar_subquery(),
    }


def _active_tier(*conditions):
    now = now_utc()
    return exists().where(Subscription.user_id == User.id, Subscription.starts_at <= now,
                          Subscription.expires_at > now, *conditions)


def _filters():
    week, month = _since(7), _since(30)
    no_image = ~exists().where(ProfileImage.user_id == User.id, ProfileImage.collection == "public", ProfileImage.deleted_at.is_(None))
    return {
        "alle": ("Alle", []),
        "nye7": ("Nye, 7 dage", [User.created_at > week]),
        "nye30": ("Nye, 30 dage", [User.created_at > month]),
        "aktive7": ("Aktive, 7 dage", [exists().where(UserSession.user_id == User.id, UserSession.last_seen_at > week)]),
        "bekraeftede": ("Bekræftede", [User.email_verified_at.isnot(None)]),
        "ubekraeftede": ("Ubekræftede", [User.email_verified_at.is_(None)]),
        "pause": ("På pause", [exists().where(Profile.user_id == User.id, Profile.paused_at.isnot(None))]),
        "begraenset": ("Begrænsede", [User.status == "restricted"]),
        "suspenderet": ("Suspenderede", [User.status == "suspended"]),
        "slettede": ("Slettede", [User.status == "deleted"]),
        "premium": ("Premium", [_active_tier(Subscription.tier == "premium")]),
        "premium_plus": ("Premium+", [_active_tier(Subscription.tier == "premium_plus")]),
        "tidlige_fugle": ("Tidlige fugle", [exists().where(Subscription.user_id == User.id, Subscription.source == "early_bird")]),
        "uden_billede": ("Uden billede", [no_image]),
        "nye_uden_billede": ("Nye uden billede, 30 dage", [User.created_at > month, no_image]),
        "anmeldt": ("Anmeldte", [exists().where(ModerationCase.subject_user_id == User.id)]),
        "aabne_sager": ("Med åbne sager", [exists().where(ModerationCase.subject_user_id == User.id,
                                                         ModerationCase.status != "closed")]),
        "blokeret": ("Blokeret af andre", [exists().where(UserBlock.blocked_id == User.id)]),
        "test": ("Testkonti", []),
    }


SORTS = {
    "nyeste": "Nyeste først",
    "aeldste": "Ældste først",
    "navn": "Navn",
    "aktivitet": "Senest aktiv",
    "anmeldelser": "Flest anmeldelser",
    "blokeringer": "Blokeret af flest",
    "skjult": "Skjult af flest",
    "likes": "Flest likes, 7 dage",
    "beskeder": "Flest beskeder, 7 dage",
}


def filter_labels():
    return {key: label for key, (label, _) in _filters().items()}


@dataclass
class Row:
    user: User
    reports: int
    reports_30: int
    blocked_by: int
    hidden_by: int
    likes_7: int
    messages_7: int
    images: int
    last_seen: object


def user_list(filter_key="alle", q="", sort="nyeste", page=1):
    """(rækker, antal i alt, flere sider). Testkonti vises kun under filteret "test"."""
    filters = _filters()
    if filter_key not in filters:
        filter_key = "alle"
    if sort not in SORTS:
        sort = "nyeste"
    signals = signal_columns()
    conditions = list(filters[filter_key][1]) + [User.is_test.is_(filter_key == "test")]
    q = (q or "").strip()
    if filter_key != "slettede" and not q:
        conditions.append(User.status != "deleted")  # slettede har deres egen liste (deleted_list); søgning finder dem
    if q:
        # Også den oprindelige e-mail for slettede konti, så de kan findes, fx ved en henvendelse fra politiet.
        conditions.append(or_(User.display_name.ilike(f"%{q}%"), User.email == q.lower(), User.deleted_email == q.lower(),
                              User.handle == q))
    order = {
        "nyeste": [User.created_at.desc()],
        "aeldste": [User.created_at.asc()],
        "navn": [func.lower(User.display_name)],
        "aktivitet": [signals["last_seen"].desc().nulls_last()],
        "anmeldelser": [signals["reports"].desc()],
        "blokeringer": [signals["blocked_by"].desc()],
        "skjult": [signals["hidden_by"].desc()],
        "likes": [signals["likes_7"].desc()],
        "beskeder": [signals["messages_7"].desc()],
    }[sort] + [User.id.desc()]
    total = db.session.scalar(select(func.count(User.id)).where(*conditions))
    query = (
        select(User, *[col.label(name) for name, col in signals.items()])
        .where(*conditions).order_by(*order).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE + 1)
    )
    rows = [Row(user, *values) for user, *values in db.session.execute(query).all()]
    return rows[:PAGE_SIZE], total, len(rows) > PAGE_SIZE


@dataclass
class DeletedRow:
    user: User
    purge_after: object
    on_hold: bool
    images: int
    messages: int
    cases: int


def deleted_list(q="", page=1):
    """Slettede konti, der stadig bevares, nyeste sletning først. (rækker, antal i alt, flere sider)."""
    from ..account.services import held_user_ids, retention_status

    conditions = [User.status == "deleted", User.is_test.is_(False)]
    q = (q or "").strip()
    if q:
        conditions.append(or_(User.display_name.ilike(f"%{q}%"), User.deleted_email == q.lower(), User.handle == q))
    total = db.session.scalar(select(func.count(User.id)).where(*conditions))
    columns = (
        _count(ProfileImage.id, ProfileImage.user_id == User.id).label("images"),
        _count(Message.id, Message.sender_id == User.id).label("messages"),
        _count(ModerationCase.id, or_(ModerationCase.subject_user_id == User.id,
                                      ModerationCase.reporter_id == User.id)).label("cases"),
        User.id.in_(held_user_ids()).label("on_hold"),
    )
    rows = db.session.execute(
        select(User, *columns).where(*conditions).order_by(User.deleted_at.desc(), User.id.desc())
        .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE + 1)
    ).all()
    result = [DeletedRow(user, retention_status(user)[0], bool(on_hold), images, messages, cases)
              for user, images, messages, cases, on_hold in rows[:PAGE_SIZE]]
    return result, total, len(rows) > PAGE_SIZE


def user_signals(user):
    signals = signal_columns()
    values = db.session.execute(select(*[col.label(name) for name, col in signals.items()]).where(User.id == user.id)).one()
    return dict(values._mapping)


# Tilsyn: hvem skiller sig ud? (titel, forklaring, sortering, signal)
OVERSIGHT = [
    ("Mest anmeldt", "Antal sager, hvor brugeren er den anmeldte.", "anmeldelser", "reports"),
    ("Blokeret af flest", "Mange blokeringer kan tyde på chikane eller spam.", "blokeringer", "blocked_by"),
    ("Skjult af flest", "Mange skjulninger kan tyde på upassende profil eller indhold.", "skjult", "hidden_by"),
    ("Flest likes, 7 dage", "Usædvanligt mange likes kan tyde på automatisering eller spam.", "likes", "likes_7"),
    ("Flest beskeder, 7 dage", "Usædvanligt mange beskeder kan tyde på spam eller svindel.", "beskeder", "messages_7"),
]


def oversight(limit=10):
    sections = []
    for title, explanation, sort, signal in OVERSIGHT:
        rows, _, _ = user_list("alle", "", sort, 1)
        rows = [r for r in rows if not r.user.is_deleted]
        sections.append((title, explanation, sort, signal, [r for r in rows[:limit] if getattr(r, signal)]))
    new_without_image, total, _ = user_list("nye_uden_billede", "", "nyeste", 1)
    return sections, new_without_image[:limit], total
