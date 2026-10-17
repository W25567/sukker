from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from ..auth.forms import ActionForm
from ..auth.sessions import login_required, safe_next_url
from ..extensions import db, limiter
from ..media.services import avatar_ids
from ..notifications import mark_matches_seen
from ..profiles.services import age_of, can_view_profile, find_user_by_handle
from ..core.features import has_feature
from . import services
from .queries import find_match

bp = Blueprint("matches", __name__)


def _back(handle):
    return redirect(safe_next_url(request.args.get("fra")) or url_for("profiles.view", handle=handle))


def _target(handle):
    user = find_user_by_handle(handle)
    if user is None or not can_view_profile(g.user, user):
        abort(404)
    return user


@bp.route("/like/<handle>", methods=["POST"])
@login_required
@limiter.limit("120 per hour", methods=["POST"])
def like(handle):
    target = _target(handle)
    if ActionForm().validate_on_submit():
        try:
            result = services.like(g.user, target)
            db.session.commit()
        except services.LikeRefused as refusal:
            db.session.rollback()
            flash(str(refusal), "error")
        else:
            if result.matched:
                flash(f"Det er et match! Du og {target.display_name} kan lide hinanden.", "success")
    return _back(handle)


@bp.route("/like/<handle>/fortryd", methods=["POST"])
@login_required
def unlike(handle):
    target = _target(handle)
    if ActionForm().validate_on_submit():
        services.unlike(g.user, target)
        db.session.commit()
    return _back(handle)


@bp.route("/likes")
@login_required
def index():
    """Matches og likes til brugeren (specifikation 6.1 og 7)."""
    can_see_likers = has_feature(g.user, "likes.view_received")
    likes = services.received_likes(g.user)
    seen_before = g.user.matches_seen_at
    matches = services.active_matches(g.user)
    likers = [liker for _, liker in likes] if can_see_likers else []
    mark_matches_seen(g.user)  # tallet ved Likes i menuen nulstilles, når listen er set
    db.session.commit()
    return render_template(
        "matches/index.html",
        matches=[(other, age_of(other), seen_before is None or match.matched_at > seen_before)
                 for match, other in matches],
        likers=[(liker, age_of(liker)) for liker in likers],
        avatars=avatar_ids(g.user, [other for _, other in matches] + likers),
        like_count=len(likes),
        can_see_likers=can_see_likers,
        action_form=ActionForm(),
    )


@bp.route("/match/<handle>/fjern", methods=["GET", "POST"])
@login_required
def remove(handle):
    """Fjerner et match. Brugeren får først at vide, hvad der sker med samtalen (specifikation 7.2)."""
    other = find_user_by_handle(handle)
    match = find_match(g.user.id, other.id) if other else None
    if match is None or not match.is_active:
        abort(404)
    form = ActionForm()
    if form.validate_on_submit():
        services.remove_match(g.user, other)
        db.session.commit()
        flash(f"Matchet med {other.display_name} er fjernet.", "info")
        return redirect(url_for("matches.index"))
    return render_template("matches/confirm_remove.html", other=other, form=form)
