from datetime import datetime, time

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for
from flask_wtf import FlaskForm
from sqlalchemy import or_, select

from ..auth.models import User
from ..auth.roles import admin_required
from ..core import features
from ..core.settings import SETTING_META, get_setting
from ..core.timeutil import DANISH_TZ
from ..extensions import db
from ..subscriptions.services import TIER_LABELS, active_subscription, effective_tier
from ..subscriptions.models import Subscription
from . import metrics, services
from .models import UserFeatureGrant

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/")
@admin_required
def dashboard():
    return render_template("admin/dashboard.html", m=metrics.collect(), system=metrics.system_status(current_app))


@bp.route("/brugere")
@admin_required
def users():
    query = (request.args.get("q") or "").strip()
    results = []
    if query:
        results = db.session.scalars(
            select(User).where(or_(User.display_name.ilike(f"%{query}%"), User.email == query.lower()))
            .order_by(User.display_name).limit(50)
        ).all()
    return render_template("admin/users.html", query=query, results=results, tests=services.test_accounts())


def _parse_date(raw):
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise services.AdminError("Vælg en gyldig dato.")


@bp.route("/bruger/<int:user_id>", methods=["GET", "POST"])
@admin_required
def user(user_id):
    target = db.session.get(User, user_id)
    if target is None:
        abort(404)
    if request.method == "POST":
        if not FlaskForm().validate_on_submit():
            abort(400)
        form, action = request.form, request.form.get("action")
        note = (form.get("note") or "").strip() or None
        try:
            if action == "status":
                if form.get("status") not in ("active", "restricted", "suspended"):
                    raise services.AdminError("Ukendt status.")
                services.set_status(g.user, target, form["status"], note)
            elif action == "role":
                if form.get("role") not in ("user", "moderator", "admin"):
                    raise services.AdminError("Ukendt rolle.")
                services.set_role(g.user, target, form["role"])
            elif action == "period":
                months = int(form.get("months") or 0)
                if form.get("tier") not in ("premium", "premium_plus") or not 1 <= months <= 36:
                    raise services.AdminError("Vælg niveau og 1-36 måneder.")
                services.add_period(g.user, target, form["tier"], months, note)
            elif action == "set_tier":
                if form.get("tier") not in features.TIERS:
                    raise services.AdminError("Ukendt niveau.")
                until = _parse_date(form.get("until")) if form["tier"] != "free" else None
                services.set_tier_until(g.user, target, form["tier"], until, note)
            elif action == "grant":
                expires = _parse_date(form["expires"]) if form.get("expires") else None
                expires_at = datetime.combine(expires, time(23, 59, 59), tzinfo=DANISH_TZ) if expires else None
                services.add_grant(g.user, target, form.get("feature"), form.get("granted") == "1", expires_at, note)
            elif action == "remove_grant":
                grant = db.session.get(UserFeatureGrant, int(form.get("grant_id") or 0))
                if grant is None or grant.user_id != target.id:
                    abort(404)
                services.remove_grant(g.user, grant)
            else:
                abort(400)
            db.session.commit()
            flash("Ændringen er gemt og registreret i revisionsloggen.", "success")
        except (services.AdminError, ValueError) as error:
            db.session.rollback()
            flash(str(error) if isinstance(error, services.AdminError) else "Ugyldig værdi.", "error")
        return redirect(url_for("admin.user", user_id=target.id))

    subscription = active_subscription(target.id)
    return render_template(
        "admin/user.html",
        target=target,
        tier_label=TIER_LABELS[effective_tier(target.id)],
        subscription=subscription,
        periods=db.session.scalars(select(Subscription).where(Subscription.user_id == target.id).order_by(Subscription.starts_at.desc())).all(),
        feature_rows=[(key, label, *features.feature_source(target, key)) for key, label in features.FEATURES.items()],
        grants=services.grants_for(target),
        feature_labels=features.FEATURES,
        tier_labels=TIER_LABELS,
        form=FlaskForm(),
    )


@bp.route("/indstillinger", methods=["GET", "POST"])
@admin_required
def settings():
    if request.method == "POST":
        if not FlaskForm().validate_on_submit():
            abort(400)
        try:
            values = {key: services.parse_setting(kind, request.form.get(key)) for key, _, kind in SETTING_META}
            mapping = {tier: [f for f in features.FEATURES if request.form.get(f"tier:{tier}:{f}")] for tier in features.TIERS}
            disabled = [f for f in features.FEATURES if request.form.get(f"disabled:{f}")]
            services.update_settings(g.user, values)
            services.update_features(g.user, mapping, disabled)
            db.session.commit()
            flash("Indstillingerne er gemt og registreret i revisionsloggen.", "success")
        except services.AdminError as error:
            db.session.rollback()
            flash(str(error), "error")
        return redirect(url_for("admin.settings"))
    return render_template(
        "admin/settings.html",
        settings=[(key, label, kind, get_setting(key)) for key, label, kind in SETTING_META],
        features=features.FEATURES,
        tiers=features.TIERS,
        tier_labels=TIER_LABELS,
        mapping=features.tier_features(),
        disabled=features.disabled_features(),
        form=FlaskForm(),
    )


@bp.route("/log")
@admin_required
def log():
    return render_template("admin/log.html", entries=services.audit_log())
