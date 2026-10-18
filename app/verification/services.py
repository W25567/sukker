import secrets

from sqlalchemy import select

from ..core.timeutil import now_utc
from ..extensions import db
from ..media import storage
from ..media.models import ProfileImage
from ..media.processing import ImageRejected, process_upload
from ..media.services import LIVE
from .models import PhotoVerification

# Gestussen vælges tilfældigt, så en selfie ikke kan være et gammelt billede eller hentet fra nettet.
GESTURES = {
    "tre_fingre": "Hold tre fingre op ved siden af dit ansigt",
    "tommel": "Vis en tommelfinger op ved din kind",
    "naese": "Peg på din næse med én finger",
    "hoved": "Læg en hånd oven på dit hoved",
    "v_tegn": "Lav et V-tegn med to fingre ved siden af dit ansigt",
}
MAX_SELFIE_BYTES = 10 * 1024 * 1024


class VerificationRefused(ValueError):
    """Beskeden kan vises direkte for brugeren."""


def random_gesture():
    return secrets.choice(list(GESTURES))


def public_images(user_id):
    return db.session.scalars(
        select(ProfileImage).where(ProfileImage.user_id == user_id, ProfileImage.collection == "public", LIVE)
        .order_by(ProfileImage.position, ProfileImage.id)
    ).all()


def latest(user):
    return db.session.scalar(select(PhotoVerification).where(PhotoVerification.user_id == user.id)
                             .order_by(PhotoVerification.id.desc()).limit(1))


def pending_for(user):
    return db.session.scalar(select(PhotoVerification).where(PhotoVerification.user_id == user.id,
                                                             PhotoVerification.status == "pending"))


def submit(user, gesture, data):
    """Gemmer selfien som en anmodning, der venter på en moderator. Kalderen committer."""
    if user.photo_verified_at is not None:
        raise VerificationRefused("Din profil er allerede verificeret.")
    if pending_for(user) is not None:
        raise VerificationRefused("Du har allerede sendt en selfie. Den venter på at blive vurderet.")
    if gesture not in GESTURES:
        raise VerificationRefused("Siden er udløbet. Prøv igen.")
    if not public_images(user.id):
        raise VerificationRefused("Du skal have mindst ét offentligt profilbillede, som selfien kan sammenlignes med.")
    if not data:
        raise VerificationRefused("Vælg eller tag en selfie.")
    try:
        processed = process_upload(data, max_still_bytes=MAX_SELFIE_BYTES, max_animated_bytes=0,
                                   animated_refusal="Selfien kan ikke være animeret.")
    except ImageRejected as rejection:
        raise VerificationRefused(str(rejection))
    row = PhotoVerification(user_id=user.id, gesture=gesture, public_id=secrets.token_urlsafe(16))
    db.session.add(row)
    db.session.flush()
    storage.save_files(row, processed)
    return row


def pending():
    return db.session.scalars(select(PhotoVerification).where(PhotoVerification.status == "pending")
                              .order_by(PhotoVerification.created_at)).all()


def recent_decisions(limit=20):
    return db.session.scalars(select(PhotoVerification).where(PhotoVerification.status != "pending")
                              .order_by(PhotoVerification.decided_at.desc()).limit(limit)).all()


def _forget_selfie(row):
    """Selfien slettes, så snart den er vurderet. Kun afgørelsen gemmes."""
    if row.public_id:
        storage.delete_files_for(row.collection, row.public_id)
        row.public_id = None


def _notify(actor, user, body):
    from ..messaging import admin_threads

    try:
        admin_threads.send_from_admin(actor, user, body)
    except admin_threads.AdminMessageRefused:
        pass


def decide(actor, row, approve, note=None):
    """Godkender eller afviser. En afvisning kræver en begrundelse, som brugeren får at se."""
    from ..auth.models import User

    note = (note or "").strip()
    if row.status != "pending":
        raise VerificationRefused("Anmodningen er allerede afgjort.")
    if not approve and not note:
        raise VerificationRefused("Skriv en begrundelse, som brugeren kan se.")
    user = db.session.get(User, row.user_id)
    now = now_utc()
    row.status = "approved" if approve else "rejected"
    row.decided_at, row.decided_by_id, row.note = now, actor.id, note or None
    _forget_selfie(row)
    if approve:
        user.photo_verified_at = now
        _notify(actor, user, "Din profil er verificeret. Andre kan nu se mærket \"Verificeret\" på din profil. "
                             "Din selfie er slettet.")
    else:
        _notify(actor, user, f"Vi kunne ikke verificere din profil: {note} Din selfie er slettet, og du er "
                             "velkommen til at prøve igen under Min profil.")
    db.session.flush()


def withdraw_pending(user):
    """Når brugeren sletter sin profil, slettes en selfie, der venter, og anmodningen lukkes."""
    row = pending_for(user)
    if row is not None:
        row.status, row.decided_at, row.note = "rejected", now_utc(), "Brugeren slettede sin profil."
        _forget_selfie(row)


def revoke(actor, user, note):
    """Fjerner mærket, fx hvis profilbillederne senere er skiftet ud med billeder af en anden."""
    note = (note or "").strip()
    if user.photo_verified_at is None:
        raise VerificationRefused("Profilen er ikke verificeret.")
    if not note:
        raise VerificationRefused("Skriv en begrundelse, som brugeren kan se.")
    user.photo_verified_at = None
    approved = db.session.scalar(select(PhotoVerification).where(
        PhotoVerification.user_id == user.id, PhotoVerification.status == "approved")
        .order_by(PhotoVerification.id.desc()).limit(1))
    if approved is not None:
        approved.status = "revoked"
        approved.note = f"{approved.note + ' / ' if approved.note else ''}Fjernet: {note}"
        approved.decided_at, approved.decided_by_id = now_utc(), actor.id
    _notify(actor, user, f"Mærket \"Verificeret\" er fjernet fra din profil: {note} Du kan verificere profilen "
                         "igen under Min profil.")
    db.session.flush()
