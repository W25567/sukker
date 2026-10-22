"""Administrative handlinger. Alle ændringer af adgang, status og indstillinger skrives i revisionsloggen."""
import re
import secrets
from datetime import datetime, time

from sqlalchemy import delete, select

from ..auth.models import User
from ..auth.passwords import hash_password
from ..core import features
from ..core.settings import SETTING_META, WORDLIST_META, get_setting, set_setting
from ..location.services import kommuner_for_postcode
from ..core.timeutil import DANISH_TZ, now_utc
from ..extensions import db
from ..moderation.services import set_account_status
from ..pages.legal import TERMS_VERSION
from ..profiles.services import get_or_create_profile
from ..subscriptions.models import Subscription
from ..subscriptions.services import grant_period
from .models import AdminAuditLog, UserFeatureGrant


class AdminError(ValueError):
    pass


def audit(admin, action, target=None, **details):
    db.session.add(AdminAuditLog(
        admin_id=admin.id, admin_username=admin.display_name,
        target_user_id=target.id if target else None, target_username=target.display_name if target else None,
        action=action, details=details or None,
    ))


# --- Brugere ----------------------------------------------------------------------------------------------


def set_status(admin, user, status, reason):
    if user.id == admin.id:
        raise AdminError("Du kan ikke ændre status på din egen konto.")
    if user.is_deleted:
        raise AdminError("Brugeren har slettet sin profil. En slettet konto kan ikke genåbnes.")
    if status == "expelled" and not reason:
        raise AdminError("Skriv en begrundelse for den permanente lukning.")
    if status == "expelled" and user.role != "user":
        raise AdminError("Fjern først brugerens rolle som moderator eller administrator.")
    old = user.status
    set_account_status(user, status)
    audit(admin, "status", user, old=old, new=status, reason=reason)


def set_role(admin, user, role):
    if user.id == admin.id:
        raise AdminError("Du kan ikke ændre din egen rolle.")
    old, user.role = user.role, role
    audit(admin, "role", user, old=old, new=role)


def add_period(admin, user, tier, months, note):
    period = grant_period(user, months, tier=tier, source="admin", granted_by=admin, note=note)
    audit(admin, "grant_period", user, tier=tier, months=months, until=period.expires_at.isoformat(), note=note)


def set_tier_until(admin, user, tier, until, note):
    """Skifter brugerens niveau fra nu og til og med datoen `until` (specifikation 11). Free afslutter perioderne."""
    now = now_utc()
    for period in db.session.scalars(select(Subscription).where(Subscription.user_id == user.id, Subscription.expires_at > now)):
        if period.starts_at >= now:
            db.session.delete(period)  # fremtidige forlængelser bortfalder
        else:
            period.expires_at = now
    db.session.flush()
    expires = None
    if tier != "free":
        expires = datetime.combine(until, time(23, 59, 59), tzinfo=DANISH_TZ)
        if expires <= now:
            raise AdminError("Udløbsdatoen skal ligge i fremtiden.")
        db.session.add(Subscription(user_id=user.id, tier=tier, source="admin", starts_at=now, expires_at=expires,
                                    granted_by_id=admin.id, note=note))
    audit(admin, "set_tier", user, tier=tier, until=expires.isoformat() if expires else None, note=note)


def add_grant(admin, user, feature, granted, expires_at, note):
    if feature not in features.FEATURES:
        raise AdminError("Ukendt funktion.")
    db.session.add(UserFeatureGrant(user_id=user.id, feature=feature, granted=granted, expires_at=expires_at,
                                    note=note, granted_by_id=admin.id))
    audit(admin, "feature_grant", user, feature=feature, granted=granted,
          expires=expires_at.isoformat() if expires_at else None, note=note)


def remove_grant(admin, grant):
    user = db.session.get(User, grant.user_id)
    audit(admin, "feature_grant_removed", user, feature=grant.feature, granted=grant.granted)
    db.session.delete(grant)


def grants_for(user):
    return db.session.scalars(
        select(UserFeatureGrant).where(UserFeatureGrant.user_id == user.id).order_by(UserFeatureGrant.created_at.desc())
    ).all()


# --- Indstillinger ---------------------------------------------------------------------------------------------


def parse_wordlist(raw):
    """Ét ord pr. linje eller adskilt med komma. Kun bogstaver (og tal, der ligner bogstaver, fx 1 for i)."""
    from ..core.name_rules import normalize_word

    words = []
    for part in re.split(r"[\n,;]+", raw or ""):
        part = part.strip()
        if not part:
            continue
        if len(part) > 40:
            raise AdminError(f"\"{part[:40]}…\" er for langt. Højst 40 tegn pr. ord.")
        if not re.fullmatch(r"[^\W_]+(?:[ \-][^\W_]+)*", part):
            raise AdminError(f"\"{part}\" må kun indeholde bogstaver og tal (og evt. mellemrum eller bindestreg).")
        if not normalize_word(part):
            raise AdminError(f"\"{part}\" indeholder ingen bogstaver.")
        if part.lower() not in words:
            words.append(part.lower())
    return words


def update_settings(admin, values):
    """Gemmer ændrede indstillinger. `values` er allerede validerede Python-værdier."""
    keys = [key for key, _, _ in SETTING_META] + [key for key, _, _ in WORDLIST_META] + ["interest_catalog"]
    for key in keys:
        if key in values and values[key] != get_setting(key):
            audit(admin, "setting", key=key, old=get_setting(key), new=values[key])
            set_setting(key, values[key])


def update_features(admin, mapping, disabled):
    if mapping != features.tier_features():
        audit(admin, "tier_features", old=features.tier_features(), new=mapping)
        set_setting("tier_features", features.stored_mapping(mapping))
    if set(disabled) != features.disabled_features():
        audit(admin, "disabled_features", old=sorted(features.disabled_features()), new=sorted(disabled))
        set_setting("disabled_features", sorted(disabled))


def audit_log(limit=500, action=None):
    query = select(AdminAuditLog).order_by(AdminAuditLog.at.desc()).limit(limit)
    if action:
        query = query.where(AdminAuditLog.action == action)
    return db.session.scalars(query).all()


def audit_log_for(target, limit=100):
    """Administrationsloggen for én bruger: ændringer og hvem der har set brugerens data."""
    return db.session.scalars(
        select(AdminAuditLog).where(AdminAuditLog.target_user_id == target.id)
        .order_by(AdminAuditLog.at.desc()).limit(limit)
    ).all()


AUDIT_LABELS = {
    "view_user": "Så brugerens data", "admin_message": "Skrev som Administrator", "status": "Skiftede status", "role": "Skiftede rolle",
    "grant_period": "Gav Premium-tid", "set_tier": "Skiftede niveau", "feature_grant": "Individuel regel",
    "feature_grant_removed": "Fjernede regel", "setting": "Ændrede indstilling", "tier_features": "Ændrede rettigheder",
    "disabled_features": "Slog funktioner fra", "test_account": "Testkonto",
    "account_deleted": "Brugeren slettede sin profil", "account_purged": "Data slettet endeligt",
    "image_removed": "Fjernede billede", "image_restored": "Genskabte billede", "name_removed": "Slettede visningsnavn",
}


# --- Testkonti -------------------------------------------------------------------------------------------------


TEST_ACCOUNTS = [("Test_free", "free", "kvinde"), ("Test_premium", "premium", "mand"), ("Test_premiumplus", "premium_plus", "kvinde")]


def create_test_accounts(admin=None, domain="example.com"):
    """Opretter (eller nulstiller) én testkonto pr. niveau. Testkonti kan kun se og kontakte andre testkonti.
    Returnerer [(vist navn, e-mail, password)]. Passwordet vises kun én gang."""
    created = []
    for username, tier, gender in TEST_ACCOUNTS:
        email = f"{username.lower()}@{domain}"
        password = secrets.token_urlsafe(12)
        user = db.session.scalar(select(User).where(User.email == email))
        if user is None:
            kommuner = kommuner_for_postcode("8000")
            user = User(display_name=username, email=email, birthdate=datetime(1990, 1, 1).date(), postcode="8000",
                        kommune_code=kommuner[0].code if kommuner else None,
                        password_hash="", terms_accepted_at=now_utc(), terms_version=TERMS_VERSION,
                        email_verified_at=now_utc(), is_test=True)
            db.session.add(user)
            db.session.flush()
        profile = get_or_create_profile(user)
        profile.gender = profile.gender or gender
        user.password_hash = hash_password(password)
        db.session.execute(delete(Subscription).where(Subscription.user_id == user.id))
        if tier != "free":
            grant_period(user, 120, tier=tier, source="admin", note="Testkonto")
        if admin is not None:
            audit(admin, "test_account", user, tier=tier)
        created.append((username, email, password))
    return created


def test_accounts():
    return db.session.scalars(select(User).where(User.is_test.is_(True)).order_by(User.display_name)).all()


def parse_setting(kind, raw):
    """Fortolker en formularværdi efter indstillingens type. Rejser AdminError ved ugyldige værdier."""
    if kind == "bool":
        return raw in ("y", "on", "true", "1")
    raw = (raw or "").strip()
    if kind == "optional_int" and raw == "":
        return None
    try:
        value = int(raw)
    except ValueError:
        raise AdminError("Skriv et helt tal.")
    if value < 0:
        raise AdminError("Tallet må ikke være negativt.")
    return value
