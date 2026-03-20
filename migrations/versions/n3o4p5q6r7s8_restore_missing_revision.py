"""restore_missing_revision

Revision ID: n3o4p5q6r7s8
Revises: m1n2o3p4q5r6
Create Date: 2026-03-16 00:00:00.000000
"""
from typing import Sequence, Union


revision: str = "n3o4p5q6r7s8"
down_revision: Union[str, Sequence[str], None] = "m1n2o3p4q5r6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
