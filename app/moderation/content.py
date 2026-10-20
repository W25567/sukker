"""Fjernelse af upassende indhold (specifikation 4.5, 9.3 og 11).

- Moderatorer og administratorer kan fjerne et upassende billede, også uden at nogen har anmeldt det.
  Billedet forsvinder for alle, også ejeren, som i stedet ser en markering "Fjernet af moderator" og selv kan
  slette markeringen. Filen bevares til sporbarhed efter opbevaringsreglen.
- Administratorer kan slette et upassende visningsnavn. Navnet erstattes med REMOVED_NAME, indtil brugeren
  vælger et nyt.
- Hver fjernelse oprettes som en afsluttet moderationssag med en kopi af materialet, så brugeren kan klage, og så
  handlingen kan spores. Brugeren får en kort besked fra Administrator.
"""
from ..brands import current_brand
from ..core.timeutil import now_utc
from ..extensions import db
from ..media.access import forget_image
from ..media.services import list_images
from . import services

REMOVED_NAME = "Navn slettet af moderator"

IMAGE_REASONS = [
    ("seksuelt", "Seksuelt eller udfordrende indhold"),
    ("upassende", "Upassende eller stødende indhold"),
    ("andre", "Viser andre uden samtykke eller er ikke af brugeren"),
    ("kontakt", "Indeholder kontaktoplysninger eller reklame"),
    ("andet", "Andet brud på reglerne"),
]
IMAGE_REASON_LABELS = dict(IMAGE_REASONS)


class ContentError(ValueError):
    pass


def _notify(actor, user, body):
    """Kort besked til brugeren i samtalen med Administrator. Fejler stille, hvis det ikke kan lade sig gøre
    (fx hvis den, der fjerner, er brugeren selv)."""
    from ..messaging import admin_threads

    try:
        admin_threads.send_from_admin(actor, user, body)
    except admin_threads.AdminMessageRefused:
        pass


def remove_image(actor, image, owner, reason):
    """Fjerner et upassende billede. Returnerer den oprettede sag."""
    if image.is_removed or image.is_deleted:
        raise ContentError("Billedet er allerede fjernet.")
    if reason not in IMAGE_REASON_LABELS:
        raise ContentError("Vælg en begrundelse.")
    label = IMAGE_REASON_LABELS[reason]
    case = services.create_case(
        source="moderator", category="indhold", subject=owner, actor=actor,
        description=f"Billede fjernet uden anmeldelse: {label}",
        evidence={"type": "image", "user_id": owner.id, "username": owner.display_name,
                  "collection": image.collection, "images": [image.public_id], "reason": label,
                  "captured_at": now_utc().isoformat()},
    )
    image.moderated_at, image.moderated_by_id, image.moderation_reason = now_utc(), actor.id, label
    forget_image(image.id)  # et fjernet billede indgår ikke i nogen udvalgt adgang til private billeder
    db.session.flush()
    for position, remaining in enumerate(list_images(owner.id, image.collection)):
        remaining.position = position
    services.add_event(case, actor, "decision", f"content_removed: billede fjernet ({label})")
    case.decision, case.decision_note = "content_removed", label
    case.status, case.closed_at = "closed", now_utc()
    _notify(actor, owner, f"Et af dine billeder er fjernet, fordi det ikke overholder reglerne på {current_brand().name} "
                          f"({label.lower()}). Det kan ikke længere ses af andre. Du kan slette markeringen under "
                          "Min profil. Er du uenig, kan du svare her eller klage over afgørelsen.")
    return case


def removal_case(image):
    """Sagen, hvor billedet blev fjernet (afgørelsen content_removed med billedet som bevis)."""
    from sqlalchemy import select

    from .models import ModerationCase

    cases = db.session.scalars(select(ModerationCase).where(
        ModerationCase.subject_user_id == image.user_id, ModerationCase.decision == "content_removed")
        .order_by(ModerationCase.created_at.desc())).all()
    return next((c for c in cases if image.public_id in (c.evidence or {}).get("images", [])), None)


def restore_image(actor, image, owner, note=None):
    """Genskaber et billede, en moderator har fjernet, fx når brugeren har klaget og fået medhold. Billedet lægges
    sidst i sin samling. Et billede, brugeren selv har slettet, kan ikke genskabes."""
    from sqlalchemy import func, select

    from ..media.models import ProfileImage

    if not image.is_removed:
        raise ContentError("Billedet er ikke fjernet.")
    if image.is_deleted:
        raise ContentError("Brugeren har selv slettet billedet, så det kan ikke genskabes.")
    image.moderated_at, image.moderated_by_id, image.moderation_reason = None, None, None
    image.position = db.session.scalar(
        select(func.coalesce(func.max(ProfileImage.position) + 1, 0)).where(
            ProfileImage.user_id == owner.id, ProfileImage.collection == image.collection,
            ProfileImage.deleted_at.is_(None), ProfileImage.moderated_at.is_(None), ProfileImage.id != image.id))
    note = (note or "").strip()
    case = removal_case(image)
    if case is not None:
        services.add_event(case, actor, "restored", "Billedet er genskabt" + (f": {note}" if note else ""))
    _notify(actor, owner, "Et billede, der var fjernet fra din profil, er genskabt efter en ny vurdering. Det kan igen "
                          "ses på din profil. Vi beklager.")
    return case


def remove_display_name(admin, user, note=None):
    """Sletter et upassende visningsnavn. Det oprindelige navn står i administrationsloggen."""
    from ..admin.services import audit

    if user.display_name == REMOVED_NAME:
        raise ContentError("Navnet er allerede slettet.")
    original = user.display_name
    # Loggen gemmer navnet, som det var, så den skrives før navnet ændres.
    audit(admin, "name_removed", user, original=original, note=(note or "").strip() or None)
    user.display_name = REMOVED_NAME
    user.name_removed_at = now_utc()
    _notify(admin, user, f"Dit visningsnavn er slettet, fordi det ikke overholder reglerne på {current_brand().name}. "
                         "Vælg et nyt navn under Konto. Andre ser nu \"" + REMOVED_NAME + "\".")
    return original
