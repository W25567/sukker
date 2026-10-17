from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Identity, Index, Integer, SmallInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db


class ProfileImage(db.Model):
    """Et billede i en brugers offentlige samling eller private arkiv (specifikation 4).

    Filerne ligger uden for webserverens offentlige område. `public_id` er et tilfældigt id, så
    billeder ikke kan findes ved at gætte, men adgangen kontrolleres alligevel ved hver forespørgsel.
    """

    __tablename__ = "profile_images"
    __table_args__ = (
        CheckConstraint("collection IN ('public', 'private')", name="collection"),
        Index("ix_profile_images_user_collection", "user_id", "collection", "position"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    public_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    collection: Mapped[str] = mapped_column(String(10), nullable=False, server_default="public")
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    is_animated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
