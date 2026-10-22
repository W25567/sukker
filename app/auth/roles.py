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

# Handlinger på en anden bruger går altid ét niveau ned: en moderator kan kun handle på brugere, en administrator på
# brugere og moderatorer. Ingen kan handle på sig selv eller på en på samme niveau. Andre administratorer ændres kun
# fra kommandolinjen (flask sukker set-role).
RANK = {"user": 0, "moderator": 1, "admin": 2}
ROLE_LABELS = {"user": "bruger", "moderator": "moderator", "admin": "administrator"}


def outranks(actor, target):
    return actor is not None and target is not None and RANK[actor.role] > RANK[target.role]


def out_of_reach_message(target):
    return (f"{target.display_name} er {ROLE_LABELS[target.role]}. Du kan kun handle på brugere på et lavere niveau "
            f"end dit eget.")
