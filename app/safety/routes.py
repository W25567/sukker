from flask import Blueprint, abort, flash, g, redirect, render_template, url_for
from sqlalchemy import select

from ..auth.forms import ActionForm
from ..auth.models import User
from ..auth.sessions import login_required
from ..extensions import db, limiter
from ..messaging import services as messaging
from ..messaging.models import Conversation, Message
from ..moderation import services as moderation
from ..profiles.services import can_view_profile, find_user_by_handle
from . import services
from .forms import ReportForm

bp = Blueprint("safety", __name__)


def _user_or_404(handle):
    user = find_user_by_handle(handle)
    if user is None or user.id == g.user.id:
        abort(404)
    return user


# --- Skjul og blokér -------------------------------------------------------------------------------------


@bp.route("/bruger/<handle>/skjul", methods=["POST"])
@login_required
def hide(handle):
    target = _user_or_404(handle)
    if ActionForm().validate_on_submit():
        services.hide(g.user, target)
        db.session.commit()
        flash(f"{target.display_name} vises ikke længere for dig i Udforsk. Du kan fortryde under Konto.", "info")
    return redirect(url_for("explore.index"))


@bp.route("/bruger/<handle>/bloker", methods=["GET", "POST"])
@login_required
def block(handle):
    target = _user_or_404(handle)
    form = ActionForm()
    if form.validate_on_submit():
        services.block(g.user, target)
        db.session.commit()
        flash(f"{target.display_name} er blokeret. I kan ikke længere se eller kontakte hinanden.", "info")
        return redirect(url_for("explore.index"))
    return render_template("safety/confirm_block.html", target=target, form=form)


@bp.route("/konto/blokeringer")
@login_required
def settings():
    return render_template(
        "safety/settings.html",
        blocked=services.blocked_users(g.user),
        hidden=services.hidden_users(g.user),
        action_form=ActionForm(),
    )


@bp.route("/konto/blokeringer/<int:user_id>/ophaev", methods=["POST"])
@login_required
def unblock(user_id):
    if ActionForm().validate_on_submit():
        services.unblock(g.user, user_id)
        db.session.commit()
        flash("Blokeringen er ophævet.", "info")
    return redirect(url_for("safety.settings"))


@bp.route("/konto/skjulte/<int:user_id>/vis", methods=["POST"])
@login_required
def unhide(user_id):
    if ActionForm().validate_on_submit():
        services.unhide(g.user, user_id)
        db.session.commit()
        flash("Profilen vises igen i Udforsk.", "info")
    return redirect(url_for("safety.settings"))


# --- Anmeld ----------------------------------------------------------------------------------------------------


def _handle_report(form, target, evidence, back_url, what):
    if form.validate_on_submit():
        moderation.create_case(
            source="report", category=form.reason.data, subject=target, reporter=g.user,
            description=(form.description.data or "").strip() or None, evidence=evidence,
        )
        if target is not None and form.also_block.data:
            services.block(g.user, target)
        elif target is not None and form.also_hide.data:
            services.hide(g.user, target)
        db.session.commit()
        return render_template("safety/reported.html", blocked=bool(form.also_block.data))
    return render_template("safety/report.html", form=form, target=target, back_url=back_url, what=what)


@bp.route("/anmeld/profil/<handle>", methods=["GET", "POST"])
@login_required
@limiter.limit("20 per day", methods=["POST"])
def report_profile(handle):
    target = _user_or_404(handle)
    has_conversation = messaging.access(g.user, messaging.find_conversation(g.user.id, target.id)) is not None
    if not (can_view_profile(g.user, target) or has_conversation):
        abort(404)
    return _handle_report(ReportForm(), target, moderation.profile_evidence(target),
                          url_for("profiles.view", handle=target.handle), "profilen")


@bp.route("/anmeld/samtale/<int:conversation_id>", methods=["GET", "POST"])
@login_required
@limiter.limit("20 per day", methods=["POST"])
def report_conversation(conversation_id):
    conversation = db.session.get(Conversation, conversation_id)
    current = messaging.access(g.user, conversation)
    if current is None:
        abort(404)
    messages = messaging.visible_messages(g.user, conversation)
    ids = {m.sender_id for m in messages if m.sender_id}
    usernames = {u.id: u.display_name for u in db.session.scalars(select(User).where(User.id.in_(ids)))}
    evidence = moderation.conversation_evidence(conversation, messages, usernames)
    return _handle_report(ReportForm(), current.other, evidence,
                          url_for("messaging.conversation", conversation_id=conversation.id), "samtalen")


@bp.route("/anmeld/besked/<int:message_id>", methods=["GET", "POST"])
@login_required
@limiter.limit("20 per day", methods=["POST"])
def report_message(message_id):
    message = db.session.get(Message, message_id)
    if message is None or message.sender_id == g.user.id:
        abort(404)
    conversation = db.session.get(Conversation, message.conversation_id)
    current = messaging.access(g.user, conversation)
    if current is None or message not in messaging.visible_messages(g.user, conversation):
        abort(404)
    sender = db.session.get(User, message.sender_id) if message.sender_id else None
    return _handle_report(ReportForm(), sender, moderation.message_evidence(message, sender),
                          url_for("messaging.conversation", conversation_id=conversation.id), "beskeden")
