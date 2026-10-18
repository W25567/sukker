"""Moderationssager, afgørelser og moderatoradgang (specifikation 9.1 og 9.3)."""
import os
import shutil
from datetime import timedelta

from flask import current_app
from sqlalchemy import select

from ..auth.models import User
from ..auth.sessions import revoke_sessions
from ..core.timeutil import now_utc, today_local
from ..extensions import db
from ..media import storage
from ..media.services import list_images
from ..profiles.fields import FIELDS, display_value
from ..profiles.models import Profile
from ..profiles.services import age_of
from .models import HIGH_PRIORITY, CaseEvent, ModerationCase, ModeratorAccessLog

ACCESS_WINDOW = timedelta(hours=2)
EVIDENCE_MESSAGES = 50
EVIDENCE_IMAGES = 10


class ModerationError(ValueError):
    pass


# --- Sager -------------------------------------------------------------------------------------------


def create_case(*, source, category, subject=None, reporter=None, actor=None, description=None, evidence=None):
    case = ModerationCase(
        source=source,
        category=category,
        priority="high" if category in HIGH_PRIORITY else "normal",
        subject_user_id=subject.id if subject else None,
        reporter_id=reporter.id if reporter else None,
        description=description,
        evidence=evidence,
    )
    db.session.add(case)
    db.session.flush()
    add_event(case, actor or reporter, "created", f"Sag oprettet ({source})")
    if evidence and evidence.get("images"):
        case.evidence = {**evidence, "copied_images": _copy_evidence_images(case, evidence["images"])}
    return case


def add_event(case, actor, kind, text=None):
    db.session.add(CaseEvent(case_id=case.id, actor_id=actor.id if actor else None, kind=kind, text=text))


def assign(case, moderator):
    case.assigned_to_id = moderator.id
    if case.status == "open":
        case.status = "in_progress"
    add_event(case, moderator, "assigned", f"Tildelt {moderator.display_name}")


def add_note(case, moderator, text):
    add_event(case, moderator, "note", text)


def decide(case, moderator, decision, note):
    """Afgør sagen og udfører en eventuel kontohandling. Sagen lukkes."""
    subject = db.session.get(User, case.subject_user_id) if case.subject_user_id else None
    if decision in ("restricted", "suspended", "lifted") and subject is None:
        raise ModerationError("Sagen har ingen bruger, kontohandlingen kan udføres på.")
    if subject is not None and subject.id == moderator.id:
        raise ModerationError("Du kan ikke træffe afgørelse i en sag om dig selv.")
    if decision in ("restricted", "suspended", "lifted"):
        if subject.is_deleted:
            add_event(case, moderator, "note", "Brugeren har slettet sin profil. Kontoen er lukket, så status ændres ikke.")
        set_account_status(subject, {"restricted": "restricted", "suspended": "suspended", "lifted": "active"}[decision])
    case.decision, case.decision_note = decision, note
    case.status, case.closed_at = "closed", now_utc()
    add_event(case, moderator, "decision", f"{decision}: {note}" if note else decision)


def set_account_status(user, status):
    if user.is_deleted:
        return  # en slettet konto forbliver slettet; afgørelsen står i sagen
    user.status, user.status_changed_at = status, now_utc()
    if status == "suspended":
        revoke_sessions(user.id)


MAX_RETENTION = timedelta(days=366)


def retain_evidence(case, moderator, until, reason):
    """Forlænger opbevaringen af beviser i en alvorlig sag, fx ved politianmeldelse (specifikation 13.1).

    Højst et år ad gangen, så behovet bliver vurderet igen mindst hvert år.
    """
    today = today_local()
    if case.evidence_purged_at is not None:
        raise ModerationError("Beviserne i sagen er allerede slettet.")
    if not today < until <= today + MAX_RETENTION:
        raise ModerationError("Vælg en dato i fremtiden, højst et år frem.")
    case.evidence_retained_until = until
    add_event(case, moderator, "retention", f"Beviser opbevares til {until.strftime('%d-%m-%Y')}: {reason}")


def reopen(case, moderator):
    case.status, case.closed_at = "in_progress", None
    add_event(case, moderator, "status", "Sagen er genåbnet")


def queue(status_filter=("open", "in_progress")):
    return db.session.scalars(
        select(ModerationCase).where(ModerationCase.status.in_(status_filter))
        .order_by((ModerationCase.priority == "high").desc(), ModerationCase.created_at)
    ).all()


def case_events(case):
    return db.session.execute(
        select(CaseEvent, User.display_name).outerjoin(User, User.id == CaseEvent.actor_id)
        .where(CaseEvent.case_id == case.id).order_by(CaseEvent.at, CaseEvent.id)
    ).all()


# --- Kopi af anmeldt materiale -------------------------------------------------------------------------


def profile_evidence(user):
    profile = db.session.get(Profile, user.id)
    fields = {f.label: display_value(profile, f) for f in FIELDS if profile and display_value(profile, f)}
    return {
        "type": "profile",
        "user_id": user.id,
        "username": user.display_name,
        "age": age_of(user),
        "fields": fields,
        "hidden_fields": list(profile.hidden_fields) if profile else [],
        "images": [image.public_id for image in list_images(user.id)][:EVIDENCE_IMAGES],
        "captured_at": now_utc().isoformat(),
    }


def message_evidence(message, sender):
    return {
        "type": "message",
        "message_id": message.id,
        "conversation_id": message.conversation_id,
        "sender_id": sender.id if sender else None,
        "sender_username": sender.display_name if sender else None,
        "body": message.body,
        "sent_at": message.created_at.isoformat(),
        "captured_at": now_utc().isoformat(),
    }


def conversation_evidence(conversation, messages, usernames):
    recent = messages[-EVIDENCE_MESSAGES:]
    return {
        "type": "conversation",
        "conversation_id": conversation.id,
        "messages": [
            {"id": m.id, "sender": usernames.get(m.sender_id, "Slettet bruger"), "body": m.body, "sent_at": m.created_at.isoformat()}
            for m in recent
        ],
        "captured_at": now_utc().isoformat(),
    }


def _evidence_dir(case):
    return os.path.join(current_app.config["MEDIA_ROOT"], "evidence", str(case.id))


def _copy_evidence_images(case, public_ids):
    """Kopierer de anmeldte billeder, så de bevares, selvom brugeren sletter dem."""
    from ..media.models import ProfileImage

    target = _evidence_dir(case)
    copied = []
    for public_id in public_ids:
        image = db.session.scalar(select(ProfileImage).where(ProfileImage.public_id == public_id))
        if image is None or not os.path.exists(storage.file_path(image, "stor")):
            continue
        os.makedirs(target, exist_ok=True)
        shutil.copyfile(storage.file_path(image, "stor"), os.path.join(target, f"{public_id}.webp"))
        copied.append(public_id)
    return copied


def evidence_image_path(case, public_id):
    path = os.path.join(_evidence_dir(case), f"{public_id}.webp")
    return path if public_id in (case.evidence or {}).get("copied_images", []) and os.path.exists(path) else None


# --- Moderatoradgang og revisionslog ------------------------------------------------------------------------


def log_access(moderator, target, reason, case=None):
    """Registrerer, at en moderator åbner en brugers data. Kræver en begrundelse (specifikation 9.3)."""
    reason = (reason or "").strip()
    if not reason:
        raise ModerationError("Skriv en begrundelse for adgangen.")
    entry = ModeratorAccessLog(
        moderator_id=moderator.id, moderator_username=moderator.display_name,
        target_user_id=target.id, target_username=target.display_name,
        case_id=case.id if case else None, reason=reason,
    )
    db.session.add(entry)
    if case is not None:
        add_event(case, moderator, "access", f"Åbnede {target.display_name}s data: {reason}")
    return entry


def has_recent_access(moderator, target_id):
    """Har moderatoren en registreret adgang til brugeren inden for de seneste to timer?"""
    return db.session.scalar(
        select(ModeratorAccessLog.id).where(
            ModeratorAccessLog.moderator_id == moderator.id,
            ModeratorAccessLog.target_user_id == target_id,
            ModeratorAccessLog.at > now_utc() - ACCESS_WINDOW,
        ).limit(1)
    ) is not None


def access_log(limit=500):
    return db.session.scalars(select(ModeratorAccessLog).order_by(ModeratorAccessLog.at.desc()).limit(limit)).all()
