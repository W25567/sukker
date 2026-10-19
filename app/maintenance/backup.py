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
# Selfies til verificering (verification/) kommer bevidst ikke med: de slettes, så snart de er vurderet, og
# må ikke leve videre i backups. Efter en gendannelse sender brugeren blot en ny selfie.
MEDIA_PARTS = ("public", "private", "evidence", "bugreports")
PG_TOOLS = {"pg_dump", "pg_restore"}
COUNTED_TABLES = ("users", "profiles", "profile_images", "messages", "matches", "moderation_cases")


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
    if name in PG_TOOLS and pg_bin:
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


def _connection(url):
    """(argumenter, miljø) til pg_dump og pg_restore. Passwordet gives via miljøet, ikke på kommandolinjen."""
    url = make_url(url)
    args = ["--host", url.host or "localhost", "--port", str(url.port or 5432), "--username", url.username,
            "--dbname", url.database]
    return args, dict(os.environ, PGPASSWORD=url.password or "")


def dump_database(target):
    """pg_dump af skemaet sukker som migrator."""
    args, env = _connection(current_app.config["MIGRATOR_DATABASE_URL"] or current_app.config["SQLALCHEMY_DATABASE_URI"])
    args = [_tool("pg_dump"), "--format=custom", "--schema=sukker", "--file", target] + args
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
    # Backupserveren spejler de natlige backups (--delete følger rotationen). Beskyttelsen mod, at en angriber
    # med adgang til serveren også sletter kopierne, er snapshots på backupserveren (deployment/README.md).
    ssh = f"ssh -p {int(current_app.config['BACKUP_REMOTE_PORT'])} -o BatchMode=yes"
    args = [_tool("rsync"), "-a", "--delete", "-e", ssh, "--include", "sukker-*-nightly.tar.age", "--exclude", "*",
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
                # Til kontrol efter en gendannelse (restore_check): antal rækker i de vigtigste tabeller.
                "row_counts": {table: db.session.scalar(text(f"SELECT count(*) FROM sukker.{table}"))
                               for table in COUNTED_TABLES},
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


def _safe_extract(tar, target):
    """Pakker kun almindelige filer ud, uden stier uden for målmappen (filter "data")."""
    tar.extractall(target, filter="data")


def restore_check(path, identity=None, database_url=None):
    """Afprøver en backup uden at røre serveren. Køres på en maskine med den private nøgle, fx den ansvarliges
    egen pc (deployment/README.md):

    1. dekrypterer filen med `identity` (den private age-nøgle)
    2. kontrollerer filernes SHA-256 mod manifestet
    3. kontrollerer, at pg_restore kan læse databasedumpet, og at billedarkivet har det forventede antal mapper
    4. gendanner med `database_url` databasen i en tom database og sammenligner antal rækker med manifestet

    Den dekrypterede kopi slettes bagefter, også hvis noget fejler. Returnerer (manifest, [(trin, ok, detalje)])."""
    checks = []
    work = tempfile.mkdtemp(prefix="sukker-restore-check-")
    try:
        bundle = path
        if path.endswith(".age"):
            if not identity:
                raise BackupError("Backuppen er krypteret. Angiv den private nøgle med --identity.")
            bundle = os.path.join(work, "bundle.tar")
            result = subprocess.run([_tool("age"), "--decrypt", "--identity", identity, "--output", bundle, path],
                                    capture_output=True, text=True)
            if result.returncode != 0:
                raise BackupError(f"Dekryptering fejlede: {result.stderr.strip()[:500]}")
            checks.append(("Dekryptering", True, "OK"))
        with tarfile.open(bundle) as tar:
            _safe_extract(tar, work)
        with open(os.path.join(work, "manifest.json"), encoding="utf-8") as fh:
            manifest = json.load(fh)

        bad = [part for part, digest in manifest["files"].items() if _sha256(os.path.join(work, part)) != digest]
        checks.append(("Kontrolsummer", not bad, "OK" if not bad else f"Ændret eller beskadiget: {', '.join(bad)}"))

        dump = os.path.join(work, "database.dump")
        listing = subprocess.run([_tool("pg_restore"), "--list", dump], capture_output=True, text=True)
        tables = sum(1 for line in listing.stdout.splitlines() if " TABLE DATA " in line)
        checks.append(("Databasedump kan læses", listing.returncode == 0,
                       f"{tables} tabeller med data" if listing.returncode == 0 else listing.stderr.strip()[:300]))

        media = os.path.join(work, "media.tar")
        if os.path.exists(media):
            with tarfile.open(media) as tar:
                folders = {"/".join(name.split("/")[:2]) for name in tar.getnames() if name.count("/") >= 1}
            expected = manifest.get("media_entries")
            checks.append(("Billedarkiv", len(folders) == expected, f"{len(folders)} mapper (forventet {expected})"))

        if database_url:
            args, env = _connection(database_url)
            result = subprocess.run([_tool("pg_restore"), "--no-owner", "--no-privileges", "--exit-on-error"]
                                    + args + [dump], env=env, capture_output=True, text=True)
            checks.append(("Gendannelse i databasen", result.returncode == 0,
                           "OK" if result.returncode == 0 else result.stderr.strip()[:300]))
            if result.returncode == 0:
                from sqlalchemy import create_engine

                engine = create_engine(database_url)
                try:
                    with engine.connect() as connection:
                        for table, expected in (manifest.get("row_counts") or {}).items():
                            actual = connection.scalar(text(f"SELECT count(*) FROM sukker.{table}"))
                            checks.append((f"Rækker i {table}", actual == expected, f"{actual} (forventet {expected})"))
                finally:
                    engine.dispose()
        return manifest, checks
    finally:
        shutil.rmtree(work, ignore_errors=True)


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
