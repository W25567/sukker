from datetime import UTC, datetime

from sqlalchemy import func, select

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
    """Den aktive periode med højeste niveau, eller None for Free.

    Premium for livstid til tidlige fugle gælder kun, mens brugeren har mindst ét offentligt billede."""
    at = at or now_utc()
    rows = db.session.scalars(_active_query(user_id, at)).all()
    if any(row.source == "early_bird" for row in rows) and not has_public_image(user_id):
        rows = [row for row in rows if row.source != "early_bird"]
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


# Premium for livstid gemmes som en periode, der aldrig udløber i praksis. En dag før årets udgang, så
# omregning til dansk tid ikke løber over år 9999.
LIFETIME_END = datetime(9999, 12, 30, tzinfo=UTC)


def early_bird_count():
    from ..auth.models import User

    return db.session.scalar(
        select(func.count(Subscription.id)).join(User, User.id == Subscription.user_id)
        .where(Subscription.source == "early_bird", User.is_test.is_(False))
    )


def early_bird_open():
    """Er kampagnen for tidlige fugle åben? Styres af administrator (indstillingerne early_bird_*)."""
    if not get_setting("early_bird_enabled"):
        return False
    limit = get_setting("early_bird_limit")
    return limit is None or early_bird_count() < limit


def has_public_image(user_id):
    from ..media.models import ProfileImage

    return db.session.scalar(
        select(ProfileImage.id).where(ProfileImage.user_id == user_id, ProfileImage.collection == "public").limit(1)
    ) is not None


def has_early_bird(user):
    return db.session.scalar(
        select(Subscription.id).where(Subscription.user_id == user.id, Subscription.source == "early_bird").limit(1)
    ) is not None


def grant_early_bird(user):
    """Premium for livstid til en bekræftet bruger med mindst ét billede, mens kampagnen er åben.
    Gives højst én gang. Kaldes ved bekræftelse, ved upload af billede og på velkomstsiden."""
    if (user.is_test or not user.is_verified or has_early_bird(user) or not has_public_image(user.id)
            or not early_bird_open()):
        return None
    subscription = Subscription(user_id=user.id, tier="premium", source="early_bird", starts_at=now_utc(),
                                expires_at=LIFETIME_END, note="Tidlig fugl")
    db.session.add(subscription)
    db.session.flush()
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
