from flask import Blueprint, abort, render_template, request

from ..extensions import csrf, db, limiter
from . import unsubscribe as afmeld

bp = Blueprint("notify", __name__)


@bp.route("/afmeld/<token>", methods=["GET", "POST"])
@csrf.exempt  # linket er selv hemmeligheden, og mailprogrammer sender en POST uden CSRF-token (RFC 8058)
@limiter.limit("30 per hour")
def unsubscribe(token):
    found = afmeld.read_token(token)
    if found is None:
        abort(404)
    user, group = found
    done = False
    if request.method == "POST":
        afmeld.unsubscribe(user, group)
        db.session.commit()
        done = True
    response = render_template("notify/unsubscribe.html", token=token, label=afmeld.GROUP_LABELS[group], done=done)
    return response, 200, {"Cache-Control": "no-store"}
