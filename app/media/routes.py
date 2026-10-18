from flask import Blueprint, abort, current_app, g, make_response, send_file

from ..auth.models import User
from ..extensions import IMAGE_LIMIT, db, limiter, user_or_ip
from ..moderation.signals import scraping_breach
from . import storage
from .models import ProfileImage
from .services import can_view_image

bp = Blueprint("media", __name__)


@bp.route("/billede/<public_id>/<variant>.webp")
@limiter.limit(IMAGE_LIMIT, key_func=user_or_ip, on_breach=scraping_breach)
def image(public_id, variant):
    """Leverer et billede efter adgangskontrol (specifikation 4.3).

    Svaret er 404, både når billedet ikke findes, og når brugeren ikke har adgang, så det ikke kan
    afgøres, om et billede findes. Besøgende, der ikke er logget ind, ser aldrig billeder.
    """
    if variant not in storage.VARIANTS or g.user is None:
        abort(404)
    img = db.session.scalar(db.select(ProfileImage).where(ProfileImage.public_id == public_id))
    if img is None:
        abort(404)
    owner = db.session.get(User, img.user_id)
    if not can_view_image(g.user, img, owner):
        abort(404)

    if current_app.config["MEDIA_X_ACCEL_PREFIX"]:
        # Nginx leverer filen fra en intern lokation, efter at Flask har godkendt adgangen.
        response = make_response("")
        response.headers["X-Accel-Redirect"] = storage.internal_path(img, variant)
        response.headers["Content-Type"] = "image/webp"
    else:
        response = send_file(storage.file_path(img, variant), mimetype="image/webp", conditional=True, etag=True)
    # Må ikke gemmes i delte caches, og browseren skal spørge serveren igen, så tilbagekaldt adgang virker straks.
    response.headers["Cache-Control"] = "private, no-cache"
    return response
