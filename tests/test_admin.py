"""Tests for rettighedsmodel, administration, revisionslog og testkonti (M7)."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.admin.models import AdminAuditLog, UserFeatureGrant
from app.admin.services import create_test_accounts
from app.auth.models import User
from app.core.features import has_feature
from app.core.settings import get_setting
from app.core.timeutil import now_utc, today_local
from app.extensions import db
from app.subscriptions.models import Subscription
from app.subscriptions.services import effective_tier

from .conftest import h, link_from, login, register


def make_user(app, outbox, username, role="user"):
    client = app.test_client()
    email = f"{username.lower()}@example.com"
    register(client, display_name=username, email=email)
    client.get(link_from(outbox[-1], "/bekraeft-email/"))
    login(client, email=email)
    if role != "user":
        user(username).role = role
        db.session.commit()
    return client


def user(username):
    return db.session.scalar(select(User).where(User.display_name == username))


def make_free(username):
    for period in db.session.scalars(select(Subscription).where(Subscription.user_id == user(username).id)):
        period.starts_at = now_utc().replace(year=2020)
        period.expires_at = now_utc()
    db.session.commit()


def audit_actions():
    return db.session.scalars(select(AdminAuditLog.action).order_by(AdminAuditLog.id)).all()


def post_user(admin, username, **data):
    return admin.post(f"/admin/bruger/{user(username).id}", data=data)


# --- Rettighedsmodel ------------------------------------------------------------------------------------


def test_default_features_per_tier(app, outbox, ctx):
    make_user(app, outbox, "Anna")
    anna = user("Anna")
    assert has_feature(anna, "search.filters") and not has_feature(anna, "messages.unmatched.send")
    make_free("Anna")
    assert has_feature(anna, "messages.read") and not has_feature(anna, "search.filters")


def test_individual_grant_and_revoke(app, outbox, ctx):
    admin = make_user(app, outbox, "Alma", role="admin")
    make_user(app, outbox, "Anna")
    make_free("Anna")
    post_user(admin, "Anna", action="grant", feature="search.filters", granted="1", note="Test")
    assert has_feature(user("Anna"), "search.filters")

    make_user(app, outbox, "Bodil")  # Premium
    post_user(admin, "Bodil", action="grant", feature="likes.view_received", granted="0")
    assert not has_feature(user("Bodil"), "likes.view_received")
    assert audit_actions() == ["feature_grant", "feature_grant"]


def test_expired_grant_no_longer_applies(app, outbox, ctx):
    make_user(app, outbox, "Anna")
    make_free("Anna")
    db.session.add(UserFeatureGrant(user_id=user("Anna").id, feature="search.filters", granted=True,
                                    expires_at=now_utc() - timedelta(minutes=1)))
    db.session.commit()
    assert not has_feature(user("Anna"), "search.filters")


def test_global_switch_overrides_everything(app, outbox, ctx):
    admin = make_user(app, outbox, "Alma", role="admin")
    anna = make_user(app, outbox, "Anna")
    admin.post("/admin/indstillinger", data=_settings_form(disabled=["search.filters"]))
    assert not has_feature(user("Anna"), "search.filters")
    assert "kræver Premium" in anna.get("/udforsk").get_data(as_text=True)


def test_tier_mapping_is_central_and_editable(app, outbox, ctx):
    admin = make_user(app, outbox, "Alma", role="admin")
    anna = make_user(app, outbox, "Anna")  # Premium
    make_user(app, outbox, "Bodil").post(f"/like/{h('Anna')}")
    assert "Bodil" in anna.get("/likes").get_data(as_text=True)

    mapping = {"premium": ["messages.read", "messages.reply", "search.filters", "media.animated"]}
    admin.post("/admin/indstillinger", data=_settings_form(mapping=mapping))
    assert "Bodil" not in anna.get("/likes").get_data(as_text=True)
    assert "tier_features" in audit_actions()


def _settings_form(values=None, mapping=None, disabled=()):
    from app.core.features import DEFAULT_TIER_FEATURES
    from app.core.settings import DEFAULTS, SETTING_META

    data = {}
    for key, _, kind in SETTING_META:
        value = (values or {}).get(key, DEFAULTS[key])
        if kind == "bool":
            if value:
                data[key] = "y"
        else:
            data[key] = "" if value is None else str(value)
    for tier, features in {**DEFAULT_TIER_FEATURES, **(mapping or {})}.items():
        for feature in features:
            data[f"tier:{tier}:{feature}"] = "1"
    for feature in disabled:
        data[f"disabled:{feature}"] = "1"
    return data


# --- Administration -----------------------------------------------------------------------------------------


def test_admin_area_is_admin_only(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    mod = make_user(app, outbox, "Mogens", role="moderator")
    admin = make_user(app, outbox, "Alma", role="admin")
    for client in (anna, mod):
        assert client.get("/admin/").status_code == 404
        assert client.get("/admin/indstillinger").status_code == 404
    assert admin.get("/admin/").status_code == 200
    assert admin.get(f"/admin/bruger/{user('Anna').id}").status_code == 200


def test_change_settings_is_validated_and_logged(app, outbox, ctx):
    admin = make_user(app, outbox, "Alma", role="admin")
    admin.post("/admin/indstillinger", data=_settings_form(values={"free_match_limit": 12, "premium_match_limit": None}))
    assert get_setting("free_match_limit") == 12
    log = db.session.scalar(select(AdminAuditLog).where(AdminAuditLog.action == "setting"))
    assert log.details == {"key": "free_match_limit", "old": 30, "new": 12}

    bad = _settings_form()
    bad["free_match_limit"] = "mange"
    admin.post("/admin/indstillinger", data=bad)
    assert get_setting("free_match_limit") == 12


def test_admin_subscription_actions(app, outbox, ctx):
    admin = make_user(app, outbox, "Alma", role="admin")
    make_user(app, outbox, "Anna")
    until = (today_local() + timedelta(days=60)).isoformat()

    post_user(admin, "Anna", action="set_tier", tier="premium_plus", until=until, note="Ambassadør")
    assert effective_tier(user("Anna").id) == "premium_plus"
    post_user(admin, "Anna", action="set_tier", tier="free")
    assert effective_tier(user("Anna").id) == "free"
    post_user(admin, "Anna", action="period", tier="premium", months="2", note="Kompensation")
    assert effective_tier(user("Anna").id) == "premium"
    assert audit_actions() == ["set_tier", "set_tier", "grant_period"]


def test_admin_status_and_role_changes(app, outbox, ctx):
    admin = make_user(app, outbox, "Alma", role="admin")
    anna = make_user(app, outbox, "Anna")
    post_user(admin, "Anna", action="status", status="suspended", note="Svindel")
    assert user("Anna").status == "suspended"
    assert anna.get("/konto/").status_code == 302
    post_user(admin, "Anna", action="role", role="moderator")
    assert user("Anna").role == "moderator"

    post_user(admin, "Alma", action="role", role="user")
    assert user("Alma").role == "admin"  # kan ikke ændre sin egen rolle
    assert audit_actions() == ["status", "role"]


def test_admin_audit_log_cannot_be_changed(app, outbox, ctx):
    admin = make_user(app, outbox, "Alma", role="admin")
    make_user(app, outbox, "Anna")
    post_user(admin, "Anna", action="role", role="moderator")
    with pytest.raises(DBAPIError):
        db.session.execute(text("DELETE FROM sukker.admin_audit_log"))
    db.session.rollback()


# --- Testkonti og målinger ----------------------------------------------------------------------------------


def test_test_accounts_are_separated_from_real_users(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    accounts = create_test_accounts()
    db.session.commit()
    assert [effective_tier(user(name).id) for name, _, _ in accounts] == ["free", "premium", "premium_plus"]

    assert "Test_" not in anna.get("/udforsk").get_data(as_text=True)
    assert anna.get(f"/bruger/{h('Test_free')}").status_code == 404
    assert anna.post(f"/like/{h('Test_free')}").status_code == 404

    tester = app.test_client()
    login(tester, email=accounts[1][1], password=accounts[1][2])
    html = tester.get("/udforsk").get_data(as_text=True)
    assert "Test_free" in html and "Anna" not in html


def test_dashboard_excludes_test_accounts(app, outbox, ctx):
    admin = make_user(app, outbox, "Alma", role="admin")
    make_user(app, outbox, "Anna")
    create_test_accounts()
    db.session.commit()
    from app.admin.metrics import collect

    assert collect()["users"]["total"] == 2  # Admin og Anna
    page = admin.get("/admin/").get_data(as_text=True)
    assert "Systemstatus" in page and "Låste Free/Free-matches" in page
    assert db.session.scalar(select(func.count(User.id)).where(User.is_test.is_(True))) == 3
