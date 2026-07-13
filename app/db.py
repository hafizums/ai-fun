"""SQLAlchemy engine and session helpers for SQLite."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import Settings


class Base(DeclarativeBase):
    """Declarative base for ORM models."""


def _sqlite_connect_args(database_url: str) -> dict[str, Any]:
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


def create_db_engine(settings: Settings) -> Engine:
    """Create a SQLAlchemy engine for the configured database URL."""
    engine = create_engine(
        settings.database_url,
        connect_args=_sqlite_connect_args(settings.database_url),
        future=True,
    )

    if settings.database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection: Any, _connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db(engine: Engine) -> None:
    """Create tables if they do not exist (Gate 1 controlled bootstrap).

    Schema migrations should be introduced before material schema evolution.
    """
    # Import models so metadata is populated.
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_session(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    """Yield a DB session and ensure cleanup."""
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
