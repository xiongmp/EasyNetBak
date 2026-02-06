from __future__ import annotations

from typing import Sequence

from alembic import op


revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    return None


def downgrade() -> None:
    return None
