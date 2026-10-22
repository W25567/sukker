"""Ansøgninger om at blive moderator.

- Alle aktive brugere med bekræftet e-mail kan søge, én ansøgning ad gangen. Den kan trækkes tilbage.
- En administrator godkender eller afviser. Godkendes den, får brugeren rollen moderator, og ændringen står i
  administrationsloggen. Brugeren får svaret som besked fra Administrator.
- Administrator kan også fjerne en moderator igen (rollen bruger) under Ansøgninger eller på brugerens side.
"""
from sqlalchemy import func, select

from ..core.timeutil import now_utc
from ..extensions import db
from .models import ModeratorApplication

MAX_TEXT = 2000


class ApplicationRefused(ValueError):
    pass


def pending_for(user):
    return db.session.scalar(select(ModeratorApplication).where(
        ModeratorApplication.user_id == user.id, ModeratorApplication.status == "pending"))


def latest_for(user):
    return db.session.scalar(select(ModeratorApplication).where(ModeratorApplication.user_id == user.id)
                             .order_by(ModeratorApplication.id.desc()).limit(1))


def cannot_apply(user):
    """Hvorfor brugeren ikke kan søge lige nu, eller None."""
    if user.role != "user":
        return "Du er allerede en del af teamet."
    if user.status != "active":
        return "Din konto er begrænset, så du kan ikke søge lige nu."
    if not user.is_verified:
        return "Bekræft din e-mail, før du søger."
    if pending_for(user) is not None:
        return "Du har allerede en ansøgning, der venter på svar."
    return None


def apply(user, motivation, experience, availability):
    reason = cannot_apply(user)
    if reason:
        raise ApplicationRefused(reason)
    motivation = (motivation or "").strip()
    if not motivation:
        raise ApplicationRefused("Fortæl, hvorfor du gerne vil være moderator.")
    if len(motivation) > MAX_TEXT or len(experience or "") > MAX_TEXT:
        raise ApplicationRefused(f"Højst {MAX_TEXT} tegn pr. felt.")
    application = ModeratorApplication(user_id=user.id, motivation=motivation,
                                       experience=(experience or "").strip() or None,
                                       availability=(availability or "").strip()[:200] or None)
    db.session.add(application)
    return application


def withdraw(user):
    application = pending_for(user)
    if application is not None:
        application.status, application.decided_at = "withdrawn", now_utc()


def decide(admin, application, approve, note=None):
    """Godkender (brugeren bliver moderator) eller afviser. Brugeren får svaret som besked fra Administrator."""
    from ..admin.services import AdminError, audit, set_role
    from ..auth.models import User
    from ..messaging.admin_threads import send_from_admin

    if application.status != "pending":
        raise ApplicationRefused("Ansøgningen er allerede afgjort.")
    applicant = db.session.get(User, application.user_id)
    note = (note or "").strip() or None
    if approve:
        if applicant.status != "active" or applicant.role != "user":
            raise ApplicationRefused("Ansøgeren er ikke længere en aktiv bruger og kan ikke blive moderator.")
        try:
            set_role(admin, applicant, "moderator")
        except AdminError as error:
            raise ApplicationRefused(str(error))
        text = ("Din ansøgning om at blive moderator er godkendt. Velkommen i teamet! Du finder Moderation i menuen. "
                "Husk, at al adgang til brugeres oplysninger registreres, og at du har tavshedspligt om det, du ser.")
    else:
        text = "Tak for din ansøgning om at blive moderator. Vi har valgt ikke at gå videre med den denne gang."
        if note:
            text += f" {note}"
    application.status = "approved" if approve else "rejected"
    application.decided_at, application.decided_by_id, application.decision_note = now_utc(), admin.id, note
    audit(admin, "moderator_application", applicant, application_id=application.id, approved=approve, note=note)
    if not applicant.is_deleted:
        send_from_admin(admin, applicant, text)


def pending_count():
    return db.session.scalar(select(func.count(ModeratorApplication.id))
                             .where(ModeratorApplication.status == "pending"))


def all_applications():
    """Ventende først, derefter de nyeste."""
    return db.session.scalars(select(ModeratorApplication).order_by(
        (ModeratorApplication.status == "pending").desc(), ModeratorApplication.created_at.desc())).all()
