from functools import wraps

from flask import Blueprint, abort, flash, g, redirect, render_template, request, send_file, session, url_for
from flask_wtf import FlaskForm
from flask_wtf.file import FileField
from sqlalchemy import func, select

from ..auth.models import User
from ..auth.sessions import login_required
from ..extensions import db, limiter
from ..media import storage
from ..media.models import ProfileImage
from ..media.processing import ACCEPT
from ..media.services import LIVE
from . import services
from .models import STATUS_LABELS, PhotoVerification

bp = Blueprint("verification", __name__)

GESTURE_KEY = "verify_gesture"


class SelfieForm(FlaskForm):
    selfie = FileField("Din selfie")


def staff_required(view):
    """Moderatorer og administratorer. Andre får 404 (auth/roles.py)."""
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if g.user.role not in ("moderator", "admin"):
            abort(404)
        return view(*args, **kwargs)
    return wrapped


@bp.app_template_global("pending_verifications")
def pending_verifications():
    return db.session.scalar(select(func.count(PhotoVerification.id)).where(PhotoVerification.status == "pending"))


# --- Brugeren -----------------------------------------------------------------------------------------------


@bp.route("/profil/verificering", methods=["GET", "POST"])
@login_required
@limiter.limit("10 per day", methods=["POST"])
def start():
    form = SelfieForm()
    # Gestussen gemmes i sessionen, så den ikke kan vælges frit ved at ændre formularen.
    gesture = session.get(GESTURE_KEY)
    if gesture not in services.GESTURES:
        gesture = session[GESTURE_KEY] = services.random_gesture()
    if form.validate_on_submit():
        upload = request.files.get("selfie")
        try:
            services.submit(g.user, gesture, upload.read() if upload and upload.filename else b"")
            db.session.commit()
        except services.VerificationRefused as refusal:
            db.session.rollback()
            form.selfie.errors = [str(refusal)]
        else:
            session.pop(GESTURE_KEY, None)
            flash("Tak! Din selfie er sendt. Du får besked i Beskeder, når den er vurderet.", "success")
            return redirect(url_for("profiles.me") + "#verificering")
    return (render_template("verification/start.html", form=form, gesture=services.GESTURES[gesture],
                            latest=services.latest(g.user), has_public=bool(services.public_images(g.user.id)),
                            accept=ACCEPT),
            400 if form.errors else 200)


# --- Moderation ---------------------------------------------------------------------------------------------


def _row_or_404(verification_id):
    row = db.session.get(PhotoVerification, verification_id)
    if row is None:
        abort(404)
    return row


@bp.route("/moderation/verificeringer")
@staff_required
def queue():
    pending, decided = services.pending(), services.recent_decisions()
    ids = {r.user_id for r in pending + decided}
    users = {u.id: u for u in db.session.scalars(select(User).where(User.id.in_(ids)))} if ids else {}
    return render_template("verification/queue.html", pending=pending, decided=decided, users=users,
                           statuses=STATUS_LABELS)


@bp.route("/moderation/verificering/<int:verification_id>", methods=["GET", "POST"])
@staff_required
def review(verification_id):
    row = _row_or_404(verification_id)
    user = db.session.get(User, row.user_id)
    if request.method == "POST":
        if not FlaskForm().validate_on_submit():
            abort(400)
        action, note = request.form.get("action"), request.form.get("note")
        try:
            if action in ("approve", "reject"):
                services.decide(g.user, row, action == "approve", note)
                flash("Verificeringen er godkendt." if action == "approve" else "Verificeringen er afvist.", "success")
            elif action == "revoke":
                services.revoke(g.user, user, note)
                flash("Mærket er fjernet.", "success")
            else:
                abort(400)
            db.session.commit()
        except services.VerificationRefused as refusal:
            db.session.rollback()
            flash(str(refusal), "error")
            return redirect(url_for("verification.review", verification_id=row.id))
        return redirect(url_for("verification.queue"))
    return render_template("verification/review.html", row=row, user=user, gesture=services.GESTURES.get(row.gesture),
                           images=services.public_images(user.id), statuses=STATUS_LABELS,
                           decided_by=db.session.get(User, row.decided_by_id) if row.decided_by_id else None,
                           form=FlaskForm())


def _send(path):
    response = send_file(path, mimetype="image/webp")
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.route("/moderation/verificering/<int:verification_id>/selfie.webp")
@staff_required
def selfie(verification_id):
    row = _row_or_404(verification_id)
    if not row.public_id:
        abort(404)
    return _send(storage.file_path(row, "stor"))


@bp.route("/moderation/verificering/<int:verification_id>/billede/<public_id>.webp")
@staff_required
def profile_image(verification_id, public_id):
    """Brugerens offentlige billeder i fuld størrelse til sammenligning med selfien."""
    row = _row_or_404(verification_id)
    image = db.session.scalar(select(ProfileImage).where(
        ProfileImage.public_id == public_id, ProfileImage.user_id == row.user_id,
        ProfileImage.collection == "public", LIVE))
    if image is None:
        abort(404)
    return _send(storage.file_path(image, "stor"))
