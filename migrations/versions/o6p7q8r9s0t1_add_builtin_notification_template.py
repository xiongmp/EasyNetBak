"""add the built-in detailed notification template

Revision ID: o6p7q8r9s0t1
Revises: n5o6p7q8r9s0
Create Date: 2026-07-16 12:00:00.000000
"""
from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "o6p7q8r9s0t1"
down_revision: Union[str, Sequence[str], None] = "n5o6p7q8r9s0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BUILTIN_KEY = "legacy_detailed_email"
_POLICY_KEYS = ("legacy_failure", "legacy_config_change", "legacy_summary")


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _column_names(table: str) -> set[str]:
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(table)
    }


def _ensure_unique_builtin_key() -> None:
    inspector = sa.inspect(op.get_bind())
    unique_columns = {
        tuple(constraint.get("column_names") or [])
        for constraint in inspector.get_unique_constraints("notificationtemplate")
    }
    unique_columns.update(
        tuple(index.get("column_names") or [])
        for index in inspector.get_indexes("notificationtemplate")
        if index.get("unique")
    )
    if ("builtin_key",) not in unique_columns:
        op.create_index(
            "ux_notificationtemplate_builtin_key",
            "notificationtemplate",
            ["builtin_key"],
            unique=True,
        )


def upgrade() -> None:
    tables = _table_names()
    if "notificationtemplate" not in tables or "notificationpolicy" not in tables:
        raise RuntimeError(
            "Notification routing tables are missing; apply revision "
            "n5o6p7q8r9s0 before this migration."
        )

    columns = _column_names("notificationtemplate")
    if "builtin_key" not in columns:
        op.add_column(
            "notificationtemplate",
            sa.Column("builtin_key", sa.String(length=64), nullable=True),
        )
    if "renderer_key" not in columns:
        op.add_column(
            "notificationtemplate",
            sa.Column("renderer_key", sa.String(length=64), nullable=True),
        )

    bind = op.get_bind()
    now = datetime.utcnow()
    template_id = bind.execute(
        sa.text(
            "SELECT id FROM notificationtemplate "
            "WHERE builtin_key = :builtin_key ORDER BY id LIMIT 1"
        ),
        {"builtin_key": _BUILTIN_KEY},
    ).scalar_one_or_none()
    if template_id is None:
        template_id = bind.execute(
            sa.text(
                "SELECT id FROM notificationtemplate "
                "WHERE name = :name ORDER BY id LIMIT 1"
            ),
            {"name": "Legacy detailed email"},
        ).scalar_one_or_none()

    if template_id is None:
        bind.execute(
            sa.text(
                "INSERT INTO notificationtemplate "
                "(name, event_type, channel_type, locale, subject_template, "
                "body_template, content_type, builtin_key, renderer_key, created_at, updated_at) "
                "VALUES (:name, :event_type, :channel_type, :locale, :subject_template, "
                ":body_template, :content_type, :builtin_key, :renderer_key, :created_at, :updated_at)"
            ),
            {
                "name": "Legacy detailed email",
                "event_type": "*",
                "channel_type": "smtp",
                "locale": "zh-CN",
                "subject_template": "",
                "body_template": (
                    "The original detailed EasyNetBak email renderer is used at delivery time."
                ),
                "content_type": "html",
                "builtin_key": _BUILTIN_KEY,
                "renderer_key": _BUILTIN_KEY,
                "created_at": now,
                "updated_at": now,
            },
        )
        template_id = bind.execute(
            sa.text(
                "SELECT id FROM notificationtemplate WHERE builtin_key = :builtin_key"
            ),
            {"builtin_key": _BUILTIN_KEY},
        ).scalar_one()
    else:
        bind.execute(
            sa.text(
                "UPDATE notificationtemplate SET event_type = :event_type, "
                "channel_type = :channel_type, content_type = :content_type, "
                "builtin_key = :builtin_key, renderer_key = :renderer_key, "
                "updated_at = :updated_at WHERE id = :template_id"
            ),
            {
                "event_type": "*",
                "channel_type": "smtp",
                "content_type": "html",
                "builtin_key": _BUILTIN_KEY,
                "renderer_key": _BUILTIN_KEY,
                "updated_at": now,
                "template_id": int(template_id),
            },
        )

    bind.execute(
        sa.text(
            "UPDATE notificationpolicy SET template_id = :template_id, updated_at = :updated_at "
            "WHERE builtin_key IN (:failure, :change, :summary)"
        ),
        {
            "template_id": int(template_id),
            "updated_at": now,
            "failure": _POLICY_KEYS[0],
            "change": _POLICY_KEYS[1],
            "summary": _POLICY_KEYS[2],
        },
    )
    _ensure_unique_builtin_key()


def downgrade() -> None:
    if "notificationtemplate" not in _table_names():
        return

    bind = op.get_bind()
    if "notificationpolicy" in _table_names():
        bind.execute(
            sa.text(
                "UPDATE notificationpolicy SET template_id = NULL "
                "WHERE builtin_key IN (:failure, :change, :summary)"
            ),
            {
                "failure": _POLICY_KEYS[0],
                "change": _POLICY_KEYS[1],
                "summary": _POLICY_KEYS[2],
            },
        )
    if "builtin_key" in _column_names("notificationtemplate"):
        bind.execute(
            sa.text("DELETE FROM notificationtemplate WHERE builtin_key = :builtin_key"),
            {"builtin_key": _BUILTIN_KEY},
        )

    inspector = sa.inspect(bind)
    for index in inspector.get_indexes("notificationtemplate"):
        if index.get("name") == "ux_notificationtemplate_builtin_key":
            op.drop_index(index["name"], table_name="notificationtemplate")

    unique_constraints = {
        constraint.get("name")
        for constraint in sa.inspect(bind).get_unique_constraints("notificationtemplate")
        if tuple(constraint.get("column_names") or []) == ("builtin_key",)
        and constraint.get("name")
    }
    with op.batch_alter_table("notificationtemplate") as batch_op:
        for constraint_name in unique_constraints:
            batch_op.drop_constraint(constraint_name, type_="unique")
        columns = _column_names("notificationtemplate")
        if "renderer_key" in columns:
            batch_op.drop_column("renderer_key")
        if "builtin_key" in columns:
            batch_op.drop_column("builtin_key")
