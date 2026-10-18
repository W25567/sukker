"""Endelig sletning af data, der ikke længere er synlige eller i brug (specifikation 13.1).

Generel regel: Skjulte og slettede data slettes senest `data_retention_months` efter, at de blev skjult
eller slettet. Kører dagligt fra sukker-maintenance.timer. Alle trin kan køres igen uden skade.
"""
import os
import shutil
import time
from datetime import timedelta

from flask import current_app
from sqlalchemy import and_, delete, exists, not_, or_, select

from ..account.services import delete_files, purge_deleted_accounts, purge_deleted_images
from ..media.access import purge_closed as purge_closed_private_access
from ..support.services import purge_closed as purge_closed_bug_reports
from ..auth.models import EmailToken, UserSession
from ..core.settings import get_setting
from ..core.timeutil import add_months, now_utc, today_local
from ..extensions import db
from ..media.models import ProfileImage
from ..messaging.models import Conversation, ConversationMember, Message, MessageDeletion
from ..moderation.models import CaseEvent, ModerationCase

# Filer, der netop er ved at blive skrevet, må ikke ryddes væk. Upload flytter en færdig mappe på plads.
FILE_GRACE_SECONDS = 3600
TOKEN_KEEP = timedelta(days=7)
REVOKED_SESSION_KEEP = timedelta(days=30)


def cutoff():
    return add_months(now_utc(), -get_setting("data_retention_months"))


def purge_hidden_conversations(before):
    """Samtaler fra fjernede matches (eller blokeringer), der har været skjult i hele perioden (afsnit 7.2).
    Ved et senere match starter en ny, tom samtale."""
    result = db.session.execute(delete(Conversation).where(Conversation.hidden_at < before))
    return result.rowcount


def purge_orphaned_conversations():
    """Samtaler, hvor begge deltagere har slettet deres profil. Ingen kan længere se dem."""
    no_members = ~exists().where(ConversationMember.conversation_id == Conversation.id)
    return db.session.execute(delete(Conversation).where(no_members)).rowcount


def purge_deleted_messages(before):
    """Beskeder, som alle tilbageværende deltagere har slettet eller ryddet for længe nok siden.

    Sletning gælder kun for den, der sletter (afsnit 7.2). Så længe modparten kan se beskeden, er den i brug.
    """
    member = ConversationMember
    # Korrelationen skal være eksplicit: underforespørgslerne henviser til både beskeden i DELETE og deltageren.
    deleted_by_member = (
        select(MessageDeletion.message_id)
        .where(MessageDeletion.message_id == Message.id, MessageDeletion.user_id == member.user_id,
               MessageDeletion.deleted_at < before)
        .correlate_except(MessageDeletion).exists()
    )
    hidden_for_member = or_(
        deleted_by_member,
        and_(member.cleared_before.isnot(None), Message.created_at <= member.cleared_before,
             member.cleared_before < before),
    )
    still_visible_to_someone = (
        select(member.user_id).where(member.conversation_id == Message.conversation_id, not_(hidden_for_member))
        .correlate_except(member).exists()
    )
    has_members = select(member.user_id).where(member.conversation_id == Message.conversation_id).correlate_except(member).exists()
    stmt = delete(Message).where(has_members, ~still_visible_to_someone)
    return db.session.execute(stmt.execution_options(synchronize_session=False)).rowcount


def purge_case_evidence(before):
    """Beviser fra afsluttede sager (afsnit 9.1). Selve sagen, afgørelsen og historikken bevares.
    Sager med forlænget opbevaring springes over, til datoen er passeret."""
    today = today_local()
    cases = db.session.scalars(
        select(ModerationCase).where(
            ModerationCase.status == "closed",
            ModerationCase.closed_at < before,
            ModerationCase.evidence_purged_at.is_(None),
            or_(ModerationCase.evidence_retained_until.is_(None), ModerationCase.evidence_retained_until < today),
        )
    ).all()
    for case in cases:
        case.evidence = None
        case.description = None
        case.evidence_purged_at = now_utc()
        db.session.add(CaseEvent(case_id=case.id, kind="purge", text="Beviser slettet efter opbevaringsreglen"))
    return len(cases)  # billedkopierne slettes af purge_orphan_files efter commit


def purge_sessions_and_tokens():
    now = now_utc()
    expired = now - current_app.config["PERMANENT_SESSION_LIFETIME"]
    sessions = db.session.execute(
        delete(UserSession).where(or_(UserSession.last_seen_at < expired,
                                      UserSession.revoked_at < now - REVOKED_SESSION_KEEP))
    ).rowcount
    tokens = db.session.execute(
        delete(EmailToken).where(or_(EmailToken.expires_at < now - TOKEN_KEEP, EmailToken.used_at < now - TOKEN_KEEP))
    ).rowcount
    return sessions, tokens


def _old_enough(path):
    try:
        return time.time() - os.path.getmtime(path) > FILE_GRACE_SECONDS
    except OSError:
        return False


def purge_orphan_files():
    """Billedmapper uden et billede i databasen, efterladte midlertidige mapper og bevismapper uden sag."""
    root = current_app.config["MEDIA_ROOT"]
    known = set(db.session.scalars(select(ProfileImage.public_id)))
    removed = 0
    for collection in ("public", "private"):
        base = os.path.join(root, collection)
        if not os.path.isdir(base):
            continue
        for name in os.listdir(base):
            path = os.path.join(base, name)
            if os.path.isdir(path) and name not in known and _old_enough(path):
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
    evidence_root = os.path.join(root, "evidence")
    if os.path.isdir(evidence_root):
        keep = {str(i) for i in db.session.scalars(
            select(ModerationCase.id).where(ModerationCase.evidence_purged_at.is_(None)))}
        for name in os.listdir(evidence_root):
            path = os.path.join(evidence_root, name)
            if name not in keep and _old_enough(path):
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
    return removed


def run():
    """Kører alle trin og returnerer, hvor meget der blev slettet."""
    before = cutoff()
    counts = {}
    # Slettede konti og billeder bevares i perioden til sporbarhed og slettes derefter endeligt
    # (app/account/services.py). Ikke mens brugeren er part i en sag, der holder data tilbage.
    # Først, så samtaler, der derved står uden deltagere, ryddes i samme kørsel.
    counts["slettede_konti"], account_files = purge_deleted_accounts(before)
    image_files = purge_deleted_images(before)
    counts["slettede_billeder"] = len(image_files)
    counts["fejlrapporter"], report_files = purge_closed_bug_reports(before)
    counts["afviste_adgange"] = purge_closed_private_access(before)
    counts.update({
        "skjulte_samtaler": purge_hidden_conversations(before),
        "samtaler_uden_deltagere": purge_orphaned_conversations(),
        "slettede_beskeder": purge_deleted_messages(before),
        "beviser": purge_case_evidence(before),
    })
    counts["sessioner"], counts["engangslinks"] = purge_sessions_and_tokens()
    db.session.commit()
    # Filer ryddes efter commit, så de kun forsvinder, når databasen er opdateret.
    delete_files(account_files + image_files + report_files)
    counts["filer"] = purge_orphan_files()
    return counts
