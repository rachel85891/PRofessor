"""Fixtures for DockerCodeRunner integration tests: real Docker, an isolated label prefix."""

import json
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from hw_checker.adapters.docker_runner.engine import Resource, SdkDockerEngine
from hw_checker.adapters.docker_runner.runner import DockerCodeRunner
from hw_checker.adapters.docker_runner.runner_config import ContainerLimits, DockerRunnerConfig
from hw_checker.config.settings import RunnerSettings, StepLimits
from hw_checker.domain import ExecutionRequest, HttpMethod, HttpProbe

FIXTURES = Path(__file__).parents[2] / "fixtures"
FIXTURE = FIXTURES / "sample_webapi"
ABSOLUTE_PATH_FIXTURE = FIXTURES / "sample_webapi_absolute_path"
USER_SIGNIN_FIXTURE = FIXTURES / "sample_webapi_user_signin"
TWO_CONTROLLERS_FIXTURE = FIXTURES / "sample_webapi_two_controllers"
PROJECT = Path("WebApi") / "WebApi"
"""Where the sample fixture's Web project sits, relative to the fixture root."""
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
        probe_timeout_seconds=settings.probe_timeout_seconds,
        readiness_poll_seconds=0.5,
        app_port=settings.app_port,
        app_uid=settings.app_uid,
        app_gid=settings.app_gid,
        nuget_cache_volume=CACHE_VOLUME,
        label_prefix=PREFIX,
        max_archive_bytes=settings.max_archive_mb * 1024 * 1024,
        max_file_bytes=settings.max_file_mb * 1024 * 1024,
    )


def probe(
    requirement_id: str,
    method: HttpMethod,
    path: str,
    status: int,
    body: str | None = None,
    alternatives: tuple[str, ...] = (),
) -> HttpProbe:
    return HttpProbe(
        requirement_id=requirement_id,
        assignment_id="hw01",
        method=method,
        path=path,
        path_alternatives=alternatives,
        body=body,
        expected_status=status,
    )


TEST_PASSWORD = "pw123"  # pragma: allowlist secret
"""A made-up password for the fixture's login endpoint."""


def user_body(user_name: str, password: str, **names: str) -> str:
    """A JSON body for the users API, with the field names student projects use."""
    return json.dumps({"userName": user_name, "password": password, **names})


NEW_USER = user_body("newuser", TEST_PASSWORD, firstName="New", lastName="User")
SAMPLE_PROBES = (
    probe("hw01-r1", HttpMethod.GET, "/api/users", 200),
    probe("hw01-r2", HttpMethod.GET, "/api/users/3", 404),
    probe("hw01-r3", HttpMethod.POST, "/api/users", 201, NEW_USER),
    probe("hw01-r3", HttpMethod.GET, "/api/users/3", 200),
    probe("hw01-r4", HttpMethod.POST, "/api/users/login", 200, user_body("newuser", TEST_PASSWORD)),
    probe("hw01-r4", HttpMethod.POST, "/api/users/login", 401, user_body("newuser", "wrong")),
    probe("hw01-r5", HttpMethod.PUT, "/api/users/3", 204, NEW_USER.replace("New", "Renamed")),
    probe("hw01-r5", HttpMethod.DELETE, "/api/users/3", 204),
    probe("hw01-r5", HttpMethod.GET, "/api/users/3", 404),
)
ABSOLUTE_PATH_PROBES = (
    probe("hw01-r1", HttpMethod.GET, "/api/users", 200),
    probe("hw01-r1", HttpMethod.POST, "/api/users", 201, user_body("u2", TEST_PASSWORD)),
    probe("hw01-r1", HttpMethod.GET, "/api/users/2", 200),
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

    def copy(
        name: str = "repo",
        edit: Callable[[Path], None] | None = None,
        source: Path = FIXTURE,
    ) -> Path:
        target = tmp_path / name
        shutil.copytree(source, target, ignore=shutil.ignore_patterns("bin", "obj"))
        if edit is not None:
            edit(target)
        return target

    return copy


def make_request(
    working_copy: Path,
    logs_dir: Path,
    repo_id: str = "it__sample",
    probes: tuple[HttpProbe, ...] = SAMPLE_PROBES,
) -> ExecutionRequest:
    return ExecutionRequest(
        run_id="it-run",
        repo_id=repo_id,
        working_copy=working_copy,
        probes=probes,
        logs_dir=logs_dir,
    )


def replace_in(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} not found in {path}"
    path.write_text(text.replace(old, new), encoding="utf-8")


LOGIN_ALTERNATIVES = ("/api/user/login", "/api/users/signin", "/api/user/signin")
USER_SIGNIN_PROBES = (
    probe("hw01-r1", HttpMethod.GET, "/api/users", 200, alternatives=("/api/user",)),
    probe(
        "hw01-r1",
        HttpMethod.POST,
        "/api/users",
        201,
        user_body("newuser", TEST_PASSWORD),
        alternatives=("/api/user",),
    ),
    probe("hw01-r1", HttpMethod.GET, "/api/users/2", 200, alternatives=("/api/user/2",)),
    probe(
        "hw01-r2",
        HttpMethod.POST,
        "/api/users/login",
        200,
        user_body("newuser", TEST_PASSWORD),
        alternatives=LOGIN_ALTERNATIVES,
    ),
    probe("hw01-r3", HttpMethod.DELETE, "/api/users/2", 204, alternatives=("/api/user/2",)),
)
"""Against `sample_webapi_user_signin`: every route is singular, login is `signin`, and
there is no DELETE action, so the hw01-r3 probe has no declared route."""


def user_or_users(
    method: HttpMethod, singular: str, status: int, body: str | None = None
) -> HttpProbe:
    """A probe naming the singular route first and the plural one as the alternative."""
    return probe(
        "hw01-r1",
        method,
        singular,
        status,
        body,
        alternatives=(singular.replace("/api/user", "/api/users", 1),),
    )


TWO_CONTROLLERS_PROBES = (
    user_or_users(HttpMethod.POST, "/api/user", 201, user_body("newuser", TEST_PASSWORD)),
    user_or_users(HttpMethod.GET, "/api/user/3", 200),
    user_or_users(HttpMethod.PUT, "/api/user/3", 204, user_body("renamed", TEST_PASSWORD)),
    user_or_users(HttpMethod.DELETE, "/api/user/3", 204),
    user_or_users(HttpMethod.GET, "/api/user/3", 404),
    probe(
        "hw01-r2",
        HttpMethod.POST,
        "/api/users/login",
        200,
        user_body("tester1", "pass1"),
        alternatives=("/api/user/login",),
    ),
)
"""Against `sample_webapi_two_controllers`: every probe but the login can go to either
controller by its candidates, and only UsersController holds the user the POST creates."""
