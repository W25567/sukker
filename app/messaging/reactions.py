"""Reaktioner på beskeder: hjerte, tommel op, griner, overrasket, ked af det og vred.

- Man reagerer på den andens beskeder, ikke sine egne. Én reaktion pr. person pr. besked: en ny erstatter den
  gamle, og samme reaktion igen fjerner den.
- I samtaler mellem medlemmer gælder samme regler som for at skrive: kan man ikke skrive (fx Free/Free eller en
  slettet modpart), kan man heller ikke reagere. Beskeder, man har slettet for sig selv, kan man ikke reagere på.
- I samtalen med Administrator reagerer brugeren på Administrators beskeder, og administratorerne på brugerens.
  Brugeren ser kun "Administrator", aldrig hvilken administrator der reagerede.
- Reaktioner giver ingen notifikation, men vises med det samme hos den anden (versionen i services.thread_version).
"""
from sqlalchemy import func, select

from ..core.timeutil import now_utc
from ..extensions import db
from .models import AdminMessage, AdminMessageReaction, Message, MessageDeletion, MessageReaction

# (nøgle, emoji, betegnelse). Nøglerne står i databasen (models.py), så de kan ikke omdøbes uden en migration.
REACTIONS = [
    ("hjerte", "❤️", "Hjerte"),
    ("tommel", "👍", "Tommel op"),
    ("griner", "😂", "Griner"),
    ("overrasket", "😮", "Overrasket"),
    ("ked", "😢", "Ked af det"),
    ("vred", "😠", "Vred"),
]
EMOJI = {key: emoji for key, emoji, _ in REACTIONS}
LABELS = {key: label for key, _, label in REACTIONS}


class ReactionRefused(ValueError):
    pass


def _toggle(existing, reaction, create):
    """Samme reaktion igen fjerner den, en anden erstatter den. Returnerer den nye reaktion eller None."""
    if reaction not in EMOJI:
        raise ReactionRefused("Ukendt reaktion.")
    if existing is not None and existing.reaction == reaction:
        db.session.delete(existing)
        return None
    if existing is None:
        existing = create()
        db.session.add(existing)
    existing.reaction, existing.created_at = reaction, now_utc()
    return reaction


# --- Samtaler mellem medlemmer ------------------------------------------------------------------------------


def react(user, conversation, message, reaction):
    from .services import access

    current = access(user, conversation)
    if current is None or message.conversation_id != conversation.id:
        raise ReactionRefused("Beskeden findes ikke.")
    if message.sender_id == user.id:
        raise ReactionRefused("Du kan ikke reagere på dine egne beskeder.")
    if db.session.get(MessageDeletion, (message.id, user.id)) is not None:
        raise ReactionRefused("Beskeden findes ikke.")
    if not current.can_write:
        raise ReactionRefused(current.lock_reason)
    existing = db.session.get(MessageReaction, (message.id, user.id))
    return _toggle(existing, reaction, lambda: MessageReaction(message_id=message.id, user_id=user.id))


def for_messages(message_ids):
    """{besked-id: {bruger-id: reaktion}}"""
    result = {}
    if message_ids:
        for row in db.session.scalars(select(MessageReaction).where(MessageReaction.message_id.in_(message_ids))):
            result.setdefault(row.message_id, {})[row.user_id] = row.reaction
    return result


def conversation_stamp(conversation_id):
    """Ændrer sig, når en reaktion i samtalen sættes, skiftes eller fjernes."""
    count, latest = db.session.execute(
        select(func.count(), func.max(MessageReaction.created_at))
        .join(Message, Message.id == MessageReaction.message_id).where(Message.conversation_id == conversation_id)
    ).one()
    return f"{count}.{int(latest.timestamp() * 1000) if latest else 0}"


# --- Samtalen med Administrator --------------------------------------------------------------------------------


def react_in_admin_thread(message, reaction, *, admin=None, user=None):
    """Brugeren (user) reagerer på Administrators besked, eller en administrator (admin) på brugerens."""
    from_admin = admin is not None
    if message.from_admin == from_admin:
        raise ReactionRefused("Du kan ikke reagere på dine egne beskeder.")
    if not from_admin and message.user_id != user.id:
        raise ReactionRefused("Beskeden findes ikke.")
    admin_id = admin.id if admin else None
    existing = db.session.get(AdminMessageReaction, (message.id, from_admin))
    result = _toggle(existing, reaction,
                     lambda: AdminMessageReaction(message_id=message.id, from_admin=from_admin, admin_id=admin_id))
    if result is not None and existing is not None:
        existing.admin_id = admin_id  # en anden administrator har skiftet reaktionen
    return result


def for_admin_messages(message_ids):
    """{besked-id: {"admin": reaktion, "user": reaktion}}"""
    result = {}
    if message_ids:
        for row in db.session.scalars(select(AdminMessageReaction)
                                      .where(AdminMessageReaction.message_id.in_(message_ids))):
            result.setdefault(row.message_id, {})["admin" if row.from_admin else "user"] = row.reaction
    return result


def admin_thread_stamp(user_id):
    count, latest = db.session.execute(
        select(func.count(), func.max(AdminMessageReaction.created_at))
        .join(AdminMessage, AdminMessage.id == AdminMessageReaction.message_id).where(AdminMessage.user_id == user_id)
    ).one()
    return f"{count}.{int(latest.timestamp() * 1000) if latest else 0}"
