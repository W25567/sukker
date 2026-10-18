"""Fejlrapporter med skærmbilleder

Revision ID: d3e8f12a7c55
Revises: c7d2a91e4b30
Create Date: 2026-10-18 14:00:00.000000

Bagudkompatibel: to nye tabeller, som den kørende version ikke bruger.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd3e8f12a7c55'
down_revision = 'c7d2a91e4b30'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('bug_reports',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=True),
    sa.Column('module', sa.String(length=20), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('user_agent', sa.String(length=255), nullable=True),
    sa.Column('status', sa.String(length=10), server_default='open', nullable=False),
    sa.Column('admin_note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('closed_by_id', sa.BigInteger(), nullable=True),
    sa.CheckConstraint("module IN ('udforsk', 'likes', 'beskeder', 'profil', 'konto', 'andet')", name=op.f('ck_bug_reports_module')),
    sa.CheckConstraint("status IN ('open', 'closed')", name=op.f('ck_bug_reports_status')),
    sa.CheckConstraint('length(description) BETWEEN 1 AND 4000', name=op.f('ck_bug_reports_description_length')),
    sa.ForeignKeyConstraint(['closed_by_id'], ['sukker.users.id'], name=op.f('fk_bug_reports_closed_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['sukker.users.id'], name=op.f('fk_bug_reports_user_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_bug_reports')),
    schema='sukker'
    )
    op.create_index(op.f('ix_sukker_bug_reports_user_id'), 'bug_reports', ['user_id'], unique=False, schema='sukker')
    op.create_table('bug_report_images',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('report_id', sa.BigInteger(), nullable=False),
    sa.Column('public_id', sa.String(length=32), nullable=False),
    sa.Column('position', sa.SmallInteger(), server_default='0', nullable=False),
    sa.Column('width', sa.Integer(), nullable=False),
    sa.Column('height', sa.Integer(), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['report_id'], ['sukker.bug_reports.id'], name=op.f('fk_bug_report_images_report_id_bug_reports'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_bug_report_images')),
    sa.UniqueConstraint('public_id', name=op.f('uq_bug_report_images_public_id')),
    schema='sukker'
    )
    op.create_index(op.f('ix_sukker_bug_report_images_report_id'), 'bug_report_images', ['report_id'], unique=False, schema='sukker')


def downgrade():
    op.drop_index(op.f('ix_sukker_bug_report_images_report_id'), table_name='bug_report_images', schema='sukker')
    op.drop_table('bug_report_images', schema='sukker')
    op.drop_index(op.f('ix_sukker_bug_reports_user_id'), table_name='bug_reports', schema='sukker')
    op.drop_table('bug_reports', schema='sukker')
