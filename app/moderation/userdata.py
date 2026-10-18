"""Samler alle gemte data om en bruger til moderatorvisningen (specifikation 9.3).

Visningen ændrer intet for brugerne: ingen læsestatus, ingen notifikationer.
"""
from sqlalchemy import or_, select

from ..auth.models import User, UserSession
from ..extensions import db
from ..location.services import kommune_name
from ..matches.models import Like, Match
from ..media.models import PrivateAccess, ProfileImage
from ..media.services import list_images
from ..messaging.models import Conversation, ConversationMember, Message, MessageDeletion
from ..profiles.fields import FIELDS, display_value
from ..profiles.models import Profile
from ..profiles.services import age_of
from ..safety.models import UserBlock
from ..subscriptions.models import Subscription
from .models import ModerationCase


def staff_label(user):
    """Navnet til administration og moderation. En slettet bruger beholder navnet og markeres med e-mail i parentes."""
    if user is None:
        return "Slettet bruger"
    if user.is_deleted:
        return f"{user.display_name} · slettet bruger ({user.deleted_email})"
    return user.display_name


def _usernames(ids):
    ids = {i for i in ids if i}
    return {u.id: staff_label(u) for u in db.session.scalars(select(User).where(User.id.in_(ids)))} if ids else {}


def collect(user):
    profile = db.session.get(Profile, user.id)
    likes_given = db.session.scalars(select(Like).where(Like.liker_id == user.id)).all()
    likes_received = db.session.scalars(select(Like).where(Like.likee_id == user.id)).all()
    matches = db.session.scalars(select(Match).where(or_(Match.user_low_id == user.id, Match.user_high_id == user.id))).all()
    blocks = db.session.scalars(select(UserBlock).where(or_(UserBlock.blocker_id == user.id, UserBlock.blocked_id == user.id))).all()
    cases = db.session.scalars(
        select(ModerationCase).where(or_(ModerationCase.subject_user_id == user.id, ModerationCase.reporter_id == user.id))
        .order_by(ModerationCase.created_at.desc())
    ).all()

    conversations = []
    for conversation in db.session.scalars(
        select(Conversation).join(ConversationMember, ConversationMember.conversation_id == Conversation.id)
        .where(ConversationMember.user_id == user.id)
    ):
        messages = db.session.scalars(
            select(Message).where(Message.conversation_id == conversation.id).order_by(Message.created_at)
        ).all()
        deleted = set(db.session.scalars(
            select(MessageDeletion.message_id).where(MessageDeletion.message_id.in_([m.id for m in messages]))
        )) if messages else set()
        conversations.append({"conversation": conversation, "messages": messages, "deleted": deleted,
                              "other_id": conversation.other_id(user.id)})

    # Hvem har adgang til brugerens private album, og hvis album har brugeren adgang til (specifikation 9.3)?
    private_access = db.session.scalars(
        select(PrivateAccess).where(or_(PrivateAccess.owner_id == user.id, PrivateAccess.viewer_id == user.id))
        .order_by(PrivateAccess.requested_at.desc())
    ).all()
    names = _usernames(
        [a.owner_id for a in private_access] + [a.viewer_id for a in private_access] +
        [l.likee_id for l in likes_given] + [l.liker_id for l in likes_received]
        + [m.other_id(user.id) for m in matches] + [b.blocker_id for b in blocks] + [b.blocked_id for b in blocks]
        + [c["other_id"] for c in conversations]
        + [m.sender_id for c in conversations for m in c["messages"]]
    )
    return {
        "user": user,
        "age": age_of(user),
        "kommune": kommune_name(user.kommune_code),
        "profile": profile,
        "fields": [(f.label, display_value(profile, f), f.name in profile.hidden_fields) for f in FIELDS] if profile else [],
        "images": {"public": list_images(user.id, "public"), "private": list_images(user.id, "private"),
                   # Billeder, brugeren selv har slettet. De bevares i opbevaringsperioden til sporbarhed.
                   "deleted": db.session.scalars(select(ProfileImage).where(
                       ProfileImage.user_id == user.id, ProfileImage.deleted_at.isnot(None))
                       .order_by(ProfileImage.deleted_at.desc())).all()},
        "subscriptions": db.session.scalars(select(Subscription).where(Subscription.user_id == user.id).order_by(Subscription.starts_at)).all(),
        "likes_given": likes_given,
        "likes_received": likes_received,
        "matches": matches,
        "blocks": blocks,
        "cases": cases,
        "conversations": conversations,
        "private_access": private_access,
        "sessions": db.session.scalars(select(UserSession).where(UserSession.user_id == user.id).order_by(UserSession.created_at.desc())).all(),
        "names": names,
    }
