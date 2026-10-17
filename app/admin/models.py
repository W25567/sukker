from datetime import datetime

from sqlalchemy import DDL, BigInteger, Boolean, DateTime, ForeignKey, Identity, String, Text, event, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db


class UserFeatureGrant(db.Model):
    """En individuel regel for én funktion, fx til test. `granted=False` fratager funktionen (specifikation 10.4)."""

    __tablename__ = "user_feature_grants"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    feature: Mapped[str] = mapped_column(String(40), nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
    granted_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AdminAuditLog(db.Model):
    """Revisionslog over administrative handlinger, der ændrer adgang, status eller indstillinger (specifikation 11).

    Kan ikke ændres eller slettes. Navne gemmes som de var, og der er ingen fremmednøgler."""

    __tablename__ = "admin_audit_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    admin_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    admin_username: Mapped[str] = mapped_column(String(30), nullable=False)
    target_user_id: Mapped[int | None] = mapped_column(BigInteger)
    target_username: Mapped[str | None] = mapped_column(String(30))
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    details: Mapped[dict | None] = mapped_column(JSONB)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)


# Samme SQL står i migrationen for M7.
APPEND_ONLY_SQL = """
CREATE OR REPLACE FUNCTION sukker.admin_audit_log_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'admin_audit_log kan ikke ændres eller slettes';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER admin_audit_log_append_only
    BEFORE UPDATE OR DELETE ON sukker.admin_audit_log
    FOR EACH ROW EXECUTE FUNCTION sukker.admin_audit_log_append_only();
"""

event.listen(AdminAuditLog.__table__, "after_create", DDL(APPEND_ONLY_SQL))
