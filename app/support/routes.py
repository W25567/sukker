from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from flask_wtf import FlaskForm
from wtforms import MultipleFileField, SelectField, TextAreaField
from wtforms.validators import DataRequired, Length

from ..auth.sessions import login_required
from ..extensions import db, limiter
from ..media.processing import ACCEPT
from . import services
from .models import MODULES

bp = Blueprint("support", __name__)


class BugReportForm(FlaskForm):
    module = SelectField("Hvor er fejlen?", choices=[("", "Vælg")] + MODULES,
                         validators=[DataRequired("Vælg, hvor fejlen er.")])
    description = TextAreaField(
        "Beskriv fejlen",
        validators=[DataRequired("Beskriv fejlen."),
                    Length(max=services.MAX_DESCRIPTION, message=f"Højst {services.MAX_DESCRIPTION} tegn.")],
    )
    screenshots = MultipleFileField("Skærmbilleder (valgfrit, op til 3)")


@bp.route("/profil/fejlrapport", methods=["GET", "POST"])
@login_required
@limiter.limit("10 per day", methods=["POST"])
def bug_report():
    """Brugeren rapporterer en fejl med beskrivelse, modul og op til tre skærmbilleder. Ses under
    Administration → Fejlrapporter."""
    form = BugReportForm()
    if form.validate_on_submit():
        files = [f for f in request.files.getlist("screenshots") if f and f.filename]
        try:
            services.create_report(g.user, form.module.data, form.description.data,
                                   [(f.filename, f.read()) for f in files], request.user_agent.string)
            db.session.commit()
        except services.ReportRefused as refusal:
            db.session.rollback()
            if refusal.field == "screenshots":
                form.screenshots.errors = [str(refusal)]
            else:
                flash(str(refusal), "error")
        else:
            flash("Tak! Fejlrapporten er sendt til administrationen.", "success")
            return redirect(url_for("profiles.me"))
    return (render_template("support/bug_report.html", form=form, accept=ACCEPT, max_files=services.MAX_SCREENSHOTS),
            400 if form.errors else 200)
