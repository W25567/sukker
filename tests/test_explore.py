"""Tests for kommuner, omtrentlig afstand og Udforsk (M3).

Bruger syntetiske kommuner, så testene ikke afhænger af de rigtige DAGI-data:
    A og B er naboer (0 km), C ligger 37 km væk, D ligger 140 km væk.
    Postnummer 1000 ligger i A, 2000 i B, 3000 i C, 4000 i D, og 5000 dækker både A (70 %) og B (30 %).
"""
import re

import pytest
from sqlalchemy import select

from app.auth.models import User
from app.core.settings import set_setting
from app.core.timeutil import now_utc, today_local
from app.extensions import db
from app.location.distance import distance_bucket, distance_label
from app.location.models import Kommune, KommuneDistance, Postcode, PostcodeKommune
from app.profiles.models import Profile
from app.subscriptions.models import Subscription

from .conftest import h, link_from, login, register

KM = {("A", "B"): 0, ("A", "C"): 37, ("A", "D"): 140, ("B", "C"): 30, ("B", "D"): 130, ("C", "D"): 100}


@pytest.fixture
def geo(ctx):
    names = {"A": "Aby", "B": "Bby", "C": "Cby", "D": "Dby"}
    db.session.add_all(Kommune(code=f"0{c}{c}{c}"[:4], name=n) for c, n in names.items())
    db.session.add_all(Postcode(postcode=p, name=f"By {p}") for p in ("1000", "2000", "3000", "4000", "5000"))
    db.session.flush()
    code = {c: f"0{c}{c}{c}"[:4] for c in names}
    for postcode, kommune in (("1000", "A"), ("2000", "B"), ("3000", "C"), ("4000", "D")):
        db.session.add(PostcodeKommune(postcode=postcode, kommune_code=code[kommune], share=1.0, is_primary=True))
    db.session.add(PostcodeKommune(postcode="5000", kommune_code=code["A"], share=0.7, is_primary=True))
    db.session.add(PostcodeKommune(postcode="5000", kommune_code=code["B"], share=0.3, is_primary=False))
    for a in names:
        for b in names:
            km = 0 if a == b else KM.get((a, b), KM.get((b, a)))
            db.session.add(KommuneDistance(from_code=code[a], to_code=code[b], km=km))
    db.session.commit()
    return code


def make_user(app, outbox, username, postcode, *, birthdate="1990-05-17", verify=True, **profile_fields):
    client = app.test_client()
    register(client, display_name=username, email=f"{username.lower()}@example.com", postcode=postcode, birthdate=birthdate)
    if verify:
        client.get(link_from(outbox[-1], "/bekraeft-email/"))
    login(client, email=f"{username.lower()}@example.com")
    if profile_fields:
        user = db.session.scalar(select(User).where(User.display_name == username))
        profile = db.session.get(Profile, user.id)
        for key, value in profile_fields.items():
            setattr(profile, key, value)
        db.session.commit()
    return client


def usernames(response):
    import re

    return re.findall(r"<strong>([^,<]+), \d+</strong>", response.get_data(as_text=True))


def make_free(username):
    """Lader brugerens Premium-periode udløbe."""
    user = db.session.scalar(select(User).where(User.display_name == username))
    for subscription in db.session.scalars(select(Subscription).where(Subscription.user_id == user.id)):
        subscription.starts_at = now_utc().replace(year=2020)
        subscription.expires_at = now_utc()
    db.session.commit()


# --- Afstand ---------------------------------------------------------------------------------


def test_distance_buckets_and_labels(ctx):
    assert distance_label(None) is None
    assert distance_label(0) == "I nærheden"
    assert distance_label(9.9) == "I nærheden"
    assert distance_label(37) == "ca. 40 km"
    assert distance_label(45) == "ca. 50 km"  # ,5 rundes op som i PostgreSQL
    assert distance_bucket(12) == 10

    set_setting("distance_nearby_km", 25)
    set_setting("distance_rounding_km", 25)
    db.session.commit()
    assert distance_label(20) == "I nærheden"
    assert distance_label(37) == "ca. 25 km"
    assert distance_label(140) == "ca. 150 km"


# --- Kommune ved oprettelse ----------------------------------------------------------------------


def test_registration_sets_kommune_from_postcode(client, geo):
    register(client, postcode="3000")
    assert db.session.scalar(select(User.kommune_code)) == geo["C"]


def test_registration_rejects_unknown_postcode(client, geo):
    response = register(client, postcode="9999")
    assert "Postnummeret findes ikke" in response.get_data(as_text=True)
    assert db.session.scalar(select(User.id)) is None


def test_postcode_in_several_kommuner_asks_for_kommune(client, geo):
    response = register(client, postcode="5000")
    html = response.get_data(as_text=True)
    assert "dækker flere kommuner" in html and "Aby" in html and "Bby" in html
    assert db.session.scalar(select(User.id)) is None

    register(client, postcode="5000", kommune=geo["B"])
    assert db.session.scalar(select(User.kommune_code)) == geo["B"]


def test_kommune_choice_before_submitting(client, geo):
    """Kommunevælgeren hentes, mens postnummeret skrives (static/js/kommune.js), så passwordet ikke går tabt."""
    assert [k["name"] for k in client.get("/opret/kommuner?postnummer=5000").get_json()] == ["Aby", "Bby"]
    assert len(client.get("/opret/kommuner?postnummer=3000").get_json()) == 1
    assert client.get("/opret/kommuner?postnummer=9999").get_json() == []
    assert client.get("/opret/kommuner?postnummer=abc").get_json() == []
    page = client.get("/opret").get_data(as_text=True)
    assert 'data-kommune-field data-url="/opret/kommuner"' in page and "js/kommune.js" in page
    # Sendes formularen alligevel uden kommune, står der, hvorfor passwordet er tomt
    html = register(client, postcode="5000").get_data(as_text=True)
    assert "Skriv dit password igen" in html


def test_registration_works_before_geo_data_is_loaded(client, ctx):
    register(client, postcode="8000")
    user = db.session.scalar(select(User))
    assert user.postcode == "8000" and user.kommune_code is None


# --- Udforsk -----------------------------------------------------------------------------------------


def test_explore_sorts_by_coarse_distance_and_shows_labels(app, outbox, geo):
    viewer = make_user(app, outbox, "Viewer", "1000")
    make_user(app, outbox, "Fjern", "4000")
    make_user(app, outbox, "Midt", "3000")
    make_user(app, outbox, "Nabo", "2000")

    response = viewer.get("/udforsk")
    assert usernames(response) == ["Nabo", "Midt", "Fjern"]
    html = response.get_data(as_text=True)
    assert "I nærheden" in html and "ca. 40 km" in html and "ca. 140 km" in html
    assert not re.search(r"\b37\s*km", html)  # den præcise afstand vises aldrig


def test_explore_excludes_self_unverified_paused_hidden_and_matches_only(app, outbox, geo):
    viewer = make_user(app, outbox, "Viewer", "1000")
    make_user(app, outbox, "Synlig", "2000")
    make_user(app, outbox, "Ubekraeftet", "2000", verify=False)
    make_user(app, outbox, "Pause", "2000", paused_at=now_utc())
    make_user(app, outbox, "Skjult", "2000", hidden_from_search=True)
    make_user(app, outbox, "KunMatches", "2000", matches_only=True)

    assert usernames(viewer.get("/udforsk")) == ["Synlig"]


def test_kommune_only_shown_when_allowed(app, outbox, geo):
    viewer = make_user(app, outbox, "Viewer", "1000")
    make_user(app, outbox, "Viser", "3000", show_kommune=True)
    make_user(app, outbox, "Skjuler", "4000")
    html = viewer.get("/udforsk").get_data(as_text=True)
    assert re.search(r"\bCby\b", html) and not re.search(r"\bDby\b", html)  # ordgrænser: ikke inde i tokens


def test_admin_can_hide_distance(app, outbox, geo):
    viewer = make_user(app, outbox, "Viewer", "1000")
    make_user(app, outbox, "Nabo", "2000")
    make_user(app, outbox, "Fjern", "4000")
    make_user(app, outbox, "Midt", "3000", show_kommune=True)
    set_setting("show_distance", False)
    db.session.commit()

    response = viewer.get("/udforsk?afstand=10")
    html = response.get_data(as_text=True)
    assert usernames(response) == ["Midt", "Fjern", "Nabo"]  # nyeste først, afstandsfilteret ignoreres
    assert "I nærheden" not in html and " km" not in html and 'name="afstand"' not in html
    assert 'name="sortering"' not in html and "Rækkefølge: nyeste profiler først." in html
    assert re.search(r"\bCby\b", html)  # kommunen vises stadig
    profile = viewer.get(f"/bruger/{h('Nabo')}").get_data(as_text=True)
    assert "I nærheden" not in profile
    assert "Omtrentlig afstand." not in app.test_client().get("/").get_data(as_text=True)


def test_admin_can_hide_kommune(app, outbox, geo):
    viewer = make_user(app, outbox, "Viewer", "1000")
    midt = make_user(app, outbox, "Midt", "3000", show_kommune=True)
    set_setting("show_kommune", False)
    db.session.commit()

    html = viewer.get("/udforsk").get_data(as_text=True)
    assert not re.search(r"\bCby\b", html) and "ca. 40 km" in html  # afstanden vises stadig
    assert not re.search(r"\bCby\b", viewer.get(f"/bruger/{h('Midt')}").get_data(as_text=True))
    assert 'name="show_kommune"' not in midt.get("/profil").get_data(as_text=True)

    midt.post("/profil/synlighed", data={"show_activity": "y"})  # brugerens eget valg bevares
    user = db.session.scalar(select(User).where(User.display_name == "Midt"))
    assert db.session.get(Profile, user.id).show_kommune is True
    set_setting("show_kommune", True)
    db.session.commit()
    assert re.search(r"\bCby\b", viewer.get("/udforsk").get_data(as_text=True))


def test_free_users_cannot_filter(app, outbox, geo):
    viewer = make_user(app, outbox, "Viewer", "1000")
    make_user(app, outbox, "Nabo", "2000")
    make_user(app, outbox, "Fjern", "4000")
    make_free("Viewer")

    response = viewer.get("/udforsk?afstand=10")
    assert usernames(response) == ["Nabo", "Fjern"]  # filteret ignoreres på serveren
    assert "kræver Premium" in response.get_data(as_text=True)


def test_premium_distance_and_age_filters(app, outbox, geo):
    viewer = make_user(app, outbox, "Viewer", "1000")  # Premium via bekræftet e-mail
    make_user(app, outbox, "Nabo", "2000", birthdate="1995-01-01")
    make_user(app, outbox, "Midt", "3000", birthdate="1970-01-01")
    make_user(app, outbox, "Fjern", "4000", birthdate="1995-01-01")

    assert usernames(viewer.get("/udforsk?afstand=50")) == ["Nabo", "Midt"]
    young = today_local().year - 1995 - 1
    assert usernames(viewer.get(f"/udforsk?alder_fra={young - 2}&alder_til={young + 2}")) == ["Nabo", "Fjern"]


def test_filters_skip_profiles_that_hide_the_field(app, outbox, geo):
    viewer = make_user(app, outbox, "Viewer", "1000")
    make_user(app, outbox, "Aaben", "2000", gender="kvinde", relationship_types=["kaereste"])
    make_user(app, outbox, "Lukket", "2000", gender="kvinde", hidden_fields=["relationship_types"], relationship_types=["kaereste"])
    # Køn kan ikke skjules (det bruges til at matche), så et gammelt valg om at skjule køn ignoreres.
    make_user(app, outbox, "GammelSkjult", "2000", gender="kvinde", hidden_fields=["gender"])

    assert sorted(usernames(viewer.get("/udforsk?koen=kvinde"))) == ["Aaben", "GammelSkjult", "Lukket"]
    assert usernames(viewer.get("/udforsk?soeger=kaereste")) == ["Aaben"]


def test_interested_in_always_applies_also_for_free(app, outbox, geo):
    viewer = make_user(app, outbox, "Viewer", "1000", seeking_genders=["kvinde"])
    make_user(app, outbox, "Kvinden", "2000", gender="kvinde")
    make_user(app, outbox, "Manden", "2000", gender="mand")
    make_user(app, outbox, "Skjult", "2000", gender="kvinde", hidden_fields=["gender"])
    make_user(app, outbox, "Ukendt", "2000", gender=None)  # fra før køn var påkrævet
    make_free("Viewer")

    response = viewer.get("/udforsk")
    assert sorted(usernames(response)) == ["Kvinden", "Skjult"]
    assert "Viser kvinder" in response.get_data(as_text=True)

    other = make_user(app, outbox, "Begge", "1000", seeking_genders=["kvinde", "mand"])  # selv kvinde
    assert sorted(usernames(other.get("/udforsk"))) == ["Kvinden", "Manden", "Skjult", "Viewer"]
    alle = make_user(app, outbox, "Alle", "1000")
    assert len(usernames(alle.get("/udforsk"))) == 6  # uden valg vises alle andre, der er interesseret i en


def test_photos_only_filter(app, outbox, geo):
    import io

    from PIL import Image

    viewer = make_user(app, outbox, "Viewer", "1000")
    med = make_user(app, outbox, "MedBillede", "2000")
    make_user(app, outbox, "UdenBillede", "2000")
    buffer = io.BytesIO()
    Image.new("RGB", (200, 200), "red").save(buffer, "JPEG")
    med.post("/profil/billeder", data={"image": (io.BytesIO(buffer.getvalue()), "a.jpg")}, content_type="multipart/form-data")

    assert usernames(viewer.get("/udforsk?kun_billeder=y")) == ["MedBillede"]


def test_pagination(app, outbox, geo, monkeypatch):
    from app.explore import services

    monkeypatch.setattr(services, "PAGE_SIZE", 2)
    viewer = make_user(app, outbox, "Viewer", "1000")
    for name in ("Aaa", "Bbb", "Ccc"):
        make_user(app, outbox, name, "2000")

    first = viewer.get("/udforsk")
    assert len(usernames(first)) == 2 and "side=2" in first.get_data(as_text=True)
    second = viewer.get("/udforsk?side=2")
    assert len(usernames(second)) == 1 and "Vis flere" not in second.get_data(as_text=True)


def test_profile_view_shows_distance_and_safe_back_link(app, outbox, geo):
    viewer = make_user(app, outbox, "Viewer", "1000")
    make_user(app, outbox, "Midt", "3000")
    html = viewer.get(f"/bruger/{h('Midt')}?fra=/udforsk%3Fside%3D2").get_data(as_text=True)
    assert "ca. 40 km" in html and 'href="/udforsk?side=2"' in html
    html = viewer.get(f"/bruger/{h('Midt')}?fra=https://evil.example/").get_data(as_text=True)
    assert 'href="https://evil.example' not in html and "Tilbage til Udforsk" not in html


# --- Byg og indlæs kommunedata ---------------------------------------------------------------------


def _square(x, y, size):
    return {"type": "Polygon", "coordinates": [[[x, y], [x + size, y], [x + size, y + size], [x, y + size], [x, y]]]}


def test_build_and_load_geo(tmp_path, ctx):
    from app.location import build
    from app.location.loader import load

    kommuner = [
        {"properties": {"kommunekode": "101", "navn": "Venstre"}, "geometry": _square(0, 0, 10_000)},
        {"properties": {"kommunekode": "102", "navn": "Midte"}, "geometry": _square(10_000, 0, 10_000)},
        {"properties": {"kommunekode": "103", "navn": "Fjern"}, "geometry": _square(60_000, 0, 10_000)},
    ]
    postnumre = [
        {"properties": {"postnummer": "1111", "navn": "Venstreby"}, "geometry": _square(1_000, 1_000, 2_000)},
        {"properties": {"postnummer": "2222", "navn": "Grænseby"}, "geometry": _square(8_000, 0, 4_000)},
    ]
    result = build.build(kommuner, postnumre)
    _, _, mapping, distances = result
    assert ("1111", "0101", 1.0, True) in mapping
    border = [m for m in mapping if m[0] == "2222"]  # halvdelen i hver kommune
    assert {m[1] for m in border} == {"0101", "0102"} and sum(m[3] for m in border) == 1
    km = {(a, b): d for a, b, d in distances}
    # Afstanden måles mellem midtpunkterne, så naboer ikke automatisk får 0 km.
    assert km[("0101", "0101")] == 0.0
    assert km[("0101", "0102")] == 10.0
    assert km[("0102", "0103")] == 50.0 and km[("0103", "0102")] == 50.0

    build.write_csv(tmp_path, *result)
    db.session.add(User(
        display_name="Gammel", email="gammel@example.com", password_hash="x", birthdate=today_local().replace(year=1980),
        postcode="1111", terms_accepted_at=now_utc(), terms_version="1",
    ))
    db.session.commit()

    counts = load(str(tmp_path))
    db.session.commit()
    assert counts["kommuner"] == 3 and counts["brugere"] == 1
    assert db.session.scalar(select(User.kommune_code).where(User.display_name == "Gammel")) == "0101"
