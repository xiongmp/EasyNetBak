"""remove ssh_key_path from credential

Revision ID: 3a6c5d4e2f1b
Revises: f8dbd5098d13
Create Date: 2026-02-05 15:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '3a6c5d4e2f1b'
down_revision: Union[str, Sequence[str], None] = 'cc18662bb34f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Helper to check if column exists
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    
    if 'credential' in existing_tables:
        columns = [c['name'] for c in inspector.get_columns('credential')]
        if 'ssh_key_path' in columns:
            with op.batch_alter_table('credential', schema=None) as batch_op:
                batch_op.drop_column('ssh_key_path')


def downgrade() -> None:
    # Helper to check if column exists
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    
    if 'credential' in existing_tables:
        columns = [c['name'] for c in inspector.get_columns('credential')]
        if 'ssh_key_path' not in columns:
            with op.batch_alter_table('credential', schema=None) as batch_op:
                batch_op.add_column(sa.Column('ssh_key_path', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
