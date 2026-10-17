"""Små opslag om matches uden afhængigheder til profiler, så profiles.services kan bruge dem."""
from sqlalchemy import func, or_, select

from ..extensions import db
from .models import Match


def pair(a_id, b_id):
    return (a_id, b_id) if a_id < b_id else (b_id, a_id)


def find_match(a_id, b_id):
    low, high = pair(a_id, b_id)
    return db.session.scalar(select(Match).where(Match.user_low_id == low, Match.user_high_id == high))


def has_active_match(a_id, b_id):
    match = find_match(a_id, b_id)
    return match is not None and match.is_active


def active_match_count(user_id):
    return db.session.scalar(
        select(func.count(Match.id)).where(
            or_(Match.user_low_id == user_id, Match.user_high_id == user_id), Match.removed_at.is_(None)
        )
    )
