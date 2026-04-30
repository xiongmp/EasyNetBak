"""add_devicegroup_tree_fields

Revision ID: x4y5z6a7b8c9
Revises: w3x4y5z6a7b8
Create Date: 2026-04-29 12:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "x4y5z6a7b8c9"
down_revision: str | Sequence[str] | None = "w3x4y5z6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    with op.batch_alter_table("devicegroup") as batch_op:
        if not _has_column("devicegroup", "parent_id"):
            batch_op.add_column(sa.Column("parent_id", sa.Integer(), nullable=True))
        if not _has_column("devicegroup", "path"):
            batch_op.add_column(sa.Column("path", sa.String(), nullable=False, server_default=""))
        if not _has_column("devicegroup", "depth"):
            batch_op.add_column(sa.Column("depth", sa.Integer(), nullable=False, server_default="0"))
        if not _has_column("devicegroup", "sort_order"):
            batch_op.add_column(sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"))

    bind = op.get_bind()
    bind.execute(sa.text("UPDATE devicegroup SET parent_id = NULL WHERE parent_id IS NOT NULL"))
    bind.execute(sa.text("UPDATE devicegroup SET depth = 0 WHERE depth IS NULL"))
    bind.execute(sa.text("UPDATE devicegroup SET sort_order = 0 WHERE sort_order IS NULL"))

    rows = bind.execute(sa.text("SELECT id FROM devicegroup WHERE id IS NOT NULL")).fetchall()
    for row in rows:
        bind.execute(
            sa.text("UPDATE devicegroup SET path = :path WHERE id = :group_id"),
            {"path": f"/{int(row[0])}/", "group_id": int(row[0])},
        )

    indexes = _index_names("devicegroup")
    if "ix_devicegroup_parent_id" not in indexes:
        op.create_index("ix_devicegroup_parent_id", "devicegroup", ["parent_id"], unique=False)
    if "ix_devicegroup_path" not in indexes:
        op.create_index("ix_devicegroup_path", "devicegroup", ["path"], unique=False)

    with op.batch_alter_table("devicegroup") as batch_op:
        if _has_column("devicegroup", "path"):
            batch_op.alter_column("path", server_default=None)
        if _has_column("devicegroup", "depth"):
            batch_op.alter_column("depth", server_default=None)
        if _has_column("devicegroup", "sort_order"):
            batch_op.alter_column("sort_order", server_default=None)


def downgrade() -> None:
    indexes = _index_names("devicegroup")
    if "ix_devicegroup_path" in indexes:
        op.drop_index("ix_devicegroup_path", table_name="devicegroup")
    if "ix_devicegroup_parent_id" in indexes:
        op.drop_index("ix_devicegroup_parent_id", table_name="devicegroup")

    with op.batch_alter_table("devicegroup") as batch_op:
        if _has_column("devicegroup", "sort_order"):
            batch_op.drop_column("sort_order")
        if _has_column("devicegroup", "depth"):
            batch_op.drop_column("depth")
        if _has_column("devicegroup", "path"):
            batch_op.drop_column("path")
        if _has_column("devicegroup", "parent_id"):
            batch_op.drop_column("parent_id")
