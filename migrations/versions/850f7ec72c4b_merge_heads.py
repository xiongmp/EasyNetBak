"""merge_heads

Revision ID: 850f7ec72c4b
Revises: 046b9b0bfc25, 9b2a1c4d7f00
Create Date: 2026-03-10 12:15:05.581050

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '850f7ec72c4b'
down_revision: Union[str, Sequence[str], None] = ('046b9b0bfc25', '9b2a1c4d7f00')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
