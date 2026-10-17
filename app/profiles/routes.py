from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired

from ..auth.forms import ActionForm
from ..auth.sessions import login_required, safe_next_url
from ..core.timeutil import now_utc
from ..extensions import db, limiter
from ..location.distance import distance_label
from ..location.services import km_between, kommune_name
from ..matches.queries import has_active_match
from ..matches.services import has_liked
from ..media import services as media
from ..media.processing import ACCEPT, ImageRejected
from .fields import completeness, visible_fields
from .forms import ProfileForm, VisibilityForm
from .services import age_of, can_view_profile, find_user_by_handle, get_or_create_profile

bp = Blueprint("profiles", __name__)


class UploadForm(FlaskForm):
    image = FileField("Billede", validators=[FileRequired("Vælg et billede.")])


def _render_profile_view(user, *, preview):
    profile = get_or_create_profile(user)
    return render_template(
        "profiles/view.html",
        user=user,
        age=age_of(user),
        images=media.list_images(user.id),
        groups=visible_fields(profile, as_owner=False),
        preview=preview,
        distance=None if preview else distance_label(km_between(g.user.kommune_code, user.kommune_code)),
        kommune=kommune_name(user.kommune_code) if profile.show_kommune else None,
        back_url=safe_next_url(request.args.get("fra")),
        liked=False if preview else has_liked(g.user.id, user.id),
        matched=False if preview else has_active_match(g.user.id, user.id),
    )


@bp.route("/profil")
@login_required
def me():
    profile = get_or_create_profile(g.user)
    images = media.list_images(g.user.id)
    visibility_form = VisibilityForm()
    visibility_form.load(profile)
    db.session.commit()
    return render_template(
        "profiles/me.html",
        profile=profile,
        images=images,
        completeness=completeness(profile, len(images)),
        upload_form=UploadForm(),
        visibility_form=visibility_form,
        action_form=ActionForm(),
        accept=ACCEPT,
    )


@bp.route("/profil/rediger", methods=["GET", "POST"])
@login_required
def edit():
    profile = get_or_create_profile(g.user)
    form = ProfileForm()
    if form.validate_on_submit():
        form.save(profile)
        db.session.commit()
        flash("Din profil er gemt.", "success")
        return redirect(url_for("profiles.me"))
    if not form.is_submitted():
        form.load(profile)
    return render_template("profiles/edit.html", form=form), 400 if form.errors else 200


@bp.route("/profil/synlighed", methods=["POST"])
@login_required
def visibility():
    form = VisibilityForm()
    if form.validate_on_submit():
        form.save(get_or_create_profile(g.user))
        db.session.commit()
        flash("Dine synlighedsindstillinger er gemt.", "success")
    return redirect(url_for("profiles.me"))


@bp.route("/profil/pause", methods=["POST"])
@login_required
def toggle_pause():
    if ActionForm().validate_on_submit():
        profile = get_or_create_profile(g.user)
        if profile.is_paused:
            profile.paused_at = None
            flash("Din profil er aktiv igen.", "success")
        else:
            profile.paused_at = now_utc()
            flash("Din profil er sat på pause. Den vises ikke i Udforsk, og du kan ikke skrive beskeder.", "info")
        db.session.commit()
    return redirect(url_for("profiles.me"))


@bp.route("/profil/forhaandsvisning")
@login_required
def preview():
    """Viser profilen præcis, som andre ser den (specifikation 3.2)."""
    return _render_profile_view(g.user, preview=True)


@bp.route("/bruger/<handle>")
@login_required
def view(handle):
    user = find_user_by_handle(handle)
    if user is None or not can_view_profile(g.user, user):
        abort(404)
    if user.id == g.user.id:
        return redirect(url_for("profiles.preview"))
    return _render_profile_view(user, preview=False)


@bp.route("/profil/billeder", methods=["POST"])
@login_required
@limiter.limit("30 per hour", methods=["POST"])
def upload():
    form = UploadForm()
    if not form.validate_on_submit():
        flash("Vælg et billede at uploade.", "error")
        return redirect(url_for("profiles.me"))
    try:
        media.upload_image(g.user, form.image.data.read())
        db.session.commit()
    except ImageRejected as rejection:
        db.session.rollback()
        flash(str(rejection), "error")
    else:
        flash("Billedet er uploadet.", "success")
    return redirect(url_for("profiles.me"))


@bp.route("/profil/billeder/<public_id>/slet", methods=["POST"])
@login_required
def delete_image(public_id):
    image = media.owned_image(g.user, public_id)
    if image is None:
        abort(404)
    if ActionForm().validate_on_submit():
        media.delete_image(image)
        db.session.commit()
        flash("Billedet er slettet.", "info")
    return redirect(url_for("profiles.me"))


@bp.route("/profil/billeder/<public_id>/primaer", methods=["POST"])
@login_required
def make_primary(public_id):
    image = media.owned_image(g.user, public_id)
    if image is None:
        abort(404)
    if ActionForm().validate_on_submit():
        media.make_primary(image)
        db.session.commit()
        flash("Profilbilledet er skiftet.", "success")
    return redirect(url_for("profiles.me"))
