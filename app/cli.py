"""Kommandoer til administration, indtil administrationspanelet er bygget (M7)."""
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
        f"{user.username}: {TIER_LABELS[tier]} til {subscription.expires_at:%d-%m-%Y}. "
        f"Aktivt niveau nu: {TIER_LABELS[current.tier] if current else 'Free'}."
    )


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


def register_cli(app):
    app.cli.add_command(sukker_cli)
