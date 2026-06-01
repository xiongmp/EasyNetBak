"""optimize_config_search_indexes

Revision ID: p4q5r6s7t8u9
Revises: n3o4p5q6r7s8, z9y8x7w6v5u4, y6z7a8b9c0d1
Create Date: 2026-03-16 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "p4q5r6s7t8u9"
down_revision: Union[str, Sequence[str], None] = ("n3o4p5q6r7s8", "z9y8x7w6v5u4", "y6z7a8b9c0d1")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    if "backuprecord" not in existing_tables:
        return
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_backuprecord_config_text_tsv_gin "
            "ON backuprecord USING gin (to_tsvector('simple', coalesce(config_text, '')))"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_backuprecord_config_text_trgm_gin "
            "ON backuprecord USING gin (config_text gin_trgm_ops)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_backuprecord_success_device_started_at "
            "ON backuprecord (device_id, started_at DESC) WHERE success = true"
        )
        return
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("backuprecord")}
    if "ix_backuprecord_success_device_started_at" not in existing_indexes:
        op.create_index(
            "ix_backuprecord_success_device_started_at",
            "backuprecord",
            ["success", "device_id", "started_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    if "backuprecord" not in existing_tables:
        return
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_backuprecord_success_device_started_at")
        op.execute("DROP INDEX IF EXISTS ix_backuprecord_config_text_trgm_gin")
        op.execute("DROP INDEX IF EXISTS ix_backuprecord_config_text_tsv_gin")
        return
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("backuprecord")}
    if "ix_backuprecord_success_device_started_at" in existing_indexes:
        op.drop_index("ix_backuprecord_success_device_started_at", table_name="backuprecord")
