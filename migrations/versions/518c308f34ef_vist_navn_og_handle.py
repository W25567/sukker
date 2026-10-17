"""Vist navn og handle

Navnet, andre ser, behøver ikke længere være unikt (kun e-mailen er). Profil-URL'er bruger i stedet et
tilfældigt handle. Kolonnen hedder fortsat "username" i databasen, så migrationen er bagudkompatibel:
den forrige version kan køre mod skemaet ved en tilbagerulning (specifikation 14.4).

Revision ID: 518c308f34ef
Revises: ec5aaac6d16d
Create Date: 2026-10-17 12:47:52.689811

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '518c308f34ef'
down_revision = 'ec5aaac6d16d'
branch_labels = None
depends_on = None

# Bruges kun, når en række oprettes uden handle (fx af den forrige version). Appen laver sine egne.
HANDLE_DEFAULT = "substr(md5(random()::text || clock_timestamp()::text), 1, 10)"


def upgrade():
    op.drop_index('uq_users_username_lower', table_name='users', schema='sukker')
    op.add_column('users', sa.Column('handle', sa.String(length=12), nullable=True,
                                     server_default=sa.text(HANDLE_DEFAULT)), schema='sukker')
    op.execute(f"UPDATE sukker.users SET handle = {HANDLE_DEFAULT} WHERE handle IS NULL")
    op.alter_column('users', 'handle', nullable=False, schema='sukker')
    op.create_index('uq_users_handle', 'users', ['handle'], unique=True, schema='sukker')
    op.execute("COMMENT ON COLUMN sukker.users.username IS 'Vist navn (display_name i koden). Ikke unikt.'")


def downgrade():
    op.drop_index('uq_users_handle', table_name='users', schema='sukker')
    op.drop_column('users', 'handle', schema='sukker')
    op.create_index('uq_users_username_lower', 'users', [sa.text('lower(username)')], unique=True, schema='sukker')
