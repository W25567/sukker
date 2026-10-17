"""Indlæser kommunedata fra CSV-filerne i data/geo/ i databasen. Kræver ingen geometribiblioteker."""
import csv
import os

from sqlalchemy import delete, insert, select, update

from ..auth.models import User
from ..extensions import db
from .models import Kommune, KommuneDistance, Postcode, PostcodeKommune


def _rows(directory, name):
    with open(os.path.join(directory, name), encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def load(directory):
    """Erstatter alle kommunedata i én transaktion og udfylder kommunen for brugere, der mangler den."""
    kommuner = _rows(directory, "kommuner.csv")
    postnumre = _rows(directory, "postnumre.csv")
    mapping = _rows(directory, "postnummer_kommuner.csv")
    distances = _rows(directory, "kommune_afstande.csv")

    for model in (KommuneDistance, PostcodeKommune, Postcode):
        db.session.execute(delete(model))
    # Kommuner slettes ikke, fordi brugere peger på dem. De opdateres eller tilføjes.
    existing = set(db.session.scalars(select(Kommune.code)))
    for row in kommuner:
        if row["code"] in existing:
            db.session.execute(update(Kommune).where(Kommune.code == row["code"]).values(name=row["name"]))
        else:
            db.session.add(Kommune(code=row["code"], name=row["name"]))
    db.session.flush()

    db.session.execute(insert(Postcode), postnumre)
    db.session.execute(insert(PostcodeKommune), [
        {**row, "share": float(row["share"]), "is_primary": row["is_primary"] in ("True", "true", "1")}
        for row in mapping
    ])
    db.session.execute(insert(KommuneDistance), [{**row, "km": float(row["km"])} for row in distances])

    backfilled = backfill_user_kommuner()
    return {"kommuner": len(kommuner), "postnumre": len(postnumre), "afstande": len(distances), "brugere": backfilled}


def backfill_user_kommuner():
    """Giver brugere uden kommune deres postnummers primære kommune."""
    primary = (
        select(PostcodeKommune.kommune_code)
        .where(PostcodeKommune.postcode == User.postcode, PostcodeKommune.is_primary.is_(True))
        .scalar_subquery()
    )
    result = db.session.execute(
        update(User).where(User.kommune_code.is_(None)).values(kommune_code=primary).execution_options(synchronize_session=False)
    )
    return result.rowcount
