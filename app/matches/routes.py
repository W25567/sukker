from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from ..auth.forms import ActionForm
from ..auth.sessions import login_required, safe_next_url
from ..extensions import db, limiter
from ..profiles.services import age_of, can_view_profile, find_user_by_username
from ..subscriptions.services import effective_tier
from . import services
from .queries import find_match

bp = Blueprint("matches", __name__)


def _back(username):
    return redirect(safe_next_url(request.args.get("fra")) or url_for("profiles.view", username=username))


def _target(username):
    user = find_user_by_username(username)
    if user is None or not can_view_profile(g.user, user):
        abort(404)
    return user


@bp.route("/like/<username>", methods=["POST"])
@login_required
@limiter.limit("120 per hour", methods=["POST"])
def like(username):
    target = _target(username)
    if ActionForm().validate_on_submit():
        try:
            result = services.like(g.user, target)
            db.session.commit()
        except services.LikeRefused as refusal:
            db.session.rollback()
            flash(str(refusal), "error")
        else:
            if result.matched:
                flash(f"Det er et match! Du og {target.username} kan lide hinanden.", "success")
    return _back(username)


@bp.route("/like/<username>/fortryd", methods=["POST"])
@login_required
def unlike(username):
    target = _target(username)
    if ActionForm().validate_on_submit():
        services.unlike(g.user, target)
        db.session.commit()
    return _back(username)


@bp.route("/likes")
@login_required
def index():
    """Matches og likes til brugeren (specifikation 6.1 og 7)."""
    can_see_likers = effective_tier(g.user.id) != "free"
    likes = services.received_likes(g.user)
    return render_template(
        "matches/index.html",
        matches=[(other, age_of(other)) for _, other in services.active_matches(g.user)],
        likers=[(liker, age_of(liker)) for _, liker in likes] if can_see_likers else [],
        like_count=len(likes),
        can_see_likers=can_see_likers,
        action_form=ActionForm(),
    )


@bp.route("/match/<username>/fjern", methods=["GET", "POST"])
@login_required
def remove(username):
    """Fjerner et match. Brugeren får først at vide, hvad der sker med samtalen (specifikation 7.2)."""
    other = find_user_by_username(username)
    match = find_match(g.user.id, other.id) if other else None
    if match is None or not match.is_active:
        abort(404)
    form = ActionForm()
    if form.validate_on_submit():
        services.remove_match(g.user, other)
        db.session.commit()
        flash(f"Matchet med {other.username} er fjernet.", "info")
        return redirect(url_for("matches.index"))
    return render_template("matches/confirm_remove.html", other=other, form=form)
