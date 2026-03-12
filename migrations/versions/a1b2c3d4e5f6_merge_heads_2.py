"""merge_heads_2

Revision ID: a1b2c3d4e5f6
Revises: g7h8i9j0k1l2, d2f4a9b7c1e3
Create Date: 2026-03-11 10:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = ('g7h8i9j0k1l2', 'd2f4a9b7c1e3')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    pass

def downgrade() -> None:
    pass
