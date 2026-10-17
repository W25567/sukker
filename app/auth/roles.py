"""Adgang efter rolle. Andre får 404, så værktøjerne ikke kan opdages (specifikation 9.3 og 11)."""
from functools import wraps

from flask import abort, g

from .sessions import login_required


def role_required(role):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if g.user.role != role:
                abort(404)
            return view(*args, **kwargs)
        return wrapped
    return decorator


moderator_required = role_required("moderator")
admin_required = role_required("admin")
