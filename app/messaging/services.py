"""Regler for samtaler (specifikation 8.2, 7.2 og 3.3).

- Kun personer med et aktivt match kan skrive sammen (henvendelser uden match kommer i fase 2).
- Har mindst én af de to Premium eller Premium+, kan begge skrive. To Free-brugere kan kun læse.
- Fjernes matchet, skjules samtalen for begge. Matcher de igen, kommer den tilbage med historikken.
- "Slet for mig" fjerner kun beskeder for den, der sletter, og de kommer aldrig igen.
- Har modparten slettet sin konto, kan samtalen læses, men ikke besvares.
"""
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, func, or_, select

from ..auth.models import User
from ..core.timeutil import now_utc
from ..extensions import db
from ..matches.models import MatchEvent
from ..matches.queries import find_match, has_active_match, pair
from ..matches.services import active_matches
from ..profiles.models import Profile
from ..safety.queries import is_blocked_between
from ..core.features import has_feature
from .models import Conversation, ConversationMember, Message, MessageDeletion

MAX_LENGTH = 2000
DELETED_USER = "Slettet bruger"


class MessageRefused(Exception):
    """Beskeden blev ikke sendt. Beskeden kan vises direkte for brugeren."""


@dataclass
class Access:
    can_write: bool
    lock_reason: str | None
    other: User | None


@dataclass
class TimelineItem:
    kind: str  # "message" eller "match_removed"
    at: datetime
    message: Message | None = None
    mine: bool = False


# --- Opslag --------------------------------------------------------------------------------------


def find_conversation(a_id, b_id):
    low, high = pair(a_id, b_id)
    return db.session.scalar(select(Conversation).where(Conversation.user_low_id == low, Conversation.user_high_id == high))


def get_or_create_conversation(a_id, b_id):
    conversation = find_conversation(a_id, b_id)
    if conversation is None:
        low, high = pair(a_id, b_id)
        conversation = Conversation(user_low_id=low, user_high_id=high)
        db.session.add(conversation)
        db.session.flush()
        db.session.add_all([ConversationMember(conversation_id=conversation.id, user_id=low),
                            ConversationMember(conversation_id=conversation.id, user_id=high)])
        db.session.flush()
    return conversation


def membership(conversation, user_id):
    return db.session.get(ConversationMember, (conversation.id, user_id))


# --- Adgang ----------------------------------------------------------------------------------------


def write_block_reason(sender, other):
    """Hvorfor `sender` ikke kan skrive til `other` lige nu, eller None hvis det er tilladt."""
    if not sender.is_verified:
        return "Bekræft din e-mail for at skrive beskeder."
    if not sender.is_active_account:
        return "Din konto er midlertidigt begrænset, mens en sag behandles. Du kan stadig læse dine beskeder."
    if not other.is_active_account:
        return "Du kan ikke skrive til denne person lige nu."
    profile = db.session.get(Profile, sender.id)
    if profile is not None and profile.is_paused:
        return "Din profil er på pause. Aktivér den for at skrive beskeder."
    # Har mindst én af de to funktionen, kan begge skrive (specifikation 8.2).
    if not (has_feature(sender, "messages.reply") or has_feature(other, "messages.reply")):
        return ("I har begge Free, så samtalen er låst. Når en af jer har Premium eller Premium+, "
                "kan I skrive sammen igen. I kan stadig læse jeres tidligere beskeder.")
    return None


def access(user, conversation):
    """Adgangen til en samtale, eller None hvis brugeren ikke må se den. Kontrolleres på serveren ved
    hver forespørgsel."""
    if conversation is None or conversation.hidden_at is not None or membership(conversation, user.id) is None:
        return None
    if not has_feature(user, "messages.read"):
        return None
    other_id = conversation.other_id(user.id)
    other = db.session.get(User, other_id) if other_id else None
    if other is None:
        return Access(can_write=False, lock_reason=f"{DELETED_USER} har slettet sin profil.", other=None)
    if is_blocked_between(user.id, other.id) or not has_active_match(user.id, other.id):
        return None
    reason = write_block_reason(user, other)
    return Access(can_write=reason is None, lock_reason=reason, other=other)


# --- Beskeder ----------------------------------------------------------------------------------------


def _visible_messages_query(user_id, member):
    query = select(Message).where(
        Message.conversation_id == member.conversation_id,
        ~select(MessageDeletion.message_id)
        .where(MessageDeletion.message_id == Message.id, MessageDeletion.user_id == user_id)
        .exists(),
    )
    if member.cleared_before is not None:
        query = query.where(Message.created_at > member.cleared_before)
    return query


def visible_messages(user, conversation):
    member = membership(conversation, user.id)
    return db.session.scalars(_visible_messages_query(user.id, member).order_by(Message.created_at, Message.id)).all()


def timeline(user, conversation):
    """Beskeder og markeringer af fjernede matches i tidsorden."""
    member = membership(conversation, user.id)
    items = [TimelineItem("message", m.created_at, m, m.sender_id == user.id) for m in visible_messages(user, conversation)]
    other_id = conversation.other_id(user.id)
    match = find_match(user.id, other_id) if other_id else None
    if match is not None:
        removals = db.session.scalars(
            select(MatchEvent.at).where(MatchEvent.match_id == match.id, MatchEvent.kind == "removed")
        ).all()
        items += [TimelineItem("match_removed", at) for at in removals
                  if member.cleared_before is None or at > member.cleared_before]
    return sorted(items, key=lambda item: item.at)


def send_message(user, conversation, body):
    current = access(user, conversation)
    if current is None:
        raise MessageRefused("Samtalen findes ikke.")
    if not current.can_write:
        raise MessageRefused(current.lock_reason)
    body = (body or "").strip()
    if not body:
        raise MessageRefused("Skriv en besked.")
    if len(body) > MAX_LENGTH:
        raise MessageRefused(f"En besked må højst være {MAX_LENGTH} tegn.")
    now = now_utc()
    message = Message(conversation_id=conversation.id, sender_id=user.id, body=body, created_at=now)
    db.session.add(message)
    conversation.last_message_at = now
    for member in db.session.scalars(select(ConversationMember).where(ConversationMember.conversation_id == conversation.id)):
        member.archived = False  # en ny besked henter samtalen frem fra arkivet
        if member.user_id == user.id:
            member.last_read_at = now
    return message


def mark_read(user, conversation):
    membership(conversation, user.id).last_read_at = now_utc()


def delete_message_for_me(user, message):
    if db.session.get(MessageDeletion, (message.id, user.id)) is None:
        db.session.add(MessageDeletion(message_id=message.id, user_id=user.id))


def clear_for_me(user, conversation):
    """Sletter hele samtalens historik for brugeren. Modparten beholder sin."""
    membership(conversation, user.id).cleared_before = now_utc()


def set_archived(user, conversation, archived):
    membership(conversation, user.id).archived = archived


# --- Ulæste og indbakke ---------------------------------------------------------------------------------


def unread_count(user, conversation):
    member = membership(conversation, user.id)
    query = _visible_messages_query(user.id, member).where(
        or_(Message.sender_id.is_(None), Message.sender_id != user.id)
    )
    if member.last_read_at is not None:
        query = query.where(Message.created_at > member.last_read_at)
    return db.session.scalar(select(func.count()).select_from(query.subquery()))


def total_unread(user):
    """Antal ulæste beskeder til bundnavigationen."""
    member = ConversationMember
    deleted = select(MessageDeletion.message_id).where(
        MessageDeletion.message_id == Message.id, MessageDeletion.user_id == user.id
    ).exists()
    return db.session.scalar(
        select(func.count(Message.id))
        .join(member, and_(member.conversation_id == Message.conversation_id, member.user_id == user.id))
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Conversation.hidden_at.is_(None),
            or_(Message.sender_id.is_(None), Message.sender_id != user.id),
            or_(member.last_read_at.is_(None), Message.created_at > member.last_read_at),
            or_(member.cleared_before.is_(None), Message.created_at > member.cleared_before),
            ~deleted,
        )
    )


@dataclass
class InboxItem:
    other: User | None
    conversation: Conversation | None
    last_message: Message | None
    unread: int
    locked: bool
    archived: bool
    activity_at: datetime


def inbox(user, archived=False):
    """Samtaler med aktive matches, også dem uden beskeder endnu, og læsbare samtaler med slettede brugere."""
    items = []
    for match, other in active_matches(user):
        conversation = find_conversation(user.id, other.id)
        items.append(_inbox_item(user, other, conversation, fallback_at=match.matched_at))

    orphaned = db.session.scalars(
        select(Conversation)
        .join(ConversationMember, and_(ConversationMember.conversation_id == Conversation.id,
                                       ConversationMember.user_id == user.id))
        .where(Conversation.hidden_at.is_(None),
               or_(Conversation.user_low_id.is_(None), Conversation.user_high_id.is_(None)))
    ).all()
    for conversation in orphaned:
        item = _inbox_item(user, None, conversation, fallback_at=conversation.created_at)
        if item.last_message is not None:
            items.append(item)

    items = [item for item in items if item.archived == archived]
    return sorted(items, key=lambda item: item.activity_at, reverse=True)


def _inbox_item(user, other, conversation, fallback_at):
    if conversation is None:
        return InboxItem(other, None, None, 0, bool(other and write_block_reason(user, other)), False, fallback_at)
    member = membership(conversation, user.id)
    last = db.session.scalar(_visible_messages_query(user.id, member).order_by(Message.created_at.desc()).limit(1))
    locked = other is None or write_block_reason(user, other) is not None
    return InboxItem(
        other=other,
        conversation=conversation,
        last_message=last,
        unread=unread_count(user, conversation),
        locked=locked,
        archived=member.archived,
        activity_at=last.created_at if last else fallback_at,
    )
