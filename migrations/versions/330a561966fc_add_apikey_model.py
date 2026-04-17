"""add apikey model

Revision ID: 330a561966fc
Revises: s1t2u3v4w5x6
Create Date: 2026-04-17 10:37:10.648471

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '330a561966fc'
down_revision: Union[str, Sequence[str], None] = 's1t2u3v4w5x6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "apikey" not in inspector.get_table_names():
        op.create_table('apikey',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(), nullable=False),
            sa.Column('key_hash', sa.String(), nullable=False),
            sa.Column('prefix', sa.String(), nullable=False),
            sa.Column('is_active', sa.Boolean(), server_default='1', nullable=False),
            sa.Column('scopes', sa.String(), server_default="'all'", nullable=False),
            sa.Column('created_by', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('expires_at', sa.DateTime(), nullable=True),
            sa.Column('last_used_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_apikey_created_at'), 'apikey', ['created_at'], unique=False)
        op.create_index(op.f('ix_apikey_created_by'), 'apikey', ['created_by'], unique=False)
        op.create_index(op.f('ix_apikey_expires_at'), 'apikey', ['expires_at'], unique=False)
        op.create_index(op.f('ix_apikey_last_used_at'), 'apikey', ['last_used_at'], unique=False)
        op.create_index(op.f('ix_apikey_name'), 'apikey', ['name'], unique=False)
        op.create_index(op.f('ix_apikey_prefix'), 'apikey', ['prefix'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "apikey" in inspector.get_table_names():
        op.drop_index(op.f('ix_apikey_prefix'), table_name='apikey')
        op.drop_index(op.f('ix_apikey_name'), table_name='apikey')
        op.drop_index(op.f('ix_apikey_last_used_at'), table_name='apikey')
        op.drop_index(op.f('ix_apikey_expires_at'), table_name='apikey')
        op.drop_index(op.f('ix_apikey_created_by'), table_name='apikey')
        op.drop_index(op.f('ix_apikey_created_at'), table_name='apikey')
        op.drop_table('apikey')
