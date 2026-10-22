from flask import Blueprint, flash, g, redirect, render_template, url_for
from flask_wtf import FlaskForm
from wtforms import BooleanField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length

from ..auth.forms import ActionForm
from ..auth.sessions import login_required
from ..extensions import db, limiter
from . import services

bp = Blueprint("staffing", __name__)


class ApplicationForm(FlaskForm):
    motivation = TextAreaField("Hvorfor vil du gerne være moderator?", validators=[
        DataRequired("Fortæl, hvorfor du gerne vil være moderator."), Length(max=services.MAX_TEXT)])
    experience = TextAreaField("Har du erfaring med moderation, frivilligt arbejde eller lignende? (valgfrit)",
                               validators=[Length(max=services.MAX_TEXT)])
    availability = StringField("Hvor meget tid kan du bruge om ugen? (valgfrit)", validators=[Length(max=200)])
    confidentiality = BooleanField("Jeg forstår, at al min adgang til brugeres oplysninger registreres, og at jeg har "
                                   "tavshedspligt om det, jeg ser som moderator",
                                   validators=[DataRequired("Du skal acceptere tavshedspligten.")])


@bp.route("/moderator/ansoeg", methods=["GET", "POST"])
@login_required
@limiter.limit("5 per day", methods=["POST"])
def apply():
    """Søg om at blive moderator. Svaret kommer som besked fra Administrator."""
    form = ApplicationForm()
    if form.validate_on_submit():
        try:
            services.apply(g.user, form.motivation.data, form.experience.data, form.availability.data)
            db.session.commit()
            flash("Tak for din ansøgning. Du får svar i Beskeder fra Administrator.", "success")
            return redirect(url_for("staffing.apply"))
        except services.ApplicationRefused as refusal:
            db.session.rollback()
            flash(str(refusal), "error")
    return render_template("staffing/apply.html", form=form, action_form=ActionForm(),
                           reason=services.cannot_apply(g.user), pending=services.pending_for(g.user),
                           latest=services.latest_for(g.user))


@bp.route("/moderator/ansoeg/traek-tilbage", methods=["POST"])
@login_required
def withdraw():
    if ActionForm().validate_on_submit():
        services.withdraw(g.user)
        db.session.commit()
        flash("Din ansøgning er trukket tilbage.", "info")
    return redirect(url_for("staffing.apply"))
