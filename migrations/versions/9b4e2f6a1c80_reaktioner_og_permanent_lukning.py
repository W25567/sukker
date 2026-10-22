"""Reaktioner på beskeder og permanent lukning af konti

Revision ID: 9b4e2f6a1c80
Revises: 7d2284bde0c8
Create Date: 2026-10-22 10:00:00

Bagudkompatibel: to nye tabeller, som den kørende version ikke kender, og en udvidet tilladt værdi for
users.status ("expelled"), som den kørende version aldrig sætter.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '9b4e2f6a1c80'
down_revision = '7d2284bde0c8'
branch_labels = None
depends_on = None

REACTIONS = "reaction IN ('hjerte', 'tommel', 'griner', 'overrasket', 'ked', 'vred')"


def upgrade():
    op.create_table('message_reactions',
    sa.Column('message_id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('reaction', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint(REACTIONS, name=op.f('ck_message_reactions_reaction')),
    sa.ForeignKeyConstraint(['message_id'], ['sukker.messages.id'], name=op.f('fk_message_reactions_message_id_messages'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['sukker.users.id'], name=op.f('fk_message_reactions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('message_id', 'user_id', name=op.f('pk_message_reactions')),
    schema='sukker'
    )
    op.create_table('admin_message_reactions',
    sa.Column('message_id', sa.BigInteger(), nullable=False),
    sa.Column('from_admin', sa.Boolean(), nullable=False),
    sa.Column('admin_id', sa.BigInteger(), nullable=True),
    sa.Column('reaction', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint(REACTIONS, name=op.f('ck_admin_message_reactions_reaction')),
    sa.ForeignKeyConstraint(['message_id'], ['sukker.admin_messages.id'], name=op.f('fk_admin_message_reactions_message_id_admin_messages'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['admin_id'], ['sukker.users.id'], name=op.f('fk_admin_message_reactions_admin_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('message_id', 'from_admin', name=op.f('pk_admin_message_reactions')),
    schema='sukker'
    )
    op.execute("ALTER TABLE sukker.users DROP CONSTRAINT IF EXISTS ck_users_status")
    op.create_check_constraint(op.f('ck_users_status'), 'users',
                               "status IN ('active', 'restricted', 'suspended', 'expelled', 'deleted')", schema='sukker')


def downgrade():
    op.execute("ALTER TABLE sukker.users DROP CONSTRAINT IF EXISTS ck_users_status")
    op.create_check_constraint(op.f('ck_users_status'), 'users',
                               "status IN ('active', 'restricted', 'suspended', 'deleted')", schema='sukker')
    op.drop_table('admin_message_reactions', schema='sukker')
    op.drop_table('message_reactions', schema='sukker')
