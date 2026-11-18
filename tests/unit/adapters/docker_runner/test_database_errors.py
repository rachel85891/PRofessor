"""The run log's database connection failures (`database_unavailable`).

The logs are synthetic; they read like an ASP.NET Core app's output with each client.
"""

import pytest

from hw_checker.adapters.docker_runner.database_errors import (
    MAX_UNREACHABLE_DATABASES,
    unreachable_databases_in_log,
)

EF_CONNECTION = (
    "fail: Microsoft.EntityFrameworkCore.Database.Connection[20004]\n"
    "      An error occurred using the connection to database 'ShopDb' on server "
    "'TEST-PC\\SQLEXPRESS'.\n"
)
SQL_SERVER = (
    "      Microsoft.Data.SqlClient.SqlException (0x80131904): A network-related or "
    "instance-specific error occurred while establishing a connection to SQL Server. The "
    "server was not found or was not accessible.\n"
)
CONNECTION_STRING = "Server=TEST-PC\\SQLEXPRESS;Database=ShopDb;User Id=sa;Password=hunter2"


@pytest.mark.parametrize(
    ("line", "found"),
    [
        (EF_CONNECTION, "database 'ShopDb'"),
        (SQL_SERVER, "SQL Server"),
        (
            "System.Data.SqlClient.SqlException (0x80131904): A network-related or "
            "instance-specific error occurred while establishing a connection to SQL Server.",
            "SQL Server",
        ),
        ("Npgsql.NpgsqlException (0x80004005): Failed to connect to 127.0.0.1:5432", "PostgreSQL"),
        (
            "MySqlConnector.MySqlException (0x80004005): Unable to connect to any of the "
            "specified MySQL hosts.",
            "MySQL",
        ),
        (
            "MySql.Data.MySqlClient.MySqlException (0x80004005): Unable to connect to any of "
            "the specified MySQL hosts.",
            "MySQL",
        ),
        (
            "Microsoft.Data.Sqlite.SqliteException (0x80004005): SQLite Error 14: 'unable to "
            "open database file'.",
            "SQLite",
        ),
    ],
)
def test_each_recognized_connection_failure_is_found(line: str, found: str) -> None:
    assert unreachable_databases_in_log("info: started\n" + line + "\n") == (found,)


def test_a_clean_log_or_another_database_error_gives_nothing() -> None:
    clean = "info: Microsoft.Hosting.Lifetime[14]\n      Now listening on: http://[::]:8080\n"
    bad_query = "Microsoft.Data.SqlClient.SqlException (0x80131904): Invalid column name 'Age'."

    assert unreachable_databases_in_log(clean) == ()
    assert unreachable_databases_in_log(bad_query) == ()


def test_the_failures_are_kept_once_each_in_order_of_appearance() -> None:
    log = EF_CONNECTION + SQL_SERVER + EF_CONNECTION + SQL_SERVER

    assert unreachable_databases_in_log(log) == ("database 'ShopDb'", "SQL Server")


def test_at_most_a_few_failures_are_kept() -> None:
    log = "".join(EF_CONNECTION.replace("ShopDb", f"Db{index}") for index in range(10))

    assert len(unreachable_databases_in_log(log)) == MAX_UNREACHABLE_DATABASES


@pytest.mark.parametrize(
    "log",
    [
        EF_CONNECTION.replace("ShopDb", CONNECTION_STRING),
        SQL_SERVER.replace("SQL Server.", f"SQL Server. {CONNECTION_STRING}"),
        f"Connection string: {CONNECTION_STRING}\n" + EF_CONNECTION + SQL_SERVER,
    ],
)
def test_a_connection_string_is_never_kept(log: str) -> None:
    found = unreachable_databases_in_log(log)

    assert found
    assert not any("hunter2" in item or "Server=" in item or "TEST-PC" in item for item in found)


def test_without_a_readable_name_the_ef_core_failure_is_still_found() -> None:
    log = "fail: Microsoft.EntityFrameworkCore.Database.Connection[20004]\n      boom\n"

    assert unreachable_databases_in_log(log) == ("a database (Entity Framework Core)",)
