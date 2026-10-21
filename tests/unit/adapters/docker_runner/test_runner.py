import logging
from pathlib import Path

import pytest

from hw_checker.adapters.docker_runner.engine import EngineError, Mount
from hw_checker.adapters.docker_runner.runner import DockerCodeRunner
from hw_checker.adapters.docker_runner.runner_config import DockerRunnerConfig
from hw_checker.domain import (
    ExecutionRequest,
    FailureKind,
    HttpMethod,
    HttpProbe,
    StepStatus,
)
from hw_checker.domain.errors import ExecutionEnvironmentError
from tests.unit.adapters.docker_runner.fake_engine import FakeClock, FakeEngine
from tests.unit.adapters.docker_runner.test_engine_and_config import config as base_config

WEB_PROJECT = '<Project Sdk="Microsoft.NET.Sdk.Web"><PropertyGroup /></Project>'
BUILD_ERROR = "Program.cs(3,1): error CS0103: The name 'x' does not exist [/work/src/Api.csproj]"


def probe(method: HttpMethod, path: str, status: int, body: str | None = None) -> HttpProbe:
    return HttpProbe(
        requirement_id="hw01-r1",
        assignment_id="hw01",
        method=method,
        path=path,
        body=body,
        expected_status=status,
    )


PROBES = (
    probe(HttpMethod.GET, "/api/products", 200),
    probe(HttpMethod.GET, "/api/products/999", 404),
    probe(HttpMethod.POST, "/api/products", 201, '{"name":"Desk","price":250}'),
)
STATUSES = {
    ("GET", "/api/products"): 200,
    ("GET", "/api/products/999"): 404,
    ("POST", "/api/products"): 201,
}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "Controllers").mkdir(parents=True)
    (root / "Shop.Api.csproj").write_text(WEB_PROJECT, encoding="utf-8")
    (root / "Controllers" / "ProductsController.cs").write_text("class C {}", encoding="utf-8")
    return root


def config() -> DockerRunnerConfig:
    return base_config(max_archive_bytes=10_000_000, max_file_bytes=1_000_000)


def request(repo: Path, tmp_path: Path, **overrides: object) -> ExecutionRequest:
    values: dict[str, object] = {
        "run_id": "run-1",
        "repo_id": "dana__api",
        "working_copy": repo,
        "probes": PROBES,
        "environment": (("FEATURE_FLAG", "on"),),
        "logs_dir": tmp_path / "logs",
    }
    return ExecutionRequest.model_validate(values | overrides)


def runner(engine: FakeEngine) -> DockerCodeRunner:
    clock = FakeClock()
    return DockerCodeRunner(config(), engine, clock=clock, sleep=clock.sleep)


def test_success_runs_every_step_and_probe(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine(probe_statuses=STATUSES)

    result = runner(engine).execute(request(repo, tmp_path))

    assert result.steps == (StepStatus.SUCCEEDED,) * 3
    assert result.failure_kind is None
    assert [(r.actual_status, r.passed) for r in result.probe_results] == [
        (200, True),
        (404, True),
        (201, True),
    ]
    assert result.logs_path == tmp_path / "logs"


def test_step_order_and_cleanup_after_success(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine()

    runner(engine).execute(request(repo, tmp_path))

    operations = [
        call if call.startswith(("create_container", "start", "put")) else call.split()[0]
        for call in engine.calls
        if call.startswith(("create", "start", "put"))
    ]
    assert operations == [
        "create_volume",
        "create_container copy",
        "put_archive /work",
        "create_container restore",
        "start restore",
        "create_container build",
        "start build",
        "create_network",
        "create_container app",
        "create_container probe",
        "start probe",
        "start app",
    ]
    assert engine.leftovers == 0


def test_container_settings(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine()
    runner_config = config()

    runner(engine).execute(request(repo, tmp_path))

    (restore,) = engine.specs("restore")
    (build,) = engine.specs("build")
    (app,) = engine.specs("app")
    (sidecar,) = engine.specs("probe")
    work = restore.mounts[0].volume
    assert restore.network_mode == "bridge"
    assert restore.mounts == (
        Mount(work, "/work", read_only=False),
        Mount(runner_config.nuget_cache_volume, "/nuget", read_only=False),
    )
    assert restore.command == ("dotnet", "restore", "/work/src/Shop.Api.csproj")
    assert build.network_mode == "none"
    assert build.mounts[1] == Mount(runner_config.nuget_cache_volume, "/nuget", read_only=True)
    assert build.command[:4] == ("dotnet", "publish", "/work/src/Shop.Api.csproj", "--no-restore")
    assert app.network is not None and app.network == sidecar.network
    assert engine.networks == {} and app.network in engine.removed
    assert app.mounts == (Mount(work, "/work", read_only=True),)
    assert app.read_only_root and app.tmpfs == {"/tmp": "size=64m"}
    assert app.command == ("dotnet", "/work/out/Shop.Api.dll")
    assert app.environment == {"FEATURE_FLAG": "on", "ASPNETCORE_HTTP_PORTS": "8080"}
    for spec, limits in (
        (restore, runner_config.restore_limits),
        (build, runner_config.build_limits),
        (app, runner_config.run_limits),
        (sidecar, runner_config.probe_limits),
    ):
        assert spec.user == "1654:1654"
        assert (spec.memory_mb, spec.cpus, spec.pids) == (
            limits.memory_mb,
            limits.cpus,
            limits.pids,
        )
        assert spec.labels["hw-checker-test"] == "1"
        assert spec.labels["hw-checker-test.run"] == "run-1"
        assert spec.labels["hw-checker-test.repo"] == "dana__api"


def test_internal_network_is_used_for_the_run(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine()
    seen: list[bool] = []
    original = engine.create_network

    def record(name: str, labels: object, internal: bool) -> None:
        seen.append(internal)
        original(name, labels, internal)  # type: ignore[arg-type]

    engine.create_network = record  # type: ignore[method-assign]

    runner(engine).execute(request(repo, tmp_path))

    assert seen == [True]


def test_probes_run_in_order_with_body_and_content_type(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine(probe_statuses=STATUSES)

    runner(engine).execute(request(repo, tmp_path))

    probe_commands = engine.exec_commands[1:]
    urls = [command[command.index("--") + 1] for command in probe_commands]
    assert [url.split(":8080", 1)[1] for url in urls] == [
        "/api/products",
        "/api/products/999",
        "/api/products",
    ]
    post = probe_commands[2]
    assert post[post.index("--request") + 1] == "POST"
    assert post[post.index("--header") + 1] == "Content-Type: application/json"
    assert post[post.index("--data-raw") + 1] == '{"name":"Desk","price":250}'
    assert "--header" not in probe_commands[0]


def test_probe_that_gets_no_answer_records_an_error(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine(probe_statuses={("GET", "/api/products"): 500})

    result = runner(engine).execute(request(repo, tmp_path, probes=PROBES[:1]))

    (outcome,) = result.probe_results
    assert (outcome.actual_status, outcome.passed) == (500, False)


def test_restore_failure_skips_build_and_run(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine(
        exit_codes={"restore": 1},
        logs_by_kind={"restore": "error NU1101: Unable to find package X"},
    )

    result = runner(engine).execute(request(repo, tmp_path))

    assert result.steps == (StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.SKIPPED)
    assert result.failure_kind is FailureKind.BUILD_FAILED
    assert engine.specs("build") == []
    assert engine.leftovers == 0


def test_build_failure_has_the_error_in_the_excerpt(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine(exit_codes={"build": 1}, logs_by_kind={"build": BUILD_ERROR})

    result = runner(engine).execute(request(repo, tmp_path))

    assert result.failure_kind is FailureKind.BUILD_FAILED
    assert "error CS0103" in result.logs_excerpt
    assert engine.specs("app") == []
    assert (tmp_path / "logs" / "build.log").read_text(encoding="utf-8") == BUILD_ERROR
    assert engine.leftovers == 0


def test_startup_crash_stops_readiness_early(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine(app="crash", logs_by_kind={"app": "Unhandled exception. boom"})

    result = runner(engine).execute(request(repo, tmp_path))

    assert result.failure_kind is FailureKind.STARTUP_FAILED
    assert result.steps == (StepStatus.SUCCEEDED, StepStatus.SUCCEEDED, StepStatus.FAILED)
    assert len(engine.exec_commands) == 1
    assert result.probe_results == ()
    assert "Unhandled exception" in result.logs_excerpt
    assert engine.leftovers == 0


def test_app_that_never_answers_times_out(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine(app="hang")

    result = runner(engine).execute(request(repo, tmp_path))

    assert result.failure_kind is FailureKind.TIMEOUT
    # About 5 s readiness timeout / 0.1 s poll; float steps may add one more poll.
    assert 50 <= len(engine.exec_commands) <= 52
    assert engine.leftovers == 0


def test_build_that_runs_past_its_timeout_is_stopped(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine(exit_codes={"build": None})

    result = runner(engine).execute(request(repo, tmp_path))

    assert result.failure_kind is FailureKind.TIMEOUT
    assert engine.leftovers == 0


def test_oom_kill_is_an_environment_error(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine(exit_codes={"build": 137}, oom_kinds={"build"})

    result = runner(engine).execute(request(repo, tmp_path))

    assert result.failure_kind is FailureKind.ENVIRONMENT_ERROR


@pytest.mark.parametrize("operation", ["start:restore", "logs:build", "create_network", "exec"])
def test_engine_error_mid_step_is_an_environment_error(
    repo: Path, tmp_path: Path, operation: str
) -> None:
    engine = FakeEngine(fail_on={operation: EngineError("engine hiccup")})

    result = runner(engine).execute(request(repo, tmp_path))

    assert result.failure_kind is FailureKind.ENVIRONMENT_ERROR
    assert engine.leftovers == 0


def test_engine_error_while_copying_the_code_fails_prepare(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine(fail_on={"put_archive": EngineError("disk full")})

    result = runner(engine).execute(request(repo, tmp_path))

    assert result.steps == (StepStatus.FAILED, StepStatus.SKIPPED, StepStatus.SKIPPED)
    assert result.failure_kind is FailureKind.ENVIRONMENT_ERROR
    assert engine.leftovers == 0


def test_unexpected_exception_mid_step_still_cleans_up(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine(fail_on={"logs:build": RuntimeError("bug")})

    with pytest.raises(RuntimeError, match="bug"):
        runner(engine).execute(request(repo, tmp_path))

    assert engine.leftovers == 0


def test_cleanup_error_does_not_hide_the_original_error(
    repo: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    engine = FakeEngine(
        fail_on={"logs:build": RuntimeError("bug"), "remove_volume": EngineError("busy")}
    )

    with caplog.at_level(logging.WARNING), pytest.raises(RuntimeError, match="bug"):
        runner(engine).execute(request(repo, tmp_path))

    assert any("Cleanup could not remove volume" in r.getMessage() for r in caplog.records)


def test_cleanup_error_does_not_hide_a_successful_result(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine(fail_on={"remove_network": EngineError("busy")})

    result = runner(engine).execute(request(repo, tmp_path))

    assert result.failure_kind is None


def test_project_not_found_creates_nothing(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    engine = FakeEngine()

    result = runner(engine).execute(request(empty, tmp_path))

    assert result.failure_kind is FailureKind.PROJECT_NOT_FOUND
    assert result.steps == (StepStatus.FAILED, StepStatus.SKIPPED, StepStatus.SKIPPED)
    assert engine.calls == []
    assert "no .csproj" in (tmp_path / "logs" / "prepare.log").read_text(encoding="utf-8")


def test_oversized_repo_fails_prepare_without_docker(repo: Path, tmp_path: Path) -> None:
    (repo / "big.bin").write_bytes(b"\0" * 2_000_000)
    engine = FakeEngine()

    result = runner(engine).execute(request(repo, tmp_path))

    assert result.failure_kind is FailureKind.ENVIRONMENT_ERROR
    assert result.prepare_status is StepStatus.FAILED
    assert "big.bin is 2,000,000 bytes" in result.logs_excerpt
    assert engine.calls == []


@pytest.mark.parametrize(
    ("engine", "message"),
    [
        (FakeEngine(reachable=False), "Docker is not reachable"),
        (FakeEngine(images_present=False), "Docker images are missing"),
        (FakeEngine(cache_present=False), "package cache volume"),
        (FakeEngine(fail_on={"create_volume": EngineError("down")}), None),
    ],
)
def test_raises_when_no_step_could_start(
    repo: Path, tmp_path: Path, engine: FakeEngine, message: str | None
) -> None:
    if message is None:
        result = runner(engine).execute(request(repo, tmp_path))
        assert result.prepare_status is StepStatus.FAILED
        return
    with pytest.raises(ExecutionEnvironmentError, match=message):
        runner(engine).execute(request(repo, tmp_path))
    assert engine.leftovers == 0


def test_logs_are_written_per_step(repo: Path, tmp_path: Path) -> None:
    runner(FakeEngine()).execute(request(repo, tmp_path))

    written = sorted(path.name for path in (tmp_path / "logs").iterdir())
    assert written == ["build.log", "prepare.log", "restore.log", "run.log"]


def test_environment_values_are_never_logged(
    repo: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    secret = request(repo, tmp_path, environment=(("ConnectionStrings__Db", "pw=hunter2"),))

    with caplog.at_level(logging.DEBUG):
        runner(FakeEngine()).execute(secret)

    assert all("hunter2" not in record.getMessage() for record in caplog.records)
