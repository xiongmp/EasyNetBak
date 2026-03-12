"""merge_recovery_codes_head

Revision ID: j7k8l9m0n1o2
Revises: a1b2c3d4e5f6, h1i2j3k4l5m6
Create Date: 2026-03-11 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = "j7k8l9m0n1o2"
down_revision: Union[str, Sequence[str], None] = ("a1b2c3d4e5f6", "h1i2j3k4l5m6")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
