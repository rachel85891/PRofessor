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


def connect_from(
    engine: SdkDockerEngine, config: SqlServerConfig, network: str, database: str
) -> tuple[int, str]:
    """Run `SELECT DB_NAME()` against `database` from a throwaway container on `network`."""
    container = engine.create_container(
        ContainerSpec(
            name=f"{PREFIX}-client-{uuid.uuid4().hex[:8]}",
            image=config.image,
            command=(
                "-C",
                "-S",
                config.alias,
                "-U",
                config.user,
                "-P",
                SA_PASSWORD,
                "-d",
                database,
                "-b",
                "-l",
                CLIENT_LOGIN_TIMEOUT_SECONDS,
                "-Q",
                "SELECT DB_NAME()",
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
        server.create_database(name_b, network_b)
        assert f"Database={name_a}" in connection_a

        code_a, output_a = connect_from(engine, sql_config, network_a, name_a)
        code_b, output_b = connect_from(engine, sql_config, network_b, name_b)
        code_other, _ = connect_from(engine, sql_config, network_other, name_a)

        assert code_a == 0 and name_a in output_a
        assert code_b == 0 and name_b in output_b
        assert code_other != 0  # the server is not attached to this repo's network

        server.drop_database(name_a, network_a)
        code_gone, _ = connect_from(engine, sql_config, network_b, name_a)
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
