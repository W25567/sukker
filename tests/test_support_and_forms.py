"""Tests for slettede profiler i administrationen, upload af flere billeder, fødselsdato i tre felter,
forbud mod kontaktoplysninger, kontakt til Administrator og fejlrapporter."""
import io
import os

from PIL import Image
from sqlalchemy import select

from app.auth.models import User
from app.core.timeutil import add_months, now_utc
from app.extensions import db
from app.maintenance import retention
from app.media import storage
from app.media.models import ProfileImage
from app.profiles.models import Profile
from app.support.models import BugReport, BugReportImage

from .conftest import register
from .test_zodiac_and_admin import make_user, user


def jpeg(color="red", size=(300, 300)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "JPEG")
    return buffer.getvalue()


# --- Slettede profiler ------------------------------------------------------------------------------------


def test_deleted_profiles_card_and_list(app, outbox, ctx):
    admin = make_user(app, outbox, "Hanne", role="admin")
    anna = make_user(app, outbox, "Anna")
    anna.post("/konto/slet", data={"password": "et-langt-password", "confirm": "y"})
    dashboard = admin.get("/admin/").get_data(as_text=True)
    assert 'href="/admin/slettede"' in dashboard and "Slettede profiler" in dashboard
    page = admin.get("/admin/slettede").get_data(as_text=True)
    assert ">Anna<" in page and "anna@example.com" in page
    assert ">Anna<" not in admin.get("/admin/brugere").get_data(as_text=True)  # ikke blandt aktive brugere
    assert admin.get("/admin/slettede?q=anna@example.com").status_code == 200
    assert anna.get("/admin/slettede").status_code in (302, 404)


# --- Upload af flere billeder -----------------------------------------------------------------------------


def test_upload_several_images_at_once(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    files = [(io.BytesIO(jpeg(c)), f"{c}.jpg") for c in ("red", "green", "blue")]
    response = anna.post("/profil/billeder", data={"image": files}, content_type="multipart/form-data",
                         follow_redirects=True)
    assert "3 billeder er uploadet" in response.get_data(as_text=True)
    assert db.session.query(ProfileImage).count() == 3


def test_upload_from_script_returns_json(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    response = anna.post("/profil/billeder", data={"image": (io.BytesIO(jpeg()), "a.jpg")},
                         content_type="multipart/form-data", headers={"X-Upload": "1"})
    assert response.get_json() == {"uploaded": 1}
    bad = anna.post("/profil/billeder", data={"image": (io.BytesIO(b"ikke et billede"), "x.jpg")},
                    content_type="multipart/form-data", headers={"X-Upload": "1"})
    assert bad.get_json() == {"uploaded": 0}
    assert "x.jpg" in anna.get("/profil").get_data(as_text=True)  # fejlen vises som besked på siden


# --- Fødselsdato i tre felter -----------------------------------------------------------------------------


def test_birthdate_from_three_dropdowns(app, outbox, ctx):
    client = app.test_client()
    page = client.get("/opret").get_data(as_text=True)
    assert page.count('name="birthdate"') == 3 and '<option value="1985">1985</option>' in page
    register(client, birthdate=["17", "5", "1985"])
    assert db.session.scalar(select(User.birthdate)).isoformat() == "1985-05-17"


def test_birthdate_errors(app, ctx):
    client = app.test_client()
    page = register(client, birthdate=["31", "2", "1985"]).get_data(as_text=True)
    assert "Den dato findes ikke" in page and page.count('class="error"') >= 1
    assert "Vælg din fødselsdato" not in page  # kun én fejl
    assert "Vælg dag, måned og år" in register(client, birthdate=["", "2", "1985"]).get_data(as_text=True)
    assert "Vælg din fødselsdato" in register(client, birthdate=["", "", ""]).get_data(as_text=True)
    assert db.session.scalar(select(User)) is None


# --- Kontaktoplysninger i profilen ------------------------------------------------------------------------


def test_contact_info_is_refused_in_profile_and_name(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    for text in ("Skriv på anna@example.com", "Ring 12 34 56 78", "Se www.annas-side.dk"):
        response = anna.post("/profil/rediger", data={"presentation": text})
        assert response.status_code == 400 and "ikke tilladt på profilen" in response.get_data(as_text=True)
    assert db.session.get(Profile, user("Anna").id).presentation is None
    anna.post("/profil/rediger", data={"presentation": "Jeg boede i Aarhus 1998-2005 og er 182 cm"})
    assert db.session.get(Profile, user("Anna").id).presentation.startswith("Jeg boede")
    anna.post("/konto/navn", data={"display_name": "Anna 12345678"})
    assert user("Anna") is not None  # navnet blev ikke ændret
    terms = app.test_client().get("/vilkaar").get_data(as_text=True)
    assert "markedsføring" in terms and "kontaktoplysninger" in terms


# --- Skriv til Administrator --------------------------------------------------------------------------------


def test_user_can_start_conversation_with_administrator(app, outbox, ctx):
    admin = make_user(app, outbox, "Hanne", role="admin")
    anna = make_user(app, outbox, "Anna")
    assert 'href="/beskeder/administrator"' in anna.get("/profil").get_data(as_text=True)
    anna.post("/beskeder/administrator", data={"body": "Hvordan sletter jeg et billede?"})
    assert "Hvordan sletter jeg et billede?" in anna.get("/beskeder/administrator").get_data(as_text=True)
    assert "Administrator" in anna.get("/beskeder/").get_data(as_text=True)
    threads = admin.get("/admin/samtaler").get_data(as_text=True)
    assert "Anna" in threads and 'class="unread-count"' in threads


# --- Fejlrapporter ----------------------------------------------------------------------------------------


def test_bug_report_with_screenshots_and_admin_handling(app, outbox, ctx):
    admin = make_user(app, outbox, "Hanne", role="admin")
    anna = make_user(app, outbox, "Anna")
    assert 'href="/profil/fejlrapport"' in anna.get("/profil").get_data(as_text=True)
    response = anna.post("/profil/fejlrapport", data={
        "module": "beskeder", "description": "Beskeden blev ikke sendt",
        "screenshots": [(io.BytesIO(jpeg("red", (390, 844))), "skærm1.png"), (io.BytesIO(jpeg("blue")), "skærm2.jpg")],
    }, content_type="multipart/form-data")
    assert response.status_code == 302
    report = db.session.scalar(select(BugReport))
    assert report.module == "beskeder" and report.user_id == user("Anna").id
    images = db.session.scalars(select(BugReportImage)).all()
    first_file = storage.file_path(images[0], "stor")
    assert len(images) == 2 and os.path.exists(first_file)

    listing = admin.get("/admin/fejl").get_data(as_text=True)
    assert "Beskeden blev ikke sendt" in listing and "Beskeder" in listing
    assert "Fejlrapporter" in listing and 'class="unread-count">1<' in listing
    detail = admin.get(f"/admin/fejl/{report.id}").get_data(as_text=True)
    assert images[0].public_id in detail
    assert admin.get(f"/admin/fejl/{report.id}/billede/{images[0].public_id}/lille.webp").status_code == 200
    assert anna.get(f"/admin/fejl/{report.id}").status_code in (302, 404)

    admin.post(f"/admin/fejl/{report.id}", data={"status": "closed", "note": "Rettet"})
    db.session.refresh(report)
    assert report.status == "closed" and report.admin_note == "Rettet"

    report.closed_at = add_months(now_utc(), -13)
    db.session.commit()
    assert retention.run()["fejlrapporter"] == 1
    assert not os.path.exists(first_file)


def test_bug_report_limits(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    four = [(io.BytesIO(jpeg()), f"{i}.jpg") for i in range(4)]
    response = anna.post("/profil/fejlrapport", data={"module": "andet", "description": "x", "screenshots": four},
                         content_type="multipart/form-data")
    assert response.status_code == 400 and "højst vedhæfte 3" in response.get_data(as_text=True)
    response = anna.post("/profil/fejlrapport", data={"module": "", "description": ""})
    assert response.status_code == 400
    assert db.session.scalar(select(BugReport)) is None
