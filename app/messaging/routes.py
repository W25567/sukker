from flask import Blueprint, Response, abort, flash, g, jsonify, redirect, render_template, request, url_for
from flask_wtf import FlaskForm
from wtforms import TextAreaField
from wtforms.validators import DataRequired, Length

from ..auth.forms import ActionForm
from ..auth.sessions import login_required
from ..extensions import db, limiter
from ..matches.queries import has_active_match
from ..media.services import avatar_ids
from ..profiles.services import find_user_by_handle
from . import admin_threads, services
from .models import Conversation, Message

bp = Blueprint("messaging", __name__, url_prefix="/beskeder")


class MessageForm(FlaskForm):
    body = TextAreaField(
        "Besked",
        validators=[DataRequired("Skriv en besked."),
                    Length(max=services.MAX_LENGTH, message=f"En besked må højst være {services.MAX_LENGTH} tegn.")],
    )


def _conversation_or_404(conversation_id):
    conversation = db.session.get(Conversation, conversation_id)
    current = services.access(g.user, conversation)
    if current is None:
        abort(404)
    return conversation, current


@bp.route("/")
@login_required
def inbox():
    archived = request.args.get("arkiv") == "1"
    admin_thread = None if archived else admin_threads.visible_to_user(g.user)
    admin_last = admin_threads.messages(g.user.id)[-1] if admin_thread else None
    items = services.inbox(g.user, archived=archived)
    # Fjernede matches vises med forbogstav, ikke billede: samtalen er lukket.
    avatars = avatar_ids(g.user, [item.other for item in items if not item.removed])
    return render_template("messaging/inbox.html", items=items, archived=archived, avatars=avatars,
                           admin_thread=admin_thread, admin_last=admin_last, action_form=ActionForm(),
                           admin_unread=admin_threads.unread_for_user(g.user) if admin_thread else 0)


@bp.route("/<int:conversation_id>/fjern-markering", methods=["POST"])
@login_required
def dismiss_removed(conversation_id):
    """Fjerner markeringen af en samtale fra et fjernet match fra brugerens indbakke."""
    conversation = db.session.get(Conversation, conversation_id)
    if not services.can_dismiss_removed(g.user, conversation):
        abort(404)
    if ActionForm().validate_on_submit():
        services.clear_for_me(g.user, conversation)
        db.session.commit()
    return redirect(url_for("messaging.inbox"))


class AdminReplyForm(FlaskForm):
    body = TextAreaField(
        "Svar til Administrator",
        validators=[DataRequired("Skriv en besked."),
                    Length(max=admin_threads.MAX_LENGTH, message=f"En besked må højst være {admin_threads.MAX_LENGTH} tegn.")],
    )


def _admin_thread_or_404():
    thread = admin_threads.visible_to_user(g.user)
    if thread is None:
        abort(404)
    return thread


@bp.route("/administrator", methods=["GET", "POST"])
@login_required
@limiter.limit("30 per minute;200 per day", methods=["POST"])
def admin_conversation():
    """Samtalen med Administrator. Virker uanset abonnement, match og begrænsning, så man altid kan svare."""
    _admin_thread_or_404()
    form = AdminReplyForm()
    if form.validate_on_submit():
        try:
            admin_threads.reply_from_user(g.user, form.body.data)
            db.session.commit()
        except admin_threads.AdminMessageRefused as refusal:
            db.session.rollback()
            flash(str(refusal), "error")
        return redirect(url_for("messaging.admin_conversation") + "#bund")
    admin_threads.mark_read_by_user(g.user)
    db.session.commit()
    return render_template("messaging/admin_conversation.html", form=form, action_form=ActionForm(),
                           messages=admin_threads.messages(g.user.id), version=admin_threads.version(g.user.id))


@bp.route("/administrator/nye")
@login_required
def admin_updates():
    _admin_thread_or_404()
    version = admin_threads.version(g.user.id)
    if request.args.get("version") == version:
        response = Response(status=204)
    else:
        admin_threads.mark_read_by_user(g.user)
        db.session.commit()
        response = jsonify(version=version, html=render_template("messaging/_admin_thread.html",
                                                                 messages=admin_threads.messages(g.user.id)))
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/administrator/slet", methods=["POST"])
@login_required
def admin_hide():
    """Sletter samtalen for brugeren. Skriver Administrator igen, vises hele historikken igen."""
    _admin_thread_or_404()
    if ActionForm().validate_on_submit():
        admin_threads.hide_for_user(g.user)
        db.session.commit()
        flash("Samtalen med Administrator er slettet for dig.", "info")
    return redirect(url_for("messaging.inbox"))


@bp.route("/med/<handle>")
@login_required
def start(handle):
    """Åbner samtalen med et match og opretter den, første gang den åbnes."""
    other = find_user_by_handle(handle)
    if other is None or not has_active_match(g.user.id, other.id):
        abort(404)
    conversation = services.get_or_create_conversation(g.user.id, other.id)
    db.session.commit()
    return redirect(url_for("messaging.conversation", conversation_id=conversation.id))


@bp.route("/<int:conversation_id>", methods=["GET", "POST"])
@login_required
@limiter.limit("30 per minute;500 per day", methods=["POST"])
def conversation(conversation_id):
    conversation, current = _conversation_or_404(conversation_id)
    form = MessageForm()
    if form.validate_on_submit():
        try:
            services.send_message(g.user, conversation, form.body.data)
            db.session.commit()
        except services.MessageRefused as refusal:
            db.session.rollback()
            flash(str(refusal), "error")
        return redirect(url_for("messaging.conversation", conversation_id=conversation.id) + "#bund")

    services.mark_read(g.user, conversation)
    db.session.commit()
    return render_template(
        "messaging/conversation.html",
        conversation=conversation,
        access=current,
        items=services.timeline(g.user, conversation),
        version=services.thread_version(g.user, conversation),
        form=form,
        action_form=ActionForm(),
        archived=services.membership(conversation, g.user.id).archived,
        deleted_user=services.DELETED_USER,
        other_deleted=current.other is None,
    )


@bp.route("/<int:conversation_id>/nye")
@login_required
def updates(conversation_id):
    """Automatisk opdatering af en åben samtale. Svarer 204, når intet er ændret, ellers tråden som HTML.
    Adgangen kontrolleres ved hver forespørgsel, så en blokering eller et fjernet match slår igennem med det samme."""
    conversation, current = _conversation_or_404(conversation_id)
    version = services.thread_version(g.user, conversation)
    if request.args.get("version") == version:
        response = Response(status=204)
    else:
        services.mark_read(g.user, conversation)  # brugeren har samtalen åben og ser beskeden
        db.session.commit()
        html = render_template("messaging/_thread.html", conversation=conversation, action_form=ActionForm(),
                               items=services.timeline(g.user, conversation), deleted_user=services.DELETED_USER,
                               other_deleted=current.other is None)
        response = jsonify(version=version, html=html)
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/<int:conversation_id>/besked/<int:message_id>/slet", methods=["POST"])
@login_required
def delete_message(conversation_id, message_id):
    conversation, _ = _conversation_or_404(conversation_id)
    message = db.session.get(Message, message_id)
    if message is None or message.conversation_id != conversation.id:
        abort(404)
    if ActionForm().validate_on_submit():
        services.delete_message_for_me(g.user, message)
        db.session.commit()
    return redirect(url_for("messaging.conversation", conversation_id=conversation.id))


@bp.route("/<int:conversation_id>/slet", methods=["POST"])
@login_required
def clear(conversation_id):
    conversation, _ = _conversation_or_404(conversation_id)
    if ActionForm().validate_on_submit():
        services.clear_for_me(g.user, conversation)
        db.session.commit()
        flash("Samtalen er slettet for dig. Den anden person kan stadig se den.", "info")
    return redirect(url_for("messaging.inbox"))


@bp.route("/<int:conversation_id>/arkiv", methods=["POST"])
@login_required
def archive(conversation_id):
    conversation, _ = _conversation_or_404(conversation_id)
    if ActionForm().validate_on_submit():
        archived = not services.membership(conversation, g.user.id).archived
        services.set_archived(g.user, conversation, archived)
        db.session.commit()
        flash("Samtalen er arkiveret." if archived else "Samtalen er hentet frem fra arkivet.", "info")
    return redirect(url_for("messaging.inbox"))
