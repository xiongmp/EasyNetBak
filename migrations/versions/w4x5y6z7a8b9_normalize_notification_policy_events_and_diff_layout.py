"""retain the notification policy and diff-layout data revision

Revision ID: w4x5y6z7a8b9
Revises: v3w4x5y6z7a8
Create Date: 2026-07-17 21:30:00.000000

The data normalization performed by this revision is also guarded by the
notification built-in schema version in ``ensure_builtin_defaults``. Keeping
this revision in the chain is required for databases that already recorded it;
new and partially upgraded databases are synchronized idempotently when the
notification service initializes.
"""
from typing import Sequence, Union


revision: str = "w4x5y6z7a8b9"
down_revision: Union[str, Sequence[str], None] = "v3w4x5y6z7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
