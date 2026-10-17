from sqlalchemy import exists, select

from ..extensions import db
from .models import Kommune, KommuneDistance, Postcode, PostcodeKommune


def geo_loaded():
    """Er kommunedata indlæst? Uden data kan appen stadig køre, men uden kommune og afstand."""
    return db.session.scalar(select(exists().where(Postcode.postcode.isnot(None))))


def postcode_exists(postcode):
    return db.session.get(Postcode, postcode) is not None


def kommuner_for_postcode(postcode):
    """Kommunerne for et postnummer, største andel først."""
    return db.session.execute(
        select(Kommune.code, Kommune.name)
        .join(PostcodeKommune, PostcodeKommune.kommune_code == Kommune.code)
        .where(PostcodeKommune.postcode == postcode)
        .order_by(PostcodeKommune.share.desc())
    ).all()


def kommune_name(code):
    kommune = db.session.get(Kommune, code) if code else None
    return kommune.name if kommune else None


def km_between(from_code, to_code):
    if not from_code or not to_code:
        return None
    row = db.session.get(KommuneDistance, (from_code, to_code))
    return None if row is None else float(row.km)
