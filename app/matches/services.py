"""Likes, matches og match-loft (specifikation 7)."""
from dataclasses import dataclass

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import aliased

from ..auth.models import User
from ..core.settings import get_setting
from ..core.timeutil import now_utc
from ..extensions import db
from ..messaging.models import Conversation
from ..profiles.models import Profile
from ..profiles.services import can_view_profile
from ..subscriptions.services import effective_tier
from .models import Like, Match, MatchEvent
from .queries import active_match_count, find_match, pair


class LikeRefused(Exception):
    """Liket blev ikke givet. Beskeden kan vises direkte for brugeren."""


@dataclass
class LikeResult:
    matched: bool


def match_limit(user):
    """Højeste antal aktive matches, før brugeren ikke kan give nye likes. None betyder ubegrænset."""
    tier = effective_tier(user.id)
    if tier == "free":
        return get_setting("free_match_limit") if get_setting("free_match_limit_enabled") else None
    return get_setting("premium_plus_match_limit" if tier == "premium_plus" else "premium_match_limit")


def has_liked(liker_id, likee_id):
    return db.session.scalar(select(Like.id).where(Like.liker_id == liker_id, Like.likee_id == likee_id)) is not None


def _lock_pair(a_id, b_id):
    """Låser begge brugere i fast rækkefølge, så to samtidige likes mellem samme par ikke kan overse hinanden,
    og så samtidige likes fra samme bruger ikke kan omgå match-loftet (specifikation 7.3)."""
    low, high = pair(a_id, b_id)
    db.session.execute(select(User.id).where(User.id.in_([low, high])).order_by(User.id).with_for_update())


def like(liker, target):
    if liker.id == target.id:
        raise LikeRefused("Du kan ikke like dig selv.")
    if not liker.is_verified:
        raise LikeRefused("Bekræft din e-mail, før du kan give likes.")
    profile = db.session.get(Profile, liker.id)
    if profile is not None and profile.is_paused:
        raise LikeRefused("Din profil er på pause. Aktivér den for at give likes.")
    if not can_view_profile(liker, target):
        raise LikeRefused("Profilen findes ikke.")

    _lock_pair(liker.id, target.id)
    if has_liked(liker.id, target.id):
        existing = find_match(liker.id, target.id)
        return LikeResult(matched=existing is not None and existing.is_active)

    # Loftet håndhæves på nye likes. Ældre likes kan stadig give match og bringe brugeren over loftet.
    limit = match_limit(liker)
    if limit is not None and active_match_count(liker.id) >= limit:
        raise LikeRefused(
            f"Du har nået grænsen på {limit} matches. Fjern et match eller opgradér for at give nye likes."
        )

    db.session.add(Like(liker_id=liker.id, likee_id=target.id))
    db.session.flush()
    if not has_liked(target.id, liker.id):
        return LikeResult(matched=False)
    _create_or_reactivate_match(liker.id, target.id, actor_id=liker.id)
    return LikeResult(matched=True)


def _create_or_reactivate_match(a_id, b_id, actor_id):
    low, high = pair(a_id, b_id)
    match = find_match(low, high)
    now = now_utc()
    if match is None:
        match = Match(user_low_id=low, user_high_id=high, matched_at=now)
        db.session.add(match)
        db.session.flush()
    else:
        match.matched_at, match.removed_at, match.removed_by_id = now, None, None
    db.session.add(MatchEvent(match_id=match.id, kind="matched", actor_id=actor_id, at=now))
    # Matcher de igen, kommer en skjult samtale tilbage med den tidligere historik (specifikation 7.2).
    _set_conversation_hidden(low, high, None)
    return match


def _set_conversation_hidden(low, high, hidden_at):
    db.session.execute(
        update(Conversation)
        .where(Conversation.user_low_id == low, Conversation.user_high_id == high)
        .values(hidden_at=hidden_at)
    )


def unlike(liker, target):
    """Trækker et like tilbage, så længe det ikke har givet et aktivt match."""
    match = find_match(liker.id, target.id)
    if match is not None and match.is_active:
        return False
    result = db.session.execute(delete(Like).where(Like.liker_id == liker.id, Like.likee_id == target.id))
    return result.rowcount > 0


def remove_match(user, other):
    """Fjerner et match. Begge likes slettes, så et nyt match kræver, at begge liker igen.
    Samtalen skjules og gemmes efter reglerne i specifikation 7.2 (M5)."""
    match = find_match(user.id, other.id)
    if match is None or not match.is_active:
        return False
    now = now_utc()
    match.removed_at, match.removed_by_id = now, user.id
    db.session.add(MatchEvent(match_id=match.id, kind="removed", actor_id=user.id, at=now))
    db.session.execute(delete(Like).where(
        or_((Like.liker_id == user.id) & (Like.likee_id == other.id), (Like.liker_id == other.id) & (Like.likee_id == user.id))
    ))
    _set_conversation_hidden(match.user_low_id, match.user_high_id, now)
    return True


def active_matches(user):
    """Brugerens aktive matches, nyeste først, som (match, anden bruger)."""
    rows = db.session.execute(
        select(Match, User)
        .join(User, or_((Match.user_low_id == user.id) & (User.id == Match.user_high_id),
                        (Match.user_high_id == user.id) & (User.id == Match.user_low_id)))
        .where(Match.removed_at.is_(None))
        .order_by(Match.matched_at.desc())
    ).all()
    return [(match, other) for match, other in rows]


def received_likes(user):
    """Likes til brugeren, som brugeren ikke selv har gengældt endnu, nyeste først."""
    own_like = aliased(Like)
    returned = select(own_like.id).where(own_like.liker_id == user.id, own_like.likee_id == Like.liker_id).exists()
    return db.session.execute(
        select(Like, User)
        .join(User, User.id == Like.liker_id)
        .where(Like.likee_id == user.id, ~returned)
        .order_by(Like.created_at.desc())
    ).all()
