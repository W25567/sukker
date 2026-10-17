"""Tidlige fugle og velkomst

Premium for livstid til tidlige fugle (ny kilde 'early_bird') og en velkomstside, der vises én gang.
Bagudkompatibel: den forrige version bruger hverken den nye kilde eller kolonnen.

Revision ID: 8472f6840715
Revises: 518c308f34ef
Create Date: 2026-10-17 12:53:09.743623

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8472f6840715'
down_revision = '518c308f34ef'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint(op.f('ck_subscriptions_source'), 'subscriptions', type_='check', schema='sukker')
    op.create_check_constraint(op.f('ck_subscriptions_source'), 'subscriptions',
                               "source IN ('signup_bonus', 'admin', 'payment', 'early_bird')", schema='sukker')
    op.create_index('uq_subscriptions_early_bird', 'subscriptions', ['user_id'], unique=True, schema='sukker',
                    postgresql_where=sa.text("source = 'early_bird'"))
    op.add_column('users', sa.Column('welcomed_at', sa.DateTime(timezone=True), nullable=True), schema='sukker')


def downgrade():
    op.drop_column('users', 'welcomed_at', schema='sukker')
    op.drop_index('uq_subscriptions_early_bird', table_name='subscriptions', schema='sukker',
                  postgresql_where=sa.text("source = 'early_bird'"))
    op.drop_constraint(op.f('ck_subscriptions_source'), 'subscriptions', type_='check', schema='sukker')
    op.create_check_constraint(op.f('ck_subscriptions_source'), 'subscriptions',
                               "source IN ('signup_bonus', 'admin', 'payment')", schema='sukker')
