"""Ansøgninger om at blive moderator

Revision ID: 5f1d8c3b7e21
Revises: 9b4e2f6a1c80
Create Date: 2026-10-22 16:00:00

Bagudkompatibel: en ny tabel, som den kørende version ikke kender.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '5f1d8c3b7e21'
down_revision = '9b4e2f6a1c80'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('moderator_applications',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('motivation', sa.Text(), nullable=False),
    sa.Column('experience', sa.Text(), nullable=True),
    sa.Column('availability', sa.String(length=200), nullable=True),
    sa.Column('status', sa.String(length=20), server_default='pending', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decided_by_id', sa.BigInteger(), nullable=True),
    sa.Column('decision_note', sa.Text(), nullable=True),
    sa.CheckConstraint("status IN ('pending', 'approved', 'rejected', 'withdrawn')", name=op.f('ck_moderator_applications_status')),
    sa.CheckConstraint('length(motivation) BETWEEN 1 AND 2000', name=op.f('ck_moderator_applications_motivation_length')),
    sa.ForeignKeyConstraint(['decided_by_id'], ['sukker.users.id'], name=op.f('fk_moderator_applications_decided_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['sukker.users.id'], name=op.f('fk_moderator_applications_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_moderator_applications')),
    schema='sukker'
    )
    op.create_index(op.f('ix_moderator_applications_user_id'), 'moderator_applications', ['user_id'], unique=False, schema='sukker')
    op.create_index('uq_moderator_applications_pending', 'moderator_applications', ['user_id'], unique=True, schema='sukker',
                    postgresql_where=sa.text("status = 'pending'"))


def downgrade():
    op.drop_index('uq_moderator_applications_pending', table_name='moderator_applications', schema='sukker')
    op.drop_index(op.f('ix_moderator_applications_user_id'), table_name='moderator_applications', schema='sukker')
    op.drop_table('moderator_applications', schema='sukker')
