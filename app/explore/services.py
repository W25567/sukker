"""Udforsk: profiler sorteret efter omtrentlig afstand, med filtre for Premium (specifikation 5 og 6)."""
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import and_, any_, case, func, literal, or_, select

from ..auth.models import User, UserSession
from ..core.settings import get_setting
from ..core.timeutil import now_utc, today_local
from ..extensions import db
from ..location.distance import bucket_expression, distance_visible, kommune_visible, label_for_bucket
from ..location.models import Kommune, KommuneDistance
from ..matches.models import Like, Match
from ..media.models import ProfileImage
from ..profiles.models import Profile
from ..profiles.services import age_of
from ..safety.models import DistanceMask
from ..safety.queries import blocked_either_way, hidden_by

PAGE_SIZE = 24
INACTIVE_DAYS_PER_MONTH = 30
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
    verified: bool = False


def _years_ago(years):
    today = today_local()
    try:
        return today.replace(year=today.year - years)
    except ValueError:  # 29. februar
        return today.replace(year=today.year - years, day=28)


def _not_hidden(field_name):
    return ~(literal(field_name) == any_(Profile.hidden_fields))


def own_gender(viewer):
    profile = db.session.get(Profile, viewer.id)
    return profile.gender if profile else None


def _welcomes(gender):
    """Profilen har ikke valgt "Interesseret i", eller valget omfatter `gender`."""
    no_choice = func.cardinality(Profile.seeking_genders) == 0
    if not gender:
        return no_choice
    return or_(no_choice, literal(gender) == any_(Profile.seeking_genders))


def seeking_genders(viewer):
    profile = db.session.get(Profile, viewer.id)
    return list(profile.seeking_genders) if profile and profile.seeking_genders else []


def inactive_before(now=None):
    """Profiler, hvis bruger ikke har været aktiv siden dette tidspunkt, vises ikke i Udforsk. None = aldrig."""
    months = get_setting("inactive_hide_months")
    if not months:
        return None
    return (now or now_utc()) - timedelta(days=INACTIVE_DAYS_PER_MONTH * months)


def search(viewer, criteria=None, page=1, hide_liked=False, newest_first=False):
    """Returnerer (kort, flere_sider). `criteria` er None for brugere uden adgang til filtre.
    `hide_liked` skjuler profiler, brugeren har liket, men som ikke har liket tilbage (et match vises stadig).

    Rækkefølgen er fast og forklares for brugeren: efter afstand og derefter nyeste profil. Med `newest_first`
    (Premium) kommer de nyeste profiler først uanset afstand. Intet andet afgør, hvem man ser.

    Har administrator slået afstand fra, sorteres altid efter nyeste profil, og afstand kan hverken ses eller
    bruges som filter, så rækkefølgen ikke afslører den. Kommunen vises kun, når både administrator og
    brugeren selv tillader det."""
    show_distance, show_kommune = distance_visible(), kommune_visible()
    # Har brugeren vist usædvanlig interesse for en profil, vises afstanden til den altid som "I nærheden",
    # også når der sorteres og filtreres (moderation/signals.py).
    masked = select(DistanceMask.viewer_id).where(DistanceMask.viewer_id == viewer.id,
                                                  DistanceMask.target_id == User.id).exists()
    bucket = case((masked, 0), else_=bucket_expression(KommuneDistance.km))
    primary_image = (
        select(ProfileImage.public_id)
        .where(ProfileImage.user_id == User.id, ProfileImage.collection == "public",
               ProfileImage.deleted_at.is_(None), ProfileImage.moderated_at.is_(None))
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

    hide_before = inactive_before()
    if hide_before is not None:
        query = query.where(User.last_active_at >= hide_before)

    # "Interesseret i" gælder altid og i begge retninger, også for Free. Det er brugerens grundvalg, ikke et
    # Premium-filter: man ser kun de køn, man søger, og kun profiler, der søger ens eget køn (eller ikke har
    # valgt), ser en. Så får ingen henvendelser fra et køn, de ikke søger. Køn er påkrævet og kan ikke skjules.
    seeking = seeking_genders(viewer)
    if seeking:
        query = query.where(Profile.gender.in_(seeking))
    query = query.where(_welcomes(own_gender(viewer)))

    if hide_liked:
        query = query.where(or_(~liked, matched))

    if criteria:
        if criteria["max_km"] is not None and show_distance:
            query = query.where(bucket <= criteria["max_km"])
        if criteria["age_min"]:
            query = query.where(User.birthdate <= _years_ago(criteria["age_min"]))
        if criteria["age_max"]:
            query = query.where(User.birthdate > _years_ago(criteria["age_max"] + 1))
        # Profiler, der har skjult et felt, kommer ikke med, når der filtreres på feltet.
        # Ellers kunne et filter afsløre den skjulte værdi.
        if criteria["genders"]:
            query = query.where(Profile.gender.in_(criteria["genders"]))
        if criteria["relationship_types"]:
            query = query.where(Profile.relationship_types.overlap(criteria["relationship_types"]),
                                _not_hidden("relationship_types"))
        if criteria["photos_only"]:
            query = query.where(primary_image.isnot(None))

    order = ((User.created_at.desc(), User.id.desc()) if newest_first or not show_distance
             else (bucket.asc().nulls_last(), User.created_at.desc(), User.id.desc()))
    query = (
        query.order_by(*order)
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
            distance_label=label_for_bucket(row_bucket) if show_distance else None,
            kommune=kommune_name if show_kommune and profile.show_kommune else None,
            presentation=_preview(profile.presentation) if "presentation" not in profile.hidden_fields else None,
            image_id=image_id,
            recently_active=bool(profile.show_activity and seen and seen >= recent_after),
            liked=is_liked,
            matched=is_matched,
            verified=user.photo_verified_at is not None,
        )
        for user, profile, kommune_name, row_bucket, image_id, seen, is_liked, is_matched in rows[:PAGE_SIZE]
    ]
    return cards, len(rows) > PAGE_SIZE


def _preview(text):
    if not text:
        return None
    return text if len(text) <= PRESENTATION_PREVIEW else text[:PRESENTATION_PREVIEW].rsplit(" ", 1)[0] + " …"
