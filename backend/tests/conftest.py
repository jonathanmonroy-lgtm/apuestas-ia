import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

os.environ.setdefault("ENVIRONMENT", "test")

from app.db.session import make_engine

BACKEND_DIR = Path(__file__).resolve().parents[1]


def alembic_config(url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    # TEST_DATABASE_URL lets CI run the same suite against Postgres.
    return os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture
def migrated_engine(db_url: str) -> Iterator[Engine]:
    """Schema built by the real migrations (not create_all), so tests cover them."""
    cfg = alembic_config(db_url)
    command.upgrade(cfg, "head")
    engine = make_engine(db_url)
    yield engine
    engine.dispose()
    command.downgrade(cfg, "base")


@pytest.fixture
def db(migrated_engine: Engine) -> Iterator[Session]:
    with sessionmaker(bind=migrated_engine, expire_on_commit=False)() as session:
        yield session
