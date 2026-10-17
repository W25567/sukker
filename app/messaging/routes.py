from flask import Blueprint, Response, abort, flash, g, jsonify, redirect, render_template, request, url_for
from flask_wtf import FlaskForm
from wtforms import TextAreaField
from wtforms.validators import DataRequired, Length

from ..auth.forms import ActionForm
from ..auth.sessions import login_required
from ..extensions import db, limiter
from ..matches.queries import has_active_match
from ..profiles.services import find_user_by_handle
from . import services
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
    return render_template("messaging/inbox.html", items=services.inbox(g.user, archived=archived), archived=archived)


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
    )


@bp.route("/<int:conversation_id>/nye")
@login_required
def updates(conversation_id):
    """Automatisk opdatering af en åben samtale. Svarer 204, når intet er ændret, ellers tråden som HTML.
    Adgangen kontrolleres ved hver forespørgsel, så en blokering eller et fjernet match slår igennem med det samme."""
    conversation, _ = _conversation_or_404(conversation_id)
    version = services.thread_version(g.user, conversation)
    if request.args.get("version") == version:
        response = Response(status=204)
    else:
        services.mark_read(g.user, conversation)  # brugeren har samtalen åben og ser beskeden
        db.session.commit()
        html = render_template("messaging/_thread.html", conversation=conversation, action_form=ActionForm(),
                               items=services.timeline(g.user, conversation), deleted_user=services.DELETED_USER)
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
