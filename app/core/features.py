"""Central, feature-baseret adgangskontrol (specifikation 10.4).

Koden spørger aldrig "har brugeren Premium?", men "har brugeren funktionen X?". Hvilke funktioner et
niveau giver, styres centralt og kan ændres af administrator uden kodeændringer. Enkelte brugere kan
få eller miste en funktion midlertidigt, fx til test.
"""
from sqlalchemy import or_, select

from ..core.timeutil import now_utc
from ..extensions import db
from .settings import get_setting

FEATURES = {
    "messages.read": "Læse modtagne beskeder",
    "messages.reply": "Skrive og svare i match-samtaler (gælder for begge, når én har den)",
    "messages.unmatched.send": "Skrive til personer uden match (fase 2)",
    "likes.view_received": "Se hvem der har liket dig",
    "search.filters": "Filtrere søgning i Udforsk",
    "search.newest_first": "Se de nyeste profiler først i Udforsk",
    "media.animated": "Uploade animerede billeder",
}

TIERS = ["free", "premium", "premium_plus"]

DEFAULT_TIER_FEATURES = {
    "free": ["messages.read"],
    "premium": ["messages.read", "messages.reply", "likes.view_received", "search.filters", "search.newest_first",
                "media.animated"],
    "premium_plus": ["messages.read", "messages.reply", "likes.view_received", "search.filters", "search.newest_first",
                     "media.animated", "messages.unmatched.send"],
}
# De funktioner, der fandtes, før fordelingen gemte, hvilke funktioner den kendte ("_known"). En funktion, som en
# gemt fordeling ikke kender, får standardfordelingen, indtil administrator gemmer fordelingen igen.
LEGACY_KNOWN = {"messages.read", "messages.reply", "messages.unmatched.send", "likes.view_received",
                "search.filters", "media.animated"}


def tier_features():
    """Den aktuelle fordeling af funktioner pr. niveau.

    Et brand uden betalte niveauer (app/brands, fx Nogen.nu) giver alle Premium-funktionerne til alle, og
    fordelingen kan ikke ændres. Premium+-funktionen om beskeder uden match gives ikke: kontakt kræver altid, at
    begge har sagt ja."""
    from ..brands import current_brand

    if not current_brand().premium:
        return {tier: list(DEFAULT_TIER_FEATURES["premium"]) for tier in TIERS}
    stored = get_setting("tier_features")
    if not stored:
        return {tier: list(DEFAULT_TIER_FEATURES[tier]) for tier in TIERS}
    known = set(stored.get("_known", LEGACY_KNOWN))
    result = {}
    for tier in TIERS:
        features = list(stored.get(tier, []))
        features += [f for f in DEFAULT_TIER_FEATURES[tier] if f not in known and f not in features]
        result[tier] = features
    return result


def stored_mapping(mapping):
    """Fordelingen, som den gemmes af administrationen, med de funktioner, den kender."""
    return {**mapping, "_known": list(FEATURES)}


def disabled_features():
    return set(get_setting("disabled_features") or [])


def _active_grant(user_id, feature):
    from ..admin.models import UserFeatureGrant

    now = now_utc()
    return db.session.scalar(
        select(UserFeatureGrant)
        .where(UserFeatureGrant.user_id == user_id, UserFeatureGrant.feature == feature,
               or_(UserFeatureGrant.expires_at.is_(None), UserFeatureGrant.expires_at > now))
        .order_by(UserFeatureGrant.created_at.desc())
        .limit(1)
    )


def feature_source(user, feature):
    """(har funktionen, forklaring). Rækkefølge: global slået fra, individuel regel, niveau."""
    from ..subscriptions.services import TIER_LABELS, effective_tier

    if feature in disabled_features():
        return False, "slået fra globalt"
    grant = _active_grant(user.id, feature)
    if grant is not None:
        return grant.granted, ("individuelt givet" if grant.granted else "individuelt frataget")
    tier = effective_tier(user.id)
    return feature in tier_features()[tier], f"via {TIER_LABELS[tier]}"


def has_feature(user, feature):
    if feature not in FEATURES:
        raise KeyError(f"Ukendt funktion: {feature}")
    return feature_source(user, feature)[0]
