"""Engine, sessions and error mapping for the SQL storage adapter."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, make_url
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import ConnectionPoolEntry

from hw_checker.domain.errors import StorageError

logger = logging.getLogger(__name__)

SQLITE_BUSY_TIMEOUT_SECONDS = 30
"""How long a SQLite writer waits for another writer's lock before failing."""


def describe_error(error: SQLAlchemyError) -> str:
    """The driver's message without the SQL parameters, which may hold student data."""
    original = getattr(error, "orig", None)
    return f"{type(error).__name__}: {original if original is not None else error}"


def is_sqlite(database_url: str) -> bool:
    return database_url.startswith("sqlite")


def sqlite_file(database_url: str) -> Path | None:
    """The database file of a SQLite URL, or None for in-memory or non-SQLite databases."""
    if not is_sqlite(database_url):
        return None
    try:
        database = make_url(database_url).database
    except ArgumentError as error:
        raise StorageError(f"invalid database URL: {error}") from error
    if not database or database == ":memory:":
        return None
    return Path(database)


def create_storage_engine(database_url: str) -> Engine:
    """Create the engine; for SQLite, enable foreign keys, WAL and a busy timeout per connection.

    Raises:
        StorageError: the URL is invalid.
    """
    try:
        if not is_sqlite(database_url):
            return create_engine(database_url, pool_pre_ping=True)
        engine = create_engine(
            database_url,
            connect_args={"timeout": SQLITE_BUSY_TIMEOUT_SECONDS, "check_same_thread": False},
        )
    except (SQLAlchemyError, ValueError) as error:
        raise StorageError(f"cannot create the database engine: {error}") from error
    event.listen(engine, "connect", _configure_sqlite_connection)
    return engine


def _configure_sqlite_connection(connection: DBAPIConnection, _record: ConnectionPoolEntry) -> None:
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_SECONDS * 1000}")
    finally:
        cursor.close()


class Database:
    """One engine per process and a short session per operation."""

    def __init__(self, database_url: str) -> None:
        self.url = database_url
        self.engine = create_storage_engine(database_url)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self, action: str) -> Iterator[Session]:
        """A session committed on success and rolled back on error.

        Raises:
            StorageError: any database error, described with `action`.
        """
        try:
            with self._sessions.begin() as session:
                yield session
        except SQLAlchemyError as error:
            logger.warning("Storage operation failed: %s (%s)", action, type(error).__name__)
            raise StorageError(f"cannot {action}: {describe_error(error)}") from error

    def dispose(self) -> None:
        """Close every pooled connection."""
        self.engine.dispose()
