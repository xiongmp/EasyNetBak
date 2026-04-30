from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

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
    safe_url = settings.database_url.replace("%", "%%")
    cfg.set_main_option("sqlalchemy.url", safe_url)
    command.upgrade(cfg, "head")



@contextmanager
def session_scope() -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def get_session() -> Iterator[Session]:
    with session_scope() as session:
        yield session
