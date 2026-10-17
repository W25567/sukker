"""Tests for likes, matches og match-loft (M4)."""
from sqlalchemy import func, select

from app.auth.models import User
from app.core.settings import set_setting
from app.core.timeutil import now_utc
from app.extensions import db
from app.matches.models import Like, Match, MatchEvent
from app.profiles.models import Profile
from app.subscriptions.models import Subscription

from .conftest import h, link_from, login, register


def make_user(app, outbox, username, *, verify=True):
    client = app.test_client()
    email = f"{username.lower()}@example.com"
    register(client, display_name=username, email=email)
    if verify:
        client.get(link_from(outbox[-1], "/bekraeft-email/"))
    login(client, email=email)
    return client


def user(username):
    return db.session.scalar(select(User).where(User.display_name == username))


def set_profile(username, **fields):
    profile = db.session.get(Profile, user(username).id)
    for key, value in fields.items():
        setattr(profile, key, value)
    db.session.commit()


def make_free(username):
    for subscription in db.session.scalars(select(Subscription).where(Subscription.user_id == user(username).id)):
        subscription.starts_at = now_utc().replace(year=2020)
        subscription.expires_at = now_utc()
    db.session.commit()


def flashed(client):
    with client.session_transaction() as session:
        return [message for _, message in session.get("_flashes", [])]


def match_count():
    return db.session.scalar(select(func.count(Match.id)).where(Match.removed_at.is_(None)))


# --- Likes og matches ------------------------------------------------------------------------


def test_one_sided_like_is_not_a_match(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    make_user(app, outbox, "Bodil")
    anna.post(f"/like/{h('Bodil')}")
    assert db.session.scalar(select(func.count(Like.id))) == 1
    assert match_count() == 0


def test_mutual_likes_create_a_match(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    bodil = make_user(app, outbox, "Bodil")
    anna.post(f"/like/{h('Bodil')}")
    bodil.post(f"/like/{h('Anna')}")
    assert match_count() == 1
    assert any("Det er et match" in m for m in flashed(bodil))
    assert "Anna" in bodil.get("/likes").get_data(as_text=True)


def test_liking_twice_is_harmless(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    make_user(app, outbox, "Bodil")
    anna.post(f"/like/{h('Bodil')}")
    anna.post(f"/like/{h('Bodil')}")
    assert db.session.scalar(select(func.count(Like.id))) == 1


def test_cannot_like_self_or_while_unverified_or_paused(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    make_user(app, outbox, "Bodil")
    ubekraeftet = make_user(app, outbox, "Carla", verify=False)

    assert anna.post(f"/like/{h('Anna')}").status_code == 302
    assert "ikke like dig selv" in flashed(anna)[-1]
    ubekraeftet.post(f"/like/{h('Bodil')}")
    assert "Bekræft din e-mail" in flashed(ubekraeftet)[-1]
    set_profile("Anna", paused_at=now_utc())
    anna.post(f"/like/{h('Bodil')}")
    assert "på pause" in flashed(anna)[-1]
    assert db.session.scalar(select(func.count(Like.id))) == 0


def test_cannot_like_hidden_profiles(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    make_user(app, outbox, "Bodil")
    set_profile("Bodil", matches_only=True)
    assert anna.post(f"/like/{h('Bodil')}").status_code == 404


def test_matches_only_profile_is_visible_to_matches(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    bodil = make_user(app, outbox, "Bodil")
    anna.post(f"/like/{h('Bodil')}")
    bodil.post(f"/like/{h('Anna')}")
    set_profile("Bodil", matches_only=True)
    assert anna.get(f"/bruger/{h('Bodil')}").status_code == 200
    carla = make_user(app, outbox, "Carla")
    assert carla.get(f"/bruger/{h('Bodil')}").status_code == 404


def test_unlike_before_match(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    make_user(app, outbox, "Bodil")
    anna.post(f"/like/{h('Bodil')}")
    anna.post(f"/like/{h('Bodil')}/fortryd")
    assert db.session.scalar(select(func.count(Like.id))) == 0


# --- Hvem har liket mig ------------------------------------------------------------------------


def test_only_premium_can_see_who_liked_them(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    bodil = make_user(app, outbox, "Bodil")
    anna.post(f"/like/{h('Bodil')}")

    premium_view = bodil.get("/likes").get_data(as_text=True)
    assert "Anna" in premium_view

    make_free("Bodil")
    free_view = bodil.get("/likes").get_data(as_text=True)
    assert "1 person har liket dig" in free_view
    assert "Anna" not in free_view


# --- Fjern match og match igen ---------------------------------------------------------------------


def test_remove_match_requires_confirmation_and_clears_likes(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    bodil = make_user(app, outbox, "Bodil")
    anna.post(f"/like/{h('Bodil')}")
    bodil.post(f"/like/{h('Anna')}")

    confirmation = anna.get(f"/match/{h('Bodil')}/fjern").get_data(as_text=True)
    assert "Historikken slettes ikke" in confirmation
    anna.post(f"/match/{h('Bodil')}/fjern")

    assert match_count() == 0
    assert db.session.scalar(select(func.count(Like.id))) == 0
    assert "Du har ingen matches endnu" in anna.get("/likes").get_data(as_text=True)


def test_rematch_reactivates_the_same_match(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    bodil = make_user(app, outbox, "Bodil")
    anna.post(f"/like/{h('Bodil')}")
    bodil.post(f"/like/{h('Anna')}")
    first_id = db.session.scalar(select(Match.id))
    anna.post(f"/match/{h('Bodil')}/fjern")

    bodil.post(f"/like/{h('Anna')}")
    anna.post(f"/like/{h('Bodil')}")
    match = db.session.scalar(select(Match))
    assert match.id == first_id and match.is_active
    kinds = db.session.scalars(select(MatchEvent.kind).order_by(MatchEvent.id)).all()
    assert kinds == ["matched", "removed", "matched"]


# --- Match-loft --------------------------------------------------------------------------------------


def _limit(n):
    set_setting("free_match_limit_enabled", True)
    set_setting("free_match_limit", n)
    db.session.commit()


def test_free_user_at_limit_cannot_give_new_likes(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    bodil = make_user(app, outbox, "Bodil")
    make_user(app, outbox, "Carla")
    anna.post(f"/like/{h('Bodil')}")
    bodil.post(f"/like/{h('Anna')}")
    make_free("Anna")
    _limit(1)

    anna.post(f"/like/{h('Carla')}")
    assert "grænsen på 1 matches" in flashed(anna)[-1]
    assert db.session.scalar(select(func.count(Like.id)).where(Like.liker_id == user("Anna").id)) == 1


def test_old_likes_can_still_match_beyond_the_limit(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    bodil = make_user(app, outbox, "Bodil")
    carla = make_user(app, outbox, "Carla")
    anna.post(f"/like/{h('Carla')}")  # før loftet nås
    anna.post(f"/like/{h('Bodil')}")
    bodil.post(f"/like/{h('Anna')}")
    make_free("Anna")
    _limit(1)

    carla.post(f"/like/{h('Anna')}")
    assert match_count() == 2


def test_limit_is_off_by_default_and_premium_is_unlimited(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    for name in ("Bodil", "Carla"):
        other = make_user(app, outbox, name)
        anna.post(f"/like/{h(name)}")
        other.post(f"/like/{h('Anna')}")
    assert match_count() == 2

    _limit(1)  # Anna har stadig Premium fra oprettelsen
    make_user(app, outbox, "Dora")
    anna.post(f"/like/{h('Dora')}")
    assert db.session.scalar(select(func.count(Like.id)).where(Like.likee_id == user("Dora").id)) == 1


def test_like_buttons_in_explore(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    make_user(app, outbox, "Bodil")
    html = anna.get("/udforsk").get_data(as_text=True)
    assert f'action="/like/{h("Bodil")}?fra=/udforsk' in html
    anna.post(f"/like/{h('Bodil')}?fra=/udforsk")
    assert "Liket" in anna.get("/udforsk").get_data(as_text=True)
