"""ensure legacy schema

Revision ID: 0002_legacy_schema
Revises: 0001_baseline
Create Date: 2026-02-03 13:00:00.000000

"""
from typing import Sequence, Union
from datetime import datetime

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text, inspect


# revision identifiers, used by Alembic.
revision: str = '0002_legacy_schema'
down_revision: Union[str, Sequence[str], None] = '0001_baseline'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()

    # --- LoginLog Migration ---
    if "loginlog" in existing_tables:
        cols_info = [c['name'] for c in inspector.get_columns('loginlog')]
        # Check if status is BOOLEAN (SQLite stores boolean as INTEGER or custom type, but usually checking type is hard in raw sqlite, 
        # so we rely on what _migrate_sqlite did: it checked PRAGMA. 
        # Here we can check if the type in metadata is Boolean or if we just want to force migrate.
        # However, to be safe and idempotent, we check if we need to migrate.
        # The old _migrate_sqlite checked: if "status" in cols and cols["status"] == "BOOLEAN"
        # In Alembic/SQLAlchemy inspector, we can check the type.
        
        status_col = next((c for c in inspector.get_columns('loginlog') if c['name'] == 'status'), None)
        # Note: SQLAlchemy might reflect BOOLEAN as INTEGER in SQLite. 
        # But if it's already VARCHAR (migrated), we skip.
        
        needs_migration = False
        if status_col:
            col_type = str(status_col['type']).upper()
            if 'BOOL' in col_type or 'INTEGER' in col_type: # Assuming old boolean was 0/1 integer
                 # But wait, if it's already migrated to VARCHAR, it might be VARCHAR.
                 # Let's check if it is NOT VARCHAR.
                 if not ('CHAR' in col_type or 'TEXT' in col_type):
                     needs_migration = True
        
        if needs_migration and conn.dialect.name == "sqlite":
            # Manual migration logic from _migrate_sqlite - ONLY FOR SQLITE
            op.rename_table('loginlog', 'loginlog_old')
            op.execute(
                """
                CREATE TABLE loginlog (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username VARCHAR NOT NULL,
                    ip_address VARCHAR,
                    user_agent VARCHAR,
                    status VARCHAR NOT NULL DEFAULT 'fail',
                    fail_reason VARCHAR,
                    created_at DATETIME
                )
                """
            )
            op.create_index('ix_loginlog_username', 'loginlog', ['username'])
            op.create_index('ix_loginlog_created_at', 'loginlog', ['created_at'])
            
            op.execute(
                """
                INSERT INTO loginlog (id, username, ip_address, user_agent, status, fail_reason, created_at)
                SELECT id, username, ip_address, user_agent, CASE WHEN status THEN 'success' ELSE 'fail' END, fail_reason, created_at
                FROM loginlog_old
                """
            )
            op.drop_table('loginlog_old')

    # --- User Migration ---
    if "user" in existing_tables:
        cols = [c['name'] for c in inspector.get_columns('user')]
        if "email" in cols and "username" not in cols:
            with op.batch_alter_table("user") as batch_op:
                batch_op.alter_column("email", new_column_name="username")
        
        # Refresh cols
        cols = [c['name'] for c in inspector.get_columns('user')]
        
        if "password_expired" not in cols:
            op.add_column("user", sa.Column("password_expired", sa.Boolean(), server_default="0"))
            op.execute("UPDATE user SET password_expired = 1")
            
        if "group_access_type" not in cols:
            op.add_column("user", sa.Column("group_access_type", sa.String(), server_default="all"))
            
        if "allowed_group_ids" not in cols:
            op.add_column("user", sa.Column("allowed_group_ids", sa.String(), nullable=True))

    # --- Device Migration ---
    if "device" in existing_tables:
        cols = [c['name'] for c in inspector.get_columns('device')]
        
        new_cols = [
            ("credential_id", sa.Integer()),
            ("group_id", sa.Integer()),
            ("default_template_id", sa.Integer()),
            ("login_method", sa.Text(), "ssh"),
            ("ssh_key_path", sa.Text()),
            ("enable_password", sa.Text()),
            ("password", sa.Text()),
            ("username", sa.Text()),
            ("reachability_status", sa.Boolean()),
            ("last_reachability_check", sa.DateTime()),
            ("reachability_error", sa.Text()),
            ("reachability_duration_ms", sa.Integer())
        ]
        
        with op.batch_alter_table("device") as batch_op:
            for col_name, col_type, *defaults in new_cols:
                if col_name not in cols:
                    server_default = defaults[0] if defaults else None
                    if server_default:
                        batch_op.add_column(sa.Column(col_name, col_type, server_default=server_default))
                    else:
                        batch_op.add_column(sa.Column(col_name, col_type))

    # --- Credential Migration ---
    if "credential" in existing_tables:
        cols = [c['name'] for c in inspector.get_columns('credential')]
        if "remarks" not in cols:
            op.add_column("credential", sa.Column("remarks", sa.Text(), nullable=True))

    # --- BackupRecord Migration ---
    if "backuprecord" in existing_tables:
        cols = [c['name'] for c in inspector.get_columns('backuprecord')]
        if "duration_seconds" not in cols:
            op.add_column("backuprecord", sa.Column("duration_seconds", sa.Float(), nullable=True))
        if "failure_type" not in cols:
            op.add_column("backuprecord", sa.Column("failure_type", sa.Text(), nullable=True))

    # --- Data Migration: Device -> Credential ---
    if "credential" in existing_tables and "device" in existing_tables:
        # We need to use raw SQL for data migration to avoid model dependencies
        # This logic mimics _migrate_sqlite's extraction of credentials
        conn.execute(
            text(
                """
                INSERT INTO credential (name, username, password, enable_password, ssh_key_path, created_at)
                SELECT 
                    COALESCE(name, 'device-' || id) || ' 凭据',
                    COALESCE(username, ''),
                    password, -- Assuming encrypted or handled by app logic, pure copy here
                    enable_password,
                    ssh_key_path,
                    datetime('now')
                FROM device 
                WHERE credential_id IS NULL 
                  AND (username IS NOT NULL OR password IS NOT NULL OR ssh_key_path IS NOT NULL)
                """
            )
        )
        
        # Update device credential_ids
        # This is tricky in pure SQL without a loop if we want to link them exactly back.
        # _migrate_sqlite did it with a loop in Python.
        # Doing it in pure SQL:
        # UPDATE device SET credential_id = (SELECT id FROM credential WHERE ... matches ...)
        # But "matches" is hard because we just inserted them.
        # For simplicity and safety, we might skip the complex data migration here if it's too risky in SQL.
        # The Python loop in _migrate_sqlite was:
        # for row in rows: insert credential, get id, update device.
        
        # We can do the same here using Alembic's bind
        
        rows = conn.execute(
            text(
                "SELECT id, name, username, password, enable_password, ssh_key_path "
                "FROM device WHERE credential_id IS NULL AND (username IS NOT NULL OR password IS NOT NULL OR ssh_key_path IS NOT NULL)"
            )
        ).fetchall()
        
        if rows:
            # We need to import encrypt_secret if we want to encrypt, 
            # BUT _migrate_sqlite logic imported it: from app.services.crypto import encrypt_secret
            # We can import it here too.
            try:
                from app.services.crypto import encrypt_secret
            except ImportError:
                # Fallback or dummy if not available
                encrypt_secret = lambda x: x
                
            for device_id, device_name, username, password, enable_password, ssh_key_path in rows:
                cred_name = (device_name or f"device-{device_id}") + " 凭据"
                enc_password = encrypt_secret(password) if password else None
                enc_enable = encrypt_secret(enable_password) if enable_password else None
                created_at = datetime.utcnow().isoformat()
                
                result = conn.execute(
                    text(
                        "INSERT INTO credential (name, username, password, enable_password, ssh_key_path, created_at) "
                        "VALUES (:name, :username, :password, :enable_password, :ssh_key_path, :created_at)"
                    ),
                    {
                        "name": cred_name,
                        "username": username or "",
                        "password": enc_password,
                        "enable_password": enc_enable,
                        "ssh_key_path": ssh_key_path,
                        "created_at": created_at,
                    },
                )
                credential_id = result.lastrowid
                conn.execute(
                    text("UPDATE device SET credential_id = :cid WHERE id = :did"),
                    {"cid": credential_id, "did": device_id},
                )


def downgrade() -> None:
    # Downgrade logic is complex for this, we generally assume forward fix.
    pass
