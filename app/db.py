from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from app.core.settings import settings


engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _run_alembic_upgrade()


def _run_alembic_upgrade() -> None:
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[1]
    cfg_path = root / "alembic.ini"
    if not cfg_path.is_file():
        raise RuntimeError(f"Alembic config not found: {cfg_path}")

    cfg = Config(str(cfg_path))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(cfg, "head")



@contextmanager
def session_scope() -> Session:
    with Session(engine) as session:
        yield session
