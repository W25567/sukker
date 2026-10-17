"""Opslag om blokeringer uden afhængigheder til andre moduler, så de kan bruges overalt."""
from sqlalchemy import and_, or_, select

from ..extensions import db
from .models import UserBlock, UserHide


def is_blocked_between(a_id, b_id):
    """Sandt hvis en af de to har blokeret den anden."""
    return db.session.scalar(
        select(UserBlock.blocker_id).where(
            or_(and_(UserBlock.blocker_id == a_id, UserBlock.blocked_id == b_id),
                and_(UserBlock.blocker_id == b_id, UserBlock.blocked_id == a_id))
        ).limit(1)
    ) is not None


def blocked_either_way(viewer_id, other_id_column):
    """SQL-betingelse: sand hvis viewer og brugeren i `other_id_column` har blokeret hinanden i en retning."""
    return select(UserBlock.blocker_id).where(
        or_(and_(UserBlock.blocker_id == viewer_id, UserBlock.blocked_id == other_id_column),
            and_(UserBlock.blocker_id == other_id_column, UserBlock.blocked_id == viewer_id))
    ).exists()


def hidden_by(viewer_id, other_id_column):
    return select(UserHide.hider_id).where(UserHide.hider_id == viewer_id, UserHide.hidden_id == other_id_column).exists()
