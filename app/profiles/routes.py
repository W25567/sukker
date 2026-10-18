from sqlalchemy import func, select

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for
from flask_wtf import FlaskForm
from wtforms import MultipleFileField

from ..auth.forms import ActionForm
from ..auth.models import User, UserSession
from ..explore.services import RECENTLY_ACTIVE
from ..auth.sessions import login_required, safe_next_url
from ..core.timeutil import now_utc
from ..extensions import db, limiter
from ..location.distance import distance_label
from ..location.services import km_between, kommune_name
from ..matches.queries import has_active_match
from ..matches.services import has_liked
from ..media import access as private
from ..media import services as media
from ..media.models import PrivateAccess
from ..media.processing import ACCEPT, ImageRejected
from ..subscriptions.services import early_bird_open, grant_early_bird, has_early_bird
from .fields import completeness, visible_fields
from .forms import ExplorePreferenceForm, ProfileForm, VisibilityForm
from .services import age_of, can_view_profile, find_user_by_handle, get_or_create_profile
from .zodiac import visible_zodiac, zodiac_for

bp = Blueprint("profiles", __name__)


class UploadForm(FlaskForm):
    # Flere billeder kan vælges på én gang. Med JavaScript sendes de ét ad gangen (static/js/upload.js),
    # så ingen forespørgsel bliver større end grænsen på 16 MB.
    image = MultipleFileField("Billeder")


def _render_profile_view(user, *, preview):
    profile = get_or_create_profile(user)
    public = media.list_images(user.id)
    # Private billeder, den besøgende har fået adgang til, vises i galleriet efter de offentlige. Ved
    # forhåndsvisning ser ejeren profilen som en, der ikke har adgang.
    unlocked = [] if preview else private.visible_private_images(g.user, user)
    last_seen = db.session.scalar(select(func.max(UserSession.last_seen_at)).where(UserSession.user_id == user.id))
    return render_template(
        "profiles/view.html",
        user=user,
        age=age_of(user),
        images=public + unlocked,
        public_count=len(public),
        private_state=private.ViewerState(private.private_count(user.id), 0, None, None) if preview
        else private.viewer_state(g.user, user),
        recently_active=bool(profile.show_activity and last_seen and last_seen >= now_utc() - RECENTLY_ACTIVE),
        presentation=profile.presentation if "presentation" not in profile.hidden_fields else None,
        groups=visible_fields(profile, as_owner=False),
        zodiac=visible_zodiac(user, profile),
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
    explore_form = ExplorePreferenceForm()
    explore_form.hide_liked.data = profile.hide_liked
    db.session.commit()
    return render_template(
        "profiles/me.html",
        profile=profile,
        images=images,
        private_images=media.list_images(g.user.id, "private"),
        completeness=completeness(profile, len(images)),
        upload_form=UploadForm(),
        visibility_form=visibility_form,
        explore_form=explore_form,
        action_form=ActionForm(),
        accept=ACCEPT,
        is_early_bird=has_early_bird(g.user),
        early_bird_open=early_bird_open(),
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
    return render_template("profiles/edit.html", form=form, zodiac=zodiac_for(g.user.birthdate)), 400 if form.errors else 200


@bp.route("/profil/synlighed", methods=["POST"])
@login_required
def visibility():
    form = VisibilityForm()
    if form.validate_on_submit():
        form.save(get_or_create_profile(g.user))
        db.session.commit()
        flash("Dine synlighedsindstillinger er gemt.", "success")
    return redirect(url_for("profiles.me"))


@bp.route("/profil/udforsk", methods=["POST"])
@login_required
def explore_preferences():
    """Standardvalg for Udforsk. Kan slås til og fra for den enkelte visning direkte i Udforsk."""
    form = ExplorePreferenceForm()
    if form.validate_on_submit():
        form.save(get_or_create_profile(g.user))
        db.session.commit()
        flash("Dit standardvalg for Udforsk er gemt.", "success")
    return redirect(url_for("profiles.me") + "#udforsk")


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
    """Uploader et eller flere billeder. Kaldt fra upload.js (header X-Upload) svares med JSON, og siden
    genindlæses bagefter. Fejl og tidlig fugl vises som beskeder på siden i begge tilfælde."""
    form = UploadForm()
    files = [f for f in request.files.getlist("image") if f and f.filename]
    from_script = request.headers.get("X-Upload") == "1"
    if not form.validate_on_submit() or not files:
        flash("Vælg et billede at uploade.", "error")
        return jsonify(uploaded=0) if from_script else redirect(url_for("profiles.me") + "#billeder")
    uploaded = 0
    for file in files:
        try:
            media.upload_image(g.user, file.read())
            db.session.commit()
            uploaded += 1
        except ImageRejected as rejection:
            db.session.rollback()
            flash(f"{file.filename}: {rejection}" if len(files) > 1 or from_script else str(rejection), "error")
    early_bird = grant_early_bird(g.user) if uploaded else None  # Premium for livstid ved første billede
    db.session.commit()
    if early_bird is not None:
        flash("Tak, fordi du er tidlig fugl – du har nu Premium for livstid!", "success")
    if from_script:
        return jsonify(uploaded=uploaded)
    if uploaded:
        flash("Billedet er uploadet." if uploaded == 1 else f"{uploaded} billeder er uploadet.", "success")
    return redirect(url_for("profiles.me") + "#billeder")


@bp.route("/profil/billeder/<public_id>/slet", methods=["POST"])
@login_required
def delete_image(public_id):
    image = media.owned_image(g.user, public_id)
    if image is None:
        abort(404)
    if ActionForm().validate_on_submit():
        refusal = media.delete_refusal(image)
        if refusal:
            flash(refusal, "error")
        else:
            media.delete_image(image)
            db.session.commit()
            flash("Billedet er slettet.", "info")
    return redirect(url_for("profiles.me") + "#billeder")


@bp.route("/profil/billeder/<public_id>/drej/<any(venstre, hoejre):direction>", methods=["POST"])
@login_required
@limiter.limit("60 per hour", methods=["POST"])
def rotate_image(public_id, direction):
    """Drejer et billede 90 grader til venstre eller højre."""
    image = media.owned_image(g.user, public_id)
    if image is None:
        abort(404)
    if ActionForm().validate_on_submit():
        try:
            media.rotate_image(image, clockwise=direction == "hoejre")
            db.session.commit()
        except ImageRejected as rejection:
            db.session.rollback()
            flash(str(rejection), "error")
    return redirect(url_for("profiles.me") + "#billeder")


@bp.route("/profil/billeder/<public_id>/<any(privat, offentlig):target>", methods=["POST"])
@login_required
def move_image(public_id, target):
    """Gør et billede privat eller offentligt. Private billeder kan kun ses af ejeren (specifikation 4.1)."""
    image = media.owned_image(g.user, public_id)
    if image is None:
        abort(404)
    if ActionForm().validate_on_submit():
        try:
            old = media.set_collection(image, "private" if target == "privat" else "public")
        except media.ImageMoveRefused as refusal:
            db.session.rollback()
            flash(str(refusal), "error")
        else:
            public_id, moved_to = image.public_id, image.collection
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
                if old:
                    media.undo_move(public_id, moved_to, old)  # filerne tilbage, så de passer med databasen
                raise
            flash("Billedet er nu privat. Kun du kan se det." if target == "privat"
                  else "Billedet er nu offentligt på din profil.", "success")
    return redirect(url_for("profiles.me") + "#billeder")


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


# --- Private album: anmodninger og adgang (specifikation 4.2) ---------------------------------------------------


@bp.route("/bruger/<handle>/privat/anmod", methods=["POST"])
@login_required
@limiter.limit("30 per hour", methods=["POST"])
def request_private(handle):
    """Anmod om adgang til en profils private album."""
    owner = find_user_by_handle(handle)
    if owner is None or not can_view_profile(g.user, owner):
        abort(404)
    if ActionForm().validate_on_submit():
        try:
            private.request_access(g.user, owner)
            db.session.commit()
            flash(f"Din anmodning er sendt. {owner.display_name} kan give dig adgang til hele albummet eller udvalgte billeder.",
                  "success")
        except private.AccessRefused as refusal:
            db.session.rollback()
            flash(str(refusal), "error")
    return redirect(url_for("profiles.view", handle=owner.handle) + "#privat")


@bp.route("/bruger/<handle>/privat/fortryd", methods=["POST"])
@login_required
def cancel_private(handle):
    owner = find_user_by_handle(handle)
    if owner is None:
        abort(404)
    if ActionForm().validate_on_submit():
        private.cancel_request(g.user, owner)
        db.session.commit()
        flash("Din anmodning er trukket tilbage.", "info")
    return redirect(url_for("profiles.view", handle=owner.handle) + "#privat")


@bp.route("/profil/privat-adgang")
@login_required
def private_access():
    """Ejerens oversigt: anmodninger om adgang og hvem der har adgang til det private album."""
    pending, approved = private.owner_overview(g.user)
    users = [row.user for row in pending + approved]
    return render_template("profiles/private_access.html", pending=pending, approved=approved,
                           avatars=media.avatar_ids(g.user, users), private_total=private.private_count(g.user.id),
                           action_form=ActionForm())


def _own_access_or_404(access_id):
    access = db.session.get(PrivateAccess, access_id)
    if access is None or access.owner_id != g.user.id:
        abort(404)
    return access


@bp.route("/profil/privat-adgang/<int:access_id>", methods=["GET", "POST"])
@login_required
def private_access_edit(access_id):
    """Giv eller ret adgang: hele albummet eller udvalgte billeder."""
    access = _own_access_or_404(access_id)
    viewer = db.session.get(User, access.viewer_id)
    if viewer is None or access.status not in ("pending", "approved"):
        abort(404)
    form = ActionForm()
    if request.method == "POST" and form.validate_on_submit():
        try:
            private.approve(g.user, access, request.form.get("scope", "all"), request.form.getlist("image"))
            db.session.commit()
            flash(f"{viewer.display_name} har nu adgang til {'hele dit private album' if access.scope == 'all' else 'de valgte billeder'}.",
                  "success")
            return redirect(url_for("profiles.private_access"))
        except private.AccessRefused as refusal:
            db.session.rollback()
            flash(str(refusal), "error")
    return render_template("profiles/private_access_edit.html", access=access, viewer=viewer,
                           images=private.private_images(g.user.id), selected=private.selected_ids(access), form=form)


@bp.route("/profil/privat-adgang/<int:access_id>/<any(alle, afvis, tilbagekald):action>", methods=["POST"])
@login_required
def private_access_action(access_id, action):
    access = _own_access_or_404(access_id)
    viewer = db.session.get(User, access.viewer_id)
    if ActionForm().validate_on_submit():
        try:
            if action == "alle":
                private.approve(g.user, access, "all")
                message = f"{viewer.display_name} har nu adgang til hele dit private album."
            elif action == "afvis":
                private.decline(g.user, access)
                message = f"Anmodningen fra {viewer.display_name} er afvist."
            else:
                private.revoke(g.user, access)
                message = f"{viewer.display_name} har ikke længere adgang til dine private billeder."
            db.session.commit()
            flash(message, "success" if action == "alle" else "info")
        except private.AccessRefused as refusal:
            db.session.rollback()
            flash(str(refusal), "error")
    return redirect(url_for("profiles.private_access"))
