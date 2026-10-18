from ..auth.models import User
from ..core.timeutil import age_on, today_local
from ..extensions import db
from ..matches.queries import has_active_match
from ..safety.queries import is_blocked_between
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

    Kun indloggede brugere kan se profiler. Ejeren ser altid sin egen profil. Blokering virker i begge
    retninger, og begrænsede eller suspenderede konti vises ikke for andre. En profil på pause eller med
    "kun for matches" kan kun ses af personer, ejeren har et aktivt match med. Moderatorer ser profiler
    gennem moderationsværktøjet, ikke her.
    """
    if viewer is None:
        return False
    if viewer.id == owner.id:
        return True
    if owner.status != "active" or is_blocked_between(viewer.id, owner.id):
        return False
    # Testkonti og rigtige brugere kan aldrig se hinanden, så testkonti aldrig fremstår som rigtige profiler.
    if owner.is_test != viewer.is_test:
        return False
    profile = db.session.get(Profile, owner.id)
    if profile is not None and (profile.is_paused or profile.matches_only):
        return has_active_match(viewer.id, owner.id)
    return True


def needs_gender(user):
    """Profiler fra før køn blev påkrævet ved oprettelse."""
    profile = db.session.get(Profile, user.id)
    return profile is None or not profile.gender


def welcomes_gender(profile, gender):
    """Vil profilen kontaktes af nogen med dette køn? Uden valg under "Interesseret i" er alle velkomne.
    Et ukendt køn passer ikke til et valg."""
    if profile is None or not profile.seeking_genders:
        return True
    return gender in profile.seeking_genders


def is_interested_in(owner, viewer):
    """Søger `owner` det køn, `viewer` har? Bruges til, hvem der kan se og like `owner` (gensidig præference)."""
    viewer_profile = db.session.get(Profile, viewer.id)
    return welcomes_gender(db.session.get(Profile, owner.id), viewer_profile.gender if viewer_profile else None)


def find_user_by_handle(handle):
    """Brugere findes via deres tilfældige handle i URL'en, aldrig via det viste navn (som ikke er unikt)."""
    return db.session.scalar(db.select(User).where(User.handle == handle))
