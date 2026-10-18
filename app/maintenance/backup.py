"""Samordnet backup af database og billedfiler (specifikation 14.8).

En backup er én fil: et tar-arkiv med databasedump, billedfiler og et manifest, krypteret med age til en
eller flere offentlige nøgler. Serveren kan altså lave backups, men ikke læse dem. Den private nøgle
opbevares af den ansvarlige uden for serveren. Der gemmes BACKUP_KEEP natlige backups i rotation, og
mappen spejles til BACKUP_REMOTE med rsync.

Databasen dumpes før billederne. Et billede, der uploades imens, ender som en fil uden databasepost og
ryddes op senere. `flask sukker verify-media` kontrollerer efter en gendannelse, at poster og filer passer.
"""
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime

from flask import current_app
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url

from ..core.timeutil import DANISH_TZ, now_utc
from ..extensions import db
from ..media.models import ProfileImage
from ..media.storage import VARIANTS

KEEP_PER_LABEL = {"nightly": None}  # None = BACKUP_KEEP
KEEP_OTHER_LABELS = 3
MEDIA_PARTS = ("public", "private", "evidence", "bugreports")


class BackupError(RuntimeError):
    pass


def backup_dir():
    path = current_app.config["BACKUP_DIR"] or os.path.join(current_app.instance_path, "backups")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def status_file():
    return os.path.join(backup_dir(), "status.json")


def read_status():
    try:
        with open(status_file(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _write_status(status):
    path = status_file()
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(status, fh, indent=2)
    os.replace(path + ".tmp", path)


def _tool(name):
    pg_bin = current_app.config.get("PG_BIN_DIR")
    if name == "pg_dump" and pg_bin:
        return os.path.join(pg_bin, name)
    return shutil.which(name) or name


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _alembic_revision():
    if db.session.scalar(text("SELECT to_regclass('sukker.alembic_version')")) is None:
        return None
    return db.session.scalar(text("SELECT version_num FROM sukker.alembic_version"))


def dump_database(target):
    """pg_dump af skemaet sukker som migrator. Passwordet gives via miljøet, ikke på kommandolinjen."""
    url = make_url(current_app.config["MIGRATOR_DATABASE_URL"] or current_app.config["SQLALCHEMY_DATABASE_URI"])
    env = dict(os.environ, PGPASSWORD=url.password or "")
    args = [_tool("pg_dump"), "--format=custom", "--schema=sukker", "--file", target,
            "--host", url.host or "localhost", "--port", str(url.port or 5432),
            "--username", url.username, "--dbname", url.database]
    result = subprocess.run(args, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        raise BackupError(f"pg_dump fejlede: {result.stderr.strip()[:500]}")


def archive_media(target):
    """Billedfiler og beviskopier. Halvfærdige uploads (*.tmp) springes over."""
    root = current_app.config["MEDIA_ROOT"]
    count = 0
    with tarfile.open(target, "w") as tar:
        for part in MEDIA_PARTS:
            base = os.path.join(root, part)
            if not os.path.isdir(base):
                continue
            for name in sorted(os.listdir(base)):
                if name.endswith(".tmp"):
                    continue
                tar.add(os.path.join(base, name), arcname=f"{part}/{name}")
                count += 1
    return count


def _encrypt(source, target, recipients):
    args = [_tool("age")]
    for recipient in recipients:
        args += ["--recipient", recipient]
    args += ["--output", target, source]
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise BackupError(f"Kryptering med age fejlede: {result.stderr.strip()[:500]}")


def rotate(directory, label, keep):
    files = sorted(f for f in os.listdir(directory) if f.startswith("sukker-") and f"-{label}." in f)
    removed = files[:-keep] if keep and len(files) > keep else []
    for name in removed:
        os.remove(os.path.join(directory, name))
    return removed


def _offsite(directory):
    remote = current_app.config["BACKUP_REMOTE"]
    if not remote:
        return "ikke sat op"
    args = [_tool("rsync"), "-a", "--delete", "-e", "ssh -o BatchMode=yes", "--include", "sukker-*-nightly.tar.age", "--exclude", "*",
            directory.rstrip("/") + "/", remote]
    result = subprocess.run(args, capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        raise BackupError(f"Kopi uden for serveren fejlede: {result.stderr.strip()[:500]}")
    return "ok"


def run_backup(label="nightly", db_only=False):
    config = current_app.config
    directory = backup_dir()
    recipients = config["BACKUP_AGE_RECIPIENTS"]
    status = read_status()
    status.update(last_attempt=now_utc().isoformat(), label=label)
    try:
        if config["BACKUP_REQUIRE_ENCRYPTION"] and not recipients:
            raise BackupError("Backup skal krypteres, men SUKKER_BACKUP_AGE_RECIPIENTS er ikke sat.")
        stamp = datetime.now(DANISH_TZ).strftime("%Y%m%d_%H%M%S")
        name = f"sukker-{config['ENV_NAME']}-{stamp}-{label}"
        work = tempfile.mkdtemp(prefix=".tmp-", dir=directory)
        try:
            dump_database(os.path.join(work, "database.dump"))
            media_entries = 0 if db_only else archive_media(os.path.join(work, "media.tar"))
            manifest = {
                "created_at": now_utc().isoformat(),
                "environment": config["ENV_NAME"],
                "label": label,
                "alembic_revision": _alembic_revision(),
                "images_in_database": db.session.scalar(select(func.count(ProfileImage.id))),
                "media_entries": media_entries,
                "files": {part: _sha256(os.path.join(work, part)) for part in sorted(os.listdir(work))},
            }
            with open(os.path.join(work, "manifest.json"), "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, indent=2)
            bundle = os.path.join(work, "bundle.tar")
            with tarfile.open(bundle, "w") as tar:
                for part in ("manifest.json", "database.dump", "media.tar"):
                    if os.path.exists(os.path.join(work, part)):
                        tar.add(os.path.join(work, part), arcname=part)
            final = os.path.join(directory, name + (".tar.age" if recipients else ".tar"))
            if recipients:
                _encrypt(bundle, final + ".partial", recipients)
            else:
                shutil.move(bundle, final + ".partial")
            os.replace(final + ".partial", final)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        rotate(directory, label, KEEP_PER_LABEL.get(label, KEEP_OTHER_LABELS) or config["BACKUP_KEEP"])
        offsite = _offsite(directory) if label == "nightly" else "springes over"
        if label == "nightly":
            status.update(ok=True, error=None, last_success=now_utc().isoformat(), file=os.path.basename(final),
                          size=os.path.getsize(final), encrypted=bool(recipients), offsite=offsite)
            _write_status(status)
        return {"file": final, "encrypted": bool(recipients), "offsite": offsite, **manifest}
    except Exception as error:
        if label == "nightly":
            status.update(ok=False, error=str(error)[:500])
            _write_status(status)
        raise


def verify_media():
    """Efter en gendannelse: billedposter uden filer og billedmapper uden poster."""
    root = current_app.config["MEDIA_ROOT"]
    missing, known = [], set()
    for public_id, collection in db.session.execute(select(ProfileImage.public_id, ProfileImage.collection)):
        known.add(public_id)
        folder = os.path.join(root, collection, public_id)
        if not all(os.path.isfile(os.path.join(folder, filename)) for filename in VARIANTS.values()):
            missing.append(f"{collection}/{public_id}")
    orphans = []
    for collection in ("public", "private"):
        base = os.path.join(root, collection)
        if os.path.isdir(base):
            orphans += [f"{collection}/{n}" for n in os.listdir(base) if n not in known and not n.endswith(".tmp")]
    return {"images": len(known), "missing": missing, "orphans": orphans}
