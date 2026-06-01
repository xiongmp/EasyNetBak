"""restore_missing_revision_alias

Revision ID: y6z7a8b9c0d1
Revises: m1n2o3p4q5r6
Create Date: 2026-06-01 00:00:00.000000
"""
from typing import Sequence, Union


revision: str = "y6z7a8b9c0d1"
down_revision: Union[str, Sequence[str], None] = "m1n2o3p4q5r6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Compatibility placeholder for databases stamped with a removed revision id.
    pass


def downgrade() -> None:
    pass
