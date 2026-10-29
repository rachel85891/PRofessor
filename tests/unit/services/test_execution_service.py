from pathlib import Path

import pytest

from hw_checker.domain import (
    Assignment,
    ExecutionResult,
    FailureKind,
    HttpMethod,
    Requirement,
    RequirementKind,
    StepStatus,
)
from hw_checker.domain.errors import ExecutionEnvironmentError
from hw_checker.services.execution import ExecutionService
from tests.fakes.execution import SUCCEEDED, FakeCodeRunner
from tests.unit.domain import builders

WORKING_COPY = Path("clones") / "dana-api"


def http_requirement(assignment_id: str, requirement_id: str, *paths: str) -> Requirement:
    return builders.requirement(
        id=requirement_id,
        kind=RequirementKind.HTTP,
        http_probes=tuple(
            builders.probe(requirement_id=requirement_id, assignment_id=assignment_id, path=path)
            for path in paths
        ),
    )


GENERAL = Assignment(
    id="_general",
    order=0,
    title="General",
    requirements=(builders.requirement(id="gen-build", kind=RequirementKind.BUILD),),
)
HW01 = Assignment(
    id="hw01",
    order=1,
    title="Users",
    requirements=(
        http_requirement("hw01", "hw01-list", "/api/users", "/api/users/1"),
        builders.requirement(id="hw01-code", kind=RequirementKind.CODE),
        http_requirement("hw01", "hw01-login", "/api/users/login"),
    ),
)


def service(runner: FakeCodeRunner, tmp_path: Path) -> ExecutionService:
    return ExecutionService(runner, tmp_path / "runs", Path("logs"))


def test_execute_sends_every_http_probe_of_the_assignments_in_order(tmp_path: Path) -> None:
    runner = FakeCodeRunner()

    result = service(runner, tmp_path).execute("run-1", "dana-api", WORKING_COPY, (GENERAL, HW01))

    assert result == SUCCEEDED
    [request] = runner.requests
    assert [(probe.method, probe.path) for probe in request.probes] == [
        (HttpMethod.GET, "/api/users"),
        (HttpMethod.GET, "/api/users/1"),
        (HttpMethod.GET, "/api/users/login"),
    ]
    assert (request.run_id, request.repo_id, request.working_copy) == (
        "run-1",
        "dana-api",
        WORKING_COPY,
    )


def test_execute_writes_logs_under_the_run_and_repo(tmp_path: Path) -> None:
    runner = FakeCodeRunner()

    service(runner, tmp_path).execute("run-1", "dana-api", WORKING_COPY, (GENERAL,))

    assert runner.requests[0].logs_dir == tmp_path / "runs" / "run-1" / "logs" / "dana-api"
    assert runner.requests[0].probes == ()


def test_execute_returns_the_runner_result_for_a_failed_build(tmp_path: Path) -> None:
    failed = ExecutionResult(
        build_status=StepStatus.FAILED,
        run_status=StepStatus.SKIPPED,
        failure_kind=FailureKind.BUILD_FAILED,
    )
    runner = FakeCodeRunner(results={"dana-api": failed})

    result = service(runner, tmp_path).execute("run-1", "dana-api", WORKING_COPY, (HW01,))

    assert result == failed


def test_execute_raises_when_the_engine_cannot_be_reached(tmp_path: Path) -> None:
    runner = FakeCodeRunner(errors={"dana-api": ExecutionEnvironmentError("no engine")})

    with pytest.raises(ExecutionEnvironmentError):
        service(runner, tmp_path).execute("run-1", "dana-api", WORKING_COPY, (HW01,))


def test_prepare_sweeps_leftovers_before_checking_the_environment(tmp_path: Path) -> None:
    runner = FakeCodeRunner(leftovers=2)

    service(runner, tmp_path).prepare()

    assert runner.calls == ["sweep_leftovers", "check_environment"]


def test_prepare_raises_when_the_environment_is_not_ready(tmp_path: Path) -> None:
    runner = FakeCodeRunner(environment_error=ExecutionEnvironmentError("image missing"))

    with pytest.raises(ExecutionEnvironmentError, match="image missing"):
        service(runner, tmp_path).prepare()
