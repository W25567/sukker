from flask import Blueprint, g, render_template, request

from ..auth.sessions import login_required
from ..core.features import has_feature
from ..extensions import EXPLORE_LIMIT, db, limiter, user_or_ip
from ..moderation.signals import scraping_breach
from ..profiles import choices
from ..profiles.models import Profile
from .forms import FilterForm
from .services import search, seeking_genders

bp = Blueprint("explore", __name__)

LIKED_PARAM = "likede"  # ?likede=skjul eller ?likede=vis. Uden parameter gælder brugerens præference.
SORT_PARAM, NEWEST = "sortering", "nyeste"  # ?sortering=nyeste (Premium)


def hide_liked_choice(user):
    """Om profiler, brugeren har liket uden match, skal skjules. Valget i Udforsk vinder over præferencen."""
    values = request.args.getlist(LIKED_PARAM)
    value = values[-1] if values else None  # formularen sender "vis" og, når feltet er krydset af, "skjul"
    if value in ("skjul", "vis"):
        return value == "skjul"
    profile = db.session.get(Profile, user.id)
    return bool(profile and profile.hide_liked)


@bp.route("/udforsk")
@login_required
@limiter.limit(EXPLORE_LIMIT, key_func=user_or_ip, on_breach=scraping_breach)
def index():
    page = max(1, request.args.get("side", 1, type=int) or 1)
    form = FilterForm(request.args)
    # Al filtrering kræver Premium eller Premium+ (specifikation 6.3). Håndhæves her på serveren.
    # At skjule profiler, man selv har liket, er ikke et Premium-filter: det gælder for alle.
    filters_allowed = has_feature(g.user, "search.filters")
    criteria = form.criteria() if filters_allowed else None
    hide_liked = hide_liked_choice(g.user)
    # De nyeste profiler først er en Premium-funktion. Ellers er rækkefølgen altid afstand og derefter nyeste.
    newest_allowed = has_feature(g.user, "search.newest_first")
    newest_first = newest_allowed and request.args.get(SORT_PARAM) == NEWEST
    cards, has_more = search(g.user, criteria, page, hide_liked=hide_liked, newest_first=newest_first)
    seeking = choices.labels(choices.GENDERS_PLURAL, seeking_genders(g.user))

    next_args = request.args.to_dict(flat=False)
    next_args["side"] = [str(page + 1)]
    toggle_args = request.args.to_dict(flat=False)
    toggle_args.pop("side", None)
    toggle_args[LIKED_PARAM] = ["vis" if hide_liked else "skjul"]
    return render_template(
        "explore/index.html",
        cards=cards,
        form=form,
        filters_allowed=filters_allowed,
        filters_active=filters_allowed and form.active or newest_first,
        newest_allowed=newest_allowed,
        newest_first=newest_first,
        has_more=has_more,
        page=page,
        next_args=next_args,
        toggle_args=toggle_args,
        hide_liked=hide_liked,
        has_kommune=g.user.kommune_code is not None,
        seeking=seeking,
    )
