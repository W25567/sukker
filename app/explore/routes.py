from flask import Blueprint, g, render_template, request

from ..auth.sessions import login_required
from ..core.features import has_feature
from ..profiles import choices
from .forms import FilterForm
from .services import search, seeking_genders

bp = Blueprint("explore", __name__)


@bp.route("/udforsk")
@login_required
def index():
    page = max(1, request.args.get("side", 1, type=int) or 1)
    form = FilterForm(request.args)
    # Al filtrering kræver Premium eller Premium+ (specifikation 6.3). Håndhæves her på serveren.
    filters_allowed = has_feature(g.user, "search.filters")
    criteria = form.criteria() if filters_allowed else None
    cards, has_more = search(g.user, criteria, page)
    seeking = choices.labels(choices.GENDERS_PLURAL, seeking_genders(g.user))

    next_args = request.args.to_dict(flat=False)
    next_args["side"] = [str(page + 1)]
    return render_template(
        "explore/index.html",
        cards=cards,
        form=form,
        filters_allowed=filters_allowed,
        filters_active=filters_allowed and form.active,
        has_more=has_more,
        page=page,
        next_args=next_args,
        has_kommune=g.user.kommune_code is not None,
        seeking=seeking,
    )
