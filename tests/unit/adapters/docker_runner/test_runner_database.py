"""The runner with a shared SQL Server: one server per round, one database per repo's run."""

import logging
from pathlib import Path

import pytest

from hw_checker.adapters.docker_runner.engine import ContainerSpec, EngineError
from hw_checker.adapters.docker_runner.runner import DockerCodeRunner
from hw_checker.adapters.docker_runner.sql_server import SqlServerProvisioner
from hw_checker.domain import ExecutionRequest
from hw_checker.domain.errors import ExecutionEnvironmentError
from tests.unit.adapters.docker_runner.fake_engine import FakeClock, FakeEngine
from tests.unit.adapters.docker_runner.test_engine_and_config import config as base_config
from tests.unit.adapters.docker_runner.test_engine_and_config import sql_server_config
from tests.unit.adapters.docker_runner.test_runner import WEB_PROJECT

PREFIX = "hw-checker-test"
PASSWORD = "Aa1_secret-password-for-tests"  # pragma: allowlist secret


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "Controllers").mkdir(parents=True)
    (root / "Shop.Api.csproj").write_text(WEB_PROJECT, encoding="utf-8")
    (root / "Controllers" / "ProductsController.cs").write_text("class C {}", encoding="utf-8")
    return root


def request(repo: Path, tmp_path: Path) -> ExecutionRequest:
    return ExecutionRequest(
        run_id="run-1", repo_id="dana-api", working_copy=repo, logs_dir=tmp_path / "logs"
    )


def runner(engine: FakeEngine, databases: bool = True) -> DockerCodeRunner:
    clock = FakeClock()
    config = base_config(
        max_archive_bytes=10_000_000,
        max_file_bytes=1_000_000,
        database=sql_server_config() if databases else None,
    )
    provisioner = (
        SqlServerProvisioner(
            sql_server_config(),
            engine,
            PREFIX,
            clock=clock,
            sleep=clock.sleep,
            password_factory=lambda: PASSWORD,
        )
        if databases
        else None
    )
    return DockerCodeRunner(config, engine, clock=clock, sleep=clock.sleep, database=provisioner)


def only_the_server_is_left(engine: FakeEngine) -> bool:
    kinds = [container.kind for container in engine.containers.values()]
    return kinds == ["database-server"] and len(engine.networks) == 1


def sql_statements(engine: FakeEngine) -> list[str]:
    return [command[-1] for command in engine.sql_commands]


def test_a_provisioner_needs_the_sql_server_settings() -> None:
    engine = FakeEngine()
    provisioner = SqlServerProvisioner(sql_server_config(), engine, PREFIX)

    with pytest.raises(ValueError, match="needs the SQL Server settings"):
        DockerCodeRunner(base_config(), engine, database=provisioner)


# ---------------------------------------------------------------------- off


def test_with_databases_off_nothing_database_related_happens(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine()
    code_runner = runner(engine, databases=False)

    code_runner.check_environment()
    code_runner.start_round()
    code_runner.execute(request(repo, tmp_path))
    code_runner.finish_round()

    assert engine.sql_commands == []
    assert engine.specs("database-server") == []
    assert engine.connections == {}
    assert not any("connect_network" in call for call in engine.calls)


def test_the_images_do_not_include_the_server_when_databases_are_off() -> None:
    assert not any("mssql" in image for image in base_config().images)


# ---------------------------------------------------------------------- on


def test_start_round_starts_the_server_once_and_finish_round_removes_it() -> None:
    engine = FakeEngine()
    code_runner = runner(engine)

    code_runner.start_round()

    assert engine.started_kinds() == ["database-server"]
    code_runner.finish_round()
    assert engine.leftovers == 0


def test_each_run_creates_its_own_database_on_its_network_and_drops_it(
    repo: Path, tmp_path: Path
) -> None:
    engine = FakeEngine()
    code_runner = runner(engine)
    code_runner.start_round()
    attached_during_the_run: list[dict[str, tuple[str, ...]]] = []
    original = engine.start

    def watch(container: str) -> None:
        original(container)
        if engine.containers[container].kind == "app":
            attached_during_the_run.extend(dict(v) for v in engine.connections.values())

    engine.start = watch  # type: ignore[method-assign]

    code_runner.execute(request(repo, tmp_path))

    assert sql_statements(engine)[-4] == "CREATE DATABASE [hw_dana_api_run_1]"
    assert "DROP DATABASE [hw_dana_api_run_1]" in sql_statements(engine)[-2]
    (attachment,) = attached_during_the_run
    assert list(attachment.values()) == [("sqlserver",)]
    assert engine.connections and all(not v for v in engine.connections.values())
    assert only_the_server_is_left(engine)


def test_the_database_exists_before_the_app_starts(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine()
    code_runner = runner(engine)
    code_runner.start_round()
    engine.calls.clear()

    code_runner.execute(request(repo, tmp_path))

    started_app = engine.calls.index("start app")
    assert any(call.startswith("connect_network") for call in engine.calls[:started_app])
    assert not any("DROP" in statement for statement in sql_statements(engine)[:-2])


def test_the_server_is_not_given_to_the_app_yet(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine()
    code_runner = runner(engine)
    code_runner.start_round()

    code_runner.execute(request(repo, tmp_path))

    (app,) = engine.specs("app")
    assert not any("Connection" in key for key in app.environment)
    assert all(PASSWORD not in value for value in app.environment.values())


def test_every_network_stays_internal_so_nothing_reaches_the_host_or_the_internet(
    repo: Path, tmp_path: Path
) -> None:
    engine = FakeEngine()
    code_runner = runner(engine)
    code_runner.start_round()

    code_runner.execute(request(repo, tmp_path))

    assert len(engine.network_internal) == 2  # the server's own and the repo's
    assert all(engine.network_internal.values())


# ---------------------------------------------------------------------- failures


def test_start_round_failure_is_an_environment_error_and_leaves_nothing() -> None:
    engine = FakeEngine(sql_failures_before_ready=1000)

    with pytest.raises(ExecutionEnvironmentError, match="did not accept connections"):
        runner(engine).start_round()

    assert engine.leftovers == 0


def test_a_refused_create_database_is_an_environment_error_and_everything_is_removed(
    repo: Path, tmp_path: Path
) -> None:
    engine = FakeEngine(sql_results={"CREATE DATABASE": (1, "Msg 1801 refused")})
    code_runner = runner(engine)
    code_runner.start_round()

    with pytest.raises(ExecutionEnvironmentError, match="could not create the database"):
        code_runner.execute(request(repo, tmp_path))

    assert only_the_server_is_left(engine)
    assert not any(attached for attached in engine.connections.values())
    assert engine.specs("app") == []
    code_runner.finish_round()
    assert engine.leftovers == 0


def test_a_failed_attach_is_an_environment_error_and_everything_is_removed(
    repo: Path, tmp_path: Path
) -> None:
    engine = FakeEngine(fail_on={"connect_network": EngineError("endpoint failed")})
    code_runner = runner(engine)
    code_runner.start_round()

    with pytest.raises(ExecutionEnvironmentError, match="cannot attach"):
        code_runner.execute(request(repo, tmp_path))

    assert only_the_server_is_left(engine)


@pytest.mark.parametrize("operation", ["create_container:app", "start:app", "logs:app"])
def test_the_database_is_dropped_after_an_engine_failure_in_the_run(
    repo: Path, tmp_path: Path, operation: str
) -> None:
    engine = FakeEngine()
    code_runner = runner(engine)
    code_runner.start_round()
    engine.fail_on[operation] = EngineError("daemon hiccup")

    code_runner.execute(request(repo, tmp_path))

    assert "DROP DATABASE [hw_dana_api_run_1]" in sql_statements(engine)[-2]
    assert only_the_server_is_left(engine)


def test_a_database_that_cannot_be_dropped_does_not_block_the_cleanup(
    repo: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    engine = FakeEngine(sql_results={"DROP DATABASE": (1, f"Msg 3702 {PASSWORD}")})
    code_runner = runner(engine)
    code_runner.start_round()

    with caplog.at_level(logging.DEBUG):
        code_runner.execute(request(repo, tmp_path))

    assert only_the_server_is_left(engine)
    assert all(not attached for attached in engine.connections.values())
    assert PASSWORD not in "\n".join(record.getMessage() for record in caplog.records)


def test_execute_without_a_started_server_is_an_environment_error(
    repo: Path, tmp_path: Path
) -> None:
    engine = FakeEngine()

    with pytest.raises(ExecutionEnvironmentError, match="has not been started"):
        runner(engine).execute(request(repo, tmp_path))

    assert engine.leftovers == 0


def test_finish_round_never_raises_when_removal_fails() -> None:
    engine = FakeEngine()
    code_runner = runner(engine)
    code_runner.start_round()
    engine.fail_on["remove_container"] = EngineError("daemon gone")

    code_runner.finish_round()


# ---------------------------------------------------------------------- sweep and secrets


def test_the_sweep_removes_a_server_left_by_a_crashed_round() -> None:
    engine = FakeEngine()
    crashed = runner(engine)
    crashed.start_round()

    removed = runner(FakeEngine(), databases=True).sweep_leftovers()
    assert removed == 0
    removed = runner(engine).sweep_leftovers()

    assert removed == 2  # the server container and its network
    assert engine.leftovers == 0


def test_the_sweep_removes_a_server_still_attached_to_a_repo_network(
    repo: Path, tmp_path: Path
) -> None:
    engine = FakeEngine()
    code_runner = runner(engine)
    code_runner.start_round()
    engine.networks["repo-net"] = ({PREFIX: "1"}, True)
    (container,) = engine.containers
    engine.connect_network(container, "repo-net", ("sqlserver",))

    removed = runner(engine).sweep_leftovers()

    assert removed == 3
    assert engine.leftovers == 0


def test_the_database_sweep_removes_only_the_sql_servers_own_resources() -> None:
    engine = FakeEngine()
    runner(engine).start_round()
    engine.networks["repo-net"] = ({PREFIX: "1", f"{PREFIX}.kind": "network"}, True)
    engine.volumes["work"] = {PREFIX: "1", f"{PREFIX}.kind": "work"}
    other = ContainerSpec(
        name="other-app",
        image="x",
        command=(),
        labels={PREFIX: "1", f"{PREFIX}.kind": "app"},
        user="1",
        memory_mb=1,
        cpus=1,
        pids=1,
    )
    engine.create_container(other)

    removed = runner(engine).sweep_database_leftovers()

    assert removed == 2  # the server container and its network
    assert [c.kind for c in engine.containers.values()] == ["app"]
    assert list(engine.networks) == ["repo-net"]
    assert list(engine.volumes) == ["work"]


def test_the_database_sweep_with_nothing_to_remove_removes_nothing() -> None:
    assert runner(FakeEngine()).sweep_database_leftovers() == 0


def test_the_database_sweep_reports_an_unreachable_docker() -> None:
    engine = FakeEngine(fail_on={"list_labelled": EngineError("pipe gone")})

    with pytest.raises(ExecutionEnvironmentError, match="cannot list leftovers"):
        runner(engine).sweep_database_leftovers()


def test_no_log_or_exception_holds_the_password_or_connection_string(
    repo: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    engine = FakeEngine(sql_results={"CREATE DATABASE": (1, f"Msg 1801 {PASSWORD}")})
    code_runner = runner(engine)
    with caplog.at_level(logging.DEBUG):
        code_runner.start_round()
        with pytest.raises(ExecutionEnvironmentError) as raised:
            code_runner.execute(request(repo, tmp_path))
        code_runner.finish_round()

    assert PASSWORD not in str(raised.value)
    assert PASSWORD not in "\n".join(record.getMessage() for record in caplog.records)
    logged = "".join(path.read_text(encoding="utf-8") for path in (tmp_path / "logs").glob("*"))
    assert PASSWORD not in logged
