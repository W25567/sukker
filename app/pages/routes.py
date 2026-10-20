"""Offentlige sider, sitemap.xml og robots.txt (specifikation 12.5)."""
import os
from xml.sax.saxutils import escape

from flask import Blueprint, Response, abort, current_app, flash, g, redirect, render_template, request, send_from_directory, url_for
from flask_wtf import FlaskForm

from ..auth.sessions import login_required, safe_next_url
from ..core.features import disabled_features, tier_features
from ..core.security import is_public_endpoint, public_page
from ..core.settings import get_setting
from ..core.timeutil import now_utc
from ..extensions import db
from ..subscriptions.services import active_subscription, early_bird_open, grant_early_bird
from .content import absolute_url, faq_items, seo_for, visible_pages
from ..brands import current_brand
from . import prerelease
from .forms import AcceptTermsForm, PrereleaseForm
from .legal import TERMS_EFFECTIVE, TERMS_VERSION

bp = Blueprint("pages", __name__)


def _render(template, endpoint, **context):
    return render_template(template, seo=seo_for(endpoint), **context)


def _legal_context():
    config = current_app.config
    return {
        "effective": TERMS_EFFECTIVE,
        "contact_email": config["CONTACT_EMAIL"],
        "company": {"name": config["COMPANY_NAME"], "cvr": config["COMPANY_CVR"], "address": config["COMPANY_ADDRESS"]},
        "retention_months": get_setting("data_retention_months"),
        "signup_months": get_setting("signup_premium_months"),
        "inactive_months": get_setting("inactive_hide_months"),
    }


@bp.route("/tidlige-fugle")
@public_page
def early_bird():
    """Kampagneside. Findes kun, mens kampagnen er åben – bagefter svarer den 410 Gone (specifikation 12.5)."""
    if not early_bird_open():
        abort(410)
    return _render("pages/early_bird.html", "pages.early_bird")


@bp.route("/saadan-virker-det")
@public_page
def how():
    return _render("pages/how.html", "pages.how", signup_months=get_setting("signup_premium_months"))


@bp.route("/tryghed")
@public_page
def safety():
    return _render("pages/safety.html", "pages.safety", retention_months=get_setting("data_retention_months"))


# Rækker i sammenligningen. Funktioner fra fase 2 vises først, når de findes.
PREMIUM_ROWS = [
    ("messages.read", "Læse beskeder, du modtager"),
    ("messages.reply", "Skrive i samtaler med dine matches"),
    ("likes.view_received", "Se hvem der har liket dig"),
    ("search.filters", "Filtrere på alder, afstand, køn, interesser og relationstype"),
    ("search.newest_first", "Se de nyeste profiler først"),
    ("media.animated", "Animerede billeder på profilen"),
]


@bp.route("/premium")
@public_page
def premium():
    if not current_brand().premium:
        abort(404)  # brandet har ingen betalte niveauer (app/brands)
    mapping, disabled = tier_features(), disabled_features()
    rows = [(feature, label, [feature in mapping[tier] for tier in ("free", "premium", "premium_plus")])
            for feature, label in PREMIUM_ROWS if feature not in disabled]
    match_limit = get_setting("free_match_limit") if get_setting("free_match_limit_enabled") else None
    return _render("pages/premium.html", "pages.premium", rows=rows, match_limit=match_limit,
                   signup_months=get_setting("signup_premium_months"))


@bp.route("/ofte-stillede-spoergsmaal")
@public_page
def faq():
    items = faq_items()
    structured = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in items
        ],
    }
    return _render("pages/faq.html", "pages.faq", items=items, structured=structured)


@public_page
def purpose():
    """Formålet med siden, fx for Sukker.nu: ærlig dating uden falske profiler, rimelige priser og et menneske at
    tale med. Adressen er brandets (Brand.purpose_path) og registreres i _register_purpose."""
    return _render("pages/purpose.html", "pages.purpose", **_legal_context())


@public_page
def purpose_alias():
    """Den fælles kodes første adresse for formålssiden. Har brandet sin egen, sendes man videre dertil."""
    return redirect(url_for("pages.purpose"), 301)


def _register_purpose(state):
    from ..brands import DEFAULT_PURPOSE_PATH, brand_for

    path = brand_for(state.app.config["BRAND"]).purpose_path
    state.add_url_rule(path, "purpose", purpose)
    if path != DEFAULT_PURPOSE_PATH:
        state.add_url_rule(DEFAULT_PURPOSE_PATH, "purpose_alias", purpose_alias)


bp.record(_register_purpose)


@bp.route("/om")
@public_page
def about():
    return _render("pages/about.html", "pages.about", **_legal_context())


@bp.route("/vilkaar")
@public_page
def terms():
    return _render("pages/terms.html", "pages.terms", match_limit_default=get_setting("free_match_limit"),
                   **_legal_context())


@bp.route("/privatlivspolitik")
@public_page
def privacy():
    return _render("pages/privacy.html", "pages.privacy", **_legal_context())


@bp.route("/cookies")
@public_page
def cookies():
    return _render("pages/cookies.html", "pages.cookies", **_legal_context())


@bp.route("/hjaelp")
@login_required
def help():
    """Hjælp for medlemmer: de offentlige sider samlet ét sted, da sidefoden kun vises for besøgende."""
    return render_template("pages/help.html", pages=visible_pages()[1:], contact_email=current_app.config["CONTACT_EMAIL"])


@bp.route("/velkommen", methods=["GET", "POST"])
@login_required
def welcome():
    """Vises én gang (app/pages/onboarding.py). Giver Premium for livstid til tidlige fugle."""
    next_url = safe_next_url(request.args.get("next")) or url_for("explore.index")
    form = FlaskForm()
    if request.method == "POST":
        if form.validate_on_submit():
            g.user.welcomed_at = now_utc()
            db.session.commit()
        # "Udfyld min profil" går altid til profilen (med billeder). "Senere" fortsætter, hvor brugeren var på vej hen.
        return redirect(url_for("profiles.me") if request.form.get("goto") == "profil" else next_url)
    early_bird = grant_early_bird(g.user)
    db.session.commit()
    return render_template(
        "pages/welcome.html", form=form, newly_granted=early_bird is not None,
        subscription=active_subscription(g.user.id), early_bird_open=early_bird_open(),
        signup_months=get_setting("signup_premium_months"),
    )


@bp.route("/pre-release", methods=["GET", "POST"])
@public_page
def prerelease_landing():
    """Landingssiden indtil lanceringen (app/pages/prerelease.py). Bagefter sendes man til forsiden."""
    next_url = safe_next_url(request.args.get("next")) or url_for("core.home")
    if not prerelease.is_prerelease():
        return redirect(next_url)
    form = PrereleaseForm()
    if form.validate_on_submit():
        prerelease.accept(g.get("user"))
        db.session.commit()
        return redirect(next_url)
    return render_template("pages/prerelease.html", form=form, launch=prerelease.launch_at(), now=now_utc(),
                           launch_label=prerelease.launch_label(), next_url=next_url,
                           home_url=absolute_url("core.home"),
                           already_accepted=prerelease.has_accepted(), early_bird_open=early_bird_open(),
                           signup_months=get_setting("signup_premium_months")), 400 if form.errors else 200


@bp.route("/vilkaar/accepter", methods=["GET", "POST"])
@login_required
def accept_terms():
    """Vises, når vilkårene er ændret siden brugerens seneste accept (app/pages/legal.py)."""
    next_url = safe_next_url(request.args.get("next")) or url_for("explore.index")
    if g.user.terms_version == TERMS_VERSION:
        return redirect(next_url)
    form = AcceptTermsForm()
    if form.validate_on_submit():
        g.user.terms_version = TERMS_VERSION
        g.user.terms_accepted_at = now_utc()
        db.session.commit()
        flash("Tak. Du har accepteret de opdaterede vilkår.", "success")
        return redirect(next_url)
    return render_template("pages/accept_terms.html", form=form, effective=TERMS_EFFECTIVE)


# --- Teknisk SEO -------------------------------------------------------------------------------------------


@bp.route("/sitemap.xml")
@public_page
def sitemap():
    if current_app.config["NOINDEX_ALL"]:
        return Response("Ikke fundet", status=404, mimetype="text/plain")
    entries = "".join(
        f"  <url><loc>{escape(absolute_url(page.endpoint))}</loc><lastmod>{page.updated.isoformat()}</lastmod>"
        f"<priority>{page.priority}</priority></url>\n"
        for page in visible_pages()
    )
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + entries + "</urlset>\n")
    response = Response(xml, mimetype="application/xml")
    response.cache_control.public = True
    response.cache_control.max_age = 3600
    return response


def private_prefixes(app):
    """Stier bag login, udledt af URL-reglerne, så nye områder automatisk kommer med i robots.txt.

    Normalt bruges første del af stien (fx /konto). Deler den første del sti med en offentlig side
    (fx /vilkaar/accepter og /vilkaar), bruges hele den faste del af stien i stedet.
    """
    rules = [rule for rule in app.url_map.iter_rules() if rule.endpoint != "static"]
    public_paths = [rule.rule for rule in rules if is_public_endpoint(app, rule.endpoint)]
    prefixes = set()
    for rule in rules:
        if is_public_endpoint(app, rule.endpoint):
            continue
        fixed = rule.rule.split("<", 1)[0]
        first = "/" + fixed.lstrip("/").split("/", 1)[0]
        if first == "/":
            continue
        prefixes.add(fixed if any(path.startswith(first) for path in public_paths) else first)
    return sorted(prefixes)


@bp.route("/robots.txt")
@public_page
def robots():
    if current_app.config["NOINDEX_ALL"]:
        body = "User-agent: *\nDisallow: /\n"
    else:
        lines = ["User-agent: *"] + [f"Disallow: {prefix}" for prefix in private_prefixes(current_app)]
        lines += ["", f"Sitemap: {absolute_url('pages.sitemap')}"]
        body = "\n".join(lines) + "\n"
    response = Response(body, mimetype="text/plain")
    response.cache_control.public = True
    response.cache_control.max_age = 3600
    return response


@bp.route("/favicon.ico")
@public_page  # søgemaskiner skal kunne hente ikonet til søgeresultaterne
def favicon():
    folder = os.path.join(current_app.static_folder, os.path.dirname(current_brand().asset("favicon.ico")))
    return send_from_directory(folder, "favicon.ico", max_age=7 * 24 * 3600)
