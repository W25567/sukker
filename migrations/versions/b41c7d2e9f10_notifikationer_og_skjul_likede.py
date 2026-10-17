"""Notifikationer for nye matches og præferencen "skjul profiler, jeg har liket"

Revision ID: b41c7d2e9f10
Revises: ee0a150ad2a4
Create Date: 2026-10-17 17:10:00.000000

Bagudkompatibel: to nye kolonner med standardværdier. Den kørende version ignorerer dem.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b41c7d2e9f10'
down_revision = 'ee0a150ad2a4'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('matches_seen_at', sa.DateTime(timezone=True), nullable=True), schema='sukker')
    # Eksisterende matches er allerede kendt af brugerne og skal ikke vises som nye efter udgivelsen.
    op.execute("UPDATE sukker.users SET matches_seen_at = now()")
    op.add_column('profiles', sa.Column('hide_liked', sa.Boolean(), server_default='false', nullable=False), schema='sukker')


def downgrade():
    op.drop_column('profiles', 'hide_liked', schema='sukker')
    op.drop_column('users', 'matches_seen_at', schema='sukker')
