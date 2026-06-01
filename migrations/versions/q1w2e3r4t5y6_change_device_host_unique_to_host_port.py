"""change_device_host_unique_to_host_port

Revision ID: q1w2e3r4t5y6
Revises: x4y5z6a7b8c9
Create Date: 2026-05-29 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "q1w2e3r4t5y6"
down_revision: Union[str, Sequence[str], None] = "x4y5z6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_unique_constraint(table_name: str, constraint_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(item["name"] == constraint_name for item in inspector.get_unique_constraints(table_name))


def _assert_unique_host_port_values() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT host, port, COUNT(*) AS cnt
            FROM device
            GROUP BY host, port
            HAVING COUNT(*) > 1
            """
        )
    ).fetchall()
    if rows:
        values = ", ".join(f"{row[0]}:{row[1]}" for row in rows)
        raise RuntimeError(f"device.host+port contains duplicate values: {values}")


def _assert_unique_host_values() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT host, COUNT(*) AS cnt
            FROM device
            GROUP BY host
            HAVING COUNT(*) > 1
            """
        )
    ).fetchall()
    if rows:
        values = ", ".join(str(row[0]) for row in rows)
        raise RuntimeError(f"device.host contains duplicate values: {values}")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "device" not in inspector.get_table_names():
        return

    _assert_unique_host_port_values()

    has_old = _has_unique_constraint("device", "uq_device_host")
    has_new = _has_unique_constraint("device", "uq_device_host_port")
    if not has_old and has_new:
        return

    with op.batch_alter_table("device") as batch_op:
        if has_old:
            batch_op.drop_constraint("uq_device_host", type_="unique")
        if not has_new:
            batch_op.create_unique_constraint("uq_device_host_port", ["host", "port"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "device" not in inspector.get_table_names():
        return

    _assert_unique_host_values()

    has_old = _has_unique_constraint("device", "uq_device_host")
    has_new = _has_unique_constraint("device", "uq_device_host_port")
    if has_old and not has_new:
        return

    with op.batch_alter_table("device") as batch_op:
        if has_new:
            batch_op.drop_constraint("uq_device_host_port", type_="unique")
        if not has_old:
            batch_op.create_unique_constraint("uq_device_host", ["host"])
