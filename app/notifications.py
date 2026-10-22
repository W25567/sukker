"""Notifikationer: tal i menuen og midlertidige beskeder om nye beskeder og matches.

- Beskeder: ulæste beskeder fra matches og fra Administrator (messaging.services.total_unread).
- Likes: aktive matches og likes til brugeren, der er kommet, siden brugeren sidst åbnede Likes. Et match, som
  brugeren selv udløste ved at like tilbage, tæller ikke: det fik brugeren besked om med det samme.
- Siderne spørger /notifikationer med jævne mellemrum (static/js/notifications.js).
"""
from sqlalchemy import and_, or_, select

from .auth.models import User
from .core.timeutil import now_utc
from .extensions import db
from .matches.models import Match, MatchEvent
from .messaging.admin_threads import SENDER_NAME, visible_to_user
from .messaging.models import AdminMessage, Conversation, ConversationMember, Message, MessageDeletion
from .messaging.services import total_unread
from .media.models import PrivateAccess
from .notify.prefs import wants


def _new_matches_query(user):
    other = or_(and_(Match.user_low_id == user.id, User.id == Match.user_high_id),
                and_(Match.user_high_id == user.id, User.id == Match.user_low_id))
    query = (
        select(MatchEvent.id, User)
        .join(Match, Match.id == MatchEvent.match_id)
        .join(User, other)
        .where(
            Match.removed_at.is_(None),
            MatchEvent.kind == "matched",
            MatchEvent.at == Match.matched_at,
            or_(MatchEvent.actor_id.is_(None), MatchEvent.actor_id != user.id),
        )
    )
    if user.matches_seen_at is not None:
        query = query.where(Match.matched_at > user.matches_seen_at)
    return query


def new_match_count(user):
    return len(db.session.execute(_new_matches_query(user)).all())


def new_like_count(user):
    """Likes til brugeren, siden brugeren sidst åbnede Likes. Kun et antal: hvem, ser kun dem med
    likes.view_received, og Free ser allerede antallet på siden (matches/index.html)."""
    from .matches.services import received_likes

    seen = user.matches_seen_at
    return sum(1 for like, _ in received_likes(user) if seen is None or like.created_at > seen)


def new_count(user):
    """Tallet ved Likes i menuen: nye matches og nye likes."""
    return new_match_count(user) + new_like_count(user)


def mark_matches_seen(user):
    user.matches_seen_at = now_utc()


def _latest_unread_message(user):
    member = ConversationMember
    deleted = select(MessageDeletion.message_id).where(
        MessageDeletion.message_id == Message.id, MessageDeletion.user_id == user.id
    ).exists()
    return db.session.execute(
        select(Message, User)
        .join(member, and_(member.conversation_id == Message.conversation_id, member.user_id == user.id))
        .join(Conversation, Conversation.id == Message.conversation_id)
        .join(User, User.id == Message.sender_id)
        .where(
            Conversation.hidden_at.is_(None),
            Message.sender_id != user.id,
            or_(member.last_read_at.is_(None), Message.created_at > member.last_read_at),
            or_(member.cleared_before.is_(None), Message.created_at > member.cleared_before),
            ~deleted,
        )
        .order_by(Message.id.desc())
        .limit(1)
    ).first()


def _latest_unread_admin_message(user):
    thread = visible_to_user(user)
    if thread is None:
        return None
    query = select(AdminMessage).where(AdminMessage.user_id == user.id, AdminMessage.from_admin.is_(True))
    if thread.user_last_read_at is not None:
        query = query.where(AdminMessage.created_at > thread.user_last_read_at)
    return db.session.scalar(query.order_by(AdminMessage.id.desc()).limit(1))


def _admin_replies(url_for):
    """(antal samtaler med ulæste svar til Administrator, det nyeste svar) til administratorerne."""
    from .messaging.admin_threads import total_unread_for_admins
    from .messaging.models import AdminThread
    from .moderation.userdata import staff_label

    row = db.session.execute(
        select(AdminMessage, User)
        .join(AdminThread, AdminThread.user_id == AdminMessage.user_id)
        .join(User, User.id == AdminMessage.user_id)
        .where(AdminMessage.from_admin.is_(False),
               or_(AdminThread.admin_last_read_at.is_(None), AdminMessage.created_at > AdminThread.admin_last_read_at))
        .order_by(AdminMessage.id.desc()).limit(1)
    ).first()
    latest = None
    if row is not None:
        message, sender = row
        latest = {"key": f"s{message.id}", "id": message.id, "name": staff_label(sender),
                  "url": url_for("admin.user_thread", user_id=sender.id)}
    return total_unread_for_admins(), latest


def summary(user, url_for):
    """Tallene til menuen og den nyeste ulæste besked og det nyeste nye match. Navne og links vises kun i
    brugerens egen browser. Beskedteksten sendes ikke med, så den ikke dukker op på en delt skærm."""
    latest_message = None
    row = _latest_unread_message(user)
    if row is not None:
        message, sender = row
        latest_message = {"key": f"m{message.id}", "id": message.id, "name": sender.display_name,
                          "url": url_for("messaging.conversation", conversation_id=message.conversation_id)}
    admin_message = _latest_unread_admin_message(user)
    if admin_message is not None:
        latest_admin = {"key": f"a{admin_message.id}", "id": admin_message.id, "name": SENDER_NAME,
                        "url": url_for("messaging.admin_conversation")}
    else:
        latest_admin = None

    matches = db.session.execute(_new_matches_query(user).order_by(MatchEvent.id.desc())).all()
    latest_match = None
    if matches:
        event_id, other = matches[0]
        latest_match = {"key": f"x{event_id}", "id": event_id, "name": other.display_name,
                        "url": url_for("matches.index")}
    requests = db.session.execute(
        select(PrivateAccess.id, User).join(User, User.id == PrivateAccess.viewer_id)
        .where(PrivateAccess.owner_id == user.id, PrivateAccess.status == "pending", User.status == "active")
        .order_by(PrivateAccess.id.desc())
    ).all()
    latest_request = None
    if requests:
        access_id, viewer = requests[0]
        latest_request = {"key": f"r{access_id}", "id": access_id, "name": viewer.display_name,
                          "url": url_for("profiles.private_access")}
    granted = db.session.execute(
        select(PrivateAccess.decided_at, User).join(User, User.id == PrivateAccess.owner_id)
        .where(PrivateAccess.viewer_id == user.id, PrivateAccess.status == "approved", User.status == "active")
        .order_by(PrivateAccess.decided_at.desc()).limit(1)
    ).first()
    latest_grant = None
    if granted:
        decided_at, owner = granted
        # Tidspunktet bruges som id, så også en ændret adgang (fx flere billeder) giver en ny besked.
        latest_grant = {"id": int(decided_at.timestamp() * 1000), "name": owner.display_name,
                        "url": url_for("profiles.view", handle=owner.handle) + "#privat"}
    staff_threads, latest_staff = 0, None
    if user.role == "admin":
        staff_threads, latest_staff = _admin_replies(url_for)
    # Slås beskeder på siden fra, sendes den nyeste ikke med. Tallene i menuen vises altid (app/notify/prefs.py).
    if not wants(user, "message", "app"):
        latest_message = None
    if not wants(user, "admin", "app"):
        latest_admin = None
    if not wants(user, "match", "app"):
        latest_match = None
    if not wants(user, "request", "app"):
        latest_request = None
    if not wants(user, "grant", "app"):
        latest_grant = None
    if not wants(user, "staff", "app"):
        latest_staff = None
    return {
        "messages": total_unread(user),
        "matches": len(matches) + new_like_count(user),  # tallet ved Likes: nye matches og nye likes
        "requests": len(requests),
        # Kun for administratorer: ulæste svar fra brugere i samtaler med "Administrator".
        "staff_threads": staff_threads,
        "latest_staff": latest_staff,
        "latest_message": latest_message,
        "latest_admin": latest_admin,
        "latest_match": latest_match,
        "latest_request": latest_request,
        "latest_grant": latest_grant,
    }
