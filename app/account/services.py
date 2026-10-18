"""Sletning af en konto (specifikation 13.1 og 7.2).

Sletning sker i to trin, så en bruger ikke kan skjule sine spor ved at slette sig, fx under en anmeldelse
eller en politiefterforskning:

1. Når brugeren sletter sin profil, lukkes kontoen med det samme. Den kan ikke længere logge ind og vises
   ikke for andre: ikke i Udforsk, ikke som profil og ikke med navn eller billede. Samtaler, brugeren har haft,
   kan stadig læses af modparten, med afsenderen vist som "Slettet bruger". Alle data – profil, billeder,
   beskeder, likes, matches, sager og administrator-samtaler – bevares og kan ses af administration og
   moderation. E-mailen flyttes til `deleted_email`, så adressen bliver fri til en ny konto.
2. Efter opbevaringsperioden (`data_retention_months`) slettes kontoen endeligt med alle data og billedfiler
   (purge_deleted_accounts). Det sker ikke, så længe brugeren er part i en sag, der ikke er afsluttet, eller
   hvor opbevaringen er forlænget. Sagerne selv slettes aldrig.
"""
from sqlalchemy import delete, or_, select, update

from ..admin.models import AdminAuditLog
from ..auth.models import EmailToken, User
from ..auth.sessions import revoke_sessions
from ..core.settings import get_setting
from ..core.timeutil import DANISH_TZ, add_months, now_utc, today_local
from ..extensions import db
from ..matches.models import Match
from ..media import storage
from ..media.models import ProfileImage
from ..moderation.models import CaseEvent, ModerationCase

SYSTEM = (0, "System")


def tombstone_email(user):
    return f"slettet-{user.id}@slettet.invalid"


def delete_account(user):
    """Lukker kontoen og skjuler den for alle andre. Data bevares i opbevaringsperioden (se modulets beskrivelse)."""
    now = now_utc()
    user.deleted_email = user.email
    user.email = tombstone_email(user)
    user.password_hash = "!slettet"  # kan aldrig verificeres
    user.status = "deleted"
    user.status_changed_at = now
    user.deleted_at = now
    revoke_sessions(user.id)
    db.session.execute(delete(EmailToken).where(EmailToken.user_id == user.id))
    # Aktive matches ophører, så modparten ikke længere ser brugeren som match. Selve matchet og samtalen
    # bevares, og samtalen kan stadig læses af modparten.
    db.session.execute(
        update(Match)
        .where(or_(Match.user_low_id == user.id, Match.user_high_id == user.id), Match.removed_at.is_(None))
        .values(removed_at=now, removed_by_id=user.id)
    )
    open_cases = db.session.scalars(
        select(ModerationCase).where(ModerationCase.status != "closed",
                                     or_(ModerationCase.subject_user_id == user.id, ModerationCase.reporter_id == user.id))
    ).all()
    for case in open_cases:
        role = "anmeldte" if case.subject_user_id == user.id else "anmelder"
        db.session.add(CaseEvent(case_id=case.id, actor_id=user.id, kind="note",
                                 text=f"Brugeren ({role}) slettede sin profil. Data bevares, så sagen kan behandles."))
    # Den permanente log kan ikke ændres eller slettes og har ingen fremmednøgler, så sletningen kan spores,
    # også efter at kontoen er slettet endeligt. E-mailen skrives ikke i loggen.
    images = db.session.scalar(select(db.func.count(ProfileImage.id)).where(ProfileImage.user_id == user.id))
    db.session.add(AdminAuditLog(admin_id=user.id, admin_username=user.display_name, target_user_id=user.id,
                                 target_username=user.display_name, action="account_deleted",
                                 details={"billeder": images, "aabne_sager": len(open_cases)}))
    db.session.flush()


def held_user_ids():
    """Brugere, der ikke må slettes endeligt endnu: part i en sag, der ikke er afsluttet, eller hvor
    opbevaringen er forlænget."""
    today = today_local()
    held = or_(ModerationCase.status != "closed", ModerationCase.evidence_retained_until >= today)
    subjects = select(ModerationCase.subject_user_id).where(held, ModerationCase.subject_user_id.isnot(None))
    reporters = select(ModerationCase.reporter_id).where(held, ModerationCase.reporter_id.isnot(None))
    return subjects.union(reporters)


def retention_status(user):
    """(dato for endelig sletning, holdes tilbage af en sag) for en slettet konto. (None, False) ellers."""
    if not user.is_deleted or user.deleted_at is None:
        return None, False
    purge_after = add_months(user.deleted_at, get_setting("data_retention_months")).astimezone(DANISH_TZ).date()
    on_hold = db.session.scalar(select(db.literal(user.id).in_(held_user_ids())))
    return purge_after, bool(on_hold)


def purge_deleted_accounts(before):
    """Slettede konti, der har været slettet i hele opbevaringsperioden, slettes endeligt med alle data.
    Billedfilerne returneres, så de kan slettes efter commit."""
    users = db.session.scalars(
        select(User).where(User.status == "deleted", User.deleted_at < before, User.id.not_in(held_user_ids()))
    ).all()
    files = []
    for user in users:
        files += db.session.scalars(select(ProfileImage).where(ProfileImage.user_id == user.id)).all()
        for case_id in db.session.scalars(select(ModerationCase.id).where(
                or_(ModerationCase.subject_user_id == user.id, ModerationCase.reporter_id == user.id))):
            db.session.add(CaseEvent(case_id=case_id, kind="purge",
                                     text=f"Brugerens data er slettet endeligt efter opbevaringsperioden (bruger-id {user.id})."))
        db.session.add(AdminAuditLog(admin_id=SYSTEM[0], admin_username=SYSTEM[1], target_user_id=user.id,
                                     target_username=user.display_name, action="account_purged",
                                     details={"slettet": user.deleted_at.isoformat()}))
        db.session.delete(user)
    db.session.flush()
    return len(users), [(image.collection, image.public_id) for image in files]


def purge_deleted_images(before):
    """Billeder, brugeren selv har slettet, slettes endeligt efter opbevaringsperioden, medmindre brugeren er
    part i en sag, der holder data tilbage."""
    images = db.session.scalars(
        select(ProfileImage).where(ProfileImage.deleted_at < before, ProfileImage.user_id.not_in(held_user_ids()))
    ).all()
    files = [(image.collection, image.public_id) for image in images]
    for image in images:
        db.session.delete(image)
    db.session.flush()
    return files


def delete_files(files):
    for collection, public_id in files:
        storage.delete_files_for(collection, public_id)
