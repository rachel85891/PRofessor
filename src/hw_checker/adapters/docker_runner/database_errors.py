"""Finds the app's failures to connect to its database in the run log.

A student's connection string often names a database server on their own computer
(`Server=MY-PC\\SQLEXPRESS`, `localhost`), which does not exist in the container, so every
request that touches the database answers 500. The run log then holds the database
client's connection error. Only a short name of what could not be reached is kept, never
the message around it: a connection string, with its password, may be part of it.
"""

import re
from dataclasses import dataclass

MAX_UNREACHABLE_DATABASES = 4
"""The most distinct entries kept; one is usually enough to explain the failures."""
MAX_DATABASE_NAME_CHARS = 128
"""The longest database name kept (SQL Server's own limit); a longer one is left out."""


@dataclass(frozen=True)
class _ConnectionError:
    """One kind of connection failure: how its log line reads, and what it is recorded as."""

    pattern: re.Pattern[str]
    label: str


_DATABASE_NAME = rf"(?P<name>[\w.\- ]{{1,{MAX_DATABASE_NAME_CHARS}}})"
"""A database name as EF Core prints it; anything else (an `=` or `;` of a connection
string) is not kept."""

CONNECTION_ERRORS = (
    # EF Core's event 20004 (`ConnectionError`), logged when opening or using the connection
    # fails, whatever the provider. Its next line names the database and the server; only
    # the database is kept, since the server is usually the student's own computer.
    _ConnectionError(
        re.compile(
            r"Microsoft\.EntityFrameworkCore\.Database\.Connection\[20004\]"
            r"(?:\s*\n[^\n]*?connection to database '" + _DATABASE_NAME + r"')?"
        ),
        "a database (Entity Framework Core)",
    ),
    # SQL Server's client (both the current and the old .NET Framework one) when the server
    # cannot be found or reached; other `SqlException`s, such as a bad query, are left out.
    _ConnectionError(
        re.compile(
            r"(?:Microsoft|System)\.Data\.SqlClient\.SqlException\b[^\n]*"
            r"error occurred while establishing a connection to SQL Server"
        ),
        "SQL Server",
    ),
    # PostgreSQL's client when no server answers at the host and port.
    _ConnectionError(
        re.compile(r"Npgsql\.NpgsqlException\b[^\n]*Failed to connect to"), "PostgreSQL"
    ),
    # MySQL's two common clients (MySqlConnector and Oracle's MySql.Data).
    _ConnectionError(
        re.compile(
            r"(?:MySqlConnector|MySql\.Data\.MySqlClient)\.MySqlException\b[^\n]*"
            r"Unable to connect to any of the specified MySQL hosts"
        ),
        "MySQL",
    ),
    # SQLite when its file cannot be opened (a folder that does not exist in the container).
    _ConnectionError(
        re.compile(r"Microsoft\.Data\.Sqlite\.SqliteException\b[^\n]*unable to open database file"),
        "SQLite",
    ),
)
"""The database connection failures recognized in a run log, with what each is recorded as."""


def unreachable_databases_in_log(log: str) -> tuple[str, ...]:
    """What the app could not connect to, by the `CONNECTION_ERRORS` lines of `log`: the
    distinct labels (EF Core's with the database's name, when it gives one), in order of
    appearance."""
    found: list[tuple[int, str]] = []
    for error in CONNECTION_ERRORS:
        for match in error.pattern.finditer(log):
            name = match.groupdict().get("name")
            found.append((match.start(), f"database '{name}'" if name else error.label))
    labels = dict.fromkeys(label for _, label in sorted(found))
    return tuple(labels)[:MAX_UNREACHABLE_DATABASES]
