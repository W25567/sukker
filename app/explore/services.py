"""Udforsk: profiler sorteret efter omtrentlig afstand, med filtre for Premium (specifikation 5 og 6)."""
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import and_, any_, func, literal, or_, select

from ..auth.models import User, UserSession
from ..core.timeutil import now_utc, today_local
from ..extensions import db
from ..location.distance import bucket_expression, label_for_bucket
from ..location.models import Kommune, KommuneDistance
from ..matches.models import Like, Match
from ..media.models import ProfileImage
from ..profiles.models import Profile
from ..profiles.services import age_of
from ..safety.queries import blocked_either_way, hidden_by

PAGE_SIZE = 24
RECENTLY_ACTIVE = timedelta(days=3)
PRESENTATION_PREVIEW = 120


@dataclass
class Card:
    handle: str
    display_name: str
    age: int
    distance_label: str | None
    kommune: str | None
    presentation: str | None
    image_id: str | None
    recently_active: bool
    liked: bool
    matched: bool


def _years_ago(years):
    today = today_local()
    try:
        return today.replace(year=today.year - years)
    except ValueError:  # 29. februar
        return today.replace(year=today.year - years, day=28)


def _not_hidden(field_name):
    return ~(literal(field_name) == any_(Profile.hidden_fields))


def seeking_genders(viewer):
    profile = db.session.get(Profile, viewer.id)
    return list(profile.seeking_genders) if profile and profile.seeking_genders else []


def search(viewer, criteria=None, page=1):
    """Returnerer (kort, flere_sider). `criteria` er None for brugere uden adgang til filtre."""
    bucket = bucket_expression(KommuneDistance.km)
    primary_image = (
        select(ProfileImage.public_id)
        .where(ProfileImage.user_id == User.id, ProfileImage.collection == "public")
        .order_by(ProfileImage.position, ProfileImage.id)
        .limit(1)
        .scalar_subquery()
    )
    last_seen = select(func.max(UserSession.last_seen_at)).where(UserSession.user_id == User.id).scalar_subquery()
    liked = select(Like.id).where(Like.liker_id == viewer.id, Like.likee_id == User.id).exists()
    matched = (
        select(Match.id)
        .where(Match.removed_at.is_(None),
               or_(and_(Match.user_low_id == viewer.id, Match.user_high_id == User.id),
                   and_(Match.user_high_id == viewer.id, Match.user_low_id == User.id)))
        .exists()
    )

    query = (
        select(User, Profile, Kommune.name, bucket.label("bucket"), primary_image.label("image_id"),
               last_seen.label("last_seen"), liked.label("liked"), matched.label("matched"))
        .join(Profile, Profile.user_id == User.id)
        .outerjoin(Kommune, Kommune.code == User.kommune_code)
        .outerjoin(KommuneDistance, and_(KommuneDistance.from_code == viewer.kommune_code,
                                          KommuneDistance.to_code == User.kommune_code))
        .where(
            User.id != viewer.id,
            User.status == "active",
            User.is_test.is_(viewer.is_test),
            ~blocked_either_way(viewer.id, User.id),
            ~hidden_by(viewer.id, User.id),
            User.email_verified_at.isnot(None),
            Profile.paused_at.is_(None),
            Profile.hidden_from_search.is_(False),
            Profile.matches_only.is_(False),
        )
    )

    # "Interesseret i" fra brugerens egen profil gælder altid, også for Free. Det er brugerens grundvalg,
    # ikke et Premium-filter. Profiler, der har skjult deres køn, kan ikke matches mod valget og vises ikke.
    seeking = seeking_genders(viewer)
    if seeking:
        query = query.where(Profile.gender.in_(seeking), _not_hidden("gender"))

    if criteria:
        if criteria["max_km"] is not None:
            query = query.where(bucket <= criteria["max_km"])
        if criteria["age_min"]:
            query = query.where(User.birthdate <= _years_ago(criteria["age_min"]))
        if criteria["age_max"]:
            query = query.where(User.birthdate > _years_ago(criteria["age_max"] + 1))
        # Profiler, der har skjult et felt, kommer ikke med, når der filtreres på feltet.
        # Ellers kunne et filter afsløre den skjulte værdi.
        if criteria["genders"]:
            query = query.where(Profile.gender.in_(criteria["genders"]), _not_hidden("gender"))
        if criteria["relationship_types"]:
            query = query.where(Profile.relationship_types.overlap(criteria["relationship_types"]),
                                _not_hidden("relationship_types"))
        if criteria["photos_only"]:
            query = query.where(primary_image.isnot(None))

    query = (
        query.order_by(bucket.asc().nulls_last(), User.created_at.desc(), User.id.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE + 1)
    )
    rows = db.session.execute(query).all()
    recent_after = now_utc() - RECENTLY_ACTIVE
    cards = [
        Card(
            handle=user.handle,
            display_name=user.display_name,
            age=age_of(user),
            distance_label=label_for_bucket(row_bucket),
            kommune=kommune_name if profile.show_kommune else None,
            presentation=_preview(profile.presentation) if "presentation" not in profile.hidden_fields else None,
            image_id=image_id,
            recently_active=bool(profile.show_activity and seen and seen >= recent_after),
            liked=is_liked,
            matched=is_matched,
        )
        for user, profile, kommune_name, row_bucket, image_id, seen, is_liked, is_matched in rows[:PAGE_SIZE]
    ]
    return cards, len(rows) > PAGE_SIZE


def _preview(text):
    if not text:
        return None
    return text if len(text) <= PRESENTATION_PREVIEW else text[:PRESENTATION_PREVIEW].rsplit(" ", 1)[0] + " …"
