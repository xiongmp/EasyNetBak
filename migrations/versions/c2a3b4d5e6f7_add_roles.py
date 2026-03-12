"""add_roles

Revision ID: c2a3b4d5e6f7
Revises: 850f7ec72c4b
Create Date: 2026-03-10 00:00:00.000000
"""
from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "c2a3b4d5e6f7"
down_revision: Union[str, Sequence[str], None] = "850f7ec72c4b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if "role" not in existing_tables:
        op.create_table(
            "role",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("permissions", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("is_system", sa.Boolean(), server_default="0", nullable=False),
            sa.Column("is_admin", sa.Boolean(), server_default="0", nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_role_code", "role", ["code"], unique=True)
        op.create_index("ix_role_created_at", "role", ["created_at"], unique=False)
    else:
        cols = [c["name"] for c in inspector.get_columns("role")]
        with op.batch_alter_table("role", schema=None) as batch_op:
            if "code" not in cols:
                batch_op.add_column(sa.Column("code", sa.String(), nullable=False))
            if "name" not in cols:
                batch_op.add_column(sa.Column("name", sa.String(), nullable=False))
            if "permissions" not in cols:
                batch_op.add_column(sa.Column("permissions", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
            if "is_system" not in cols:
                batch_op.add_column(sa.Column("is_system", sa.Boolean(), server_default="0", nullable=False))
            if "is_admin" not in cols:
                batch_op.add_column(sa.Column("is_admin", sa.Boolean(), server_default="0", nullable=False))
            if "created_at" not in cols:
                batch_op.add_column(sa.Column("created_at", sa.DateTime(), nullable=False))
        if "code" in cols and "ix_role_code" not in {i["name"] for i in inspector.get_indexes("role")}:
            op.create_index("ix_role_code", "role", ["code"], unique=True)
        if "created_at" in cols and "ix_role_created_at" not in {i["name"] for i in inspector.get_indexes("role")}:
            op.create_index("ix_role_created_at", "role", ["created_at"], unique=False)

    operator_permissions = [
        "audit_logs.view",
        "backups.view",
        "backups.trigger",
        "config_search.view",
        "credentials.view",
        "credentials.create",
        "credentials.update",
        "credentials.delete",
        "dashboard.view",
        "devices.view",
        "devices.create",
        "devices.update",
        "devices.delete",
        "devices.backup",
        "devices.webshell",
        "groups.view",
        "groups.create",
        "groups.update",
        "groups.delete",
        "login_logs.view",
        "schedules.view",
        "schedules.create",
        "schedules.update",
        "schedules.delete",
        "templates.view",
        "templates.create",
        "templates.update",
        "templates.delete",
    ]
    readonly_permissions = [
        "audit_logs.view",
        "backups.view",
        "config_search.view",
        "credentials.view",
        "dashboard.view",
        "devices.view",
        "groups.view",
        "login_logs.view",
        "schedules.view",
        "templates.view",
    ]

    now = datetime.utcnow()
    seeds = [
        {
            "code": "admin",
            "name": "系统管理员",
            "permissions": None,
            "is_system": True,
            "is_admin": True,
        },
        {
            "code": "operator",
            "name": "操作员",
            "permissions": ",".join(sorted(operator_permissions)),
            "is_system": True,
            "is_admin": False,
        },
        {
            "code": "readonly",
            "name": "只读用户",
            "permissions": ",".join(sorted(readonly_permissions)),
            "is_system": True,
            "is_admin": False,
        },
    ]

    dialect = bind.dialect.name
    for seed in seeds:
        params = {
            "code": seed["code"],
            "name": seed["name"],
            "permissions": seed["permissions"],
            "is_system": seed["is_system"],
            "is_admin": seed["is_admin"],
            "created_at": now,
        }
        if dialect == "postgresql":
            bind.execute(
                sa.text(
                    "INSERT INTO role (code, name, permissions, is_system, is_admin, created_at) "
                    "VALUES (:code, :name, :permissions, :is_system, :is_admin, :created_at) "
                    "ON CONFLICT (code) DO NOTHING"
                ),
                params,
            )
        elif dialect == "sqlite":
            bind.execute(
                sa.text(
                    "INSERT OR IGNORE INTO role (code, name, permissions, is_system, is_admin, created_at) "
                    "VALUES (:code, :name, :permissions, :is_system, :is_admin, :created_at)"
                ),
                params,
            )
        else:
            try:
                bind.execute(
                    sa.text(
                        "INSERT INTO role (code, name, permissions, is_system, is_admin, created_at) "
                        "VALUES (:code, :name, :permissions, :is_system, :is_admin, :created_at)"
                    ),
                    params,
                )
            except Exception:
                continue


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    if "role" in existing_tables:
        op.drop_index("ix_role_created_at", table_name="role")
        op.drop_index("ix_role_code", table_name="role")
        op.drop_table("role")
