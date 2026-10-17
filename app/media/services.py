import secrets

from sqlalchemy import func, select

from ..core.settings import get_setting
from ..extensions import db
from ..profiles.services import can_view_profile
from ..core.features import has_feature
from . import storage
from .models import ProfileImage
from .processing import ImageRejected, process_upload

MB = 1024 * 1024


def list_images(user_id, collection="public"):
    return db.session.scalars(
        select(ProfileImage)
        .where(ProfileImage.user_id == user_id, ProfileImage.collection == collection)
        .order_by(ProfileImage.position, ProfileImage.id)
    ).all()


def _count(user_id, *conditions):
    return db.session.scalar(select(func.count(ProfileImage.id)).where(ProfileImage.user_id == user_id, *conditions))


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
            ProfileImage.user_id == user.id, ProfileImage.collection == collection
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


def owned_image(user, public_id):
    return db.session.scalar(select(ProfileImage).where(ProfileImage.public_id == public_id, ProfileImage.user_id == user.id))


def delete_image(image):
    collection, user_id = image.collection, image.user_id
    db.session.delete(image)
    db.session.flush()
    storage.delete_files(image)
    for position, remaining in enumerate(list_images(user_id, collection)):
        remaining.position = position


def make_primary(image):
    others = [i for i in list_images(image.user_id, image.collection) if i.id != image.id]
    image.position = 0
    for position, other in enumerate(others, start=1):
        other.position = position


def can_view_image(viewer, image, owner):
    """Adgang kontrolleres ved hver forespørgsel (specifikation 4.3).

    Offentlige billeder følger profilens synlighed. Private arkiver kommer i fase 2 og er indtil da
    kun synlige for ejeren.
    """
    if viewer is None:
        return False
    if image.collection == "public":
        return can_view_profile(viewer, owner)
    return viewer.id == owner.id
