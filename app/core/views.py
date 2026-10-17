from flask import Blueprint, current_app, g, jsonify, redirect, render_template, url_for
from sqlalchemy import text

from ..extensions import db
from .security import public_page
from .settings import get_setting

bp = Blueprint("core", __name__)


@bp.route("/")
@public_page
def home():
    if g.user is not None:
        return redirect(url_for("explore.index"))
    from ..pages.content import absolute_url, seo_for

    base = absolute_url("core.home")
    structured = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "Organization", "@id": base + "#organization", "name": "Sukker.nu", "url": base,
             "logo": absolute_url("static", filename="img/icon-512.png"),
             "email": current_app.config["CONTACT_EMAIL"]},
            {"@type": "WebSite", "@id": base + "#website", "name": "Sukker.nu", "url": base, "inLanguage": "da-DK",
             "publisher": {"@id": base + "#organization"}},
        ],
    }
    return render_template("core/home.html", seo=seo_for("core.home"), structured=structured,
                           signup_months=get_setting("signup_premium_months"))


@bp.route("/health")
def health():
    """Simpel proceskontrol (specifikation 15.5)."""
    return jsonify(status="ok")


@bp.route("/health/ready")
def ready():
    """Readiness: kontrollerer også databaseforbindelsen."""
    try:
        db.session.execute(text("SELECT 1"))
    except Exception:
        db.session.rollback()
        return jsonify(status="error", database="unavailable"), 503
    return jsonify(status="ok", database="ok")
