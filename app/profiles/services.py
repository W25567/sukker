from ..auth.models import User
from ..core.timeutil import age_on, today_local
from ..extensions import db
from ..matches.queries import has_active_match
from .models import Profile


def get_or_create_profile(user):
    profile = db.session.get(Profile, user.id)
    if profile is None:
        profile = Profile(user_id=user.id, seeking_genders=[], relationship_types=[], hidden_fields=[])
        db.session.add(profile)
        db.session.flush()
    return profile


def age_of(user):
    return age_on(user.birthdate, today_local())


def can_view_profile(viewer, owner):
    """Afgør på serveren, om `viewer` må se `owner`s profil og offentlige billeder (specifikation 3.2 og 4.1).

    Kun indloggede brugere kan se profiler. Ejeren ser altid sin egen profil. En profil på pause eller
    med "kun for matches" kan kun ses af personer, ejeren har et aktivt match med. Blokeringer (M6)
    kobles på her, når de findes.
    """
    if viewer is None:
        return False
    if viewer.id == owner.id:
        return True
    profile = db.session.get(Profile, owner.id)
    if profile is not None and (profile.is_paused or profile.matches_only):
        return has_active_match(viewer.id, owner.id)
    return True


def find_user_by_username(username):
    return db.session.scalar(db.select(User).where(db.func.lower(User.username) == username.lower()))
