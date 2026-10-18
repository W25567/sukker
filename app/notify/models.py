from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db


class NotificationMailState(db.Model):
    """Hvornår brugeren sidst har fået en e-mail af en bestemt slags (app/notify/mail.py).

    `item_at` er tidspunktet for det nyeste, brugeren har fået besked om. For påmindelser om Premium er det
    udløbstidspunktet, så hver periode kun giver én påmindelse pr. trin."""

    __tablename__ = "notification_mail_state"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    item_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
