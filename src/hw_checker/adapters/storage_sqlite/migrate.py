"""Runs the Alembic migrations programmatically; the only way the schema is created."""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError
from sqlalchemy.exc import SQLAlchemyError

from hw_checker.adapters.storage_sqlite.database import (
    create_storage_engine,
    describe_error,
    sqlite_file,
)
from hw_checker.domain.errors import StorageError

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def migrate(database_url: str) -> None:
    """Bring the database at `database_url` to the latest schema; safe to run repeatedly.

    Creates the SQLite file's parent folder if it is missing.

    Raises:
        StorageError: the folder cannot be created or a migration fails.
    """
    database_file = sqlite_file(database_url)
    if database_file is not None:
        try:
            database_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise StorageError(f"cannot create {database_file.parent}: {error}") from error
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    engine = create_storage_engine(database_url)
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    except SQLAlchemyError as error:
        raise StorageError(f"database migration failed: {describe_error(error)}") from error
    except CommandError as error:
        raise StorageError(f"database migration failed: {error}") from error
    finally:
        engine.dispose()
    logger.info("Database schema is up to date")
