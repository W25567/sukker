"""Tests for interesser (katalog, profil, fælles interesser og filter) og for administratorens egne ordlister til
viste navne og interessekataloget."""
import pytest

from app.core.name_rules import problem
from app.core.settings import get_setting, set_setting
from app.extensions import db
from app.profiles import interests
from app.profiles.models import Profile

from .conftest import h, register
from .test_zodiac_and_admin import make_user, user


def set_interests(client, *keys, **extra):
    return client.post("/profil/rediger", data={"gender": "kvinde", "interest_tags": list(keys), **extra})


def profile(name):
    return db.session.get(Profile, user(name).id)


# --- Kataloget ---------------------------------------------------------------------------------------------


def test_keys_are_stable_slugs():
    assert interests.key_for("Film og serier") == "film_og_serier"
    assert interests.key_for("Gåture") == "gaature" and interests.key_for("Øl") == "oel"


def test_parse_catalog_text():
    groups = interests.parse_text("Sport: Padel, Klatring\n\nKultur: Opera")
    assert groups == [["Sport", ["Padel", "Klatring"]], ["Kultur", ["Opera"]]]
    assert interests.parse_text("   ") is None  # tom = standardkataloget
    for bad in ("Sport Padel", ": Padel", "Sport: Padel, padel", "Sport: " + "x" * 31):
        with pytest.raises(interests.CatalogError):
            interests.parse_text(bad)


# --- Profilen ----------------------------------------------------------------------------------------------


def test_choose_interests_and_see_shared(app, outbox, ctx):
    anna, bodil = make_user(app, outbox, "Anna"), make_user(app, outbox, "Bodil")
    page = anna.get("/profil/rediger").get_data(as_text=True)
    assert 'name="interest_tags" type="checkbox" value="gaature"' in page and "Natur og bevægelse" in page
    set_interests(anna, "gaature", "kaffe", "musik")
    set_interests(bodil, "kaffe", "musik", "gaming")
    assert profile("Anna").interest_tags == ["gaature", "kaffe", "musik"]

    view = bodil.get(f"/bruger/{h('Anna')}").get_data(as_text=True)
    assert '<span class="pv-tag shared">Kaffe</span>' in view and '<span class="pv-tag">Gåture</span>' in view
    assert "2 interesser til fælles med dig" in view
    assert "2 fælles interesser" in bodil.get("/udforsk").get_data(as_text=True)

    set_interests(anna, "gaature", "kaffe", "musik", hidden_fields=["interest_tags"])
    assert "til fælles" not in bodil.get(f"/bruger/{h('Anna')}").get_data(as_text=True)
    assert "fælles interesser" not in bodil.get("/udforsk").get_data(as_text=True)


def test_at_most_twelve_interests(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    keys = [key for key, _ in interests.choices()][:13]
    response = set_interests(anna, *keys)
    assert response.status_code == 400 and "højst 12" in response.get_data(as_text=True)


def test_interest_filter(app, outbox, ctx):
    viewer = make_user(app, outbox, "Viewer")
    set_interests(make_user(app, outbox, "Kaffedrikker"), "kaffe")
    set_interests(make_user(app, outbox, "Gamer"), "gaming")
    set_interests(make_user(app, outbox, "Skjuler"), "kaffe", hidden_fields=["interest_tags"])
    page = viewer.get("/udforsk?interesser=kaffe").get_data(as_text=True)
    assert f"/bruger/{h('Kaffedrikker')}" in page
    assert f"/bruger/{h('Gamer')}" not in page and f"/bruger/{h('Skjuler')}" not in page


def test_admin_catalog_change_keeps_removed_interests(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    set_interests(anna, "kaffe", "musik")
    set_setting("interest_catalog", [["Sport", ["Padel"]], ["Mad", ["Kaffe"]]])
    db.session.commit()
    assert "Padel" in anna.get("/profil/rediger").get_data(as_text=True)
    set_interests(anna, "padel", "kaffe")
    db.session.refresh(profile("Anna"))
    assert sorted(profile("Anna").interest_tags) == ["kaffe", "musik", "padel"]  # musik bevares skjult
    assert "Musik" not in anna.get("/profil/forhaandsvisning").get_data(as_text=True)


# --- Administratorens lister -------------------------------------------------------------------------------


def test_admin_extends_name_lists_and_catalog(app, outbox, ctx):
    admin = make_user(app, outbox, "Hanne", role="admin")
    page = admin.get("/admin/lister").get_data(as_text=True)
    assert 'name="name_blocked_anywhere"' in page and 'name="interest_catalog"' in page and "Gælder altid" in page
    assert "Lister" in admin.get("/admin/indstillinger").get_data(as_text=True)

    form = {key: "" for key in ("name_reserved", "name_blocked_prefix", "name_allowed")}
    form.update(action="save_names", name_blocked_anywhere="Grimord\nfrækkert", name_blocked_exact="bøh",
                name_allowed="Pikachu, Fisseha")
    admin.post("/admin/lister", data=form)
    admin.post("/admin/lister", data={"action": "save_interests", "interest_catalog": "Sport: Padel, Klatring"})

    assert get_setting("name_blocked_anywhere") == ["grimord", "frækkert"]
    assert get_setting("interest_catalog") == [["Sport", ["Padel", "Klatring"]]]
    assert problem("SuperGrimord") == "offensive" and problem("Bøh") == "offensive"
    assert problem("Fisseha") is None and problem("Michelle") is None

    response = register(app.test_client(), display_name="Frækkert", email="f@example.com")
    assert "grove, krænkende eller seksuelle ord" in response.get_data(as_text=True)

    form["name_blocked_exact"] = "ok, <script>"
    response = admin.post("/admin/lister", data=form, follow_redirects=True)
    assert get_setting("name_blocked_exact") == ["bøh"]  # intet gemt ved en fejl


def test_admin_adds_one_interest(app, outbox, ctx):
    """Én interesse kan tilføjes uden at skrive hele listen af, fx Knallerter."""
    from app.profiles import interests

    admin = make_user(app, outbox, "Hanne", role="admin")
    assert get_setting("interest_catalog") is None  # standardlisten
    admin.post("/admin/lister", data={"action": "add_interest", "group": "Teknik og spil", "label": "Knallerter"})
    groups = dict(get_setting("interest_catalog"))
    assert groups["Teknik og spil"][-1] == "Knallerter" and "Gåture" in groups["Natur og bevægelse"]
    assert ("knallerter", "Knallerter") in interests.choices()
    admin.post("/admin/lister", data={"action": "add_interest", "group": "__ny__", "new_group": "Motor", "label": "Veteranbiler"})
    assert dict(get_setting("interest_catalog"))["Motor"] == ["Veteranbiler"]
    # Dubletter afvises, og intet ændres
    response = admin.post("/admin/lister", data={"action": "add_interest", "group": "Motor", "label": "knallerter"},
                          follow_redirects=True)
    assert "står flere gange" in response.get_data(as_text=True)
    # Gemmes standardlisten uændret, følger den standarden igen
    admin.post("/admin/lister", data={"action": "save_interests", "interest_catalog": interests.to_text(interests.default_catalog())})
    assert get_setting("interest_catalog") is None
