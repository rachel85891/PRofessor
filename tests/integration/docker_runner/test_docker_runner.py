"""DockerCodeRunner against real Docker Desktop, with copies of the sample Web API."""

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

import pytest

from hw_checker.adapters.docker_runner.engine import ContainerSpec, SdkDockerEngine
from hw_checker.adapters.docker_runner.runner import DockerCodeRunner
from hw_checker.domain import ExecutionResult, FailureKind, StepStatus
from tests.integration.docker_runner.conftest import (
    CACHE_VOLUME,
    PREFIX,
    leftovers,
    make_request,
    replace_in,
    runner_config,
)

pytestmark = pytest.mark.integration

SampleCopy = Callable[..., Path]


def assert_nothing_left(engine: SdkDockerEngine) -> None:
    assert leftovers(engine) == []
    assert engine.volume_exists(CACHE_VOLUME)


def test_sample_api_builds_runs_and_answers_probes(
    docker_runner: DockerCodeRunner,
    engine: SdkDockerEngine,
    sample_copy: SampleCopy,
    tmp_path: Path,
) -> None:
    result = docker_runner.execute(make_request(sample_copy(), tmp_path / "logs"))

    assert result.steps == (StepStatus.SUCCEEDED,) * 3, result.logs_excerpt
    assert [(r.probe.path, r.actual_status, r.passed) for r in result.probe_results] == [
        ("/api/products", 200, True),
        ("/api/products/999", 404, True),
        ("/api/products", 201, True),
    ]
    written = sorted(path.name for path in (tmp_path / "logs").iterdir())
    assert written == ["build.log", "prepare.log", "restore.log", "run.log"]
    assert "Restored" in (tmp_path / "logs" / "restore.log").read_text(encoding="utf-8")
    assert_nothing_left(engine)


def test_compile_error_is_a_build_failure(
    docker_runner: DockerCodeRunner,
    engine: SdkDockerEngine,
    sample_copy: SampleCopy,
    tmp_path: Path,
) -> None:
    def break_build(repo: Path) -> None:
        replace_in(
            repo / "Controllers" / "ProductsController.cs",
            "return Ok(products);",
            "return Ok(produts);",
        )

    result = docker_runner.execute(make_request(sample_copy(edit=break_build), tmp_path / "logs"))

    assert result.failure_kind is FailureKind.BUILD_FAILED
    assert result.steps == (StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.SKIPPED)
    assert "error CS" in result.logs_excerpt
    assert_nothing_left(engine)


def test_exception_at_startup_is_a_startup_failure(
    docker_runner: DockerCodeRunner,
    engine: SdkDockerEngine,
    sample_copy: SampleCopy,
    tmp_path: Path,
) -> None:
    def crash(repo: Path) -> None:
        replace_in(
            repo / "Program.cs",
            "app.Run();",
            'throw new InvalidOperationException("startup failure");',
        )

    result = docker_runner.execute(make_request(sample_copy(edit=crash), tmp_path / "logs"))

    assert result.failure_kind is FailureKind.STARTUP_FAILED
    assert "Unhandled exception" in result.logs_excerpt
    assert_nothing_left(engine)


def test_app_that_never_listens_times_out(
    docker_runner: DockerCodeRunner,
    engine: SdkDockerEngine,
    sample_copy: SampleCopy,
    tmp_path: Path,
) -> None:
    def hang(repo: Path) -> None:
        replace_in(repo / "Program.cs", "app.Run();", "Thread.Sleep(Timeout.Infinite);\napp.Run();")

    result = docker_runner.execute(make_request(sample_copy(edit=hang), tmp_path / "logs"))

    assert result.failure_kind is FailureKind.TIMEOUT
    assert result.steps == (StepStatus.SUCCEEDED, StepStatus.SUCCEEDED, StepStatus.FAILED)
    assert_nothing_left(engine)


class CountingEngine(SdkDockerEngine):
    """Counts container creations, to prove that none happen."""

    created = 0

    def create_container(self, spec: ContainerSpec) -> str:
        CountingEngine.created += 1
        return super().create_container(spec)


def test_missing_web_project_creates_no_containers(
    engine: SdkDockerEngine, docker_runner: DockerCodeRunner, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    (repo / "Core").mkdir(parents=True)
    (repo / "Core" / "Core.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk" />', encoding="utf-8"
    )
    counting = CountingEngine.from_environment(timeout_seconds=60)
    CountingEngine.created = 0

    result = DockerCodeRunner(runner_config(), counting).execute(
        make_request(repo, tmp_path / "logs")
    )

    assert result.failure_kind is FailureKind.PROJECT_NOT_FOUND
    assert CountingEngine.created == 0
    assert_nothing_left(engine)


def test_two_repos_in_parallel_both_succeed(
    docker_runner: DockerCodeRunner,
    engine: SdkDockerEngine,
    sample_copy: SampleCopy,
    tmp_path: Path,
) -> None:
    results: dict[str, ExecutionResult] = {}

    def run(name: str) -> None:
        request = make_request(sample_copy(name), tmp_path / f"logs-{name}", repo_id=f"it__{name}")
        results[name] = docker_runner.execute(request)

    threads = [threading.Thread(target=run, args=(name,)) for name in ("first", "second")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert set(results) == {"first", "second"}
    for result in results.values():
        assert result.failure_kind is None, result.logs_excerpt
        assert [probe.passed for probe in result.probe_results] == [True, True, True]
    assert_nothing_left(engine)


def test_sweep_removes_planted_leftovers_and_keeps_the_cache(
    docker_runner: DockerCodeRunner, engine: SdkDockerEngine
) -> None:
    labels = {PREFIX: "1", f"{PREFIX}.run": "crashed", f"{PREFIX}.kind": "work"}
    engine.create_volume("hw-checker-it-planted-work", labels)
    engine.create_network("hw-checker-it-planted-net", labels, internal=True)
    config = runner_config()
    engine.create_container(
        ContainerSpec(
            "hw-checker-it-planted-app",
            config.runtime_image,
            ("true",),
            labels,
            config.user,
            64,
            0.5,
            16,
        )
    )

    removed = docker_runner.sweep_leftovers()

    assert removed == 3
    assert_nothing_left(engine)


class InspectingEngine(SdkDockerEngine):
    """Captures the app container's configuration and network while it runs."""

    app_attributes: ClassVar[dict[str, Any]] = {}
    network_attributes: ClassVar[dict[str, Any]] = {}

    def start(self, container: str) -> None:
        super().start(container)
        attributes = self._client.containers.get(container).attrs
        if attributes["Config"]["Labels"].get(f"{PREFIX}.kind") == "app":
            InspectingEngine.app_attributes = attributes
            network = attributes["HostConfig"]["NetworkMode"]
            InspectingEngine.network_attributes = self._client.networks.get(network).attrs


def test_run_container_is_isolated(
    engine: SdkDockerEngine,
    docker_runner: DockerCodeRunner,
    sample_copy: SampleCopy,
    tmp_path: Path,
) -> None:
    inspecting = InspectingEngine.from_environment(timeout_seconds=120)

    result = DockerCodeRunner(runner_config(), inspecting).execute(
        make_request(sample_copy(), tmp_path / "logs")
    )

    assert result.failure_kind is None, result.logs_excerpt
    app = InspectingEngine.app_attributes
    host = app["HostConfig"]
    assert app["Config"]["User"] == "1654:1654"
    assert host["ReadonlyRootfs"] is True
    assert host["Privileged"] is False
    assert host["CapDrop"] == ["ALL"]
    assert not host["CapAdd"]
    assert "no-new-privileges" in host["SecurityOpt"]
    assert host["Memory"] == 512 * 1024 * 1024
    assert host["PidsLimit"] == 512
    assert InspectingEngine.network_attributes["Internal"] is True
    mounts = {(mount["Destination"], mount["RW"]) for mount in app["Mounts"]}
    assert mounts == {("/work", False)}
    assert all("docker.sock" not in str(mount.get("Source")) for mount in app["Mounts"])
    assert_nothing_left(engine)
