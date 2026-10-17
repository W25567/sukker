"""Brugerens egne data som JSON (specifikation 13.1: "brugeren kan hente sine egne data").

Udtrækket indeholder det, der handler om brugeren selv. Det må ikke afsløre andet, end brugeren
allerede kan se på siden: Hvem der har liket brugeren, vises kun som et antal (det er en Premium-funktion),
og hvem der har blokeret brugeren, kommer ikke med.
"""
from sqlalchemy import func, or_, select

from ..auth.models import User, UserSession
from ..core.timeutil import now_utc
from ..extensions import db
from ..location.services import kommune_name
from ..matches.models import Like, Match
from ..media.models import ProfileImage
from ..messaging.models import Message, MessageDeletion
from ..profiles.fields import FIELDS, display_value
from ..profiles.models import Profile
from ..safety.models import UserBlock, UserHide
from ..subscriptions.models import Subscription


def _iso(moment):
    return moment.isoformat() if moment else None


def _names(ids):
    ids = {i for i in ids if i}
    return {u.id: u.username for u in db.session.scalars(select(User).where(User.id.in_(ids)))} if ids else {}


def export_user_data(user):
    profile = db.session.get(Profile, user.id)
    likes = db.session.scalars(select(Like).where(Like.liker_id == user.id).order_by(Like.created_at)).all()
    matches = db.session.scalars(
        select(Match).where(or_(Match.user_low_id == user.id, Match.user_high_id == user.id)).order_by(Match.matched_at)
    ).all()
    blocks = db.session.scalars(select(UserBlock).where(UserBlock.blocker_id == user.id)).all()
    hides = db.session.scalars(select(UserHide).where(UserHide.hider_id == user.id)).all()
    messages = db.session.scalars(select(Message).where(Message.sender_id == user.id).order_by(Message.created_at)).all()
    deleted = set(db.session.scalars(select(MessageDeletion.message_id).where(MessageDeletion.user_id == user.id)))
    names = _names([l.likee_id for l in likes] + [m.other_id(user.id) for m in matches]
                   + [b.blocked_id for b in blocks] + [h.hidden_id for h in hides])

    return {
        "udtrukket": _iso(now_utc()),
        "konto": {
            "brugernavn": user.username,
            "email": user.email,
            "foedselsdato": user.birthdate.isoformat(),
            "postnummer": user.postcode,
            "kommune": kommune_name(user.kommune_code),
            "oprettet": _iso(user.created_at),
            "email_bekraeftet": _iso(user.email_verified_at),
            "vilkaar_accepteret": _iso(user.terms_accepted_at),
            "vilkaarsversion": user.terms_version,
            "status": user.status,
        },
        "profil": {
            "felter": {f.label: display_value(profile, f) for f in FIELDS if display_value(profile, f)},
            "skjulte_felter": [f.label for f in FIELDS if f.name in profile.hidden_fields],
            "paa_pause": profile.paused_at is not None,
            "skjult_fra_soegning": profile.hidden_from_search,
            "kun_for_matches": profile.matches_only,
            "vis_kommune": profile.show_kommune,
            "vis_aktivitet": profile.show_activity,
        } if profile else None,
        "billeder": [
            {"id": image.public_id, "samling": image.collection, "bredde": image.width, "hoejde": image.height,
             "animeret": image.is_animated, "uploadet": _iso(image.created_at)}
            for image in db.session.scalars(
                select(ProfileImage).where(ProfileImage.user_id == user.id).order_by(ProfileImage.id))
        ],
        "abonnementer": [
            {"niveau": s.tier, "kilde": s.source, "fra": _iso(s.starts_at), "til": _iso(s.expires_at)}
            for s in db.session.scalars(
                select(Subscription).where(Subscription.user_id == user.id).order_by(Subscription.starts_at))
        ],
        "likes_givet": [{"til": names.get(l.likee_id), "tidspunkt": _iso(l.created_at)} for l in likes],
        "likes_modtaget_antal": db.session.scalar(select(func.count(Like.id)).where(Like.likee_id == user.id)),
        "matches": [
            {"med": names.get(m.other_id(user.id)), "matchet": _iso(m.matched_at), "fjernet": _iso(m.removed_at)}
            for m in matches
        ],
        "beskeder_sendt": [
            {"samtale": m.conversation_id, "tidspunkt": _iso(m.created_at), "tekst": m.body,
             "slettet_af_dig": m.id in deleted}
            for m in messages
        ],
        "blokeringer": [{"bruger": names.get(b.blocked_id), "tidspunkt": _iso(b.created_at)} for b in blocks],
        "skjulte_profiler": [{"bruger": names.get(h.hidden_id), "tidspunkt": _iso(h.created_at)} for h in hides],
        "logins": [
            {"oprettet": _iso(s.created_at), "sidst_aktiv": _iso(s.last_seen_at), "ip": s.ip_address,
             "enhed": s.user_agent, "afsluttet": _iso(s.revoked_at)}
            for s in db.session.scalars(
                select(UserSession).where(UserSession.user_id == user.id).order_by(UserSession.created_at))
        ],
    }
