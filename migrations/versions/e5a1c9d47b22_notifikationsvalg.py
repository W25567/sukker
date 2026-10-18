"""Notifikationsvalg og e-mailnotifikationer

Revision ID: e5a1c9d47b22
Revises: 697f9e3ea0ac
Create Date: 2026-10-18 22:10:00.000000

Bagudkompatibel: en ny, valgfri kolonne og en ny tabel, som den kørende version ignorerer.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'e5a1c9d47b22'
down_revision = '697f9e3ea0ac'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('notification_prefs', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
                  schema='sukker')
    op.create_table(
        'notification_mail_state',
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('item_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['sukker.users.id'], name=op.f('fk_notification_mail_state_user_id_users'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'kind', name=op.f('pk_notification_mail_state')),
        schema='sukker',
    )


def downgrade():
    op.drop_table('notification_mail_state', schema='sukker')
    op.drop_column('users', 'notification_prefs', schema='sukker')
