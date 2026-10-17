from flask import Blueprint, g, jsonify, redirect, render_template, url_for
from sqlalchemy import text

from ..extensions import db
from .security import public_page

bp = Blueprint("core", __name__)


@bp.route("/")
@public_page
def home():
    if g.user is not None:
        return redirect(url_for("explore.index"))
    return render_template("core/home.html")


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
