from sqlalchemy import and_, delete, or_, select

from ..auth.models import User
from ..extensions import db
from ..matches.models import Like
from ..matches.queries import has_active_match
from ..matches.services import remove_match
from .models import UserBlock, UserHide


def block(user, target):
    """Kontaktforbud i begge retninger (specifikation 9.2): matchet ophører, likes slettes, og samtalen skjules."""
    if user.id == target.id:
        return
    if db.session.get(UserBlock, (user.id, target.id)) is None:
        db.session.add(UserBlock(blocker_id=user.id, blocked_id=target.id))
    if has_active_match(user.id, target.id):
        remove_match(user, target)
    db.session.execute(delete(Like).where(or_(
        and_(Like.liker_id == user.id, Like.likee_id == target.id),
        and_(Like.liker_id == target.id, Like.likee_id == user.id),
    )))


def unblock(user, target_id):
    """Ophæver kun brugerens egen blokering. Et ophævet match kommer ikke igen af sig selv."""
    db.session.execute(delete(UserBlock).where(UserBlock.blocker_id == user.id, UserBlock.blocked_id == target_id))


def hide(user, target):
    if user.id != target.id and db.session.get(UserHide, (user.id, target.id)) is None:
        db.session.add(UserHide(hider_id=user.id, hidden_id=target.id))


def unhide(user, target_id):
    db.session.execute(delete(UserHide).where(UserHide.hider_id == user.id, UserHide.hidden_id == target_id))


def blocked_users(user):
    return db.session.execute(
        select(User, UserBlock.created_at).join(UserBlock, UserBlock.blocked_id == User.id)
        .where(UserBlock.blocker_id == user.id).order_by(UserBlock.created_at.desc())
    ).all()


def hidden_users(user):
    return db.session.execute(
        select(User, UserHide.created_at).join(UserHide, UserHide.hidden_id == User.id)
        .where(UserHide.hider_id == user.id).order_by(UserHide.created_at.desc())
    ).all()


def has_blocked(user_id, target_id):
    return db.session.get(UserBlock, (user_id, target_id)) is not None


def has_hidden(user_id, target_id):
    return db.session.get(UserHide, (user_id, target_id)) is not None
