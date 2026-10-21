"""Alembic environment: runs migrations on the connection passed in by `migrate()`."""

from alembic import context
from sqlalchemy import Connection

from hw_checker.adapters.storage_sqlite.schema import Base

target_metadata = Base.metadata


def run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("run migrations through hw_checker.adapters.storage_sqlite.migrate()")
run_migrations(connection)
