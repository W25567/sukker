from flask import Blueprint, abort, flash, g, redirect, render_template, request, send_file, url_for
from flask_wtf import FlaskForm
from sqlalchemy import or_, select
from wtforms import DateField, EmailField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Email, Length

from ..auth.models import User
from ..auth.roles import admin_required, moderator_required
from ..core.settings import get_setting
from ..extensions import db, limiter
from ..media import storage
from ..media.models import ProfileImage
from . import services, userdata
from .models import DECISIONS, REASON_LABELS, STATUSES, ModerationCase

bp = Blueprint("moderation", __name__)


class NoteForm(FlaskForm):
    text = TextAreaField("Note", validators=[DataRequired("Skriv en note."), Length(max=2000)])


class DecisionForm(FlaskForm):
    decision = SelectField("Afgørelse", choices=DECISIONS)
    note = TextAreaField("Begrundelse", validators=[DataRequired("Skriv en begrundelse."), Length(max=2000)])


class RetentionForm(FlaskForm):
    until = DateField("Opbevar beviser til og med", validators=[DataRequired("Vælg en dato.")])
    reason = StringField("Begrundelse", validators=[DataRequired("Skriv en begrundelse."), Length(max=500)])


class AccessForm(FlaskForm):
    case_id = SelectField("Sag", coerce=str, validate_choice=False)
    reason = StringField("Begrundelse", validators=[DataRequired("Skriv en begrundelse."), Length(max=500)])


class NewCaseForm(FlaskForm):
    reason = TextAreaField("Hvad mistænker du?", validators=[DataRequired("Beskriv mistanken."), Length(max=2000)])


class AppealForm(FlaskForm):
    email = EmailField("Din e-mail", validators=[DataRequired("Skriv din e-mail."), Email("Skriv en gyldig e-mail.")])
    text = TextAreaField("Hvorfor er afgørelsen forkert?", validators=[DataRequired("Skriv din klage."), Length(max=3000)])


def _case_or_404(case_id):
    case = db.session.get(ModerationCase, case_id)
    if case is None:
        abort(404)
    return case


# --- Sager ----------------------------------------------------------------------------------------------


@bp.route("/moderation/")
@moderator_required
def queue():
    closed = request.args.get("lukkede") == "1"
    cases = services.queue(("closed",) if closed else ("open", "in_progress"))
    users = {u.id: u.display_name for u in db.session.scalars(
        select(User).where(User.id.in_({c.subject_user_id for c in cases if c.subject_user_id})))}
    return render_template("moderation/queue.html", cases=cases, users=users, closed=closed,
                           reasons=REASON_LABELS, statuses=dict(STATUSES))


@bp.route("/moderation/sag/<int:case_id>", methods=["GET", "POST"])
@moderator_required
def case(case_id):
    case = _case_or_404(case_id)
    note_form, decision_form = NoteForm(prefix="note"), DecisionForm(prefix="decision")
    retention_form = RetentionForm(prefix="retention")
    action = request.form.get("action")
    if request.method == "POST":
        try:
            if action == "assign" and FlaskForm().validate_on_submit():
                services.assign(case, g.user)
            elif action == "note" and note_form.validate_on_submit():
                services.add_note(case, g.user, note_form.text.data.strip())
            elif action == "decide" and decision_form.validate_on_submit():
                services.decide(case, g.user, decision_form.decision.data, decision_form.note.data.strip())
            elif action == "retain" and retention_form.validate_on_submit():
                services.retain_evidence(case, g.user, retention_form.until.data, retention_form.reason.data.strip())
            elif action == "reopen" and FlaskForm().validate_on_submit():
                services.reopen(case, g.user)
            else:
                flash("Handlingen kunne ikke udføres. Tjek felterne.", "error")
                return redirect(url_for("moderation.case", case_id=case.id))
            db.session.commit()
        except services.ModerationError as error:
            db.session.rollback()
            flash(str(error), "error")
        return redirect(url_for("moderation.case", case_id=case.id))

    return render_template(
        "moderation/case.html", case=case, events=services.case_events(case),
        subject=db.session.get(User, case.subject_user_id) if case.subject_user_id else None,
        reporter=db.session.get(User, case.reporter_id) if case.reporter_id else None,
        note_form=note_form, decision_form=decision_form, retention_form=retention_form, action_form=FlaskForm(),
        retention_months=get_setting("data_retention_months"),
        reasons=REASON_LABELS, statuses=dict(STATUSES), decisions=dict(DECISIONS),
    )


@bp.route("/moderation/sag/<int:case_id>/bevis/<public_id>.webp")
@moderator_required
def evidence_image(case_id, public_id):
    path = services.evidence_image_path(_case_or_404(case_id), public_id)
    if path is None:
        abort(404)
    response = send_file(path, mimetype="image/webp")
    response.headers["Cache-Control"] = "private, no-store"
    return response


# --- Brugerdata ---------------------------------------------------------------------------------------------


@bp.route("/moderation/brugere")
@moderator_required
def users():
    query = (request.args.get("q") or "").strip()
    results = []
    if query:
        results = db.session.scalars(
            select(User).where(or_(User.display_name.ilike(f"%{query}%"), User.email == query.lower())).order_by(User.display_name).limit(50)
        ).all()
    return render_template("moderation/users.html", query=query, results=results)


@bp.route("/moderation/bruger/<int:user_id>")
@moderator_required
def user(user_id):
    target = db.session.get(User, user_id)
    if target is None:
        abort(404)
    if not services.has_recent_access(g.user, target.id):
        form = AccessForm()
        open_cases = services.queue(("open", "in_progress"))
        form.case_id.choices = [("", "Ingen sag")] + [(str(c.id), f"Sag {c.id}") for c in open_cases
                                                      if c.subject_user_id == target.id]
        return render_template("moderation/access.html", target=target, form=form, new_case_form=NewCaseForm(prefix="ny"))
    return render_template("moderation/user.html", data=userdata.collect(target), reasons=REASON_LABELS,
                           statuses=dict(STATUSES))


@bp.route("/moderation/bruger/<int:user_id>/adgang", methods=["POST"])
@moderator_required
def request_access(user_id):
    target = db.session.get(User, user_id)
    form = AccessForm()
    if target is None:
        abort(404)
    if form.validate_on_submit():
        case = db.session.get(ModerationCase, int(form.case_id.data)) if form.case_id.data else None
        services.log_access(g.user, target, form.reason.data, case)
        db.session.commit()
    else:
        flash("Skriv en begrundelse for adgangen.", "error")
    return redirect(url_for("moderation.user", user_id=user_id))


@bp.route("/moderation/bruger/<int:user_id>/ny-sag", methods=["POST"])
@moderator_required
def new_case(user_id):
    target = db.session.get(User, user_id)
    form = NewCaseForm(prefix="ny")
    if target is None:
        abort(404)
    if not form.validate_on_submit():
        flash("Beskriv mistanken.", "error")
        return redirect(url_for("moderation.user", user_id=user_id))
    case = services.create_case(source="moderator", category="mistanke", subject=target, actor=g.user,
                                description=form.reason.data.strip(), evidence=services.profile_evidence(target))
    services.assign(case, g.user)
    db.session.commit()
    return redirect(url_for("moderation.case", case_id=case.id))


@bp.route("/moderation/bruger/<int:user_id>/billede/<public_id>/<variant>.webp")
@moderator_required
def user_image(user_id, public_id, variant):
    """Billeder, også private, til moderatorvisningen. Kræver en registreret adgang til brugeren."""
    image = db.session.scalar(select(ProfileImage).where(ProfileImage.public_id == public_id, ProfileImage.user_id == user_id))
    if image is None or variant not in storage.VARIANTS or not services.has_recent_access(g.user, user_id):
        abort(404)
    response = send_file(storage.file_path(image, variant), mimetype="image/webp")
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.route("/moderation/log")
@admin_required
def log():
    """Revisionsloggen. Kun for administratorer, så moderatorer ikke selv gennemgår deres adgang."""
    return render_template("moderation/log.html", entries=services.access_log())


# --- Klage ----------------------------------------------------------------------------------------------------


@bp.route("/klage", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def appeal():
    """Klagevej for begrænsede og suspenderede konti (specifikation 9.3). Svaret afslører ikke, om e-mailen findes."""
    form = AppealForm()
    if g.user is not None and not form.is_submitted():
        form.email.data = g.user.email
    if form.validate_on_submit():
        subject = db.session.scalar(select(User).where(User.email == form.email.data.strip().lower()))
        services.create_case(source="appeal", category="klage", subject=subject, reporter=subject,
                             description=form.text.data.strip())
        db.session.commit()
        return render_template("moderation/appeal_received.html")
    return render_template("moderation/appeal.html", form=form)
