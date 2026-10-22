import re

from flask import Blueprint, current_app, flash, g, jsonify, redirect, render_template, request, session, url_for
from flask_limiter.util import get_remote_address
from sqlalchemy.exc import IntegrityError

from ..brands import current_brand
from ..extensions import db, limiter
from ..location.services import geo_loaded, kommuner_for_postcode
from . import services, tokens
from .forms import ActionForm, ForgotPasswordForm, LoginForm, RegistrationForm, ResetPasswordForm
from .sessions import end_current_session, login_required, safe_next_url, start_session

bp = Blueprint("auth", __name__)

LOGIN_FAILED = "Forkert e-mail eller password."
PREFILL_EMAIL_KEY = "login_email"


def _email_key():
    """Begrænser loginforsøg pr. konto, uanset hvilken IP de kommer fra."""
    return (request.form.get("email") or "").strip().lower() or get_remote_address()


@bp.route("/opret", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def register():
    if g.user is not None:
        return redirect(url_for("core.home"))
    form = RegistrationForm()
    if form.validate_on_submit():
        kommune_code = _resolve_kommune(form)
        if kommune_code is not False:
            try:
                services.register_user(form, kommune_code)
                db.session.commit()
            except IntegrityError:
                # Samtidig oprettelse med samme e-mail: samme svar som altid, så siden ikke afslører noget.
                db.session.rollback()
            return render_template("auth/check_email.html", email=form.email.data)
    return render_template("auth/register.html", form=form)


@bp.route("/opret/kommuner")
@limiter.limit("60 per minute")
def kommuner():
    """Kommunerne for et postnummer. Tilmeldingen viser kommunevælgeren, så snart postnummeret er skrevet, i stedet
    for efter "Opret profil", hvor passwordet ellers skulle skrives igen (static/js/kommune.js). Offentlige data."""
    postcode = request.args.get("postnummer", "")
    if not re.fullmatch(r"\d{4}", postcode) or not geo_loaded():
        return jsonify([])
    return jsonify([{"code": k.code, "name": k.name} for k in kommuner_for_postcode(postcode)])


def _resolve_kommune(form):
    """Finder kommunen ud fra postnummeret (specifikation 5).

    Returnerer kommunekoden, None hvis kommunedata ikke er indlæst endnu, eller False hvis brugeren
    skal rette noget. Dækker postnummeret flere kommuner, vælger brugeren selv.
    """
    if not geo_loaded():
        return None
    options = kommuner_for_postcode(form.postcode.data)
    if not options:
        form.postcode.errors.append("Postnummeret findes ikke.")
        return False
    if len(options) == 1:
        return options[0].code
    form.kommune.choices = [("", "Vælg kommune")] + [(k.code, k.name) for k in options]
    if form.kommune.data in {k.code for k in options}:
        return form.kommune.data
    form.kommune.errors = ["Postnummeret dækker flere kommuner. Vælg den, du bor i."]
    return False


@bp.route("/log-ind", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
@limiter.limit("20 per hour", key_func=_email_key, methods=["POST"])
def login():
    if g.user is not None:
        return redirect(url_for("core.home"))
    form = LoginForm()
    if form.validate_on_submit():
        user = services.authenticate(form.email.data, form.password.data)
        if user is None:
            flash(LOGIN_FAILED, "error")
        elif user.status == "suspended":
            return render_template("auth/suspended.html"), 403
        elif user.status == "expelled":
            return render_template("auth/expelled.html", contact_email=current_app.config["CONTACT_EMAIL"],
                                   company_name=current_app.config["COMPANY_NAME"]), 403
        else:
            start_session(user, remember=form.remember.data)
            db.session.commit()
            return redirect(safe_next_url(request.args.get("next")) or url_for("core.home"))
    if not form.is_submitted() and session.get(PREFILL_EMAIL_KEY):
        form.email.data = session.pop(PREFILL_EMAIL_KEY)
    return render_template("auth/login.html", form=form)


@bp.route("/log-ud", methods=["POST"])
@login_required
def logout():
    if ActionForm().validate_on_submit():
        end_current_session()
        db.session.commit()
        flash("Du er logget ud.", "info")
    return redirect(url_for("core.home"))


@bp.route("/bekraeft-email/<token>")
def verify_email(token):
    user = services.verify_email(token)
    db.session.commit()
    if user is None:
        return render_template("auth/link_invalid.html", purpose=tokens.VERIFY_EMAIL), 400
    if g.user is not None and g.user.id == user.id:
        flash(f"Din e-mail er bekræftet. Velkommen til {current_brand().name}.", "success")
        return redirect(url_for("explore.index"))
    if g.user is not None:
        # Linket hørte til en anden profil end den, der er logget ind her. Sig det, så banneret om at bekræfte
        # e-mailen ikke ser ud til at være en fejl.
        flash(f"E-mailen {user.email} er bekræftet. Du er logget ind som en anden profil ({g.user.email}).", "info")
        return redirect(url_for("explore.index"))
    # Linket logger ikke ind af sig selv, så et videresendt link ikke giver adgang til kontoen.
    # Brugeren sendes i stedet til login med e-mailen udfyldt.
    session[PREFILL_EMAIL_KEY] = user.email
    flash("Din e-mail er bekræftet. Log ind for at komme i gang.", "success")
    return redirect(url_for("auth.login"))


@bp.route("/bekraeft-email/send-igen", methods=["POST"])
@login_required
@limiter.limit("3 per hour", methods=["POST"])
def resend_verification():
    if ActionForm().validate_on_submit() and not g.user.is_verified:
        sent = services.send_verification_mail(g.user)
        db.session.commit()
        if sent:
            flash("Vi har sendt en ny bekræftelsesmail.", "info")
        else:
            flash("Mailen kunne ikke sendes lige nu. Prøv igen om lidt.", "error")
    return redirect(url_for("core.home"))


@bp.route("/bekraeft-email/ny-mail", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
@limiter.limit("3 per hour", key_func=_email_key, methods=["POST"])
def resend_verification_public():
    """Ny bekræftelsesmail uden at være logget ind. Svaret er det samme, uanset om e-mailen findes."""
    form = ForgotPasswordForm()
    if form.validate_on_submit():
        services.resend_verification(form.email.data)
        db.session.commit()
        return render_template("auth/verification_resent.html")
    return render_template("auth/resend_verification.html", form=form)


@bp.route("/glemt-password", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def forgot_password():
    form = ForgotPasswordForm()
    if form.validate_on_submit():
        services.request_password_reset(form.email.data)
        db.session.commit()
        return render_template("auth/reset_requested.html")
    return render_template("auth/forgot_password.html", form=form)


@bp.route("/nulstil-password/<token>", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def reset_password(token):
    if tokens.peek_token(token, tokens.RESET_PASSWORD) is None:
        return render_template("auth/link_invalid.html", purpose=tokens.RESET_PASSWORD), 400
    form = ResetPasswordForm()
    if form.validate_on_submit():
        user = services.reset_password(token, form.password.data)
        if user is None:
            db.session.rollback()
            return render_template("auth/link_invalid.html", purpose=tokens.RESET_PASSWORD), 400
        end_current_session()
        db.session.commit()
        flash("Dit password er ændret, og du er logget ud på alle enheder. Log ind med dit nye password.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/reset_password.html", form=form)
