import logging

import pytest

from hw_checker.adapters.docker_runner.engine import EngineError
from hw_checker.adapters.docker_runner.sql_server import (
    SqlServerProvisioner,
    database_name,
    validate_database_name,
)
from hw_checker.domain.errors import ExecutionEnvironmentError
from hw_checker.ports import DatabaseProvisioner
from tests.unit.adapters.docker_runner.fake_engine import FakeClock, FakeEngine
from tests.unit.adapters.docker_runner.test_engine_and_config import sql_server_config

PREFIX = "hw-checker-test"
PASSWORD = "Aa1_secret-password-for-tests"  # pragma: allowlist secret
LOGIN_PASSWORD = "Aa1_login-password-for-tests"  # pragma: allowlist secret
NETWORK = "repo-net-1"


def provisioner(engine: FakeEngine, **overrides: object) -> SqlServerProvisioner:
    clock = FakeClock()
    return SqlServerProvisioner(
        sql_server_config(**overrides),
        engine,
        PREFIX,
        clock=clock,
        sleep=clock.sleep,
        password_factory=lambda: PASSWORD,
        login_password_factory=lambda: LOGIN_PASSWORD,
    )


def started(engine: FakeEngine | None = None) -> tuple[SqlServerProvisioner, FakeEngine]:
    engine = engine or FakeEngine()
    server = provisioner(engine)
    server.start()
    engine.networks[NETWORK] = ({}, True)
    return server, engine


def test_provisioner_satisfies_the_port() -> None:
    port: DatabaseProvisioner = provisioner(FakeEngine())

    assert isinstance(port, SqlServerProvisioner)


# ---------------------------------------------------------------------- start


def test_start_creates_one_labelled_hardened_server_without_a_published_port() -> None:
    engine = FakeEngine()

    provisioner(engine).start()

    (spec,) = engine.specs("database-server")
    assert spec.labels == {PREFIX: "1", f"{PREFIX}.kind": "database-server"}
    assert spec.memory_mb == 2048
    assert spec.environment == {
        "ACCEPT_EULA": "Y",
        "MSSQL_PID": "Developer",
        "MSSQL_MEMORY_LIMIT_MB": "1536",
        "MSSQL_SA_PASSWORD": PASSWORD,
    }
    assert spec.added_capabilities == ("NET_BIND_SERVICE",)
    assert spec.network_aliases == ("sqlserver",)
    assert not hasattr(spec, "ports")
    assert engine.started_kinds() == ["database-server"]


def test_the_server_sits_on_an_internal_labelled_network_of_its_own() -> None:
    engine = FakeEngine()

    provisioner(engine).start()

    ((name, (labels, internal)),) = engine.networks.items()
    assert internal is True
    assert labels[f"{PREFIX}.kind"] == "database-network"
    (spec,) = engine.specs("database-server")
    assert spec.network == name


def test_start_waits_until_the_server_accepts_a_connection() -> None:
    engine = FakeEngine(sql_failures_before_ready=3)

    provisioner(engine).start()

    assert [c[-1] for c in engine.sql_commands] == ["SELECT 1"] * 4


def test_start_twice_starts_one_server() -> None:
    engine = FakeEngine()
    server = provisioner(engine)

    server.start()
    server.start()

    assert len(engine.specs("database-server")) == 1


def test_start_times_out_with_a_plain_reason_and_leaves_nothing() -> None:
    engine = FakeEngine(sql_failures_before_ready=1000)

    with pytest.raises(ExecutionEnvironmentError, match=r"did not accept connections within 10 s"):
        provisioner(engine).start()

    assert engine.leftovers == 0


def test_start_reports_a_server_that_ran_out_of_memory() -> None:
    engine = FakeEngine(server_stopped=True, oom_kinds={"database-server"})

    with pytest.raises(
        ExecutionEnvironmentError, match=r"ran out of memory \(container limit 2048"
    ):
        provisioner(engine).start()

    assert engine.leftovers == 0


def test_start_reports_a_server_that_stopped_with_its_last_log_line() -> None:
    engine = FakeEngine(
        server_stopped=True, logs_by_kind={"database-server": "starting\nOperation not permitted\n"}
    )

    with pytest.raises(ExecutionEnvironmentError, match=r"exit code 1: Operation not permitted"):
        provisioner(engine).start()


def test_start_reports_a_missing_image() -> None:
    engine = FakeEngine(images_present=False)

    with pytest.raises(ExecutionEnvironmentError, match="image is missing"):
        provisioner(engine).start()

    assert engine.created == []


@pytest.mark.parametrize(
    "operation", ["create_network", "create_container:database-server", "start"]
)
def test_start_failures_remove_what_was_created(operation: str) -> None:
    engine = FakeEngine(fail_on={operation: EngineError(f"boom {PASSWORD}")})

    with pytest.raises(ExecutionEnvironmentError, match="cannot start the SQL Server") as raised:
        provisioner(engine).start()

    assert engine.leftovers == 0
    assert PASSWORD not in str(raised.value)


def test_a_failed_start_can_be_tried_again() -> None:
    engine = FakeEngine(sql_failures_before_ready=1000)
    server = provisioner(engine)
    with pytest.raises(ExecutionEnvironmentError):
        server.start()
    engine.sql_failures_before_ready = 0

    server.start()

    assert len(engine.specs("database-server")) == 2


# ---------------------------------------------------------------------- databases


def test_create_database_runs_sqlcmd_without_any_password_in_the_command() -> None:
    server, engine = started()
    engine.sql_commands.clear()

    server.create_database("hw_dana_run1", NETWORK)

    (create, _login) = engine.sql_commands
    (shell, flag, script, sqlcmd, user, sql) = create
    assert (shell, flag) == ("sh", "-c")
    assert sqlcmd == "/opt/mssql-tools18/bin/sqlcmd"
    assert user == "sa"
    assert sql == "CREATE DATABASE [hw_dana_run1]"
    assert "$MSSQL_SA_PASSWORD" in script
    for command in engine.sql_commands:
        assert all(PASSWORD not in part and LOGIN_PASSWORD not in part for part in command)


def test_the_login_is_created_with_its_own_password_through_the_environment() -> None:
    server, engine = started()
    engine.sql_commands.clear()
    engine.sql_environments.clear()

    server.create_database("hw_a", NETWORK)

    assert engine.sql_environments == [{}, {"HW_LOGIN_PASSWORD": LOGIN_PASSWORD}]
    sql = engine.sql_commands[1][-1]
    assert "CREATE LOGIN [hw_a] WITH PASSWORD = N'$(HW_LOGIN_PASSWORD)'" in sql
    assert "DEFAULT_DATABASE = [hw_a]" in sql
    assert "CHECK_POLICY = OFF, CHECK_EXPIRATION = OFF" in sql
    assert "USE [hw_a]; CREATE USER [hw_a] FOR LOGIN [hw_a]" in sql
    assert "ALTER ROLE db_owner ADD MEMBER [hw_a]" in sql
    assert sql.endswith("USE master; DENY VIEW ANY DATABASE TO [hw_a];")
    assert "sysadmin" not in sql and "sp_addsrvrolemember" not in sql


def test_a_failed_login_drops_the_database_and_the_login_without_leaking() -> None:
    engine = FakeEngine(sql_results={"CREATE LOGIN": (1, f"Msg 15118 {LOGIN_PASSWORD}")})
    server, _ = started(engine)
    engine.sql_commands.clear()

    with pytest.raises(
        ExecutionEnvironmentError, match="could not create the database's login"
    ) as raised:
        server.create_database("hw_a", NETWORK)

    statements = [command[-1] for command in engine.sql_commands]
    assert "DROP DATABASE [hw_a]" in statements[-2]
    assert "DROP LOGIN [hw_a]" in statements[-1]
    assert LOGIN_PASSWORD not in str(raised.value)
    assert PASSWORD not in str(raised.value)


def test_a_failed_login_still_raises_when_the_undo_fails_too() -> None:
    engine = FakeEngine(
        sql_results={
            "CREATE LOGIN": (1, "Msg 15118 refused"),
            "DROP DATABASE": (1, "Msg 3702 busy"),
        }
    )
    server, _ = started(engine)

    with pytest.raises(ExecutionEnvironmentError, match="create the database's login"):
        server.create_database("hw_a", NETWORK)

    statements = [command[-1] for command in engine.sql_commands]
    assert "DROP LOGIN [hw_a]" in statements[-1]  # the login is still tried


def test_two_databases_get_two_different_passwords() -> None:
    engine = FakeEngine()
    server = SqlServerProvisioner(
        sql_server_config(), engine, PREFIX, password_factory=lambda: PASSWORD
    )
    server.start()
    engine.networks[NETWORK] = ({}, True)
    engine.sql_environments.clear()

    first = server.create_database("hw_a", NETWORK)
    second = server.create_database("hw_b", NETWORK)

    passwords = [env["HW_LOGIN_PASSWORD"] for env in engine.sql_environments if env]
    assert len(passwords) == 2 and passwords[0] != passwords[1]
    assert passwords[0].startswith("Aa1_") and len(passwords[0]) >= 20
    assert f"Password={passwords[0]};" in first
    assert f"Password={passwords[1]};" in second


def test_create_database_attaches_the_server_to_the_repo_network_under_its_alias() -> None:
    server, engine = started()
    (container,) = engine.containers

    server.create_database("hw_a", NETWORK)

    assert engine.connections[NETWORK] == {container: ("sqlserver",)}


def test_create_database_returns_the_logins_connection_string_not_sas() -> None:
    server, _ = started()

    connection = server.create_database("hw_a", NETWORK)

    assert connection == (
        f"Server=sqlserver;Database=hw_a;User Id=hw_a;Password={LOGIN_PASSWORD};"
        "Encrypt=True;TrustServerCertificate=True"
    )
    assert PASSWORD not in connection
    assert "User Id=sa" not in connection


def test_create_database_refuses_an_unsafe_name_before_any_exec() -> None:
    server, engine = started()
    engine.sql_commands.clear()

    for bad in ("x]; DROP DATABASE master;--", "has space", "", "a-b", "é"):
        with pytest.raises(ValueError, match="letters, digits"):
            server.create_database(bad, NETWORK)

    assert engine.sql_commands == []
    assert engine.connections == {}


def test_a_name_longer_than_the_limit_is_refused() -> None:
    server, _ = started()

    with pytest.raises(ValueError, match="at most 128"):
        server.create_database("a" * 129, NETWORK)


def test_create_database_before_start_is_an_environment_error() -> None:
    with pytest.raises(ExecutionEnvironmentError, match="has not been started"):
        provisioner(FakeEngine()).create_database("hw_a", NETWORK)


def test_create_database_reports_a_refusal_without_the_password() -> None:
    engine = FakeEngine(sql_results={"CREATE DATABASE": (1, f"Msg 1801 already exists {PASSWORD}")})
    server, _ = started(engine)

    with pytest.raises(ExecutionEnvironmentError, match="could not create the database") as raised:
        server.create_database("hw_a", NETWORK)

    assert PASSWORD not in str(raised.value)
    assert "Msg 1801" in str(raised.value)


def test_create_database_reports_an_engine_error_on_attach() -> None:
    engine = FakeEngine(fail_on={"connect_network": EngineError("no such network")})
    server, _ = started(engine)

    with pytest.raises(ExecutionEnvironmentError, match="cannot attach"):
        server.create_database("hw_a", NETWORK)


def test_drop_database_drops_it_then_its_login_and_detaches_the_server() -> None:
    server, engine = started()
    server.create_database("hw_a", NETWORK)
    engine.sql_commands.clear()

    server.drop_database("hw_a", NETWORK)

    (drop_db, drop_login) = [command[-1] for command in engine.sql_commands]
    assert drop_db.startswith("IF DB_ID(N'hw_a') IS NOT NULL BEGIN")
    assert "SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE [hw_a]" in drop_db
    assert "KILL " in drop_login and "login_name = N'hw_a'" in drop_login
    assert "IF EXISTS (SELECT 1 FROM sys.server_principals WHERE name = N'hw_a')" in drop_login
    assert drop_login.endswith("DROP LOGIN [hw_a];")
    assert engine.connections[NETWORK] == {}


def test_dropping_a_missing_database_is_not_an_error() -> None:
    server, engine = started()

    server.drop_database("hw_never_created", NETWORK)

    assert engine.connections.get(NETWORK, {}) == {}


def test_drop_refuses_an_unsafe_name_before_any_exec() -> None:
    server, engine = started()
    engine.sql_commands.clear()

    with pytest.raises(ValueError, match="letters, digits"):
        server.drop_database("x'; --", NETWORK)

    assert engine.sql_commands == []


def test_the_login_is_still_dropped_when_the_database_drop_fails() -> None:
    engine = FakeEngine(sql_results={"DROP DATABASE": (1, "Msg 3702 cannot drop")})
    server, _ = started(engine)
    server.create_database("hw_a", NETWORK)
    engine.sql_commands.clear()

    with pytest.raises(ExecutionEnvironmentError, match="could not drop the database"):
        server.drop_database("hw_a", NETWORK)

    assert "DROP LOGIN [hw_a]" in engine.sql_commands[-1][-1]
    assert engine.connections[NETWORK] == {}


def test_when_both_drops_fail_the_first_error_is_raised_and_both_are_tried(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = FakeEngine(
        sql_results={
            "DROP DATABASE": (1, "Msg 3702 cannot drop"),
            "DROP LOGIN": (1, "Msg 15434 logged in"),
        }
    )
    server, _ = started(engine)
    server.create_database("hw_a", NETWORK)
    engine.sql_commands.clear()

    with pytest.raises(ExecutionEnvironmentError, match="could not drop the database: ") as raised:
        server.drop_database("hw_a", NETWORK)

    assert "Msg 3702" in str(raised.value)
    assert len(engine.sql_commands) == 2
    assert "DROP LOGIN [hw_a]" in engine.sql_commands[-1][-1]
    assert engine.connections[NETWORK] == {}
    assert "Also failed" in caplog.text


def test_the_server_is_detached_when_the_login_drop_fails() -> None:
    engine = FakeEngine(sql_results={"DROP LOGIN": (1, "Msg 15434 logged in")})
    server, _ = started(engine)
    server.create_database("hw_a", NETWORK)

    with pytest.raises(ExecutionEnvironmentError, match="could not drop the database's login"):
        server.drop_database("hw_a", NETWORK)

    assert engine.connections[NETWORK] == {}


def test_the_server_is_detached_even_when_the_drop_fails() -> None:
    engine = FakeEngine(sql_results={"DROP": (1, "Msg 3702 cannot drop")})
    server, _ = started(engine)
    server.create_database("hw_a", NETWORK)

    with pytest.raises(ExecutionEnvironmentError, match="could not drop the database"):
        server.drop_database("hw_a", NETWORK)

    assert engine.connections[NETWORK] == {}


def test_drop_after_stop_does_nothing() -> None:
    server, engine = started()
    server.stop()
    calls = len(engine.calls)

    server.drop_database("hw_a", NETWORK)

    assert len(engine.calls) == calls


# ---------------------------------------------------------------------- stop


def test_stop_removes_the_server_and_its_network() -> None:
    server, engine = started()
    engine.networks.pop(NETWORK)

    server.stop()

    assert engine.leftovers == 0


def test_stop_never_raises_and_is_safe_twice() -> None:
    server, engine = started()
    engine.fail_on["remove_container"] = EngineError("daemon gone")
    engine.fail_on["remove_network"] = EngineError("daemon gone")

    server.stop()
    server.stop()


def test_stop_forgets_the_password() -> None:
    server, _ = started()
    server.stop()

    with pytest.raises(ExecutionEnvironmentError, match="has not been started"):
        server.create_database("hw_a", NETWORK)


# ---------------------------------------------------------------------- secrets


def test_nothing_logged_holds_a_password_or_connection_string(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = FakeEngine(sql_results={"DROP DATABASE": (1, f"failed {PASSWORD} {LOGIN_PASSWORD}")})
    with caplog.at_level(logging.DEBUG):
        server, _ = started(engine)
        connection = server.create_database("hw_a", NETWORK)
        with pytest.raises(ExecutionEnvironmentError) as raised:
            server.drop_database("hw_a", NETWORK)
        server.stop()

    text = "\n".join(record.getMessage() for record in caplog.records)
    for secret in (PASSWORD, LOGIN_PASSWORD, connection):
        assert secret not in text
    assert PASSWORD not in str(raised.value)
    assert LOGIN_PASSWORD not in str(raised.value)


def test_nothing_logged_holds_the_login_password_when_its_creation_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = FakeEngine(sql_results={"CREATE LOGIN": (1, f"Msg 15118 {LOGIN_PASSWORD}")})
    with caplog.at_level(logging.DEBUG):
        server, _ = started(engine)
        with pytest.raises(ExecutionEnvironmentError):
            server.create_database("hw_a", NETWORK)

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert LOGIN_PASSWORD not in text
    assert "Msg 15118" in text


# ---------------------------------------------------------------------- names


@pytest.mark.parametrize(
    ("repo", "run", "expected"),
    [
        ("dana-api", "20261120-1", "hw_dana_api_20261120_1"),
        ("repo", "run", "hw_repo_run"),
        ("a.b", "c d", "hw_a_b_c_d"),
    ],
)
def test_database_name_keeps_only_safe_characters(repo: str, run: str, expected: str) -> None:
    assert database_name(repo, run, 128) == expected


def test_a_long_database_name_is_cut_to_the_limit_and_stays_distinct() -> None:
    first = database_name("r" * 200, "run-1", 128)
    second = database_name("r" * 200, "run-2", 128)

    assert len(first) == len(second) == 128
    assert first != second
    validate_database_name(first, 128)


def test_the_same_long_name_always_gets_the_same_result() -> None:
    assert database_name("r" * 200, "x", 64) == database_name("r" * 200, "x", 64)


# ---------------------------------------------------------------------- redaction


def test_redact_removes_the_server_and_login_passwords_wherever_they_are() -> None:
    server, _ = started()
    connection = server.create_database("hw_a", NETWORK)

    text = f"first {connection}\nsecond {PASSWORD} third {LOGIN_PASSWORD}{LOGIN_PASSWORD}"
    redacted = server.redact(text)

    assert PASSWORD not in redacted and LOGIN_PASSWORD not in redacted
    assert redacted.count("***") == 4
    assert server.redact("nothing secret here") == "nothing secret here"
