from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = "9b2a1c4d7f00"
down_revision: Union[str, Sequence[str], None] = "f8dbd5098d13"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    if "user" in existing_tables:
        columns = [c["name"] for c in inspector.get_columns("user")]
        if "permissions" not in columns:
            with op.batch_alter_table("user", schema=None) as batch_op:
                batch_op.add_column(sa.Column("permissions", sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    if "user" in existing_tables:
        columns = [c["name"] for c in inspector.get_columns("user")]
        if "permissions" in columns:
            with op.batch_alter_table("user", schema=None) as batch_op:
                batch_op.drop_column("permissions")
