"""restore_missing_revision_alias

Revision ID: z9y8x7w6v5u4
Revises: m1n2o3p4q5r6
Create Date: 2026-05-29 00:00:00.000000
"""
from typing import Sequence, Union


revision: str = "z9y8x7w6v5u4"
down_revision: Union[str, Sequence[str], None] = "m1n2o3p4q5r6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Compatibility placeholder for databases stamped with a removed revision id.
    pass


def downgrade() -> None:
    pass
