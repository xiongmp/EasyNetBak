"""add user locale preference

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-07-10 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8d9e0f1a2b3"
down_revision: Union[str, Sequence[str], None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "user" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("user")}
    if "locale" not in columns:
        with op.batch_alter_table("user", schema=None) as batch_op:
            batch_op.add_column(sa.Column("locale", sa.String(length=16), server_default="zh-CN", nullable=False))
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("user")}
    if "ix_user_locale" not in indexes:
        with op.batch_alter_table("user", schema=None) as batch_op:
            batch_op.create_index("ix_user_locale", ["locale"], unique=False)
    op.execute(sa.text('UPDATE "user" SET locale = :locale WHERE locale IS NULL OR locale = :empty').bindparams(locale="zh-CN", empty=""))
    if "backuprecord" in inspector.get_table_names():
        backup_columns = {column["name"] for column in sa.inspect(bind).get_columns("backuprecord")}
        if "locale" not in backup_columns:
            with op.batch_alter_table("backuprecord", schema=None) as batch_op:
                batch_op.add_column(sa.Column("locale", sa.String(length=16), server_default="zh-CN", nullable=False))
        op.execute(sa.text("UPDATE backuprecord SET locale = :locale WHERE locale IS NULL OR locale = :empty").bindparams(locale="zh-CN", empty=""))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "user" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("user")}
    if "locale" in columns:
        with op.batch_alter_table("user", schema=None) as batch_op:
            indexes = {index["name"] for index in inspector.get_indexes("user")}
            if "ix_user_locale" in indexes:
                batch_op.drop_index("ix_user_locale")
            batch_op.drop_column("locale")
    if "backuprecord" in inspector.get_table_names():
        backup_columns = {column["name"] for column in inspector.get_columns("backuprecord")}
        if "locale" in backup_columns:
            with op.batch_alter_table("backuprecord", schema=None) as batch_op:
                batch_op.drop_column("locale")
