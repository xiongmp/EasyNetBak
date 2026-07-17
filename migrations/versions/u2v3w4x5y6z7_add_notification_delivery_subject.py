"""store the rendered notification delivery subject

Revision ID: u2v3w4x5y6z7
Revises: t1u2v3w4x5y6
Create Date: 2026-07-17 19:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "u2v3w4x5y6z7"
down_revision: Union[str, Sequence[str], None] = "t1u2v3w4x5y6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_subject_column() -> bool:
    bind = op.get_bind()
    return "subject" in {
        column["name"] for column in sa.inspect(bind).get_columns("notificationdelivery")
    }


def upgrade() -> None:
    if not _has_subject_column():
        op.add_column(
            "notificationdelivery",
            sa.Column("subject", sa.String(length=255), nullable=True),
        )


def downgrade() -> None:
    if _has_subject_column():
        with op.batch_alter_table("notificationdelivery") as batch_op:
            batch_op.drop_column("subject")
