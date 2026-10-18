import secrets

from sqlalchemy import func, select

from ..core.settings import get_setting
from ..core.timeutil import now_utc
from ..extensions import db
from ..profiles.services import can_view_profile
from ..core.features import has_feature
from . import storage
from .models import ProfileImage


def forget_image(image_id):
    from .access import forget_image as forget

    forget(image_id)
from .processing import ImageRejected, process_upload, rotate

MB = 1024 * 1024
# Billeder, der hverken er slettet eller fjernet af en moderator. De andre bevares til sporbarhed, men bruges
# ingen steder for brugerne (et fjernet billede vises kun som en markering for ejeren).
LIVE = (ProfileImage.deleted_at.is_(None)) & (ProfileImage.moderated_at.is_(None))


def list_images(user_id, collection="public"):
    return db.session.scalars(
        select(ProfileImage)
        .where(ProfileImage.user_id == user_id, ProfileImage.collection == collection, LIVE)
        .order_by(ProfileImage.position, ProfileImage.id)
    ).all()


def _count(user_id, *conditions):
    return db.session.scalar(select(func.count(ProfileImage.id)).where(ProfileImage.user_id == user_id, LIVE, *conditions))


def _animated_refusal(user):
    if not has_feature(user, "media.animated"):
        return "Animerede billeder kræver Premium eller Premium+."
    limit = get_setting("max_animated_images")
    if limit is not None and _count(user.id, ProfileImage.is_animated.is_(True)) >= limit:
        return f"Du kan højst have {limit} animerede billeder."
    return None


def upload_image(user, data, collection="public"):
    """Validerer, omkoder og gemmer et billede. Rejser ImageRejected med en besked til brugeren."""
    limit_key = "max_public_images" if collection == "public" else "max_private_images"
    limit = get_setting(limit_key)
    if limit is not None and _count(user.id, ProfileImage.collection == collection) >= limit:
        raise ImageRejected(f"Du kan højst have {limit} billeder her. Slet et billede for at uploade et nyt.")

    processed = process_upload(
        data,
        max_still_bytes=get_setting("max_upload_mb_still") * MB,
        max_animated_bytes=get_setting("max_upload_mb_animated") * MB,
        animated_refusal=_animated_refusal(user),
    )
    next_position = db.session.scalar(
        select(func.coalesce(func.max(ProfileImage.position) + 1, 0)).where(
            ProfileImage.user_id == user.id, ProfileImage.collection == collection, LIVE
        )
    )
    image = ProfileImage(
        public_id=secrets.token_urlsafe(16),
        user_id=user.id,
        collection=collection,
        position=next_position,
        is_animated=processed.animated,
        width=processed.width,
        height=processed.height,
        size_bytes=len(processed.full),
    )
    db.session.add(image)
    db.session.flush()
    storage.save_files(image, processed)
    return image


def owned_image(user, public_id, include_removed=False):
    """Brugerens eget billede. Et billede, en moderator har fjernet, kan kun findes til sletning (include_removed)."""
    alive = ProfileImage.deleted_at.is_(None) if include_removed else LIVE
    return db.session.scalar(select(ProfileImage).where(ProfileImage.public_id == public_id, ProfileImage.user_id == user.id, alive))


def removed_images(user_id, collection):
    """Billeder, en moderator har fjernet, og som ejeren ikke selv har slettet endnu."""
    return db.session.scalars(
        select(ProfileImage).where(ProfileImage.user_id == user_id, ProfileImage.collection == collection,
                                   ProfileImage.moderated_at.isnot(None), ProfileImage.deleted_at.is_(None))
        .order_by(ProfileImage.moderated_at)
    ).all()


def rotate_image(image, clockwise=True):
    """Drejer billedet 90 grader. Fx når et billede er gemt på højkant, selvom det er taget på tværs."""
    if image.is_animated:
        raise ImageRejected("Animerede billeder kan ikke drejes.")
    with open(storage.file_path(image, "stor"), "rb") as fh:
        processed = rotate(fh.read(), clockwise)
    storage.replace_files(image, processed)
    image.width, image.height, image.size_bytes = processed.width, processed.height, len(processed.full)


def delete_image(image):
    """Brugeren sletter et billede. Det forsvinder fra profilen med det samme, men billedet og filerne bevares
    til administration og moderation i opbevaringsperioden (app/maintenance/retention.py)."""
    image.deleted_at = now_utc()
    forget_image(image.id)
    db.session.flush()
    for position, remaining in enumerate(list_images(image.user_id, image.collection)):
        remaining.position = position


class ImageMoveRefused(ValueError):
    """Billedet kan ikke flyttes eller slettes. Beskeden kan vises direkte for brugeren."""


LAST_PUBLIC = ("Du skal have mindst ét offentligt billede for at have private billeder. "
               "Upload eller offentliggør et andet billede først.")


def set_collection(image, collection):
    """Gør et billede privat eller offentligt. Der skal altid være mindst ét offentligt billede, når der er
    private billeder. Billedet lægges sidst i den nye samling, og filerne flyttes med (storage.move_files).
    Kalderen committer; fejler det, skal `undo_move` kaldes, så filerne kommer tilbage."""
    if collection not in ("public", "private") or image.collection == collection:
        return None
    user_id, old = image.user_id, image.collection
    if collection == "private" and _count(user_id, ProfileImage.collection == "public") <= 1:
        raise ImageMoveRefused(LAST_PUBLIC)
    limit = get_setting("max_public_images" if collection == "public" else "max_private_images")
    if limit is not None and _count(user_id, ProfileImage.collection == collection) >= limit:
        raise ImageMoveRefused(f"Du kan højst have {limit} {'offentlige' if collection == 'public' else 'private'} "
                               "billeder. Slet et billede først.")
    image.position = db.session.scalar(
        select(func.coalesce(func.max(ProfileImage.position) + 1, 0))
        .where(ProfileImage.user_id == user_id, ProfileImage.collection == collection, LIVE)
    )
    image.collection = collection
    if collection == "public":
        forget_image(image.id)  # et offentligt billede hører ikke til nogen udvalgt adgang
    db.session.flush()
    for position, remaining in enumerate(list_images(user_id, old)):
        remaining.position = position
    storage.move_files(image.public_id, old, collection)
    return old


def undo_move(public_id, moved_to, old_collection):
    storage.move_files(public_id, moved_to, old_collection)


def delete_refusal(image):
    """Brugeren kan ikke slette sit sidste offentlige billede, så længe der er private billeder.
    Et billede, en moderator har fjernet, kan altid slettes."""
    if image.is_removed:
        return None
    if image.collection == "public" and _count(image.user_id, ProfileImage.collection == "public") <= 1 \
            and _count(image.user_id, ProfileImage.collection == "private") > 0:
        return ("Du skal have mindst ét offentligt billede for at have private billeder. "
                "Gør et privat billede offentligt, eller slet de private billeder først.")
    return None


def make_primary(image):
    others = [i for i in list_images(image.user_id, image.collection) if i.id != image.id]
    image.position = 0
    for position, other in enumerate(others, start=1):
        other.position = position


def avatar_ids(viewer, users):
    """Profilbilledet (første offentlige billede) til små runde billeder i lister, som {bruger-id: public_id}.
    Kun for profiler, som `viewer` må se, så listen aldrig linker til et billede, der giver 404."""
    visible = [u for u in users if u is not None and can_view_profile(viewer, u)]
    if not visible:
        return {}
    rows = db.session.execute(
        select(ProfileImage.user_id, ProfileImage.public_id)
        .where(ProfileImage.user_id.in_([u.id for u in visible]), ProfileImage.collection == "public", LIVE)
        .order_by(ProfileImage.user_id, ProfileImage.position.desc(), ProfileImage.id.desc())
    ).all()
    return {user_id: public_id for user_id, public_id in rows}  # den sidste pr. bruger vinder: position 0


def can_view_image(viewer, image, owner):
    """Adgang kontrolleres ved hver forespørgsel (specifikation 4.3).

    Offentlige billeder følger profilens synlighed. Private billeder kan ses af ejeren og af dem, ejeren har
    givet adgang til hele albummet eller netop dette billede (media/access.py), så længe profilen må ses.
    """
    from .access import has_image_access

    if viewer is None or image.is_deleted or image.is_removed:
        return False  # heller ikke ejeren ser et billede, en moderator har fjernet
    if viewer.id == owner.id:
        return True
    if not can_view_profile(viewer, owner):
        return False
    return image.collection == "public" or has_image_access(viewer.id, image)
