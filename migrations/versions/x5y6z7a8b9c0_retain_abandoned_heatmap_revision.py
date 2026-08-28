"""retain the abandoned heatmap metadata revision

Revision ID: x5y6z7a8b9c0
Revises: w4x5y6z7a8b9
Create Date: 2026-08-25 12:00:00.000000

This revision was briefly applied during development before the associated
schema change was abandoned. Keep the identifier in the Alembic chain so
databases that recorded it remain upgradeable. Fresh databases intentionally
perform no schema change here.
"""

from __future__ import annotations

from collections.abc import Sequence


revision: str = "x5y6z7a8b9c0"
down_revision: str | Sequence[str] | None = "w4x5y6z7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
