"""Fixtures for DockerCodeRunner integration tests: real Docker, an isolated label prefix."""

import shutil
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from hw_checker.adapters.docker_runner.engine import Resource, SdkDockerEngine
from hw_checker.adapters.docker_runner.runner import DockerCodeRunner
from hw_checker.adapters.docker_runner.runner_config import ContainerLimits, DockerRunnerConfig
from hw_checker.config.settings import RunnerSettings, StepLimits
from hw_checker.domain import ExecutionRequest, HttpMethod, HttpProbe

FIXTURE = Path(__file__).parents[2] / "fixtures" / "sample_webapi"
PREFIX = "hw-checker-it"
CACHE_VOLUME = "hw-checker-it-nuget"


def limits(step: StepLimits) -> ContainerLimits:
    return ContainerLimits(memory_mb=step.memory_mb, cpus=step.cpus, pids=step.pids)


def runner_config() -> DockerRunnerConfig:
    """The real pinned images and limits, with short timeouts and test-only names."""
    settings = RunnerSettings()
    return DockerRunnerConfig(
        sdk_image=settings.sdk_image,
        runtime_image=settings.runtime_image,
        probe_image=settings.probe_image,
        restore_limits=limits(settings.restore_limits),
        build_limits=limits(settings.build_limits),
        run_limits=limits(settings.run_limits),
        probe_limits=limits(settings.probe_limits),
        restore_timeout_seconds=300,
        build_timeout_seconds=300,
        readiness_timeout_seconds=20,
        probe_timeout_seconds=5,
        readiness_poll_seconds=0.5,
        app_port=settings.app_port,
        app_uid=settings.app_uid,
        app_gid=settings.app_gid,
        nuget_cache_volume=CACHE_VOLUME,
        label_prefix=PREFIX,
        max_archive_bytes=settings.max_archive_mb * 1024 * 1024,
        max_file_bytes=settings.max_file_mb * 1024 * 1024,
    )


SAMPLE_PROBES = (
    HttpProbe(
        requirement_id="hw01-r1",
        assignment_id="hw01",
        method=HttpMethod.GET,
        path="/api/products",
        expected_status=200,
    ),
    HttpProbe(
        requirement_id="hw01-r2",
        assignment_id="hw01",
        method=HttpMethod.GET,
        path="/api/products/999",
        expected_status=404,
    ),
    HttpProbe(
        requirement_id="hw01-r3",
        assignment_id="hw01",
        method=HttpMethod.POST,
        path="/api/products",
        body='{"name":"Desk","price":250}',
        expected_status=201,
    ),
)


@pytest.fixture(scope="session")
def engine() -> SdkDockerEngine:
    docker_engine = SdkDockerEngine.from_environment(timeout_seconds=120)
    docker_engine.ping()  # fails the session (not a skip) when Docker Desktop is not running
    return docker_engine


@pytest.fixture(scope="session")
def docker_runner(engine: SdkDockerEngine) -> Iterator[DockerCodeRunner]:
    runner = DockerCodeRunner(runner_config(), engine)
    runner.sweep_leftovers()
    runner.check_environment()
    yield runner
    runner.sweep_leftovers()
    engine.remove_volume(CACHE_VOLUME)


def leftovers(engine: SdkDockerEngine) -> list[Resource]:
    """Labelled resources other than the package cache."""
    return [
        resource
        for resource in engine.list_labelled(PREFIX)
        if resource.labels.get(f"{PREFIX}.kind") != "cache"
    ]


@pytest.fixture
def sample_copy(tmp_path: Path) -> Callable[..., Path]:
    """Copy the sample API into tmp_path, optionally changing one file."""

    def copy(name: str = "repo", edit: Callable[[Path], None] | None = None) -> Path:
        target = tmp_path / name
        shutil.copytree(FIXTURE, target, ignore=shutil.ignore_patterns("bin", "obj"))
        if edit is not None:
            edit(target)
        return target

    return copy


def make_request(
    working_copy: Path, logs_dir: Path, repo_id: str = "it__sample"
) -> ExecutionRequest:
    return ExecutionRequest(
        run_id="it-run",
        repo_id=repo_id,
        working_copy=working_copy,
        probes=SAMPLE_PROBES,
        logs_dir=logs_dir,
    )


def replace_in(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} not found in {path}"
    path.write_text(text.replace(old, new), encoding="utf-8")
