"""Kommuner, postnumre og afstande mellem kommuner (specifikation 5).

Data bygges fra DAGI (Datafordeleren) med `flask sukker build-geo` og indlæses med `flask sukker load-geo`.
"""
from sqlalchemy import Boolean, Float, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db


class Kommune(db.Model):
    __tablename__ = "kommuner"

    code: Mapped[str] = mapped_column(String(4), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)


class Postcode(db.Model):
    __tablename__ = "postnumre"

    postcode: Mapped[str] = mapped_column(String(4), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)


class PostcodeKommune(db.Model):
    """Et postnummer kan dække flere kommuner. `share` er den andel af postnummerets areal, der ligger i kommunen."""

    __tablename__ = "postnummer_kommuner"

    postcode: Mapped[str] = mapped_column(ForeignKey("postnumre.postcode", ondelete="CASCADE"), primary_key=True)
    kommune_code: Mapped[str] = mapped_column(ForeignKey("kommuner.code", ondelete="CASCADE"), primary_key=True)
    share: Mapped[float] = mapped_column(Float, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False)


class KommuneDistance(db.Model):
    """Korteste afstand mellem to kommuners grænser i km. Nabokommuner og samme kommune har 0."""

    __tablename__ = "kommune_afstande"

    from_code: Mapped[str] = mapped_column(ForeignKey("kommuner.code", ondelete="CASCADE"), primary_key=True)
    to_code: Mapped[str] = mapped_column(ForeignKey("kommuner.code", ondelete="CASCADE"), primary_key=True)
    km: Mapped[float] = mapped_column(Numeric(6, 1), nullable=False)
