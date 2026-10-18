"""Blød sletning af konti og billeder (sporbarhed)

Revision ID: c7d2a91e4b30
Revises: b41c7d2e9f10
Create Date: 2026-10-18 12:00:00.000000

Bagudkompatibel: nye kolonner er valgfrie, og statussen "deleted" bruges kun af den nye version.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c7d2a91e4b30'
down_revision = 'b41c7d2e9f10'
branch_labels = None
depends_on = None


def _drop_status_check():
    # Den første migration fik navnet dobbelt præfiks (ck_users_ck_users_status) via navnekonventionen.
    op.execute("ALTER TABLE sukker.users DROP CONSTRAINT IF EXISTS ck_users_ck_users_status")
    op.execute("ALTER TABLE sukker.users DROP CONSTRAINT IF EXISTS ck_users_status")


def upgrade():
    op.add_column('users', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True), schema='sukker')
    op.add_column('users', sa.Column('deleted_email', sa.String(length=254), nullable=True), schema='sukker')
    _drop_status_check()
    op.create_check_constraint(op.f('ck_users_status'), 'users',
                               "status IN ('active', 'restricted', 'suspended', 'deleted')", schema='sukker')
    op.add_column('profile_images', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True), schema='sukker')


def downgrade():
    op.drop_column('profile_images', 'deleted_at', schema='sukker')
    op.execute("DELETE FROM sukker.users WHERE status = 'deleted'")
    _drop_status_check()
    op.create_check_constraint(op.f('ck_users_status'), 'users',
                               "status IN ('active', 'restricted', 'suspended')", schema='sukker')
    op.drop_column('users', 'deleted_email', schema='sukker')
    op.drop_column('users', 'deleted_at', schema='sukker')
