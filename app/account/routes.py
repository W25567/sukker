from flask import Blueprint, flash, g, redirect, render_template, url_for

from ..auth import services
from ..auth.forms import ActionForm, ChangePasswordForm
from ..auth.passwords import verify_password
from ..auth.sessions import active_sessions, end_current_session, login_required, revoke_sessions
from ..extensions import db, limiter
from ..subscriptions.services import TIER_LABELS, active_subscription, effective_tier
from .devices import describe_device

bp = Blueprint("account", __name__, url_prefix="/konto")
bp.add_app_template_filter(describe_device, "device")


def _render_account(password_form=None, status=200):
    return (
        render_template(
            "account/account.html",
            password_form=password_form or ChangePasswordForm(),
            action_form=ActionForm(),
            sessions=active_sessions(g.user.id),
            current_session_id=g.user_session.id,
            tier_label=TIER_LABELS[effective_tier(g.user.id)],
            subscription=active_subscription(g.user.id),
        ),
        status,
    )


@bp.route("/")
@login_required
def overview():
    return _render_account()


@bp.route("/password", methods=["POST"])
@login_required
@limiter.limit("10 per hour", methods=["POST"])
def change_password():
    form = ChangePasswordForm()
    if not form.validate_on_submit():
        return _render_account(form, 400)
    if not verify_password(g.user.password_hash, form.current_password.data):
        form.current_password.errors.append("Det nuværende password er forkert.")
        return _render_account(form, 400)
    services.set_password(g.user, form.password.data)
    revoke_sessions(g.user.id, keep_session_id=g.user_session.id)
    db.session.commit()
    flash("Dit password er ændret. Du er logget ud på alle andre enheder.", "success")
    return redirect(url_for("account.overview"))


@bp.route("/log-ud-andre", methods=["POST"])
@login_required
def logout_other_sessions():
    if ActionForm().validate_on_submit():
        revoke_sessions(g.user.id, keep_session_id=g.user_session.id)
        db.session.commit()
        flash("Du er logget ud på alle andre enheder.", "success")
    return redirect(url_for("account.overview"))


@bp.route("/log-ud-overalt", methods=["POST"])
@login_required
def logout_everywhere():
    if ActionForm().validate_on_submit():
        revoke_sessions(g.user.id)
        end_current_session()
        db.session.commit()
        flash("Du er logget ud på alle enheder.", "info")
    return redirect(url_for("core.home"))
