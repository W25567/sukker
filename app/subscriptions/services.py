from sqlalchemy import select

from ..core.settings import get_setting
from ..core.timeutil import add_months, now_utc
from ..extensions import db
from .models import Subscription

TIER_RANK = {"free": 0, "premium": 1, "premium_plus": 2}
TIER_LABELS = {"free": "Free", "premium": "Premium", "premium_plus": "Premium+"}


def _active_query(user_id, at):
    return select(Subscription).where(
        Subscription.user_id == user_id,
        Subscription.starts_at <= at,
        Subscription.expires_at > at,
    )


def active_subscription(user_id, at=None):
    """Den aktive periode med højeste niveau, eller None for Free."""
    at = at or now_utc()
    rows = db.session.scalars(_active_query(user_id, at)).all()
    if not rows:
        return None
    return max(rows, key=lambda s: (TIER_RANK[s.tier], s.expires_at))


def effective_tier(user_id, at=None):
    subscription = active_subscription(user_id, at)
    return "free" if subscription is None else subscription.tier


def grant_period(user, months, *, tier="premium", source, granted_by=None, note=None):
    """Giver `months` måneders `tier`. Ligger brugeren allerede på samme niveau, forlænges perioden
    fra den nuværende udløbsdato, så administrator kan tildele mere Premium-tid (specifikation 10.1)."""
    if months <= 0:
        raise ValueError("months skal være positiv")
    now = now_utc()
    latest_end = db.session.scalar(
        select(Subscription.expires_at)
        .where(Subscription.user_id == user.id, Subscription.tier == tier, Subscription.expires_at > now)
        .order_by(Subscription.expires_at.desc())
        .limit(1)
    )
    starts_at = latest_end or now
    subscription = Subscription(
        user_id=user.id,
        tier=tier,
        source=source,
        starts_at=starts_at,
        expires_at=add_months(starts_at, months),
        granted_by_id=granted_by.id if granted_by else None,
        note=note,
    )
    db.session.add(subscription)
    return subscription


def grant_signup_bonus(user):
    """Premium ved oprettelse. Gives én gang, når e-mailen er bekræftet."""
    months = get_setting("signup_premium_months")
    if not months:
        return None
    already_granted = db.session.scalar(
        select(Subscription.id).where(Subscription.user_id == user.id, Subscription.source == "signup_bonus").limit(1)
    )
    if already_granted:
        return None
    return grant_period(user, months, source="signup_bonus")
