"""Kommandolinje: administration, geodata og driftsopgaver (`flask sukker --help`)."""
import os

import click
from flask.cli import AppGroup

from .auth.services import find_user_by_email
from .extensions import db
from .subscriptions.services import TIER_LABELS, active_subscription, grant_period

sukker_cli = AppGroup("sukker", help="Sukker.nu-administration.")


@sukker_cli.command("grant-premium")
@click.argument("email")
@click.option("--months", type=int, required=True, help="Antal måneder, der lægges til.")
@click.option("--tier", type=click.Choice(["premium", "premium_plus"]), default="premium")
@click.option("--note", default=None, help="Begrundelse, der gemmes sammen med perioden.")
def grant_premium(email, months, tier, note):
    """Tildeler en bruger mere Premium-tid (specifikation 10.1)."""
    user = find_user_by_email(email)
    if user is None:
        raise click.ClickException(f"Ingen bruger med e-mailen {email}.")
    subscription = grant_period(user, months, tier=tier, source="admin", note=note)
    db.session.commit()
    current = active_subscription(user.id)
    click.echo(
        f"{user.display_name}: {TIER_LABELS[tier]} til {subscription.expires_at:%d-%m-%Y}. "
        f"Aktivt niveau nu: {TIER_LABELS[current.tier] if current else 'Free'}."
    )


@sukker_cli.command("set-role")
@click.argument("email")
@click.argument("role", type=click.Choice(["user", "moderator", "admin"]))
def set_role(email, role):
    """Giver en bruger en rolle. Moderator og administrator er adskilte roller (specifikation 9.3)."""
    user = find_user_by_email(email)
    if user is None:
        raise click.ClickException(f"Ingen bruger med e-mailen {email}.")
    user.role = role
    db.session.commit()
    click.echo(f"{user.display_name} har nu rollen {role}.")


@sukker_cli.command("create-test-accounts")
def create_test_accounts():
    """Opretter eller nulstiller én testkonto pr. niveau (Free, Premium og Premium+) med nye passwords.

    Testkonti kan kun se og kontakte andre testkonti og tæller ikke med i målinger (specifikation 11)."""
    from .admin.services import create_test_accounts as create

    accounts = create()
    db.session.commit()
    click.echo("Testkonti (passwordene vises kun nu):")
    for username, email, password in accounts:
        click.echo(f"  {username:<18} {email:<32} {password}")


GEO_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "geo")


@sukker_cli.command("build-geo")
@click.option("--kommuner", "kommuner_file", type=click.Path(exists=True), help="GeoJSON med kommuner (EPSG:25832).")
@click.option("--postnumre", "postnumre_file", type=click.Path(exists=True), help="GeoJSON med postnumre (EPSG:25832).")
@click.option("--out", default=GEO_DIR, show_default=True, help="Mappe til CSV-filerne.")
def build_geo(kommuner_file, postnumre_file, out):
    """Bygger kommunedata fra DAGI. Uden filer hentes data fra Datafordeleren med SUKKER_DATAFORDELER_APIKEY."""
    from .location import build

    if kommuner_file and postnumre_file:
        kommune_features = build.read_geojson(kommuner_file)
        postnummer_features = build.read_geojson(postnumre_file)
    else:
        apikey = os.environ.get("SUKKER_DATAFORDELER_APIKEY")
        if not apikey:
            raise click.ClickException("Angiv --kommuner og --postnumre, eller sæt SUKKER_DATAFORDELER_APIKEY i .env.")
        kommune_features = _cached_layer(build, build.KOMMUNE_LAYER, apikey, "kommuner")
        postnummer_features = _cached_layer(build, build.POSTNUMMER_LAYER, apikey, "postnumre")

    click.echo(f"Beregner ud fra {len(kommune_features)} kommunefeatures og {len(postnummer_features)} postnummerfeatures ...")
    result = build.build(kommune_features, postnummer_features)
    build.write_csv(out, *result)
    kommuner, postnumre, mapping, _ = result
    flere = len({m[0] for m in mapping if not m[3]})
    click.echo(f"Skrev {len(kommuner)} kommuner og {len(postnumre)} postnumre til {out}. {flere} postnumre dækker flere kommuner.")


def _cached_layer(build, layer, apikey, name):
    """Henter et DAGI-lag og gemmer det i instance/, så en afbrudt kørsel ikke skal hente det hele igen."""
    import json

    from flask import current_app

    cache = os.path.join(current_app.instance_path, f"dagi-{name}.json")
    if os.path.exists(cache):
        click.echo(f"Bruger gemte {name} fra {cache}")
        return build.read_geojson(cache)
    click.echo(f"Henter {name} fra Datafordeleren ...")
    features = build.fetch_wfs_layer(layer, apikey)
    with open(cache, "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": features}, fh)
    return features


@sukker_cli.command("load-geo")
@click.option("--dir", "directory", default=GEO_DIR, show_default=True, help="Mappe med CSV-filerne.")
def load_geo(directory):
    """Indlæser kommunedata fra CSV i databasen."""
    from .location.loader import load

    counts = load(directory)
    db.session.commit()
    click.echo(
        f"Indlæste {counts['kommuner']} kommuner, {counts['postnumre']} postnumre og {counts['afstande']} afstande. "
        f"{counts['brugere']} brugere fik en kommune."
    )


# --- Drift (kaldes af systemd-timerne, specifikation 14.6) -----------------------------------------------------


@sukker_cli.command("maintenance")
def maintenance():
    """Daglig oprydning og sletning efter opbevaringsreglen (specifikation 13.1)."""
    from .maintenance import retention

    counts = retention.run()
    click.echo("Oprydning: " + ", ".join(f"{key} {value}" for key, value in counts.items()))


@sukker_cli.command("backup")
@click.option("--label", default="nightly", show_default=True, help="nightly roteres med BACKUP_KEEP, andre beholder 3.")
@click.option("--db-only", is_flag=True, help="Kun databasen, fx før en migration.")
def backup_command(label, db_only):
    """Krypteret backup af database og billedfiler (specifikation 14.8)."""
    from .maintenance import backup

    try:
        result = backup.run_backup(label=label, db_only=db_only)
    except backup.BackupError as error:
        raise click.ClickException(str(error))
    click.echo(f"Backup: {result['file']} (krypteret: {'ja' if result['encrypted'] else 'nej'}, "
               f"uden for serveren: {result['offsite']}, billeder i databasen: {result['images_in_database']})")


@sukker_cli.command("notify")
def notify_command():
    """Sender notifikationer pr. e-mail og påmindelser om udløb af Premium (app/notify/mail.py)."""
    from .notify import mail

    counts = mail.run()
    click.echo(f"Notifikationer: {counts['nyt']} mail(s) om nyt, {counts['premium']} påmindelse(r) om Premium, "
               f"{counts['inaktiv']} varsel/varsler om inaktivitet.")


@sukker_cli.command("monitor")
def monitor_command():
    """Tjekker backup, certifikat, diskplads, fejlrate og systemd-enheder og sender alarmer."""
    from .maintenance import monitor

    problems, sent = monitor.run()
    click.echo(f"{len(problems)} problem(er). Alarm sendt: {'ja' if sent else 'nej'}.")
    for problem in problems:
        click.echo(f"  - {problem.message}")


@sukker_cli.command("alert")
@click.argument("unit")
def alert_command(unit):
    """Sender en alarm om en fejlet systemd-enhed (bruges af OnFailure=)."""
    from .maintenance import monitor

    monitor.alert_unit_failure(unit)


@sukker_cli.command("verify-media")
def verify_media_command():
    """Kontrollerer efter en gendannelse, at billedposter og billedfiler passer sammen."""
    from .maintenance import backup

    result = backup.verify_media()
    click.echo(f"{result['images']} billeder i databasen. Mangler filer: {len(result['missing'])}. "
               f"Mapper uden post: {len(result['orphans'])}.")
    for item in result["missing"][:50]:
        click.echo(f"  mangler: {item}")
    if result["missing"]:
        raise SystemExit(1)


@sukker_cli.command("restore-check")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.option("--identity", type=click.Path(exists=True, dir_okay=False),
              help="Den private age-nøgle (sukker-backup.key). Kræves for krypterede backups.")
@click.option("--database", "database_url", help="En TOM database at gendanne i, fx "
              "postgresql://bruger:pw@localhost/sukker_restore_test. Uden den kontrolleres kun filerne.")
def restore_check_command(path, identity, database_url):
    """Afprøver en backup på din egen maskine: dekryptering, kontrolsummer, databasedump og billedarkiv.
    Rører ikke serveren eller din egen database (kun den tomme database, du angiver)."""
    from .maintenance import backup

    try:
        manifest, checks = backup.restore_check(path, identity, database_url)
    except backup.BackupError as error:
        raise click.ClickException(str(error))
    click.echo(f"Backup fra {manifest['created_at']} ({manifest['environment']}, migration {manifest['alembic_revision']})")
    for name, ok, detail in checks:
        click.echo(f"  {'OK  ' if ok else 'FEJL'} {name}: {detail}")
    if not all(ok for _, ok, _ in checks):
        raise SystemExit(1)
    click.echo("Backuppen kan gendannes.")


def register_cli(app):
    app.cli.add_command(sukker_cli)
