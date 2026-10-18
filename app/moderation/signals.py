"""Automatiske signaler om spam og misbrug (specifikation 8.4 og 13.1).

Et signal opretter en moderationssag med kilden "signal". Der sker ingen automatisk sanktion: en moderator
vurderer sagen (afsnit 9.3). Antallet af signaler pr. konto er den særskilte tæller for spam og misbrug, som
vises i administrationen. Samme slags signal oprettes højst én gang pr. døgn pr. bruger, og der gemmes kun det
nødvendige: et kort uddrag af en besked, aldrig hele samtaler.

Signaler:
- signal_konto: en ny konto bruger en e-mail, der har tilhørt en slettet konto (fx for at få ny Premium)
- signal_spam: den samme besked sendt til mange forskellige samtaler på kort tid
- signal_blokering: mange forskellige brugere har blokeret brugeren på kort tid
- signal_scraping: brugeren har ramt grænsen for, hvor mange profiler eller billeder man kan hente
- signal_interesse: brugeren har åbnet den samme profil usædvanligt mange gange på en uge. Afstanden til profilen
  vises derefter altid som "I nærheden" for brugeren, uanset hvor de bor, så den ikke kan bruges til at finde
  personen, fx ved at sammenligne afstanden fra konti forskellige steder i landet (safety.models.DistanceMask)
"""
from datetime import timedelta

from sqlalchemy import func, select

from ..core.timeutil import now_utc
from ..extensions import db
from .models import ModerationCase

SIGNAL_LABELS = {
    "signal_konto": "Signal: ny konto med e-mail fra slettet konto",
    "signal_spam": "Signal: samme besked til mange",
    "signal_blokering": "Signal: blokeret af mange",
    "signal_scraping": "Signal: mange profilvisninger",
    "signal_interesse": "Signal: usædvanlig interesse for én profil",
}
DEDUPE = timedelta(days=1)

MASS_MESSAGE_RECIPIENTS = 5
MASS_MESSAGE_WINDOW = timedelta(hours=1)
MASS_MESSAGE_MIN_LENGTH = 30  # korte hilsner som "Hej" sendt til flere matches er helt normale

BLOCK_WAVE_COUNT = 3
BLOCK_WAVE_WINDOW = timedelta(days=7)

SAMPLE_LENGTH = 300

INTEREST_WINDOW_DAYS = 7
VISIT_GAP = timedelta(minutes=5)  # flere visninger inden for 5 minutter tæller som én (fx frem og tilbage)
VISIT_KEEP_DAYS = 30  # besøgstællingerne slettes derefter (maintenance/retention.py)


def signal_count(user):
    """Den særskilte tæller for spam og misbrug pr. konto: antal signaler i alt."""
    return db.session.scalar(select(func.count(ModerationCase.id)).where(
        ModerationCase.subject_user_id == user.id, ModerationCase.source == "signal"))


def raise_signal(user, category, description, evidence=None):
    """Opretter en sag med kilden "signal", medmindre samme slags signal allerede findes fra det seneste døgn."""
    from .services import create_case

    recent = db.session.scalar(select(ModerationCase.id).where(
        ModerationCase.subject_user_id == user.id, ModerationCase.source == "signal",
        ModerationCase.category == category, ModerationCase.created_at > now_utc() - DEDUPE).limit(1))
    if recent is not None:
        return None
    return create_case(source="signal", category=category, subject=user, description=description, evidence=evidence)


def _normalized(text):
    return " ".join((text or "").lower().split())


def check_mass_message(sender, body):
    """Samme besked (uden hensyn til store bogstaver og mellemrum) sendt til mange forskellige samtaler."""
    from ..messaging.models import Message

    normalized = _normalized(body)
    if len(normalized) < MASS_MESSAGE_MIN_LENGTH:
        return None
    recipients = db.session.scalar(
        select(func.count(func.distinct(Message.conversation_id))).where(
            Message.sender_id == sender.id, Message.created_at > now_utc() - MASS_MESSAGE_WINDOW,
            func.lower(func.regexp_replace(func.trim(Message.body), r"\s+", " ", "g")) == normalized)
    )
    if recipients < MASS_MESSAGE_RECIPIENTS:
        return None
    return raise_signal(sender, "signal_spam",
                        f"Samme besked sendt til {recipients} forskellige samtaler inden for en time.",
                        {"type": "signal", "recipients": recipients, "sample": normalized[:SAMPLE_LENGTH],
                         "captured_at": now_utc().isoformat()})


def check_block_wave(user):
    """Mange forskellige brugere har blokeret `user` inden for en uge."""
    from ..safety.models import UserBlock

    blockers = db.session.scalar(select(func.count(UserBlock.blocker_id)).where(
        UserBlock.blocked_id == user.id, UserBlock.created_at > now_utc() - BLOCK_WAVE_WINDOW))
    if blockers < BLOCK_WAVE_COUNT:
        return None
    return raise_signal(user, "signal_blokering", f"Blokeret af {blockers} forskellige brugere inden for en uge.",
                        {"type": "signal", "blockers": blockers, "captured_at": now_utc().isoformat()})


def check_new_account(user, previous):
    """En ny konto bruger en e-mail, der har tilhørt en eller flere slettede konti."""
    if not previous:
        return None
    return raise_signal(user, "signal_konto",
                        f"Ny konto med samme e-mail som {len(previous)} slettet konto/konti.",
                        {"type": "signal", "previous_user_ids": [p.id for p in previous],
                         "captured_at": now_utc().isoformat()})


def is_masked(viewer_id, target_id):
    from ..safety.models import DistanceMask

    return db.session.get(DistanceMask, (viewer_id, target_id)) is not None


def record_visit(viewer, target):
    """Tæller et besøg på `target`s profil og opretter et signal og en afstandsmaske, hvis `viewer` har åbnet
    profilen usædvanligt mange gange på en uge. Kalderen committer."""
    from ..core.settings import get_setting
    from ..core.timeutil import today_local
    from ..safety.models import DistanceMask, ProfileVisit

    if viewer.id == target.id:
        return
    now, today = now_utc(), today_local()
    visit = db.session.get(ProfileVisit, (viewer.id, target.id, today))
    if visit is None:
        db.session.add(ProfileVisit(viewer_id=viewer.id, target_id=target.id, day=today, views=1, last_view_at=now))
    elif now - visit.last_view_at >= VISIT_GAP:
        visit.views += 1
        visit.last_view_at = now
    else:
        return
    db.session.flush()

    threshold = get_setting("interest_signal_views")
    if not threshold or is_masked(viewer.id, target.id):
        return
    views = db.session.scalar(select(func.sum(ProfileVisit.views)).where(
        ProfileVisit.viewer_id == viewer.id, ProfileVisit.target_id == target.id,
        ProfileVisit.day > today - timedelta(days=INTEREST_WINDOW_DAYS)))
    if views < threshold:
        return
    case = raise_signal(viewer, "signal_interesse",
                        f"Har åbnet den samme profil {views} gange på {INTEREST_WINDOW_DAYS} dage. Afstanden til "
                        "profilen vises nu altid som \"I nærheden\" for brugeren.",
                        {"type": "signal", "target_user_id": target.id, "views": views,
                         "captured_at": now.isoformat()})
    db.session.add(DistanceMask(viewer_id=viewer.id, target_id=target.id, case_id=case.id if case else None))
    db.session.flush()


def scraping_breach(limit):
    """Kaldes af Flask-Limiter, når en bruger rammer grænsen for profilvisninger, Udforsk eller billeder."""
    from flask import g, request

    user = g.get("user")
    if user is not None:
        raise_signal(user, "signal_scraping", f"Ramte grænsen {limit.limit} for {request.endpoint}.",
                     {"type": "signal", "endpoint": request.endpoint, "limit": str(limit.limit),
                      "captured_at": now_utc().isoformat()})
        db.session.commit()
    return None
