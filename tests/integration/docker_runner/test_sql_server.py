"""The shared SQL Server against real Docker: databases, network isolation and teardown.

Pulls and starts the pinned SQL Server image (about 2 GB of memory, 15-40 s to start).
"""

import time
import uuid
from collections.abc import Iterator

import pytest

from hw_checker.adapters.docker_runner.engine import ContainerSpec, SdkDockerEngine
from hw_checker.adapters.docker_runner.runner_config import SqlServerConfig
from hw_checker.adapters.docker_runner.sql_server import SqlServerProvisioner
from hw_checker.bootstrap import runner_config
from hw_checker.config.settings import DatabaseSettings, RunnerSettings
from tests.integration.docker_runner.conftest import PREFIX, leftovers

pytestmark = pytest.mark.integration

SA_PASSWORD = "Aa1_integration-test-only-0123456789"  # pragma: allowlist secret
CLIENT_TIMEOUT_SECONDS = 60
CLIENT_LOGIN_TIMEOUT_SECONDS = "5"


@pytest.fixture(scope="module")
def sql_config() -> SqlServerConfig:
    settings = RunnerSettings(database=DatabaseSettings(enabled=True))
    config = runner_config(settings).database
    assert config is not None
    return config


@pytest.fixture
def server(engine: SdkDockerEngine, sql_config: SqlServerConfig) -> Iterator[SqlServerProvisioner]:
    for resource in leftovers(engine):
        remove = {"container": engine.remove_container, "network": engine.remove_network}
        remove.get(resource.kind, engine.remove_volume)(resource.name)
    if not engine.image_exists(sql_config.image):
        engine.pull(sql_config.image)
    provisioner = SqlServerProvisioner(
        sql_config, engine, PREFIX, password_factory=lambda: SA_PASSWORD
    )
    provisioner.start()
    yield provisioner
    provisioner.stop()


def repo_network(engine: SdkDockerEngine) -> str:
    name = f"{PREFIX}-repo-net-{uuid.uuid4().hex[:8]}"
    engine.create_network(name, {PREFIX: "1", f"{PREFIX}.kind": "network"}, internal=True)
    return name


def credentials(connection_string: str) -> tuple[str, str]:
    """(`User Id`, `Password`) of an app's connection string."""
    parts = dict(item.split("=", 1) for item in connection_string.split(";"))
    return parts["User Id"], parts["Password"]


def connect_from(
    engine: SdkDockerEngine,
    config: SqlServerConfig,
    network: str,
    database: str,
    login: tuple[str, str],
    query: str = "SELECT DB_NAME()",
) -> tuple[int, str]:
    """Run `query` against `database` as `login` from a throwaway container on `network`."""
    container = engine.create_container(
        ContainerSpec(
            name=f"{PREFIX}-client-{uuid.uuid4().hex[:8]}",
            image=config.image,
            command=(
                "-C",
                "-S",
                config.alias,
                "-U",
                login[0],
                "-P",
                login[1],
                "-d",
                database,
                "-b",
                "-l",
                CLIENT_LOGIN_TIMEOUT_SECONDS,
                "-Q",
                query,
            ),
            entrypoint=(config.sqlcmd_path,),
            labels={PREFIX: "1", f"{PREFIX}.kind": "client"},
            user=config.container_user,
            memory_mb=128,
            cpus=0.5,
            pids=64,
            network=network,
        )
    )
    try:
        engine.start(container)
        deadline = time.monotonic() + CLIENT_TIMEOUT_SECONDS
        state = engine.state(container)
        while state.running and time.monotonic() < deadline:
            time.sleep(0.5)
            state = engine.state(container)
        return state.exit_code if state.exit_code is not None else -1, engine.logs(container)
    finally:
        engine.remove_container(container)


def test_the_server_has_no_published_port(
    engine: SdkDockerEngine, server: SqlServerProvisioner
) -> None:
    import docker

    (server_container,) = [r for r in engine.list_labelled(PREFIX) if r.kind == "container"]
    attributes = docker.from_env().containers.get(server_container.name).attrs

    assert not attributes["NetworkSettings"]["Ports"]
    assert not attributes["HostConfig"]["PortBindings"]
    assert attributes["HostConfig"]["Memory"] == 2048 * 1024 * 1024


def test_a_database_is_reachable_only_on_the_network_the_server_was_attached_to(
    engine: SdkDockerEngine, server: SqlServerProvisioner, sql_config: SqlServerConfig
) -> None:
    network_a, network_b, network_other = (repo_network(engine) for _ in range(3))
    name_a, name_b = "hw_it_a_run1", "hw_it_b_run1"
    try:
        connection_a = server.create_database(name_a, network_a)
        login_a = credentials(connection_a)
        login_b = credentials(server.create_database(name_b, network_b))
        assert f"Database={name_a}" in connection_a
        assert login_a[0] == name_a  # not sa

        code_a, output_a = connect_from(engine, sql_config, network_a, name_a, login_a)
        code_b, output_b = connect_from(engine, sql_config, network_b, name_b, login_b)
        code_other, _ = connect_from(engine, sql_config, network_other, name_a, login_a)

        assert code_a == 0 and name_a in output_a
        assert code_b == 0 and name_b in output_b
        assert code_other != 0  # the server is not attached to this repo's network

        server.drop_database(name_a, network_a)
        code_gone, _ = connect_from(engine, sql_config, network_b, name_a, login_a)
        assert code_gone != 0  # the database is gone
        server.drop_database(name_a, network_a)  # dropping a missing database is fine
    finally:
        server.drop_database(name_a, network_a)
        server.drop_database(name_b, network_b)
        for network in (network_a, network_b, network_other):
            engine.remove_network(network)


def test_stop_removes_the_server_and_its_network(
    engine: SdkDockerEngine, server: SqlServerProvisioner
) -> None:
    assert any(r.kind == "container" for r in leftovers(engine))

    server.stop()

    assert leftovers(engine) == []


def sa_query(engine: SdkDockerEngine, config: SqlServerConfig, query: str) -> str:
    """Run `query` as `sa` inside the server container; returns the bare output."""
    (server_container,) = [
        r.name
        for r in engine.list_labelled(PREFIX)
        if r.kind == "container" and r.labels.get(f"{PREFIX}.kind") == "database-server"
    ]
    result = engine.exec(
        server_container,
        (
            config.sqlcmd_path,
            *("-C", "-S", "localhost", "-U", "sa", "-P", SA_PASSWORD),
            *("-b", "-h", "-1", "-W", "-Q", query),
        ),
    )
    assert result.exit_code == 0, result.output
    return result.output.strip()


def test_a_repos_login_reaches_only_its_own_database(
    engine: SdkDockerEngine, server: SqlServerProvisioner, sql_config: SqlServerConfig
) -> None:
    network_a, network_b = repo_network(engine), repo_network(engine)
    name_a, name_b = "hw_it_login_a", "hw_it_login_b"
    try:
        login_a = credentials(server.create_database(name_a, network_a))
        login_b = credentials(server.create_database(name_b, network_b))
        assert "sa" not in (login_a[0], login_b[0])

        def as_a(query: str, database: str = name_a) -> tuple[int, str]:
            return connect_from(engine, sql_config, network_a, database, login_a, query)

        # A's own database: full control of it.
        code, output = as_a("CREATE TABLE t (i int); INSERT t VALUES (1); SELECT DB_NAME()")
        assert code == 0 and name_a in output
        # B's database is closed to A, even with B's name known and from A's own network.
        assert as_a("SELECT 1", name_b)[0] != 0
        assert as_a(f"USE [{name_b}]; SELECT 1")[0] != 0
        assert as_a(f"SELECT * FROM [{name_b}].sys.tables")[0] != 0
        # B's name is not even listed.
        code, output = as_a(f"SELECT name FROM sys.databases WHERE name = N'{name_b}'")
        assert code == 0 and name_b not in output
        # No server-level power.
        assert as_a(f"DROP DATABASE [{name_b}]")[0] != 0
        assert as_a("CREATE LOGIN intruder WITH PASSWORD = N'Aa1_intruder-0123456789'")[0] != 0
        assert as_a("EXEC xp_cmdshell 'whoami'")[0] != 0
        assert as_a("EXEC sp_configure 'show advanced options', 1; RECONFIGURE")[0] != 0
        # B is untouched and still works for its own login.
        code, output = connect_from(engine, sql_config, network_b, name_b, login_b)
        assert code == 0 and name_b in output
        assert (
            sa_query(
                engine,
                sql_config,
                "SELECT COUNT(*) FROM sys.server_principals WHERE name = N'intruder'",
            )
            == "0"
        )
    finally:
        server.drop_database(name_a, network_a)
        server.drop_database(name_b, network_b)
        for network in (network_a, network_b):
            engine.remove_network(network)


def test_teardown_removes_the_login_with_the_database(
    engine: SdkDockerEngine, server: SqlServerProvisioner, sql_config: SqlServerConfig
) -> None:
    network = repo_network(engine)
    name = "hw_it_login_gone"
    count_logins = f"SELECT COUNT(*) FROM sys.server_principals WHERE name = N'{name}'"
    try:
        login = credentials(server.create_database(name, network))
        assert sa_query(engine, sql_config, count_logins) == "1"
        code, _ = connect_from(engine, sql_config, network, name, login, "SELECT 1")
        assert code == 0

        server.drop_database(name, network)

        assert sa_query(engine, sql_config, count_logins) == "0"
        assert sa_query(engine, sql_config, f"SELECT DB_ID(N'{name}')") == "NULL"
        server.drop_database(name, network)  # a missing database and login are fine
    finally:
        engine.remove_network(network)
