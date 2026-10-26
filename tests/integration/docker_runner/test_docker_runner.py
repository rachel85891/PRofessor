"""DockerCodeRunner against real Docker Desktop, with copies of the sample Web API."""

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

import pytest

from hw_checker.adapters.docker_runner.engine import ContainerSpec, SdkDockerEngine
from hw_checker.adapters.docker_runner.runner import DockerCodeRunner
from hw_checker.checks.deterministic.builtin import builtin_checks
from hw_checker.checks.deterministic.checker import DeterministicRequirementChecker
from hw_checker.checks.registry import CheckRegistry
from hw_checker.domain import (
    Assignment,
    CaptureFailure,
    CaptureResult,
    ExecutionResult,
    FailureKind,
    Finding,
    HttpMethod,
    HttpProbe,
    NotEvaluated,
    NotEvaluatedReason,
    PathFixKind,
    PathNotRewrittenReason,
    Requirement,
    RequirementKind,
    RouteResolution,
    Severity,
    StepStatus,
)
from hw_checker.services.deterministic_checks import DeterministicCheckService
from tests.integration.docker_runner.conftest import (
    ABSOLUTE_PATH_FIXTURE,
    ABSOLUTE_PATH_PROBES,
    CACHE_VOLUME,
    CHAINED_PROBES,
    CUSTOM_MODEL_FIXTURE,
    CUSTOM_MODEL_PROBES,
    ITEMS_PROBES,
    LOGIN_VARIANT_FIXTURES,
    NO_ID_FIXTURE,
    NO_ID_PROBES,
    OLDER_FRAMEWORK_FIXTURES,
    PREFIX,
    PROJECT,
    SAMPLE_PROBES,
    TEST_PASSWORD,
    TWO_CONTROLLERS_FIXTURE,
    TWO_CONTROLLERS_PROBES,
    USER_SIGNIN_FIXTURE,
    USER_SIGNIN_PROBES,
    leftovers,
    login_variant_probes,
    make_request,
    replace_in,
    runner_config,
)

pytestmark = pytest.mark.integration

SampleCopy = Callable[..., Path]


def assert_nothing_left(engine: SdkDockerEngine) -> None:
    assert leftovers(engine) == []
    assert engine.volume_exists(CACHE_VOLUME)


def statuses(result: ExecutionResult) -> list[tuple[str, str, int | None]]:
    return [(r.probe.method.value, r.probe.path, r.actual_status) for r in result.probe_results]


def describe(result: ExecutionResult) -> str:
    """Every probe with its status and error, then the log excerpt, for assertion messages."""
    probes = "\n".join(
        f"{r.probe.method.value} {r.probe.path}: {r.actual_status} {r.error}".rstrip()
        for r in result.probe_results
    )
    return f"{probes}\n{result.logs_excerpt}"


def all_passed(result: ExecutionResult) -> bool:
    return bool(result.probe_results) and all(r.passed for r in result.probe_results)


def test_sample_api_writes_and_reads_its_text_file_and_answers_every_probe(
    docker_runner: DockerCodeRunner,
    engine: SdkDockerEngine,
    sample_copy: SampleCopy,
    tmp_path: Path,
) -> None:
    repo = sample_copy()
    data_file = repo / PROJECT / "users.txt"
    before = data_file.read_bytes()

    result = docker_runner.execute(make_request(repo, tmp_path / "logs"))

    assert result.steps == (StepStatus.SUCCEEDED,) * 3, result.logs_excerpt
    assert statuses(result) == [
        ("GET", "/api/users", 200),
        ("GET", "/api/users/3", 404),
        ("POST", "/api/users", 201),
        ("GET", "/api/users/3", 200),  # the POST was written to users.txt and read back
        ("POST", "/api/users/login", 200),  # login with the created user
        ("POST", "/api/users/login", 401),
        ("PUT", "/api/users/3", 204),
        ("DELETE", "/api/users/3", 204),
        ("GET", "/api/users/3", 404),
    ]
    assert all_passed(result), describe(result)
    assert all(
        r.route_resolution is RouteResolution.RESOLVED
        and r.sent_path == r.probe.path
        and r.controller == "UsersController"
        for r in result.probe_results
    )
    assert (result.primary_controller, result.serving_controllers) == (
        "UsersController",
        ("UsersController",),
    )
    assert result.path_rewrites == ()
    created = result.probe_results[2]
    assert '"userName":"newuser"' in created.response_excerpt, describe(result)
    assert result.probe_results[3].response_excerpt.startswith('{"id":3')
    assert result.routes is not None and result.routes.usable
    assert {(r.method, r.template) for r in result.routes.routes} == {
        (HttpMethod.GET, "api/Users"),
        (HttpMethod.GET, "api/Users/{id}"),
        (HttpMethod.POST, "api/Users"),
        (HttpMethod.POST, "api/Users/login"),
        (HttpMethod.PUT, "api/Users/{id}"),
        (HttpMethod.DELETE, "api/Users/{id}"),
    }
    assert data_file.read_bytes() == before, "the student's working copy is never written to"
    written = sorted(path.name for path in (tmp_path / "logs").iterdir())
    assert written == ["build.log", "prepare.log", "restore.log", "run.log"]
    assert "Restored" in (tmp_path / "logs" / "restore.log").read_text(encoding="utf-8")
    # UseHttpsRedirection finds no HTTPS port in the container, so it only logs a warning
    # and never redirects the HTTP probes.
    run_log = (tmp_path / "logs" / "run.log").read_text(encoding="utf-8")
    assert "Failed to determine the https port for redirect" in run_log
    assert_nothing_left(engine)


def test_long_run_and_repo_ids_do_not_affect_probing(
    docker_runner: DockerCodeRunner,
    engine: SdkDockerEngine,
    sample_copy: SampleCopy,
    tmp_path: Path,
) -> None:
    run_id = "round-2026-10-25-hw01-second-check"
    repo_id = "it__a-long-student-github-name__web-api-homework-repository"
    assert len(f"{PREFIX}-{run_id}-{repo_id}-app-12345678") > 63

    result = docker_runner.execute(
        make_request(sample_copy(), tmp_path / "logs", repo_id=repo_id, run_id=run_id)
    )

    assert result.steps == (StepStatus.SUCCEEDED,) * 3, describe(result)
    assert all_passed(result), describe(result)
    assert_nothing_left(engine)


@pytest.mark.parametrize("framework", sorted(OLDER_FRAMEWORK_FIXTURES))
def test_older_framework_builds_runs_and_is_marked(
    docker_runner: DockerCodeRunner,
    engine: SdkDockerEngine,
    sample_copy: SampleCopy,
    tmp_path: Path,
    framework: str,
) -> None:
    repo = sample_copy(source=OLDER_FRAMEWORK_FIXTURES[framework])

    result = docker_runner.execute(make_request(repo, tmp_path / "logs", probes=ITEMS_PROBES))

    assert result.steps == (StepStatus.SUCCEEDED,) * 3, describe(result)
    assert statuses(result) == [("GET", "/api/items", 200), ("POST", "/api/items", 201)]
    assert (result.target_framework, result.framework_outdated) == (framework, True)
    prepare_log = (tmp_path / "logs" / "prepare.log").read_text(encoding="utf-8")
    assert f"({framework})" in prepare_log
    assert_nothing_left(engine)


def test_written_data_does_not_outlive_the_run(
    docker_runner: DockerCodeRunner,
    engine: SdkDockerEngine,
    sample_copy: SampleCopy,
    tmp_path: Path,
) -> None:
    repo = sample_copy()

    first = docker_runner.execute(make_request(repo, tmp_path / "logs-1"))
    second = docker_runner.execute(make_request(repo, tmp_path / "logs-2"))

    # Each run starts from the committed users.txt: user 3 does not exist until it is posted.
    assert statuses(first) == statuses(second), describe(second)
    assert all_passed(first), describe(first)
    assert all_passed(second), describe(second)
    assert_nothing_left(engine)


def test_file_committed_with_other_letter_case_is_found_as_on_windows(
    docker_runner: DockerCodeRunner,
    engine: SdkDockerEngine,
    sample_copy: SampleCopy,
    tmp_path: Path,
) -> None:
    def rename(repo: Path) -> None:
        (repo / PROJECT / "users.txt").rename(repo / PROJECT / "Users.txt")

    result = docker_runner.execute(make_request(sample_copy(edit=rename), tmp_path / "logs"))

    (rewrite,) = result.path_rewrites
    assert (rewrite.kind, rewrite.original, rewrite.rewritten) == (
        PathFixKind.CASE,
        "users.txt",
        "Users.txt",
    )
    assert rewrite.file_path == "WebApi/WebApi/Controllers/UsersController.cs"
    assert all_passed(result), describe(result)
    assert_nothing_left(engine)


def test_absolute_path_with_a_matching_file_is_rewritten_and_passes(
    docker_runner: DockerCodeRunner,
    engine: SdkDockerEngine,
    sample_copy: SampleCopy,
    tmp_path: Path,
) -> None:
    repo = sample_copy(source=ABSOLUTE_PATH_FIXTURE)
    controller = repo / "Controllers" / "UsersController.cs"
    before = controller.read_bytes()

    result = docker_runner.execute(
        make_request(repo, tmp_path / "logs", probes=ABSOLUTE_PATH_PROBES)
    )

    (rewrite,) = result.path_rewrites
    assert rewrite.kind is PathFixKind.ABSOLUTE
    assert rewrite.original.startswith("C:\\Users\\Developer\\")
    assert rewrite.rewritten == "Data/users.txt"
    assert not result.has_unresolved_paths
    assert statuses(result) == [
        ("GET", "/api/users", 200),
        ("POST", "/api/users", 201),
        ("GET", "/api/users/2", 200),
    ], result.logs_excerpt
    assert controller.read_bytes() == before
    assert_nothing_left(engine)


def test_absolute_path_without_a_matching_file_is_flagged_and_gives_no_findings(
    docker_runner: DockerCodeRunner,
    engine: SdkDockerEngine,
    sample_copy: SampleCopy,
    tmp_path: Path,
) -> None:
    def remove_data(repo: Path) -> None:
        (repo / "Data" / "users.txt").unlink()

    result = docker_runner.execute(
        make_request(
            sample_copy(edit=remove_data, source=ABSOLUTE_PATH_FIXTURE),
            tmp_path / "logs",
            probes=ABSOLUTE_PATH_PROBES,
        )
    )

    (rewrite,) = result.path_rewrites
    assert rewrite.not_rewritten_reason is PathNotRewrittenReason.NO_MATCH
    assert result.has_unresolved_paths
    assert result.run_status is StepStatus.SUCCEEDED, result.logs_excerpt
    assert result.probe_results and not all_passed(result), describe(result)
    requirement = Requirement(
        id="hw01-r1",
        title="Users API",
        severity=Severity.MAJOR,
        kind=RequirementKind.HTTP,
        http_probes=ABSOLUTE_PATH_PROBES,
    )
    assignment = Assignment(id="hw01", order=1, title="Users", requirements=(requirement,))
    checker = DeterministicRequirementChecker(CheckRegistry(builtin_checks()))
    checked = DeterministicCheckService(checker).evaluate(assignment, tmp_path, result)
    assert checked.findings == ()
    assert checked.not_evaluated == (
        NotEvaluated(requirement_id="hw01-r1", reason=NotEvaluatedReason.ENVIRONMENT_ISSUE),
    )
    assert_nothing_left(engine)


def test_probe_alternatives_reach_the_routes_the_student_declared(
    docker_runner: DockerCodeRunner,
    engine: SdkDockerEngine,
    sample_copy: SampleCopy,
    tmp_path: Path,
) -> None:
    repo = sample_copy(source=USER_SIGNIN_FIXTURE)

    result = docker_runner.execute(make_request(repo, tmp_path / "logs", probes=USER_SIGNIN_PROBES))

    assert [(r.sent_path, r.route_resolution, r.actual_status) for r in result.probe_results] == [
        ("/api/user", RouteResolution.RESOLVED, 200),
        ("/api/user", RouteResolution.RESOLVED, 201),
        ("/api/user/2", RouteResolution.RESOLVED, 200),
        ("/api/user/signin", RouteResolution.RESOLVED, 200),
        (None, RouteResolution.ROUTE_NOT_FOUND, None),
    ], describe(result)
    assert result.routes is not None
    assert {(r.method, r.template) for r in result.routes.routes} == {
        (HttpMethod.GET, "api/User"),
        (HttpMethod.GET, "api/User/{id}"),
        (HttpMethod.POST, "api/User"),
        (HttpMethod.POST, "api/User/signin"),
    }
    requirements = tuple(
        Requirement(
            id=requirement_id,
            title=requirement_id,
            severity=Severity.MAJOR,
            kind=RequirementKind.HTTP,
            http_probes=tuple(p for p in USER_SIGNIN_PROBES if p.requirement_id == requirement_id),
        )
        for requirement_id in ("hw01-r1", "hw01-r2", "hw01-r3")
    )
    assignment = Assignment(id="hw01", order=1, title="Users", requirements=requirements)
    checker = DeterministicRequirementChecker(CheckRegistry(builtin_checks()))
    checked = DeterministicCheckService(checker).evaluate(assignment, repo, result)
    assert checked.findings == ()
    assert checked.not_evaluated == (
        NotEvaluated(requirement_id="hw01-r3", reason=NotEvaluatedReason.ROUTE_NOT_FOUND),
    )
    assert_nothing_left(engine)


def test_probes_go_to_one_controller_when_the_student_has_two(
    docker_runner: DockerCodeRunner,
    engine: SdkDockerEngine,
    sample_copy: SampleCopy,
    tmp_path: Path,
) -> None:
    repo = sample_copy(source=TWO_CONTROLLERS_FIXTURE)

    result = docker_runner.execute(
        make_request(repo, tmp_path / "logs", probes=TWO_CONTROLLERS_PROBES)
    )

    assert [(r.sent_path, r.controller, r.actual_status) for r in result.probe_results] == [
        ("/api/users", "UsersController", 201),
        ("/api/users/3", "UsersController", 200),
        ("/api/users/3", "UsersController", 204),
        ("/api/users/3", "UsersController", 204),
        ("/api/users/3", "UsersController", 404),
        ("/api/user/login", "UserController", 200),  # only the other controller serves it
    ], describe(result)
    assert all_passed(result), describe(result)
    assert result.primary_controller == "UsersController"
    assert result.serving_controllers == ("UserController", "UsersController")
    prepare_log = (tmp_path / "logs" / "prepare.log").read_text(encoding="utf-8")
    assert "probes sent to UsersController when it serves them" in prepare_log
    assert_nothing_left(engine)


def test_probe_bodies_are_adapted_to_the_students_model(
    docker_runner: DockerCodeRunner,
    engine: SdkDockerEngine,
    sample_copy: SampleCopy,
    tmp_path: Path,
) -> None:
    repo = sample_copy(source=CUSTOM_MODEL_FIXTURE)

    result = docker_runner.execute(
        make_request(repo, tmp_path / "logs", probes=CUSTOM_MODEL_PROBES)
    )

    register, login = result.probe_results
    assert register.actual_status == 201, describe(result)
    assert register.sent_body == '{"userName":"newuser","password":12345,"name":"probe-name"}'
    assert register.body_adaptation is not None and not register.body_adaptation.mismatched
    assert login.actual_status == 401, describe(result)
    assert login.body_adaptation is not None and login.body_adaptation.mismatched
    assert [(n.field, n.outcome.value) for n in login.body_adaptation.notes] == [
        ("userName", "unmatched"),
        ("password", "converted"),
        ("Email", "filled"),
    ]
    requirements = tuple(
        Requirement(
            id=requirement_id,
            title=requirement_id,
            severity=Severity.MAJOR,
            kind=RequirementKind.HTTP,
            http_probes=tuple(p for p in CUSTOM_MODEL_PROBES if p.requirement_id == requirement_id),
        )
        for requirement_id in ("hw01-r1", "hw01-r2")
    )
    assignment = Assignment(id="hw01", order=1, title="Users", requirements=requirements)
    checker = DeterministicRequirementChecker(CheckRegistry(builtin_checks()))
    checked = DeterministicCheckService(checker).evaluate(assignment, repo, result)
    assert checked.findings == ()
    assert checked.not_evaluated == (
        NotEvaluated(requirement_id="hw01-r2", reason=NotEvaluatedReason.BODY_MISMATCH),
    )
    assert_nothing_left(engine)


def evaluate(
    probes: tuple[HttpProbe, ...], repo: Path, result: ExecutionResult
) -> tuple[tuple[Finding, ...], tuple[NotEvaluated, ...]]:
    """Run the real deterministic checks over one http requirement per requirement id."""
    ids = list(dict.fromkeys(p.requirement_id for p in probes))
    requirements = tuple(
        Requirement(
            id=requirement_id,
            title=requirement_id,
            severity=Severity.MAJOR,
            kind=RequirementKind.HTTP,
            http_probes=tuple(p for p in probes if p.requirement_id == requirement_id),
        )
        for requirement_id in ids
    )
    assignment = Assignment(id="hw01", order=1, title="Users", requirements=requirements)
    checker = DeterministicRequirementChecker(CheckRegistry(builtin_checks()))
    checked = DeterministicCheckService(checker).evaluate(assignment, repo, result)
    return checked.findings, checked.not_evaluated


def test_chained_create_read_update_login_delete_scenario(
    docker_runner: DockerCodeRunner,
    engine: SdkDockerEngine,
    sample_copy: SampleCopy,
    tmp_path: Path,
) -> None:
    repo = sample_copy()

    result = docker_runner.execute(make_request(repo, tmp_path / "logs", probes=CHAINED_PROBES))

    assert [(r.probe.method.value, r.sent_path, r.actual_status) for r in result.probe_results] == [
        ("POST", "/api/users", 201),
        ("GET", "/api/users/3", 200),
        ("PUT", "/api/users/3", 204),
        ("POST", "/api/users/login", 200),
        ("POST", "/api/users/login", 401),
        ("DELETE", "/api/users/3", 204),
        ("GET", "/api/users/3", 404),
    ], describe(result)
    assert result.probe_results[0].capture == CaptureResult(
        name="userId", value="3", source_field="id"
    )
    assert evaluate(CHAINED_PROBES, repo, result) == ((), ())
    assert_nothing_left(engine)


def test_a_post_without_an_id_gives_dependency_failed_not_findings(
    docker_runner: DockerCodeRunner,
    engine: SdkDockerEngine,
    sample_copy: SampleCopy,
    tmp_path: Path,
) -> None:
    repo = sample_copy(source=NO_ID_FIXTURE)

    result = docker_runner.execute(make_request(repo, tmp_path / "logs", probes=NO_ID_PROBES))

    post, read = result.probe_results
    assert post.actual_status == 200, describe(result)
    assert post.capture == CaptureResult(name="userId", failure=CaptureFailure.NO_JSON)
    assert read.route_resolution is RouteResolution.DEPENDENCY_MISSING
    assert evaluate(NO_ID_PROBES, repo, result) == (
        (),
        (NotEvaluated(requirement_id="hw01-r2", reason=NotEvaluatedReason.DEPENDENCY_FAILED),),
    )
    assert_nothing_left(engine)


@pytest.mark.parametrize("resource", sorted(LOGIN_VARIANT_FIXTURES))
def test_login_with_field_alternatives_fits_each_login_model(
    docker_runner: DockerCodeRunner,
    engine: SdkDockerEngine,
    sample_copy: SampleCopy,
    tmp_path: Path,
    resource: str,
) -> None:
    repo = sample_copy(source=LOGIN_VARIANT_FIXTURES[resource])
    probes = login_variant_probes(resource)

    result = docker_runner.execute(make_request(repo, tmp_path / "logs", probes=probes))

    assert [r.actual_status for r in result.probe_results] == [201, 200, 401], describe(result)
    login = result.probe_results[1]
    assert login.body_adaptation is not None and not login.body_adaptation.mismatched
    if resource == "customers":
        assert login.sent_body == (
            f'{{"logInEmail":"newuser@example.com","logInPassword":"{TEST_PASSWORD}"}}'
        )
        assert {n.outcome.value for n in login.body_adaptation.notes} == {"matched_by_contains"}
    assert evaluate(probes, repo, result) == ((), ())
    assert_nothing_left(engine)


def test_compile_error_is_a_build_failure(
    docker_runner: DockerCodeRunner,
    engine: SdkDockerEngine,
    sample_copy: SampleCopy,
    tmp_path: Path,
) -> None:
    def break_build(repo: Path) -> None:
        replace_in(
            repo / PROJECT / "Controllers" / "UsersController.cs",
            "return Ok(ReadUsers());",
            "return Ok(ReadUser());",
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
            repo / PROJECT / "Program.cs",
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
        replace_in(
            repo / PROJECT / "Program.cs",
            "app.Run();",
            "Thread.Sleep(Timeout.Infinite);\napp.Run();",
        )

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
        assert [probe.passed for probe in result.probe_results] == [True] * len(SAMPLE_PROBES)
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
            config.helper_image,
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
    assert mounts == {("/work", False), ("/data", True)}
    assert app["Config"]["WorkingDir"] == "/data/project"
    assert all("docker.sock" not in str(mount.get("Source")) for mount in app["Mounts"])
    assert_nothing_left(engine)
