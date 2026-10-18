"""Tests for opbevaringsreglen, oprydning, backup og overvågning (M9)."""
import glob
import io
import json
import os
import shutil
import tarfile
import time
from datetime import timedelta

import pytest
from PIL import Image
from sqlalchemy import func, select

from app.auth.models import UserSession
from app.core.timeutil import add_months, now_utc, today_local
from app.extensions import db
from app.maintenance import backup, monitor, retention
from app.media.models import ProfileImage
from app.messaging.models import Conversation, ConversationMember, Message, MessageDeletion
from app.moderation.models import CaseEvent, ModerationCase

from .conftest import h
from .test_messaging import make_user, matched_pair, open_conversation, send, user

LONG_AGO = add_months(now_utc(), -13)


def messages():
    return db.session.scalars(select(Message.body).order_by(Message.id)).all()


def conversation():
    return db.session.scalar(select(Conversation))


# --- Skjulte samtaler og slettede beskeder ------------------------------------------------------------------


def test_hidden_conversation_is_purged_after_retention(app, outbox, ctx):
    anna, bodil = matched_pair(app, outbox)
    send(anna, open_conversation(anna, "Bodil"), "Hej")
    anna.post(f"/match/{h('Bodil')}/fjern")
    assert conversation().hidden_at is not None

    retention.run()
    assert messages() == ["Hej"]  # skjult for nylig, gemmes stadig

    conversation().hidden_at = LONG_AGO
    db.session.commit()
    counts = retention.run()
    assert counts["skjulte_samtaler"] == 1 and messages() == []

    anna.post(f"/like/{h('Bodil')}")
    bodil.post(f"/like/{h('Anna')}")
    assert "Hej" not in anna.get(open_conversation(anna, "Bodil")).get_data(as_text=True)


def test_message_is_purged_only_when_everyone_has_deleted_it(app, outbox, ctx):
    anna, bodil = matched_pair(app, outbox)
    url = open_conversation(anna, "Bodil")
    send(anna, url, "Første")
    send(anna, url, "Anden")
    first = db.session.scalar(select(Message).where(Message.body == "Første"))

    db.session.add(MessageDeletion(message_id=first.id, user_id=user("Anna").id, deleted_at=LONG_AGO))
    db.session.commit()
    retention.run()
    assert messages() == ["Første", "Anden"]  # Bodil kan stadig se den

    db.session.add(MessageDeletion(message_id=first.id, user_id=user("Bodil").id, deleted_at=now_utc()))
    db.session.commit()
    retention.run()
    assert messages() == ["Første", "Anden"]  # Bodil slettede den for nylig

    db.session.execute(db.update(MessageDeletion).values(deleted_at=LONG_AGO))
    db.session.commit()
    assert retention.run()["slettede_beskeder"] == 1
    assert messages() == ["Anden"]


def test_cleared_conversation_is_purged_when_both_cleared_long_ago(app, outbox, ctx):
    anna, bodil = matched_pair(app, outbox)
    send(anna, open_conversation(anna, "Bodil"), "Gammel")
    message = db.session.scalar(select(Message))
    message.created_at = add_months(now_utc(), -14)
    for member in db.session.scalars(select(ConversationMember)):
        member.cleared_before = LONG_AGO
    db.session.commit()
    retention.run()
    assert messages() == []


def test_conversation_without_members_is_deleted(app, outbox, ctx):
    anna, bodil = matched_pair(app, outbox)
    send(anna, open_conversation(anna, "Bodil"), "Hej")
    anna.post("/konto/slet", data={"password": "et-langt-password", "confirm": "y"})
    bodil.post("/konto/slet", data={"password": "et-langt-password", "confirm": "y"})
    counts = retention.run()
    assert counts["slettede_konti"] == 0 and messages() == ["Hej"]  # bevares i opbevaringsperioden

    for name in ("Anna", "Bodil"):
        user(name).deleted_at = LONG_AGO
    db.session.commit()
    counts = retention.run()
    assert counts["slettede_konti"] == 2 and counts["samtaler_uden_deltagere"] == 1
    assert messages() == []


# --- Beviser i moderationssager -----------------------------------------------------------------------------


def _reported_case(app, outbox):
    anna, bodil = make_user(app, outbox, "Anna"), make_user(app, outbox, "Bodil")
    buffer = io.BytesIO()
    Image.new("RGB", (300, 300), "blue").save(buffer, "JPEG")
    bodil.post("/profil/billeder", data={"image": (io.BytesIO(buffer.getvalue()), "a.jpg")},
               content_type="multipart/form-data")
    anna.post(f"/anmeld/profil/{h('Bodil')}", data={"reason": "svindel"})
    case = db.session.scalar(select(ModerationCase))
    evidence_dir = os.path.join(app.config["MEDIA_ROOT"], "evidence", str(case.id))
    assert os.listdir(evidence_dir)
    return case, evidence_dir


def _age_dir(path):
    old = time.time() - 2 * retention.FILE_GRACE_SECONDS
    os.utime(path, (old, old))


def test_evidence_is_purged_after_case_closed_long_ago(app, outbox, ctx):
    case, evidence_dir = _reported_case(app, outbox)
    retention.run()
    assert case.evidence is not None  # åben sag

    case.status, case.closed_at = "closed", LONG_AGO
    db.session.commit()
    _age_dir(evidence_dir)
    assert retention.run()["beviser"] == 1
    db.session.refresh(case)
    assert case.evidence is None and case.description is None and case.evidence_purged_at is not None
    assert not os.path.exists(evidence_dir)
    assert db.session.scalar(select(CaseEvent).where(CaseEvent.kind == "purge")) is not None


def test_retained_evidence_is_kept_until_the_date(app, outbox, ctx):
    case, _ = _reported_case(app, outbox)
    mod = make_user(app, outbox, "Moderator")
    user("Moderator").role = "moderator"
    case.status, case.closed_at = "closed", LONG_AGO
    db.session.commit()

    too_late = (today_local() + timedelta(days=400)).isoformat()
    mod.post(f"/moderation/sag/{case.id}", data={"action": "retain", "retention-until": too_late,
                                                  "retention-reason": "Politianmeldt"})
    db.session.refresh(case)
    assert case.evidence_retained_until is None

    until = today_local() + timedelta(days=200)
    mod.post(f"/moderation/sag/{case.id}", data={"action": "retain", "retention-until": until.isoformat(),
                                                  "retention-reason": "Politianmeldt"})
    db.session.refresh(case)
    assert case.evidence_retained_until == until
    assert retention.run()["beviser"] == 0

    case.evidence_retained_until = today_local() - timedelta(days=1)
    db.session.commit()
    assert retention.run()["beviser"] == 1


# --- Sessioner, engangslinks og filer -----------------------------------------------------------------------


def test_expired_session_is_rejected_and_purged(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    assert anna.get("/konto/").status_code == 200
    session = db.session.scalar(select(UserSession).where(UserSession.user_id == user("Anna").id))
    session.last_seen_at = now_utc() - app.config["PERMANENT_SESSION_LIFETIME"] - timedelta(minutes=1)
    db.session.commit()

    assert anna.get("/konto/").status_code == 302  # udløbet på serveren, selvom cookien findes
    retention.run()
    assert db.session.scalar(select(func.count(UserSession.id))) == 0


def test_orphan_files_are_removed_but_known_and_new_files_kept(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    buffer = io.BytesIO()
    Image.new("RGB", (300, 300), "red").save(buffer, "JPEG")
    anna.post("/profil/billeder", data={"image": (io.BytesIO(buffer.getvalue()), "a.jpg")},
              content_type="multipart/form-data")
    image = db.session.scalar(select(ProfileImage))
    root = os.path.join(app.config["MEDIA_ROOT"], "public")
    known = os.path.join(root, image.public_id)
    old_orphan, new_orphan = os.path.join(root, "forældet"), os.path.join(root, "under-upload.abc.tmp")
    os.makedirs(old_orphan)
    os.makedirs(new_orphan)
    _age_dir(old_orphan)
    _age_dir(known)

    retention.run()
    assert os.path.isdir(known) and os.path.isdir(new_orphan) and not os.path.exists(old_orphan)


# --- Backup -------------------------------------------------------------------------------------------------


def _pg_bin():
    found = shutil.which("pg_dump")
    if found:
        return os.path.dirname(found)
    candidates = sorted(glob.glob(r"C:\Program Files\PostgreSQL\*\bin\pg_dump.exe"))
    return os.path.dirname(candidates[-1]) if candidates else None


@pytest.fixture
def backup_config(app, tmp_path):
    saved = {k: app.config[k] for k in ("BACKUP_DIR", "PG_BIN_DIR", "BACKUP_REQUIRE_ENCRYPTION", "BACKUP_KEEP")}
    app.config.update(BACKUP_DIR=str(tmp_path / "backups"), PG_BIN_DIR=_pg_bin(), BACKUP_REQUIRE_ENCRYPTION=False,
                      BACKUP_KEEP=2)
    yield tmp_path / "backups"
    app.config.update(saved)


def test_backup_contains_database_media_and_manifest(app, outbox, ctx, backup_config):
    if not _pg_bin():
        pytest.skip("pg_dump findes ikke på maskinen")
    anna = make_user(app, outbox, "Anna")
    buffer = io.BytesIO()
    Image.new("RGB", (300, 300), "green").save(buffer, "JPEG")
    anna.post("/profil/billeder", data={"image": (io.BytesIO(buffer.getvalue()), "a.jpg")},
              content_type="multipart/form-data")

    result = backup.run_backup(label="nightly")
    image = db.session.scalar(select(ProfileImage))
    assert result["images_in_database"] == 1
    with tarfile.open(result["file"]) as tar:
        assert sorted(tar.getnames()) == ["database.dump", "manifest.json", "media.tar"]
        manifest = json.load(tar.extractfile("manifest.json"))
        with tarfile.open(fileobj=tar.extractfile("media.tar")) as media:
            assert f"public/{image.public_id}/full.webp" in media.getnames()
        assert tar.extractfile("database.dump").read(5) == b"PGDMP"
    assert manifest["images_in_database"] == 1
    status = backup.read_status()
    assert status["ok"] is True and status["encrypted"] is False and status["offsite"] == "ikke sat op"


def test_backup_rotation_keeps_the_newest(app, ctx, backup_config):
    os.makedirs(backup_config)
    for stamp in ("20261001_023000", "20261002_023000", "20261003_023000"):
        (backup_config / f"sukker-production-{stamp}-nightly.tar.age").write_bytes(b"x")
    (backup_config / "sukker-production-20261001_120000-pre-migrate.tar.age").write_bytes(b"x")
    removed = backup.rotate(str(backup_config), "nightly", 2)
    assert removed == ["sukker-production-20261001_023000-nightly.tar.age"]
    assert len(os.listdir(backup_config)) == 3


def test_production_backup_refuses_without_encryption(app, ctx, backup_config):
    app.config["BACKUP_REQUIRE_ENCRYPTION"] = True
    with pytest.raises(backup.BackupError, match="krypteres"):
        backup.run_backup()
    status = backup.read_status()
    assert status["ok"] is False and "krypteres" in status["error"]


def test_verify_media_reports_missing_files(app, outbox, ctx):
    anna = make_user(app, outbox, "Anna")
    buffer = io.BytesIO()
    Image.new("RGB", (300, 300), "red").save(buffer, "JPEG")
    anna.post("/profil/billeder", data={"image": (io.BytesIO(buffer.getvalue()), "a.jpg")},
              content_type="multipart/form-data")
    assert backup.verify_media()["missing"] == []
    image = db.session.scalar(select(ProfileImage))
    shutil.rmtree(os.path.join(app.config["MEDIA_ROOT"], "public", image.public_id))
    assert backup.verify_media()["missing"] == [f"public/{image.public_id}"]


# --- Overvågning --------------------------------------------------------------------------------------------


@pytest.fixture
def production_monitor(app, backup_config):
    saved = {k: app.config[k] for k in ("ENV_NAME", "ALERT_EMAIL", "SITE_DOMAIN")}
    app.config.update(ENV_NAME="production", ALERT_EMAIL="drift@example.com", SITE_DOMAIN=None)
    yield
    app.config.update(saved)


def test_backup_check(app, ctx, production_monitor):
    assert [p.key for p in monitor.check_backup()] == ["backup"]
    now = now_utc()
    backup._write_status({"ok": True, "last_success": (now - timedelta(hours=30)).isoformat(),
                          "encrypted": True, "offsite": "ok"})
    assert [p.key for p in monitor.check_backup(now)] == ["backup-age"]
    backup._write_status({"ok": True, "last_success": now.isoformat(), "encrypted": False, "offsite": "ikke sat op"})
    assert {p.key for p in monitor.check_backup(now)} == {"backup-encryption", "backup-offsite"}


def test_certificate_check(app, ctx, production_monitor):
    app.config["SITE_DOMAIN"] = "sukker.nu"
    now = now_utc()
    assert monitor.check_certificate(now, lambda d: now + timedelta(days=60)) == []
    assert monitor.check_certificate(now, lambda d: now + timedelta(days=5))[0].key == "certificate"


def test_error_rate_check(app, ctx, tmp_path):
    now = now_utc()
    lines = [json.dumps({"t": (now - timedelta(minutes=1)).isoformat(), "s": 500 if i < 3 else 200})
             for i in range(30)]
    lines.append(json.dumps({"t": (now - timedelta(hours=2)).isoformat(), "s": 500}))
    log = tmp_path / "access.log"
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert monitor.check_error_rate(now, str(log))[0].key == "error-rate"  # 3 af 30 = 10 %
    log.write_text("\n".join(lines[3:]) + "\n", encoding="utf-8")
    assert monitor.check_error_rate(now, str(log)) == []


def test_alerts_are_deduplicated_and_resolved(app, outbox, ctx, production_monitor):
    problem = monitor.Problem("disk:/var", "Lidt diskplads")
    now = now_utc()
    assert monitor.run(now, [problem])[1] is True
    assert outbox[-1]["to"] == "drift@example.com" and "Lidt diskplads" in outbox[-1]["body"]
    assert monitor.run(now + timedelta(hours=1), [problem])[1] is False  # allerede meldt
    assert monitor.run(now + timedelta(hours=25), [problem])[1] is True  # påmindelse efter et døgn
    assert monitor.run(now + timedelta(hours=26), [])[1] is True
    assert "alt er i orden igen" in outbox[-1]["subject"]


def test_units_are_split_between_environments():
    assert monitor.belongs_to("sukker.service", "sukker")
    assert monitor.belongs_to("sukker-backup.service", "sukker")
    assert not monitor.belongs_to("sukker-staging.service", "sukker")
    assert monitor.belongs_to("sukker-staging-backup.service", "sukker-staging")
    assert not monitor.belongs_to("sukkerfabrik.service", "sukker")


def test_ready_checks_media(client):
    assert client.get("/health/ready").get_json() == {"status": "ok", "database": "ok", "media": "ok"}
