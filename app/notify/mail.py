"""Notifikationer pr. e-mail (kører hvert 5. minut fra sukker-notify.timer: flask sukker notify).

Nyt på siden (beskeder, matches, anmodninger og adgang til private billeder, beskeder fra Administrator):
- Kun til aktive, bekræftede konti (ikke testkonti), og kun for de slags, brugeren har slået til (prefs.py).
- Kun når brugeren ikke er på siden: ingen aktivitet i IDLE. Sessionens "sidst aktiv" opdateres højst hvert
  5. minut (auth/sessions.py), så IDLE er sat derefter.
- Kun det, der er kommet, siden brugeren sidst var inde, og højst én mail pr. slags, indtil brugeren har været
  inde igen. Flere slags samles i én mail.
- Mailen er diskret: den siger aldrig hvem eller hvad, kun at der er nyt. Emnet afslører ikke, hvad siden er.

Påmindelse om Premium (specifikation 10.1): 14 og 3 dage før en periode udløber, én gang pr. trin og periode.
Perioder, der fortsætter i en ny periode, og Premium for livstid giver ingen påmindelse.
"""
from datetime import timedelta

from flask import current_app, url_for
from sqlalchemy import and_, func, or_, select

from ..auth.models import User, UserSession
from ..core.mail import send_mail
from ..core.timeutil import DANISH_TZ, now_utc
from ..extensions import db
from ..matches.models import Match, MatchEvent
from ..media.models import PrivateAccess
from ..messaging.models import AdminMessage, AdminThread, ConversationMember, Message
from ..subscriptions.models import Subscription
from .models import NotificationMailState
from .prefs import wants
from .unsubscribe import mail_headers, unsubscribe_url

IDLE = timedelta(minutes=15)
MIN_INTERVAL = timedelta(hours=1)  # aldrig to mails af samme slags inden for en time
MAX_AGE = timedelta(days=3)  # ældre nyheder giver ingen mail (fx efter en længere driftspause)
PREMIUM_STEPS = (("premium_14", 14), ("premium_3", 3))
INACTIVE_WARNING = timedelta(days=7)  # varsel en uge før en inaktiv profil skjules fra Udforsk

# Rækkefølgen er rækkefølgen i mailen.
LINES = {
    "message": "Du har nye beskeder.",
    "match": "Du har et nyt match.",
    "request": "Nogen har bedt om at se dine private billeder.",
    "grant": "Du har fået adgang til private billeder.",
    "admin": "Du har en besked fra Administrator.",
    "staff": "En bruger har svaret Administrator.",
}


# --- Det nyeste, brugeren ikke har set, pr. slags ---------------------------------------------------------


def _newest_message(user):
    from ..notifications import _latest_unread_message

    row = _latest_unread_message(user)
    return row[0].created_at if row else None


def _newest_admin(user):
    from ..notifications import _latest_unread_admin_message

    message = _latest_unread_admin_message(user)
    return message.created_at if message else None


def _newest_match(user):
    from ..notifications import _new_matches_query

    return db.session.scalar(_new_matches_query(user).with_only_columns(func.max(MatchEvent.at)))


def _newest_request(user):
    return db.session.scalar(
        select(func.max(PrivateAccess.requested_at)).join(User, User.id == PrivateAccess.viewer_id)
        .where(PrivateAccess.owner_id == user.id, PrivateAccess.status == "pending", User.status == "active"))


def _newest_grant(user):
    return db.session.scalar(
        select(func.max(PrivateAccess.decided_at)).join(User, User.id == PrivateAccess.owner_id)
        .where(PrivateAccess.viewer_id == user.id, PrivateAccess.status == "approved", User.status == "active"))


def _newest_staff(user):
    return db.session.scalar(
        select(func.max(AdminMessage.created_at)).join(AdminThread, AdminThread.user_id == AdminMessage.user_id)
        .where(AdminMessage.from_admin.is_(False),
               or_(AdminThread.admin_last_read_at.is_(None), AdminMessage.created_at > AdminThread.admin_last_read_at)))


NEWEST = {"message": _newest_message, "match": _newest_match, "request": _newest_request, "grant": _newest_grant,
          "admin": _newest_admin, "staff": _newest_staff}


def _candidates(since):
    """Brugere, der kan have fået noget nyt siden `since`, så jobbet ikke skal se på alle brugere hver gang."""
    member = ConversationMember
    queries = [
        select(member.user_id).join(Message, Message.conversation_id == member.conversation_id)
        .where(Message.created_at > since, Message.sender_id != member.user_id),
        select(AdminMessage.user_id).where(AdminMessage.from_admin.is_(True), AdminMessage.created_at > since),
        select(Match.user_low_id).where(Match.matched_at > since),
        select(Match.user_high_id).where(Match.matched_at > since),
        select(PrivateAccess.owner_id).where(PrivateAccess.status == "pending", PrivateAccess.requested_at > since),
        select(PrivateAccess.viewer_id).where(PrivateAccess.status == "approved", PrivateAccess.decided_at > since),
    ]
    staff_news = select(AdminMessage.id).where(AdminMessage.from_admin.is_(False), AdminMessage.created_at > since)
    if db.session.scalar(staff_news.limit(1)) is not None:
        queries.append(select(User.id).where(User.role == "admin"))
    ids = select(queries[0].union(*queries[1:]).subquery().c[0])
    return _eligible(select(User).where(User.id.in_(ids)))


def _eligible(query):
    return db.session.scalars(query.where(User.status == "active", User.email_verified_at.isnot(None),
                                          User.is_test.is_(False))).all()


def _states(user):
    return {state.kind: state for state in
            db.session.scalars(select(NotificationMailState).where(NotificationMailState.user_id == user.id))}


def _remember(user, states, kind, now, item_at):
    state = states.get(kind)
    if state is None:
        state = states[kind] = NotificationMailState(user_id=user.id, kind=kind, sent_at=now)
        db.session.add(state)
    state.sent_at, state.item_at = now, item_at


def _last_seen(user):
    return db.session.scalar(select(func.max(UserSession.last_seen_at)).where(UserSession.user_id == user.id))


def due_kinds(user, now, states=None):
    """De slags, brugeren skal have en mail om nu: [(slags, tidspunkt for det nyeste)]."""
    last_seen = _last_seen(user)
    if last_seen is not None and now - last_seen < IDLE:
        return []  # brugeren er på siden og får beskederne der
    states = _states(user) if states is None else states
    due = []
    for kind in LINES:
        if not wants(user, kind, "mail"):
            continue
        newest = NEWEST[kind](user)
        if newest is None or newest < now - MAX_AGE or (last_seen is not None and newest <= last_seen):
            continue
        state = states.get(kind)
        if state is not None:
            # Højst én mail pr. slags, indtil brugeren har været inde igen
            been_back = last_seen is not None and last_seen > state.sent_at
            if not been_back or now - state.sent_at < MIN_INTERVAL or (state.item_at and newest <= state.item_at):
                continue
        due.append((kind, newest))
    return due


def _site_url(endpoint, **values):
    return current_app.config["BASE_URL"].rstrip("/") + url_for(endpoint, **values)


def _send_news(user, kinds):
    return send_mail(user.email, "Der er nyt til dig på Sukker.nu", "mail/notify.txt",
                     headers=mail_headers(user, "news"), name=user.display_name,
                     lines=[LINES[kind] for kind in kinds], login_url=_site_url("auth.login"),
                     settings_url=_site_url("account.overview") + "#notifikationer",
                     unsubscribe_url=unsubscribe_url(user, "news"))


# --- Premium ------------------------------------------------------------------------------------------------


def coverage_end(user_id, now):
    """Hvornår brugerens betalte niveau slutter, når perioder i forlængelse af hinanden lægges sammen. None, hvis
    brugeren ikke har en aktiv periode."""
    periods = db.session.scalars(select(Subscription).where(Subscription.user_id == user_id,
                                                            Subscription.expires_at > now)
                                 .order_by(Subscription.starts_at)).all()
    end = None
    for period in periods:
        if end is None:
            if period.starts_at > now:
                return None
            end = period.expires_at
        elif period.starts_at <= end:
            end = max(end, period.expires_at)
    return end


def premium_due(user, now, states):
    """(trin, udløb) for den påmindelse, brugeren skal have nu, eller None."""
    from ..subscriptions.services import active_subscription

    if not wants(user, "premium", "mail"):
        return None
    subscription = active_subscription(user.id, now)
    if subscription is None or subscription.is_lifetime:
        return None
    end = coverage_end(user.id, now)
    if end is None or end.year >= 9999:
        return None
    days_left = end - now
    for step, days in reversed(PREMIUM_STEPS):  # det nærmeste trin først
        if days_left <= timedelta(days=days):
            sent = [s for key, s in states.items() if key.startswith("premium_") and s.item_at == end]
            # Et senere trin, der allerede er sendt, dækker også de tidligere (fx ved et kort gavekort).
            sent_days = [dict(PREMIUM_STEPS)[s.kind] for s in sent]
            if any(d <= days for d in sent_days):
                return None
            return step, end
    return None


def _send_premium(user, end, now):
    days = max(1, round((end - now).total_seconds() / 86400))
    local_end = end.astimezone(DANISH_TZ)
    return send_mail(user.email, f"Din Premium udløber om {days} dage" if days > 1 else "Din Premium udløber i morgen",
                     "mail/premium_expiry.txt", headers=mail_headers(user, "premium"), name=user.display_name,
                     days=days, end_date=local_end.strftime("%d-%m-%Y"), premium_url=_site_url("pages.premium"),
                     account_url=_site_url("account.overview"),
                     settings_url=_site_url("account.overview") + "#notifikationer",
                     unsubscribe_url=unsubscribe_url(user, "premium"))


def _premium_candidates(now):
    soon = select(Subscription.user_id).where(Subscription.expires_at > now,
                                              Subscription.expires_at <= now + timedelta(days=PREMIUM_STEPS[0][1]))
    return _eligible(select(User).where(User.id.in_(soon)))


# --- Inaktivitet --------------------------------------------------------------------------------------------


def _inactive_candidates(now):
    """Synlige profiler, der skjules fra Udforsk inden for INACTIVE_WARNING, og som ikke er skjult endnu."""
    from ..explore.services import inactive_before
    from ..profiles.models import Profile

    hide_before = inactive_before(now)
    if hide_before is None:
        return []
    visible = select(Profile.user_id).where(Profile.paused_at.is_(None), Profile.hidden_from_search.is_(False),
                                            Profile.matches_only.is_(False))
    return _eligible(select(User).where(User.id.in_(visible), User.last_active_at >= hide_before,
                                        User.last_active_at < hide_before + INACTIVE_WARNING))


def _send_inactive(user, now):
    from ..explore.services import inactive_before

    hidden_at = user.last_active_at + (now - inactive_before(now))
    return send_mail(user.email, "Din profil på Sukker.nu bliver snart skjult", "mail/inactive.txt",
                     headers=mail_headers(user, "inactive"), name=user.display_name, hidden_date=hidden_at.astimezone(DANISH_TZ).strftime("%d-%m-%Y"),
                     login_url=_site_url("auth.login"),
                     settings_url=_site_url("account.overview") + "#notifikationer",
                     unsubscribe_url=unsubscribe_url(user, "inactive"))


# --- Jobbet -------------------------------------------------------------------------------------------------


def run(now=None):
    """Sender de mails, der er brug for nu. Returnerer antal mails pr. slags."""
    now = now or now_utc()
    counts = {"nyt": 0, "premium": 0, "inaktiv": 0}
    with current_app.test_request_context(base_url=current_app.config["BASE_URL"]):
        for user in _candidates(now - MAX_AGE):
            states = _states(user)
            due = due_kinds(user, now, states)
            if due and _send_news(user, [kind for kind, _ in due]):
                for kind, newest in due:
                    _remember(user, states, kind, now, newest)
                counts["nyt"] += 1
                db.session.commit()
        for user in _premium_candidates(now):
            states = _states(user)
            reminder = premium_due(user, now, states)
            if reminder and _send_premium(user, reminder[1], now):
                _remember(user, states, reminder[0], now, reminder[1])
                counts["premium"] += 1
                db.session.commit()
        for user in _inactive_candidates(now):
            states = _states(user)
            state = states.get("inactive")
            # Én mail pr. periode med inaktivitet: logger brugeren ind, starter en ny periode.
            if not wants(user, "inactive", "mail") or (state is not None and state.item_at == user.last_active_at):
                continue
            if _send_inactive(user, now):
                _remember(user, states, "inactive", now, user.last_active_at)
                counts["inaktiv"] += 1
                db.session.commit()
    return counts
