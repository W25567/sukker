from datetime import datetime, time

from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, send_file, url_for
from flask_wtf import FlaskForm
from sqlalchemy import func, select

from ..account.services import retention_status
from ..auth.models import User
from ..media import storage
from ..media.models import ProfileImage
from ..messaging import admin_threads
from ..moderation import content, userdata
from ..moderation.models import DECISIONS, REASON_LABELS, STATUSES
from ..profiles.zodiac import zodiac_for
from ..auth.roles import admin_required
from ..core import features
from ..core.settings import SETTING_META, get_setting
from ..core.timeutil import DANISH_TZ
from ..extensions import db
from ..subscriptions.services import TIER_LABELS, active_subscription, effective_tier
from ..subscriptions.models import Subscription
from ..support import services as support
from ..support.models import MODULE_LABELS, BugReport, BugReportImage
from . import browse, metrics, services
from .models import UserFeatureGrant

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/")
@admin_required
def dashboard():
    return render_template("admin/dashboard.html", m=metrics.collect(), system=metrics.system_status(current_app))


@bp.route("/brugere")
@admin_required
def users():
    """Brugerliste med filtre og sortering. Alle tal på overblikket linker hertil."""
    filter_key = request.args.get("filter", "alle")
    sort = request.args.get("sort", "nyeste")
    query = (request.args.get("q") or "").strip()
    page = max(1, request.args.get("side", 1, type=int) or 1)
    rows, total, has_more = browse.user_list(filter_key, query, sort, page)
    labels = browse.filter_labels()
    return render_template(
        "admin/users.html", rows=rows, total=total, has_more=has_more, page=page, query=query,
        filter_key=filter_key if filter_key in labels else "alle", sort=sort if sort in browse.SORTS else "nyeste",
        filters=labels, sorts=browse.SORTS,
    )


@bp.route("/slettede")
@admin_required
def deleted_users():
    """Slettede profiler, der bevares i opbevaringsperioden af hensyn til sporbarhed."""
    query = (request.args.get("q") or "").strip()
    page = max(1, request.args.get("side", 1, type=int) or 1)
    rows, total, has_more = browse.deleted_list(query, page)
    return render_template("admin/deleted.html", rows=rows, total=total, has_more=has_more, page=page, query=query,
                           retention_months=get_setting("data_retention_months"))


@bp.route("/fejl")
@admin_required
def bug_reports():
    """Fejlrapporter fra brugerne. Åbne først."""
    show_closed = request.args.get("lukkede") == "1"
    reports = db.session.scalars(
        select(BugReport).where(BugReport.status == ("closed" if show_closed else "open"))
        .order_by(BugReport.created_at.desc()).limit(200)
    ).all()
    users = {u.id: u for u in db.session.scalars(select(User).where(User.id.in_({r.user_id for r in reports if r.user_id})))}
    counts = dict(db.session.execute(
        select(BugReportImage.report_id, func.count(BugReportImage.id))
        .where(BugReportImage.report_id.in_([r.id for r in reports])).group_by(BugReportImage.report_id)
    ).all()) if reports else {}
    return render_template("admin/bug_reports.html", reports=reports, users=users, counts=counts,
                           show_closed=show_closed, modules=MODULE_LABELS)


@bp.route("/fejl/<int:report_id>", methods=["GET", "POST"])
@admin_required
def bug_report(report_id):
    report = db.session.get(BugReport, report_id)
    if report is None:
        abort(404)
    if request.method == "POST":
        if not FlaskForm().validate_on_submit() or request.form.get("status") not in ("open", "closed"):
            abort(400)
        support.set_status(report, g.user, request.form["status"], request.form.get("note"))
        db.session.commit()
        flash("Fejlrapporten er lukket." if report.status == "closed" else "Fejlrapporten er åbnet igen.", "success")
        return redirect(url_for("admin.bug_reports"))
    reporter = db.session.get(User, report.user_id) if report.user_id else None
    return render_template("admin/bug_report.html", report=report, reporter=reporter, images=support.images_for(report),
                           modules=MODULE_LABELS, form=FlaskForm())


@bp.route("/fejl/<int:report_id>/billede/<public_id>/<variant>.webp")
@admin_required
def bug_report_image(report_id, public_id, variant):
    image = db.session.scalar(select(BugReportImage).where(BugReportImage.public_id == public_id,
                                                           BugReportImage.report_id == report_id))
    if image is None or variant not in storage.VARIANTS:
        abort(404)
    return _send_image(image, variant)


@bp.route("/tilsyn")
@admin_required
def oversight():
    """Hvem skiller sig ud? Signaler, der kan pege på brud på reglerne."""
    sections, new_without_image, new_without_image_total = browse.oversight()
    return render_template("admin/oversight.html", sections=sections, new_without_image=new_without_image,
                           new_without_image_total=new_without_image_total)


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
            elif action == "remove_name":
                content.remove_display_name(g.user, target, note)
            elif action == "remove_grant":
                grant = db.session.get(UserFeatureGrant, int(form.get("grant_id") or 0))
                if grant is None or grant.user_id != target.id:
                    abort(404)
                services.remove_grant(g.user, grant)
            else:
                abort(400)
            db.session.commit()
            flash("Ændringen er gemt og registreret i revisionsloggen.", "success")
        except (services.AdminError, content.ContentError, ValueError) as error:
            db.session.rollback()
            flash(str(error) if isinstance(error, (services.AdminError, content.ContentError)) else "Ugyldig værdi.", "error")
        return redirect(url_for("admin.user", user_id=target.id))

    # Administratorer ser alle oplysninger om brugeren. Hver visning registreres i administrationsloggen.
    services.audit(g.user, "view_user", target)
    db.session.commit()
    subscription = active_subscription(target.id)
    purge_after, on_hold = retention_status(target)
    return render_template(
        "admin/user.html",
        target=target,
        purge_after=purge_after,
        on_hold=on_hold,
        # Tidligere, slettede konti med samme e-mail, fx en bruger, der sletter sig og opretter sig igen.
        previous_accounts=db.session.scalars(select(User).where(
            User.deleted_email == target.admin_email, User.id != target.id)).all(),
        data=userdata.collect(target),
        signals=browse.user_signals(target),
        zodiac=zodiac_for(target.birthdate),
        user_log=services.audit_log_for(target),
        audit_labels=services.AUDIT_LABELS,
        unread_thread=admin_threads.unread_for_admin(target.id),
        reasons=REASON_LABELS, statuses=dict(STATUSES), decisions=dict(DECISIONS),
        tier_label=TIER_LABELS[effective_tier(target.id)],
        subscription=subscription,
        periods=db.session.scalars(select(Subscription).where(Subscription.user_id == target.id).order_by(Subscription.starts_at.desc())).all(),
        feature_rows=[(key, label, *features.feature_source(target, key)) for key, label in features.FEATURES.items()],
        grants=services.grants_for(target),
        feature_labels=features.FEATURES,
        tier_labels=TIER_LABELS,
        form=FlaskForm(),
        image_reasons=content.IMAGE_REASONS,
        removed_name=content.REMOVED_NAME,
    )


@bp.route("/bruger/<int:user_id>/billede/<public_id>/fjern", methods=["POST"])
@admin_required
def remove_image(user_id, public_id):
    """Fjern et upassende billede (specifikation 11: moderere profilbilleder). Registreres i loggen og som sag."""
    owner = db.session.get(User, user_id)
    image = db.session.scalar(select(ProfileImage).where(ProfileImage.public_id == public_id, ProfileImage.user_id == user_id))
    if owner is None or image is None:
        abort(404)
    if FlaskForm().validate_on_submit():
        try:
            case = content.remove_image(g.user, image, owner, request.form.get("reason"))
            services.audit(g.user, "image_removed", owner, image=image.public_id, reason=image.moderation_reason,
                           case_id=case.id)
            db.session.commit()
            flash(f"Billedet er fjernet og registreret i loggen (sag {case.id}).", "success")
        except content.ContentError as error:
            db.session.rollback()
            flash(str(error), "error")
    return redirect(url_for("admin.user", user_id=user_id) + "#billeder")


@bp.route("/samtaler")
@admin_required
def threads():
    """Alle samtaler mellem brugere og "Administrator". Ulæste svar øverst."""
    return render_template("admin/threads.html", threads=admin_threads.admin_overview())


@bp.route("/bruger/<int:user_id>/samtale", methods=["GET", "POST"])
@admin_required
def user_thread(user_id):
    """Skriv til brugeren som "Administrator", fx en henstilling. Hver besked registreres i loggen."""
    target = db.session.get(User, user_id)
    if target is None:
        abort(404)
    form = FlaskForm()
    if request.method == "POST":
        if not form.validate_on_submit():
            abort(400)
        try:
            message = admin_threads.send_from_admin(g.user, target, request.form.get("body"))
            services.audit(g.user, "admin_message", target, message_id=message.id, length=len(message.body))
            db.session.commit()
            flash("Beskeden er sendt fra Administrator og registreret i loggen.", "success")
        except admin_threads.AdminMessageRefused as refusal:
            db.session.rollback()
            flash(str(refusal), "error")
        return redirect(url_for("admin.user_thread", user_id=target.id) + "#bund")
    admin_threads.mark_read_by_admin(target.id)
    db.session.commit()
    thread = admin_threads.thread_for(target.id)
    return render_template(
        "admin/user_thread.html", target=target, form=form, thread=thread, admins=_admin_names(),
        messages=admin_threads.messages(target.id), version=admin_threads.version(target.id), unread_thread=0,
    )


def _admin_names():
    return {u.id: u.display_name for u in db.session.scalars(select(User).where(User.role == "admin"))}


@bp.route("/bruger/<int:user_id>/samtale/nye")
@admin_required
def user_thread_updates(user_id):
    """Automatisk opdatering af samtalen med brugeren. Svarer 204, når intet er ændret, ellers tråden som HTML."""
    target = db.session.get(User, user_id)
    if target is None:
        abort(404)
    version = admin_threads.version(target.id)
    if request.args.get("version") == version:
        response = current_app.response_class(status=204)
    else:
        admin_threads.mark_read_by_admin(target.id)  # administratoren har samtalen åben og ser svaret
        db.session.commit()
        html = render_template("admin/_user_thread.html", target=target, admins=_admin_names(),
                               messages=admin_threads.messages(target.id))
        response = jsonify(version=version, html=html)
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/bruger/<int:user_id>/billede/<public_id>/<variant>.webp")
@admin_required
def user_image(user_id, public_id, variant):
    """Alle brugerens billeder, også private, til brugersiden. Visningen af siden er logget."""
    image = db.session.scalar(select(ProfileImage).where(ProfileImage.public_id == public_id, ProfileImage.user_id == user_id))
    if image is None or variant not in storage.VARIANTS:
        abort(404)
    return _send_image(image, variant)


def _send_image(image, variant):
    response = send_file(storage.file_path(image, variant), mimetype="image/webp")
    response.headers["Cache-Control"] = "private, no-store"
    return response


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
    action = request.args.get("handling") or None
    return render_template("admin/log.html", entries=services.audit_log(action=action), action=action,
                           labels=services.AUDIT_LABELS)
