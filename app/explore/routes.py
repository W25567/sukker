from flask import Blueprint, g, render_template, request

from ..auth.sessions import login_required
from ..subscriptions.services import effective_tier
from .forms import FilterForm
from .services import search

bp = Blueprint("explore", __name__)


@bp.route("/udforsk")
@login_required
def index():
    page = max(1, request.args.get("side", 1, type=int) or 1)
    form = FilterForm(request.args)
    # Al filtrering kræver Premium eller Premium+ (specifikation 6.3). Håndhæves her på serveren.
    filters_allowed = effective_tier(g.user.id) != "free"
    criteria = form.criteria() if filters_allowed else None
    cards, has_more = search(g.user, criteria, page)

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
    )
