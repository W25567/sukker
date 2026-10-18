"""Adgang til private album (specifikation 4.1 og 4.2).

- Alle kan have et privat album. Andre kan bede om adgang med "Anmod om adgang".
- Ejeren kan give adgang til hele albummet (også billeder, der lægges i senere) eller kun til udvalgte
  billeder, afvise eller lade anmodningen ligge. Adgang kan altid trækkes tilbage eller ændres.
- Efter en afvisning eller tilbagetrækning kan man først anmode igen efter REQUEST_COOLDOWN, og man kan
  højst sende DAILY_REQUESTS nye anmodninger pr. døgn. Så kan en afvisning ikke omgås.
- Abonnement og matchstatus giver aldrig adgang. Blokering fjerner al adgang i begge retninger.
- Adgangen kontrolleres ved hver visning af et billede (media.services.can_view_image).
"""
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import and_, delete, func, or_, select

from ..auth.models import User
from ..core.timeutil import now_utc
from ..extensions import db
from .models import PrivateAccess, PrivateAccessImage, ProfileImage

REQUEST_COOLDOWN = timedelta(days=30)
DAILY_REQUESTS = 20
LIVE_PRIVATE = (ProfileImage.collection == "private", ProfileImage.deleted_at.is_(None))


class AccessRefused(ValueError):
    """Handlingen kan ikke udføres. Beskeden kan vises direkte for brugeren."""


def find(owner_id, viewer_id):
    return db.session.scalar(select(PrivateAccess).where(PrivateAccess.owner_id == owner_id,
                                                         PrivateAccess.viewer_id == viewer_id))


def private_images(owner_id):
    return db.session.scalars(
        select(ProfileImage).where(ProfileImage.user_id == owner_id, *LIVE_PRIVATE)
        .order_by(ProfileImage.position, ProfileImage.id)
    ).all()


def private_count(owner_id):
    return db.session.scalar(select(func.count(ProfileImage.id)).where(ProfileImage.user_id == owner_id, *LIVE_PRIVATE))


def selected_ids(access):
    return set(db.session.scalars(select(PrivateAccessImage.image_id).where(PrivateAccessImage.access_id == access.id)))


def has_image_access(viewer_id, image):
    """Har `viewer_id` fået adgang til netop dette private billede? (Profilens synlighed tjekkes af kalderen.)"""
    access = find(image.user_id, viewer_id)
    if access is None or access.status != "approved":
        return False
    if access.scope == "all":
        return True
    return db.session.get(PrivateAccessImage, (access.id, image.id)) is not None


def visible_private_images(viewer, owner):
    """De private billeder, `viewer` må se hos `owner`."""
    access = find(owner.id, viewer.id)
    if access is None or access.status != "approved":
        return []
    images = private_images(owner.id)
    if access.scope == "all":
        return images
    allowed = selected_ids(access)
    return [image for image in images if image.id in allowed]


def cooldown_until(access):
    if access is not None and access.status in ("declined", "revoked") and access.decided_at:
        until = access.decided_at + REQUEST_COOLDOWN
        if until > now_utc():
            return until
    return None


@dataclass
class ViewerState:
    """Hvad den besøgende ser ved et privat album: antal billeder, status og eventuel karensperiode."""

    total: int
    visible: int
    status: str | None  # None, pending, approved, declined, revoked
    cooldown_until: object


def viewer_state(viewer, owner):
    total = private_count(owner.id)
    access = find(owner.id, viewer.id)
    visible = len(visible_private_images(viewer, owner)) if access and access.status == "approved" else 0
    return ViewerState(total, visible, access.status if access else None, cooldown_until(access))


def request_access(viewer, owner):
    """Den besøgende beder om adgang. Kalderen har tjekket, at profilen må ses."""
    if viewer.id == owner.id:
        raise AccessRefused("Du har allerede adgang til dine egne billeder.")
    if not viewer.is_verified or not viewer.is_active_account:
        raise AccessRefused("Bekræft din e-mail, før du kan anmode om adgang.")
    if private_count(owner.id) == 0:
        raise AccessRefused("Profilen har ingen private billeder.")
    access = find(owner.id, viewer.id)
    if access is not None and access.status in ("pending", "approved"):
        return access
    until = cooldown_until(access)
    if until:
        raise AccessRefused(f"Du kan anmode igen efter {until.strftime('%d-%m-%Y')}.")
    recent = db.session.scalar(select(func.count(PrivateAccess.id)).where(
        PrivateAccess.viewer_id == viewer.id, PrivateAccess.requested_at > now_utc() - timedelta(days=1)))
    if recent >= DAILY_REQUESTS:
        raise AccessRefused("Du har sendt mange anmodninger i dag. Prøv igen i morgen.")
    if access is None:
        access = PrivateAccess(owner_id=owner.id, viewer_id=viewer.id)
        db.session.add(access)
    elif access.id:
        db.session.execute(delete(PrivateAccessImage).where(PrivateAccessImage.access_id == access.id))
    access.status, access.scope, access.requested_at, access.decided_at = "pending", "all", now_utc(), None
    db.session.flush()
    return access


def cancel_request(viewer, owner):
    access = find(owner.id, viewer.id)
    if access is not None and access.status == "pending":
        db.session.delete(access)


def approve(owner, access, scope="all", image_ids=()):
    """Godkender eller ændrer en adgang. `image_ids` bruges ved scope "selected" og skal være ejerens private billeder."""
    if access.owner_id != owner.id:
        raise AccessRefused("Anmodningen findes ikke.")
    if scope not in ("all", "selected"):
        raise AccessRefused("Vælg, hvad adgangen skal gælde.")
    allowed = {image.id for image in private_images(owner.id)}
    chosen = {int(i) for i in image_ids if str(i).isdigit() and int(i) in allowed}
    if scope == "selected" and not chosen:
        raise AccessRefused("Vælg mindst ét billede, eller giv adgang til hele albummet.")
    db.session.execute(delete(PrivateAccessImage).where(PrivateAccessImage.access_id == access.id))
    if scope == "selected":
        db.session.add_all([PrivateAccessImage(access_id=access.id, image_id=i) for i in sorted(chosen)])
    access.status, access.scope, access.decided_at = "approved", scope, now_utc()
    db.session.flush()


def decline(owner, access):
    if access.owner_id != owner.id or access.status != "pending":
        raise AccessRefused("Anmodningen findes ikke.")
    access.status, access.decided_at = "declined", now_utc()


def revoke(owner, access):
    if access.owner_id != owner.id or access.status != "approved":
        raise AccessRefused("Adgangen findes ikke.")
    db.session.execute(delete(PrivateAccessImage).where(PrivateAccessImage.access_id == access.id))
    access.status, access.decided_at = "revoked", now_utc()


def remove_between(a_id, b_id):
    """Fjerner al adgang mellem to brugere i begge retninger, fx ved blokering (specifikation 9.2)."""
    db.session.execute(delete(PrivateAccess).where(or_(
        and_(PrivateAccess.owner_id == a_id, PrivateAccess.viewer_id == b_id),
        and_(PrivateAccess.owner_id == b_id, PrivateAccess.viewer_id == a_id),
    )))


def forget_image(image_id):
    """Et billede, der bliver offentligt eller slettes, skal ikke længere stå på listen over udvalgte billeder."""
    db.session.execute(delete(PrivateAccessImage).where(PrivateAccessImage.image_id == image_id))


@dataclass
class OwnerRow:
    access: PrivateAccess
    user: User
    selected: int


def owner_overview(owner):
    """(anmodninger, har adgang) til ejerens side. Brugere, der ikke længere er aktive, vises ikke."""
    rows = db.session.execute(
        select(PrivateAccess, User).join(User, User.id == PrivateAccess.viewer_id)
        .where(PrivateAccess.owner_id == owner.id, PrivateAccess.status.in_(("pending", "approved")),
               User.status == "active")
        .order_by(PrivateAccess.requested_at.desc())
    ).all()
    counts = dict(db.session.execute(
        select(PrivateAccessImage.access_id, func.count()).join(PrivateAccess, PrivateAccess.id == PrivateAccessImage.access_id)
        .where(PrivateAccess.owner_id == owner.id).group_by(PrivateAccessImage.access_id)
    ).all())
    pending = [OwnerRow(a, u, 0) for a, u in rows if a.status == "pending"]
    approved = [OwnerRow(a, u, counts.get(a.id, 0)) for a, u in rows if a.status == "approved"]
    return pending, approved


def pending_count(owner):
    return db.session.scalar(
        select(func.count(PrivateAccess.id)).join(User, User.id == PrivateAccess.viewer_id)
        .where(PrivateAccess.owner_id == owner.id, PrivateAccess.status == "pending", User.status == "active")
    )


def purge_closed(before):
    """Afviste og tilbagetrukne adgange slettes efter opbevaringsperioden (dataminimering)."""
    return db.session.execute(delete(PrivateAccess).where(PrivateAccess.status.in_(("declined", "revoked")),
                                                          PrivateAccess.decided_at < before)).rowcount
