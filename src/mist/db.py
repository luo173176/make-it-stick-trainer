"""Database plumbing: engine creation, session scopes, default paths."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from .models import Base

ENV_DB = "MIST_DB"
MEMORY_URLS = ("sqlite:///:memory:", "sqlite://:memory:")


def default_db_path() -> Path:
    override = os.environ.get(ENV_DB)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".mist" / "mist.db"


def build_engine(target: str | Path | None = None) -> Engine:
    """Create an SQLite engine from a file path, a ``sqlite:///`` URL, or the default."""
    if target is None:
        target = default_db_path()

    text = str(target)
    if text.startswith("sqlite"):
        url = text
    else:
        path = Path(text).expanduser()
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{path.as_posix()}"

    kwargs: dict = {"future": True, "connect_args": {"check_same_thread": False}}
    if url in MEMORY_URLS:
        kwargs["poolclass"] = StaticPool
    return create_engine(url, **kwargs)


def init_engine(engine: Engine) -> None:
    Base.metadata.create_all(engine)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """Unit of work: commits on success, rolls back on error."""
    session = Session(bind=engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
